"""Discrete-time survival with censoring (plan 0015 step D, owner decision 4).

**The target, from the frozen label set alone.** Each labelled `as_of_date` becomes one record from its
`max_months` label row (the longest horizon): the month its first distress event falls in (month k is
`(as_of_date, month-end k months on]`, as the label grid counts windows), a merger's month (which
censors, as it censors the label), and the months it was observed. Month k is observed without an event
when its month-end is on or before the label rules' `alive` limit: the cutoff, moved back by the `alive`
lag for a month ending on or after KRZ's launch (plan 0009), exactly as the grid decides `alive`. So a
record never disagrees with its binary labels: distress within h months exactly when its event month is
at most h, `alive` at h exactly when it is observed through month h with no event.

**As known on a day.** A fold trains on what was settled on its eve, as the binary folds do
(`splits.settled_by`): the observed months are cut at the eve's `alive` limit, an event or a merger
counts only once known by the eve, and an event not yet known censors the record before its month. So
no training month ends in or after the test year (the purge, month by month), and the censored rows the
binary models leave out are used up to the month they are known.

**The models.**
- `survival_lightgbm`: LightGBM on monthly person-period rows (one row per month at risk, the target
  whether the event fell in it), with decision 1's settings and the month as one more input; nulls kept.
  The probability of distress within h months is one minus the product of the monthly survival.
- `survival_forest`: scikit-survival's random survival forest on the records (it accepts nulls), the
  comparison; its survival function read at h months.

Both predict the binary models' test rows, so they enter the same cells and the same Brier score. Each is
fitted once per run and test year and serves both horizons. Beside the cells, scikit-survival's
time-dependent metrics on the test year's records: Uno's concordance (IPCW) and the integrated Brier
score, with the censoring distribution estimated from the training records.
"""

# polars's, LightGBM's and scikit-survival's signatures reference types pyright cannot resolve;
# scoped to this module.
# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false
# pyright: reportUnknownArgumentType=false, reportMissingTypeStubs=false

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import date

import lightgbm as lgb
import numpy as np
import numpy.typing as npt
import polars as pl
from sksurv.ensemble import RandomSurvivalForest
from sksurv.metrics import concordance_index_ipcw, integrated_brier_score
from sksurv.util import Surv

from distress_radar.features.contracts import KEY_COLUMNS
from distress_radar.models.classical import (
    Array,
    FitSummary,
    FoldPredictions,
    empty_predictions,
    with_probability,
)
from distress_radar.models.dataset import TARGET, event_day
from distress_radar.models.gbm import feature_matrix, fit_booster, hyperparameters, model_inputs
from distress_radar.models.splits import (
    BacktestConfig,
    Fold,
    LabelTiming,
    LightGbmConfig,
    SurvivalForestConfig,
    events,
)

HAZARD = "survival_lightgbm"
FOREST = "survival_forest"
MONTH = "month"  # the hazard model's extra input: the month at risk, 1 to `max_months`

# A record: one labelled `as_of_date`, its months, and the label columns `events()` reads.
RECORD_COLUMNS = [
    "krs",
    "as_of_date",
    "outcome_class",  # the distress class of its event, else null
    "event_date",
    "event_known_from",
    "trigger_event_type",
    "regime_flag",  # of the `max_months` window
    "cutoff_date",
    "event_month",  # the month of its first distress event; null when none in `max_months`
    "exit_month",  # the month of a merger, which censors; null when none
    "exit_known_from",
]


@dataclass(frozen=True)
class SurvivalRecords:
    frame: pl.DataFrame  # RECORD_COLUMNS, then the feature columns, sorted by krs and as_of_date
    max_months: int


def _month_index(day: pl.Expr) -> pl.Expr:
    return day.dt.year().cast(pl.Int32) * 12 + day.dt.month().cast(pl.Int32)


def survival_records(
    features: pl.DataFrame,
    labels: pl.DataFrame,
    max_months: int,
    distress_classes: Sequence[str],
) -> SurvivalRecords:
    """One record per labelled `as_of_date`, censored rows included, joined to its features."""
    rows = labels.filter(pl.col("horizon_months") == max_months)
    if rows.is_empty():
        raise ValueError(f"the label set has no {max_months}-month rows to build survival from")
    unknown = rows.filter(
        pl.col("outcome_class").is_not_null()
        & (pl.col("outcome_class") != "alive")
        & ~pl.col("outcome_class").is_in(list(distress_classes))
    )
    if not unknown.is_empty():
        classes = sorted(unknown.get_column("outcome_class").unique().to_list())
        raise ValueError(f"outcome classes neither alive nor distress: {classes}")
    month = _month_index(event_day()) - _month_index(pl.col("as_of_date"))
    distress = pl.col("outcome_class").is_in(list(distress_classes))
    # A censored row with a trigger left by merger (the grid's only censoring event).
    merged = pl.col("censored") & pl.col("trigger_event_type").is_not_null()
    frame = rows.select(
        "krs",
        "as_of_date",
        pl.when(distress).then(pl.col("outcome_class")).alias("outcome_class"),
        pl.when(distress).then(pl.col("event_date")).alias("event_date"),
        pl.when(distress).then(pl.col("event_known_from")).alias("event_known_from"),
        pl.when(distress).then(pl.col("trigger_event_type")).alias("trigger_event_type"),
        "regime_flag",
        "cutoff_date",
        pl.when(distress).then(month).cast(pl.Int32).alias("event_month"),
        pl.when(merged).then(month).cast(pl.Int32).alias("exit_month"),
        pl.when(merged).then(pl.col("event_known_from")).alias("exit_known_from"),
    )
    if not frame.filter(pl.col("as_of_date") != pl.col("as_of_date").dt.month_end()).is_empty():
        raise ValueError("survival records count months from month-end as_of_dates")
    feature_columns = [c for c in features.columns if c not in KEY_COLUMNS]
    unmatched = frame.join(features, on=["krs", "as_of_date"], how="anti")
    if not unmatched.is_empty():
        raise ValueError(
            f"{unmatched.height} labelled rows have no feature row, e.g. "
            f"{unmatched.select('krs', 'as_of_date').row(0)}"
        )
    joined = frame.join(
        features.select("krs", "as_of_date", *feature_columns),
        on=["krs", "as_of_date"],
        how="left",
        validate="1:1",
    ).sort("krs", "as_of_date")
    return SurvivalRecords(joined, max_months)


def observed(records: SurvivalRecords, timing: LabelTiming, on: date | None = None) -> pl.DataFrame:
    """Each record's `time` (months observed, 0 to `max_months`) and `event` (whether its last
    observed month holds its distress event), as known on `on`, or by the label set's own cutoff when
    `on` is None. `TARGET` repeats `event`, so `splits.events` counts the distinct events."""
    frame = records.frame
    limit = pl.col("cutoff_date")
    if on is not None:
        limit = pl.min_horizontal(limit, pl.lit(on, dtype=pl.Date))
    months = frame.select(
        "krs",
        "as_of_date",
        limit.alias("limit"),
        pl.int_ranges(1, records.max_months + 1, dtype=pl.Int32).alias("k"),
    ).explode("k", empty_as_null=False)
    n = _month_index(pl.col("as_of_date")) - 1 + pl.col("k")
    month_end = pl.date(n // 12, n % 12 + 1, 1).dt.month_end()
    lagged = (
        pl.col("limit").dt.offset_by(f"-{timing.alive_lag_months}mo")
        if timing.alive_lag_months
        else pl.col("limit")
    )
    alive_limit = pl.when(month_end >= timing.krz_launch).then(lagged).otherwise(pl.col("limit"))
    settled = (
        months.with_columns((month_end <= alive_limit).alias("settled"))
        .group_by("krs", "as_of_date")
        .agg(pl.col("settled").sum().cast(pl.Int32).alias("settled_months"))
    )
    known = pl.col("event_known_from") <= limit
    exit_known = pl.col("exit_known_from") <= limit
    censor = pl.min_horizontal(
        pl.col("settled_months"),
        pl.when(exit_known).then(pl.col("exit_month") - 1),
        pl.when(pl.col("event_month").is_not_null() & ~known).then(pl.col("event_month") - 1),
    )
    has_event = pl.col("event_month").is_not_null() & known
    return (
        frame.join(settled, on=["krs", "as_of_date"], how="left", validate="1:1")
        .with_columns(
            pl.when(has_event)
            .then(pl.col("event_month"))
            .otherwise(pl.max_horizontal(censor, pl.lit(0)))
            .cast(pl.Int32)
            .alias("time"),
            has_event.alias("event"),
            has_event.alias(TARGET),
        )
        .drop("settled_months")
    )


def person_periods(
    x: Array, time: npt.NDArray[np.int32], event: npt.NDArray[np.bool_]
) -> tuple[Array, npt.NDArray[np.bool_]]:
    """One row per record and month at risk (1 to `time`), the month appended as the last column;
    the target is true only in an event's own month."""
    month = (
        np.concatenate([np.arange(1, t + 1, dtype=np.float64) for t in time])
        if time.size
        else (np.empty(0))
    )
    rows = np.repeat(x, time, axis=0)
    y = np.repeat(event, time) & (month == np.repeat(time, time))
    return np.column_stack([rows, month]), y


def hazard_survival(booster: lgb.Booster, x: Array, max_months: int) -> Array:
    """S(k) for k = 1 to `max_months`, per row: the product of one minus each month's hazard."""
    grid = np.column_stack(
        [np.repeat(x, max_months, axis=0), np.tile(np.arange(1, max_months + 1), x.shape[0])]
    )
    hazard = np.asarray(booster.predict(grid), dtype=np.float64).reshape(x.shape[0], max_months)
    return np.cumprod(1.0 - hazard, axis=1)


def forest_survival(forest: RandomSurvivalForest, x: Array, max_months: int) -> Array:
    """S(k) for k = 1 to `max_months`, per row: the forest's step function, 1 before its first
    event time."""
    curves = np.asarray(forest.predict_survival_function(x, return_array=True), dtype=np.float64)
    times = np.asarray(forest.unique_times_, dtype=np.float64)
    at = np.searchsorted(times, np.arange(1, max_months + 1), side="right") - 1
    padded = np.column_stack([np.ones(x.shape[0]), curves])
    return padded[:, at + 1]


def fit_hazard(
    x: Array,
    time: npt.NDArray[np.int32],
    event: npt.NDArray[np.bool_],
    config: LightGbmConfig,
    weight: Array | None = None,
) -> lgb.Booster:
    """Decision 1's LightGBM on the person-period rows; a record's weight repeats on its months."""
    rows, y = person_periods(x, time, event)
    return fit_booster(
        rows,
        y,
        config,
        hyperparameters(config),
        None if weight is None else np.repeat(weight, time),
    )


def fit_forest(
    x: Array,
    time: npt.NDArray[np.int32],
    event: npt.NDArray[np.bool_],
    config: SurvivalForestConfig,
    weight: Array | None = None,
) -> RandomSurvivalForest:
    forest = RandomSurvivalForest(
        n_estimators=config.n_estimators,
        min_samples_leaf=config.min_samples_leaf,
        max_features=config.max_features,
        random_state=config.seed,
        n_jobs=config.n_jobs,
    )
    forest.fit(x, Surv.from_arrays(event, time.astype(np.float64)), sample_weight=weight)
    return forest


@dataclass(frozen=True)
class SurvivalFit:
    """The survival models of one run and test year, and their time-dependent metrics."""

    test_year: int
    columns: list[str]
    max_months: int
    train_records: int
    train_person_months: int
    train_events: int
    note: str  # why not fitted, or ""
    hazard: lgb.Booster | None
    forest: RandomSurvivalForest | None
    metrics: list[dict[str, object]]  # one per model: SURVIVAL_METRICS_SCHEMA


SURVIVAL_METRICS_SCHEMA: dict[str, pl.DataType | type[pl.DataType]] = {
    "model": pl.String,
    "test_year": pl.Int32,
    "train_records": pl.Int64,
    "train_events": pl.Int64,
    "test_records": pl.Int64,
    "test_events": pl.Int64,
    "evaluable": pl.Boolean,
    "note": pl.String,
    "concordance_ipcw": pl.Float64,
    "integrated_brier": pl.Float64,
}


def fit_survival(
    records: SurvivalRecords,
    test_year: int,
    config: BacktestConfig,
    timing: LabelTiming,
) -> SurvivalFit:
    """Both models on the records settled on the eve of `test_year`, and their metrics on the
    records dated in it (as the label set has them)."""
    if config.survival is None or config.lightgbm is None:
        raise ValueError(f"{config.backtest} has no survival model")
    max_months = records.max_months
    columns = model_inputs(records.frame.columns)
    train = observed(records, timing, date(test_year - 1, 12, 31)).filter(
        (pl.col("as_of_date") < date(test_year, 1, 1)) & (pl.col("time") > 0)
    )
    test = observed(records, timing).filter(
        (pl.col("as_of_date").dt.year() == test_year) & (pl.col("time") > 0)
    )
    time = train.get_column("time").to_numpy()
    event = train.get_column("event").to_numpy()
    train_events = events(train).height
    note = ""
    if train.is_empty():
        note = "no training records"
    elif not bool(event.any()):
        note = "the training records hold no event"
    hazard: lgb.Booster | None = None
    forest: RandomSurvivalForest | None = None
    if not note:
        x = feature_matrix(train, columns)
        hazard = fit_hazard(x, time, event, config.lightgbm)
        forest = fit_forest(x, time, event, config.survival.comparison)
    fit = SurvivalFit(
        test_year=test_year,
        columns=columns,
        max_months=max_months,
        train_records=train.height,
        train_person_months=int(time.sum()),
        train_events=train_events,
        note=note,
        hazard=hazard,
        forest=forest,
        metrics=[],
    )
    metrics = [_metrics(fit, model, train, test, config.min_events) for model in (HAZARD, FOREST)]
    return replace(fit, metrics=metrics)


def survival_curves(fit: SurvivalFit, model: str, rows: pl.DataFrame) -> Array:
    """S(1..max_months) per row of `rows`, from a fitted model."""
    x = feature_matrix(rows, fit.columns)
    if model == HAZARD and fit.hazard is not None:
        return hazard_survival(fit.hazard, x, fit.max_months)
    if model == FOREST and fit.forest is not None:
        return forest_survival(fit.forest, x, fit.max_months)
    raise ValueError(f"{model} is not fitted for {fit.test_year}")


def _metrics(
    fit: SurvivalFit, model: str, train: pl.DataFrame, test: pl.DataFrame, min_events: int
) -> dict[str, object]:
    test_events = events(test).height
    row: dict[str, object] = {
        "model": model,
        "test_year": fit.test_year,
        "train_records": fit.train_records,
        "train_events": fit.train_events,
        "test_records": test.height,
        "test_events": test_events,
        "evaluable": False,
        "note": fit.note,
        "concordance_ipcw": None,
        "integrated_brier": None,
    }
    if fit.note:
        return row
    if min(fit.train_events, test_events) < min_events:
        row["note"] = f"fewer than {min_events} events"
        return row
    survival_train = Surv.from_arrays(
        train.get_column("event").to_numpy(), train.get_column("time").to_numpy().astype(float)
    )
    test_time = test.get_column("time").to_numpy().astype(float)
    survival_test = Surv.from_arrays(test.get_column("event").to_numpy(), test_time)
    curves = survival_curves(fit, model, test)
    # IPCW needs the censoring distribution where the test times are: within the training
    # follow-up, and the integrated score within the test follow-up too.
    horizon = int(min(fit.max_months, int(train.get_column("time").to_numpy().max())))
    times = np.arange(1, fit.max_months + 1)
    times = times[(times >= test_time.min()) & (times < min(test_time.max(), horizon))]
    if times.size < 2:
        row["note"] = "the test follow-up is too short"
        return row
    row["concordance_ipcw"] = float(
        concordance_index_ipcw(
            survival_train, survival_test, 1.0 - curves[:, horizon - 1], tau=horizon
        )[0]
    )
    row["integrated_brier"] = float(
        integrated_brier_score(survival_train, survival_test, curves[:, times - 1], times)
    )
    row["evaluable"] = True
    return row


def fold_predictions(fit: SurvivalFit, fold: Fold) -> list[FoldPredictions]:
    """Both models' P(distress within the fold's horizon) on the binary fold's test rows."""
    out: list[FoldPredictions] = []
    for model in (HAZARD, FOREST):
        summary = FitSummary(
            model=model,
            horizon_months=fold.horizon_months,
            test_year=fold.test_year,
            train_rows=fit.train_records,
            train_events=fit.train_events,
            train_rows_left_out=0,
            test_rows=fold.test.height,
            test_events=events(fold.test).height,
            test_rows_left_out=0,
            fitted=not fit.note,
            note=fit.note,
        )
        if fit.note or fold.test.is_empty():
            out.append(FoldPredictions(summary, empty_predictions()))
            continue
        curves = survival_curves(fit, model, fold.test)
        probability = 1.0 - curves[:, fold.horizon_months - 1]
        out.append(FoldPredictions(summary, with_probability(fold.test, probability)))
    return out
