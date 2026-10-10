"""Generation 3, LightGBM (plan 0015 step C): inputs, nulls, the inner year, tuning, calibration."""

# polars's signatures reference types pyright cannot resolve; scoped to this module.
# pyright: reportUnknownMemberType=false

from __future__ import annotations

from datetime import date
from typing import Any

import numpy as np
import polars as pl
import pytest
from tests.models.frames import PANEL_RATIOS, TIMING, panel

from distress_radar.features.config import load_feature_set
from distress_radar.models import gbm
from distress_radar.models.backtest import run_backtest
from distress_radar.models.baselines import load_classical_model
from distress_radar.models.dataset import TARGET, modelling_dataset
from distress_radar.models.gbm import (
    CALIBRATED,
    RAW,
    PlattMap,
    feature_matrix,
    fit_platt,
    fit_predict_gbm,
    inner_split,
    model_inputs,
)
from distress_radar.models.report import render_report
from distress_radar.models.splits import (
    BacktestConfig,
    BootstrapConfig,
    Fold,
    load_backtest_config,
    purged_folds,
)

CLASSES = ("bankruptcy", "restructuring", "liquidation", "silent_exit")


def _config(min_tuning_events: int = 30, min_events: int = 3, trials: int = 4) -> BacktestConfig:
    config = load_backtest_config("backtest_v5")
    assert config.tuning is not None
    return config.model_copy(
        update={
            "population": None,
            "min_events": min_events,
            "tuning": config.tuning.model_copy(
                update={"min_tuning_events": min_tuning_events, "trials": trials}
            ),
        }
    )


def _folds(horizon: int = 12, years: tuple[int, ...] = (2023,), nulls: bool = False) -> list[Fold]:
    features, labels = panel(30, seed=3)
    if nulls:  # a third of one ratio missing, as a small entity's simplified form leaves it
        features = features.with_columns(
            pl.when(pl.int_range(pl.len()) % 3 == 0)
            .then(None)
            .otherwise(pl.col("quick_ratio"))
            .alias("quick_ratio")
        )
    dataset = modelling_dataset(features, labels, horizon, CLASSES)
    return purged_folds(dataset, years, TIMING)


def test_the_inputs_are_the_features_never_keys_dates_companions_or_labels() -> None:
    fold = _folds()[0]
    assert model_inputs(fold.train.columns) == list(PANEL_RATIOS)
    # A label column that gained a companion is still not an input.
    columns = [*fold.train.columns, "regime_flag__known_from", "krs__known_from"]
    assert model_inputs(columns) == list(PANEL_RATIOS)


def test_nulls_reach_the_fit_as_missing_values_never_filled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fold = _folds(nulls=True)[0]
    seen: list[np.ndarray[Any, Any]] = []
    fit = gbm.fit_booster

    def spy(x: Any, *args: Any, **kwargs: Any) -> Any:
        seen.append(x.copy())
        return fit(x, *args, **kwargs)

    monkeypatch.setattr(gbm, "fit_booster", spy)
    raw, _ = fit_predict_gbm(fold, _config(), TIMING)
    assert raw.summary.fitted
    assert raw.summary.train_rows == fold.train.height
    assert raw.summary.train_rows_left_out == 0
    nulls = fold.train.get_column("quick_ratio").null_count()
    assert nulls > 0
    column = list(PANEL_RATIOS).index("quick_ratio")
    final = seen[-1]  # the fold model, fitted on every training row
    assert final.shape[0] == fold.train.height
    assert int(np.isnan(final[:, column]).sum()) == nulls
    assert int(np.isnan(np.delete(final, column, axis=1)).sum()) == 0
    assert raw.predictions.height == fold.test.height


def test_a_null_is_nan_and_a_flag_is_zero_or_one() -> None:
    rows = pl.DataFrame({"a": [1.5, None], "b": [True, False], "c": [None, 3]})
    x = feature_matrix(rows, ["a", "b", "c"])
    assert np.isnan(x[1, 0]) and np.isnan(x[0, 2])
    assert x[:, 1].tolist() == [1.0, 0.0]


@pytest.mark.parametrize("horizon", [12, 24])
def test_the_inner_year_is_inside_the_training_rows_and_before_the_test_year(
    horizon: int,
) -> None:
    for fold in _folds(horizon, (2021, 2022, 2023, 2024)):
        inner = inner_split(fold, TIMING)
        assert inner is not None
        assert inner.test_year < fold.test_year
        keys = ["krs", "as_of_date"]
        training = fold.train.select(keys)
        for rows in (inner.train, inner.test):
            assert rows.select(keys).join(training, on=keys, how="anti").is_empty()
            assert rows.select(keys).join(fold.test.select(keys), on=keys).is_empty()
        # The inner year is the last whose `alive` rows are all in: every month of it has one per
        # entity alive then (the panel's entities are all labelled monthly).
        alive = inner.test.filter(~pl.col(TARGET))
        assert alive.get_column("as_of_date").dt.month().n_unique() == 12
        later = fold.train.filter(pl.col("as_of_date").dt.year() == inner.test_year + 1)
        assert later.filter(~pl.col(TARGET)).get_column("as_of_date").dt.month().n_unique() < 12
        if not inner.train.is_empty():  # the earliest 24-month folds have none
            last = inner.train.get_column("as_of_date").max()
            assert isinstance(last, date) and last < date(inner.test_year, 1, 1)


def test_tuning_and_calibration_read_only_the_inner_split(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fold = _folds()[0]
    inner = inner_split(fold, TIMING)
    assert inner is not None
    rows: dict[str, tuple[int, int]] = {}
    tune, platt = gbm.tune, gbm.fit_platt

    def tune_spy(train: Any, validation: Any, *args: Any) -> Any:
        rows["tune"] = (train[0].shape[0], validation[0].shape[0])
        return tune(train, validation, *args)

    def platt_spy(score: Any, y: Any, *args: Any) -> Any:
        rows["platt"] = (0, score.shape[0])
        return platt(score, y, *args)

    monkeypatch.setattr(gbm, "tune", tune_spy)
    monkeypatch.setattr(gbm, "fit_platt", platt_spy)
    fit_predict_gbm(fold, _config(min_tuning_events=1, min_events=2), TIMING)
    assert rows["tune"] == (inner.train.height, inner.test.height)
    assert rows["platt"] == (0, inner.test.height)


def test_below_the_tuning_gate_the_defaults_are_used_and_said() -> None:
    fold = _folds()[0]
    raw, calibrated = fit_predict_gbm(fold, _config(min_events=2), TIMING)
    defaults = gbm.hyperparameters(_config().lightgbm)  # type: ignore[arg-type]
    assert dict(raw.summary.hyperparameters) == defaults
    assert raw.summary.tuning.startswith("defaults: inner year ")
    assert f"holds {raw.summary.inner_events} events, tuning needs 30" in raw.summary.tuning
    assert calibrated.summary.calibration.startswith("Platt on ")


def test_above_the_gate_the_search_picks_the_settings() -> None:
    fold = _folds()[0]
    raw, _ = fit_predict_gbm(fold, _config(min_tuning_events=1), TIMING)
    assert raw.summary.tuning.startswith("tuned on ")
    assert "4 trials" in raw.summary.tuning
    values = dict(raw.summary.hyperparameters)
    space = _config().tuning.space  # type: ignore[union-attr]
    for name, bounds in space.items():
        assert bounds.low <= values[name] <= bounds.high
    assert values != gbm.hyperparameters(_config().lightgbm)  # type: ignore[arg-type]


def test_calibration_maps_the_score_and_the_raw_model_is_kept() -> None:
    fold = _folds()[0]  # its inner year, 2020, holds 2 events
    raw, calibrated = fit_predict_gbm(fold, _config(min_events=2), TIMING)
    assert raw.summary.model == RAW and calibrated.summary.model == CALIBRATED
    p_raw = raw.predictions.get_column("probability").to_numpy()
    p_cal = calibrated.predictions.get_column("probability").to_numpy()
    assert not np.allclose(p_raw, p_cal)
    assert ((p_cal > 0) & (p_cal < 1)).all()
    # Platt is monotone: the ranking, and so the AUC, is the raw model's.
    order = np.argsort(p_raw, kind="stable")
    assert (np.diff(p_cal[order]) >= -1e-12).all()


def test_an_inner_year_with_too_few_events_keeps_the_raw_score() -> None:
    fold = _folds()[0]
    raw, calibrated = fit_predict_gbm(fold, _config(min_events=10_000), TIMING)
    assert calibrated.summary.calibration.startswith("raw: inner year ")
    assert "calibration needs 10000" in calibrated.summary.calibration
    assert calibrated.predictions.equals(raw.predictions)


def test_platt_recovers_a_known_map() -> None:
    rng = np.random.default_rng(0)
    score = rng.normal(0, 2, 20_000)
    truth = PlattMap(slope=0.7, intercept=-1.5)
    y = rng.random(score.size) < truth.apply(score)
    fitted = fit_platt(score, y)
    assert fitted.slope == pytest.approx(0.7, abs=0.05)
    assert fitted.intercept == pytest.approx(-1.5, abs=0.05)


def test_the_model_learns_the_direction() -> None:
    fold = _folds()[0]
    _, calibrated = fit_predict_gbm(fold, _config(), TIMING)
    scored = calibrated.predictions
    distress = scored.filter(pl.col(TARGET)).get_column("probability").mean()
    alive = scored.filter(~pl.col(TARGET)).get_column("probability").mean()
    assert distress > alive  # type: ignore[operator]


def test_a_fold_with_one_class_is_reported_not_fitted() -> None:
    fold = _folds()[0]
    alive = Fold(
        fold.horizon_months,
        fold.test_year,
        fold.train.filter(~pl.col(TARGET)),
        fold.test,
        0,
    )
    for result in fit_predict_gbm(alive, _config(), TIMING):
        assert not result.summary.fitted
        assert result.summary.note == "the training rows hold one class"
        assert result.predictions.is_empty()


def test_two_runs_give_identical_predictions_tuned_or_not() -> None:
    fold = _folds()[0]
    for config in (_config(), _config(min_tuning_events=1, min_events=2)):
        first = fit_predict_gbm(fold, config, TIMING)
        second = fit_predict_gbm(fold, config, TIMING)
        for a, b in zip(first, second, strict=True):
            assert a.summary == b.summary
            assert a.predictions.equals(b.predictions)


def test_the_backtest_runs_generation_3_beside_the_others() -> None:
    features, labels = panel(30, seed=3)
    config = _config().model_copy(
        update={
            # v1's regression: the panel holds ratios, not v5's text flag.
            "logistic_regression": load_backtest_config("backtest_v1").logistic_regression,
            "test_years": (2022, 2023),
            "bootstrap": BootstrapConfig(replicates=100, seed=7, level=0.95),
        }
    )
    feature_set = load_feature_set("feature_set_v3").feature_set
    models = [load_classical_model("altman_z2_2000", feature_set)]
    result = run_backtest(features, labels, config, TIMING, models)
    fitted = result.cells.filter(pl.col("fitted"))
    assert {RAW, CALIBRATED} <= set(fitted.get_column("model").to_list())
    report = render_report(result, "0" * 40)
    assert CALIBRATED in report
    # Generation 3 never reads the sensitivity run's flag: no_regime only filters rows.
    no_regime = result.predictions.filter(
        (pl.col("run") == "no_regime") & (pl.col("model") == CALIBRATED)
    )
    assert not no_regime.is_empty()
