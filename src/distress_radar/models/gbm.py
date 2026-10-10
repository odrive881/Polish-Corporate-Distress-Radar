"""Generation 3: gradient-boosted trees in LightGBM (plan 0015 step C, owner decisions 1–3 and 9).

One binary model per horizon and fold, on the same target and folds as generation 2, reading every
feature of the pinned feature set with its nulls: nothing is imputed (invariant 4), LightGBM routes
a missing value down its own branch. Identifiers, dates, `*__known_from` companions and the label
columns (`regime_flag` among them) are never inputs.

Each fold's training rows are split once more, as `splits.split` splits the dataset: their last
settled year (the latest whose `alive` rows were all settled on the fold's eve; a later year holds
mostly its events, which settle sooner) is the inner validation year, fitted on the rows whose
window closed before it and settled on its eve. That inner split, never the test year, serves two purposes:

- **Tuning** (decision 2): where the inner year holds `min_tuning_events` distinct events, Optuna's
  seeded TPE search picks the settings by the inner year's Brier score; elsewhere decision 1's
  defaults are used, and the summary says why.
- **Calibration** (decision 3): where the inner year holds `min_events` distinct events, a model
  fitted on the inner training rows scores the inner year, and a one-variable logistic map (Platt)
  from its raw score to probability is fitted on those rows. It maps the fold model's test scores,
  which come from a model fitted on all the training rows: the map is learnt where the scores are
  out-of-sample. Elsewhere the raw probability stands, and the summary says so.

Both versions are scored: `lightgbm_raw`, and `lightgbm`, the calibrated one, which is the
model's result. Every fit is byte-reproducible: deterministic, row-wise, one thread, fixed seeds.
The fitting functions take an optional per-row weight (decision 9), unused until list v2.
"""

# polars's, scikit-learn's, LightGBM's and Optuna's signatures reference types pyright cannot
# resolve; scoped to this module.
# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false
# pyright: reportUnknownArgumentType=false, reportMissingTypeStubs=false

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date

import lightgbm as lgb
import numpy as np
import numpy.typing as npt
import optuna
import polars as pl
from sklearn.linear_model import LogisticRegression

from distress_radar.features.contracts import KEY_COLUMNS, KNOWN_FROM_SUFFIX
from distress_radar.models.classical import (
    Array,
    FitSummary,
    FoldPredictions,
    empty_predictions,
    one_class_note,
    with_probability,
)
from distress_radar.models.dataset import LABEL_COLUMNS, TARGET
from distress_radar.models.evaluation import brier
from distress_radar.models.splits import (
    BacktestConfig,
    Fold,
    LabelTiming,
    LightGbmConfig,
    TuningConfig,
    events,
    settled_by,
    split,
)

RAW = "lightgbm_raw"
CALIBRATED = "lightgbm"
# Never a model input, whatever the feature set holds: the row's identity and the label.
NOT_INPUTS = frozenset({*KEY_COLUMNS, TARGET, *LABEL_COLUMNS})


def model_inputs(columns: list[str]) -> list[str]:
    """The feature columns, in order: those with a `*__known_from` companion, the keys and label
    columns never."""
    present = set(columns)
    return [c for c in columns if f"{c}{KNOWN_FROM_SUFFIX}" in present and c not in NOT_INPUTS]


def feature_matrix(rows: pl.DataFrame, columns: list[str]) -> Array:
    """The inputs as floats, a null as NaN (LightGBM's missing value); a flag is 0 or 1."""
    return rows.select(pl.col(columns).cast(pl.Float64)).to_numpy()


def hyperparameters(config: LightGbmConfig) -> dict[str, float]:
    """Decision 1's values of the settings the search may change."""
    return {
        "num_leaves": config.num_leaves,
        "min_data_in_leaf": config.min_data_in_leaf,
        "learning_rate": config.learning_rate,
        "num_iterations": config.num_iterations,
        "lambda_l2": config.lambda_l2,
        "feature_fraction": config.feature_fraction,
    }


def booster_params(config: LightGbmConfig, values: dict[str, float]) -> dict[str, object]:
    """LightGBM's parameters: the fixed ones from the config, the tunable ones from `values`."""
    return {
        "objective": "binary",
        "num_leaves": int(values["num_leaves"]),
        "min_data_in_leaf": int(values["min_data_in_leaf"]),
        "learning_rate": values["learning_rate"],
        "lambda_l2": values["lambda_l2"],
        "feature_fraction": values["feature_fraction"],
        "bagging_fraction": config.bagging_fraction,
        "bagging_freq": 0,
        "use_missing": config.use_missing,
        "zero_as_missing": config.zero_as_missing,
        "deterministic": config.deterministic,
        "force_row_wise": config.force_row_wise,
        "num_threads": config.num_threads,
        "seed": config.seed,
        # A Dataset is built per fit, so its binning need not survive a change of leaf minimum.
        "feature_pre_filter": False,
        "verbose": -1,
    }


def fit_booster(
    x: Array,
    y: npt.NDArray[np.bool_],
    config: LightGbmConfig,
    values: dict[str, float],
    weight: Array | None = None,
) -> lgb.Booster:
    data = lgb.Dataset(x, label=y.astype(np.float64), weight=weight)
    return lgb.train(
        booster_params(config, values), data, num_boost_round=int(values["num_iterations"])
    )


def raw_score(booster: lgb.Booster, x: Array) -> Array:
    """The booster's log-odds, before its own sigmoid."""
    return np.asarray(booster.predict(x, raw_score=True), dtype=np.float64)


def probability(booster: lgb.Booster, x: Array) -> Array:
    return np.asarray(booster.predict(x), dtype=np.float64)


@dataclass(frozen=True)
class PlattMap:
    """P(distress) = 1 / (1 + exp(-(slope * score + intercept))), on a booster's raw score."""

    slope: float
    intercept: float

    def apply(self, score: Array) -> Array:
        return 1.0 / (1.0 + np.exp(-(self.slope * score + self.intercept)))


def fit_platt(score: Array, y: npt.NDArray[np.bool_], weight: Array | None = None) -> PlattMap:
    """The unpenalised one-variable logistic fit of `y` on `score`."""
    model = LogisticRegression(C=np.inf, solver="lbfgs", max_iter=1000)
    model.fit(score.reshape(-1, 1), y, sample_weight=weight)
    slope = float(np.asarray(model.coef_, dtype=np.float64).ravel()[0])
    intercept = float(np.asarray(model.intercept_, dtype=np.float64).ravel()[0])
    return PlattMap(slope, intercept)


def tune(
    train: tuple[Array, npt.NDArray[np.bool_]],
    validation: tuple[Array, npt.NDArray[np.bool_]],
    config: LightGbmConfig,
    tuning: TuningConfig,
) -> dict[str, float]:
    """The settings of the trial with the lowest Brier score on `validation`; untuned settings
    keep decision 1's values."""

    def objective(trial: optuna.Trial) -> float:
        values = hyperparameters(config)
        for name, space in tuning.space.items():
            if space.type == "int":
                values[name] = trial.suggest_int(
                    name, int(space.low), int(space.high), log=space.log
                )
            else:
                values[name] = trial.suggest_float(name, space.low, space.high, log=space.log)
        booster = fit_booster(*train, config, values)
        return brier(validation[1], probability(booster, validation[0]))

    optuna.logging.set_verbosity(optuna.logging.WARNING)
    study = optuna.create_study(
        direction="minimize", sampler=optuna.samplers.TPESampler(seed=tuning.seed)
    )
    study.optimize(objective, n_trials=tuning.trials)
    return {**hyperparameters(config), **study.best_params}


def inner_split(fold: Fold, timing: LabelTiming) -> Fold | None:
    """The fold's training rows split at their last settled year: the latest year whose `alive`
    rows were all settled on the fold's eve, so the year is not left holding mostly its events
    (an `alive` label settles later than an event under the label rules' lag). None when the
    training rows hold no such year."""
    if fold.train.is_empty():
        return None
    first = int(fold.train.select(pl.col("as_of_date").dt.year().min()).item())
    eve = date(fold.test_year - 1, 12, 31)
    for year in range(fold.test_year - 1, first - 1, -1):
        last_alive = pl.DataFrame(
            {"as_of_date": [date(year, 12, 31)], TARGET: [False], "event_known_from": [None]},
            schema={"as_of_date": pl.Date, TARGET: pl.Boolean, "event_known_from": pl.Date},
        )
        if last_alive.select(settled_by(eve, fold.horizon_months, timing)).item():
            return split(fold.train, fold.horizon_months, year, timing)
    return None


def fit_predict_gbm(
    fold: Fold, config: BacktestConfig, timing: LabelTiming
) -> tuple[FoldPredictions, FoldPredictions]:
    """`lightgbm_raw` and `lightgbm` (calibrated where the inner year allows) on one fold."""
    if config.lightgbm is None or config.tuning is None or config.calibration is None:
        raise ValueError(f"{config.backtest} has no generation 3")
    columns = model_inputs(fold.train.columns)
    y = fold.train.get_column(TARGET).to_numpy()
    note = one_class_note(y)
    summary = FitSummary(
        model=RAW,
        horizon_months=fold.horizon_months,
        test_year=fold.test_year,
        train_rows=fold.train.height,
        train_events=events(fold.train).height,
        train_rows_left_out=0,  # nulls are inputs, so no row is incomplete
        test_rows=fold.test.height,
        test_events=events(fold.test).height,
        test_rows_left_out=0,
        fitted=not note,
        note=note,
    )
    if note:
        return (
            FoldPredictions(summary, empty_predictions()),
            FoldPredictions(replace(summary, model=CALIBRATED), empty_predictions()),
        )
    inner = inner_split(fold, timing)
    inner_year = inner.test_year if inner is not None else None
    inner_events = events(inner.test).height if inner is not None else 0
    values = hyperparameters(config.lightgbm)
    platt: PlattMap | None = None
    if inner is not None:
        inner_train = (
            feature_matrix(inner.train, columns),
            inner.train.get_column(TARGET).to_numpy(),
        )
        inner_validation = (
            feature_matrix(inner.test, columns),
            inner.test.get_column(TARGET).to_numpy(),
        )
        inner_note = one_class_note(inner_train[1]) or one_class_note(inner_validation[1])
        tuning_note = _gate(
            inner.test_year, inner_events, inner_note, config.tuning.min_tuning_events, "tuning"
        )
        if not tuning_note:
            values = tune(inner_train, inner_validation, config.lightgbm, config.tuning)
            tuning_note = (
                f"tuned on {inner.test_year} ({inner_events} events, {config.tuning.trials} trials)"
            )
        calibration_note = _gate(
            inner.test_year, inner_events, inner_note, config.min_events, "calibration"
        )
        if not calibration_note:
            inner_booster = fit_booster(*inner_train, config.lightgbm, values)
            platt = fit_platt(raw_score(inner_booster, inner_validation[0]), inner_validation[1])
            calibration_note = f"Platt on {inner.test_year} ({inner_events} events)"
    else:
        tuning_note = "defaults: no settled inner year"
        calibration_note = "raw: no settled inner year"
    booster = fit_booster(feature_matrix(fold.train, columns), y, config.lightgbm, values)
    summary = replace(
        summary,
        hyperparameters=tuple((k, float(v)) for k, v in values.items()),
        inner_year=inner_year,
        inner_events=inner_events,
        tuning=tuning_note,
        calibration=calibration_note,
    )
    calibrated = replace(summary, model=CALIBRATED)
    if fold.test.is_empty():
        return (
            FoldPredictions(summary, empty_predictions()),
            FoldPredictions(calibrated, empty_predictions()),
        )
    x = feature_matrix(fold.test, columns)
    raw = probability(booster, x)
    mapped = platt.apply(raw_score(booster, x)) if platt is not None else raw
    return (
        FoldPredictions(summary, with_probability(fold.test, raw)),
        FoldPredictions(calibrated, with_probability(fold.test, mapped)),
    )


def _gate(year: int, inner_events: int, inner_note: str, needed: int, what: str) -> str:
    """Why the inner year cannot serve `what` (tuning or calibration), or "" when it can."""
    fallback = "defaults" if what == "tuning" else "raw"
    if inner_note:
        return f"{fallback}: inner year {year}: {inner_note}"
    if inner_events < needed:
        return f"{fallback}: inner year {year} holds {inner_events} events, {what} needs {needed}"
    return ""
