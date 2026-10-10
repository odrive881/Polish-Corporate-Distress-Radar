"""Discrete-time survival (plan 0015 step D): the target from the label set, person-periods, the
probabilities, the purge month by month, and the backtest's survival cells and metrics."""

# polars's signatures reference types pyright cannot resolve; scoped to this module.
# pyright: reportUnknownMemberType=false

from __future__ import annotations

from datetime import date

import numpy as np
import polars as pl
import pytest
from tests.models.frames import TIMING, features, labels, panel

from distress_radar.features.config import load_feature_set
from distress_radar.models.backtest import run_backtest
from distress_radar.models.baselines import load_classical_model
from distress_radar.models.dataset import TARGET, modelling_dataset
from distress_radar.models.splits import (
    BacktestConfig,
    BootstrapConfig,
    load_backtest_config,
    purged_folds,
)
from distress_radar.models.survival import (
    FOREST,
    HAZARD,
    SurvivalRecords,
    fit_hazard,
    fit_survival,
    forest_survival,
    hazard_survival,
    observed,
    person_periods,
    survival_records,
)

CLASSES = ("bankruptcy", "restructuring", "liquidation", "silent_exit")
JAN18 = date(2018, 1, 31)


def _records() -> SurvivalRecords:
    """A: bankrupt in its 5th month. B: alive through 24. C: censored by the label lag after 2
    months. D: merged in its 3rd month, which censors."""
    rows = [
        ("0000000001", JAN18, 24, "bankruptcy", date(2018, 6, 10), False),
        ("0000000002", JAN18, 24, "alive", None, False),
        ("0000000003", date(2025, 6, 30), 24, None, None, False),
        ("0000000004", JAN18, 24, None, None, False),
    ]
    frame = labels(rows).with_columns(
        pl.when(pl.col("krs") == "0000000004")
        .then(pl.lit("merged"))
        .otherwise(pl.col("trigger_event_type"))
        .alias("trigger_event_type"),
        pl.when(pl.col("krs") == "0000000004")
        .then(pl.lit(date(2018, 4, 5)))
        .otherwise(pl.col("event_known_from"))
        .alias("event_known_from"),
    )
    feats = features([(k, d, 0.1) for k, d, *_ in rows])
    return survival_records(feats, frame, 24, CLASSES)


def _times(frame: pl.DataFrame) -> dict[str, tuple[int, bool]]:
    return {r["krs"][-1]: (r["time"], r["event"]) for r in frame.iter_rows(named=True)}


def test_the_target_stops_at_the_event_or_the_censoring_time() -> None:
    times = _times(observed(_records(), TIMING))
    assert times == {"1": (5, True), "2": (24, False), "3": (2, False), "4": (2, False)}


def test_on_an_eve_only_what_was_settled_counts() -> None:
    # On 2018-03-31 the bankruptcy (known 2018-06-10) is not yet known: censored at 2 months.
    early = _times(observed(_records(), TIMING, date(2018, 3, 31)))
    assert early["1"] == (2, False)
    # The merger (known 2018-04-05) is not known either: observed through March, as alive.
    assert early["4"] == (2, False)
    # On 2018-12-31 the bankruptcy is known; the alive record is observed through December.
    late = _times(observed(_records(), TIMING, date(2018, 12, 31)))
    assert late["1"] == (5, True)
    assert late["2"] == (11, False)


def test_a_record_agrees_with_its_binary_labels() -> None:
    feats, frame = panel(30, seed=3)
    # The panel labels `alive` up to its last month, past the label rules' lag; a label set never
    # does. Its rows follow the rules while every 24-month window ends a year before the cutoff.
    frame = frame.filter(pl.col("as_of_date") <= date(2023, 8, 31))
    records = observed(survival_records(feats, frame, 24, CLASSES), TIMING).select(
        "krs", "as_of_date", "time", "event"
    )
    for horizon in (12, 24):
        rows = frame.filter((pl.col("horizon_months") == horizon) & ~pl.col("censored")).join(
            records, on=["krs", "as_of_date"], validate="1:1"
        )
        distress = rows.get_column("outcome_class") != "alive"
        within = rows.get_column("event") & (rows.get_column("time") <= horizon)
        assert (distress == within).all()
        assert (rows.filter(~distress).get_column("time") >= horizon).all()
        # A censored label is never observed through its horizon with no event.
        censored = frame.filter((pl.col("horizon_months") == horizon) & pl.col("censored")).join(
            records, on=["krs", "as_of_date"]
        )
        assert (censored.get_column("time") < horizon).all()
        assert not censored.get_column("event").any()


@pytest.mark.parametrize("horizon", [12, 24])
def test_on_a_folds_eve_a_record_agrees_with_the_folds_training_labels(horizon: int) -> None:
    feats, frame = panel(30, seed=3)
    records = survival_records(feats, frame, 24, CLASSES)
    dataset = modelling_dataset(feats, frame, horizon, CLASSES)
    for fold in purged_folds(dataset, (2021, 2023, 2025), TIMING):
        seen = observed(records, TIMING, date(fold.test_year - 1, 12, 31))
        rows = fold.train.join(
            seen.select("krs", "as_of_date", "time", "event"), on=["krs", "as_of_date"]
        )
        assert rows.height == fold.train.height
        within = rows.get_column("event") & (rows.get_column("time") <= horizon)
        assert (rows.get_column(TARGET) == within).all()
        assert (rows.filter(~pl.col(TARGET)).get_column("time") >= horizon).all()


def test_no_training_month_ends_in_or_after_the_test_year() -> None:
    feats, frame = panel(30, seed=3)
    records = survival_records(feats, frame, 24, CLASSES)
    for year in (2020, 2022, 2024):
        seen = observed(records, TIMING, date(year - 1, 12, 31)).filter(pl.col("time") > 0)
        n = (
            pl.col("as_of_date").dt.year() * 12
            + pl.col("as_of_date").dt.month()
            - 1
            + pl.col("time")
        )
        last = seen.select(pl.date(n // 12, n % 12 + 1, 1).dt.month_end().max()).item()
        assert last < date(year, 1, 1)


def test_person_periods_stop_at_the_event_or_censoring_and_censored_rows_hold_no_event() -> None:
    x = np.array([[1.0], [2.0], [3.0]])
    rows, y = person_periods(x, np.array([3, 2, 0], dtype=np.int32), np.array([True, False, True]))
    assert rows.tolist() == [[1.0, 1.0], [1.0, 2.0], [1.0, 3.0], [2.0, 1.0], [2.0, 2.0]]
    assert y.tolist() == [False, False, True, False, False]


def test_the_probability_is_one_minus_the_product_of_monthly_survival() -> None:
    rng = np.random.default_rng(0)
    x = rng.normal(size=(400, 2))
    time = rng.integers(1, 25, 400).astype(np.int32)
    event = rng.random(400) < 0.3 + 0.3 * (x[:, 0] > 0)
    config = load_backtest_config("backtest_v5").lightgbm
    assert config is not None
    booster = fit_hazard(x, time, event, config)
    curves = hazard_survival(booster, x[:3], 24)
    for i in range(3):
        grid = np.column_stack([np.repeat(x[i : i + 1], 24, axis=0), np.arange(1, 25)])
        hazard = np.asarray(booster.predict(grid))
        assert 1 - curves[i, 11] == pytest.approx(1 - np.prod(1 - hazard[:12]))
        assert 1 - curves[i, 23] == pytest.approx(1 - np.prod(1 - hazard))
    assert (np.diff(curves, axis=1) <= 1e-12).all()


def test_the_forests_curve_is_one_before_its_first_event_time() -> None:
    class Forest:
        unique_times_ = np.array([3.0, 7.0])

        def predict_survival_function(self, x: object, return_array: bool) -> np.ndarray:
            return np.array([[0.9, 0.5]])

    curves = forest_survival(Forest(), np.zeros((1, 1)), 8)  # type: ignore[arg-type]
    assert curves.tolist() == [[1.0, 1.0, 0.9, 0.9, 0.9, 0.9, 0.5, 0.5]]


def _config() -> BacktestConfig:
    config = load_backtest_config("backtest_v5")
    assert config.survival is not None
    return config.model_copy(
        update={
            "population": None,
            "logistic_regression": load_backtest_config("backtest_v1").logistic_regression,
            "test_years": (2022, 2023),
            "bootstrap": BootstrapConfig(replicates=100, seed=7, level=0.95),
            "min_events": 2,
            "survival": config.survival.model_copy(
                update={
                    "comparison": config.survival.comparison.model_copy(update={"n_estimators": 10})
                }
            ),
        }
    )


def test_a_fit_is_deterministic_and_reports_its_metrics() -> None:
    feats, frame = panel(30, seed=3)
    records = survival_records(feats, frame, 24, CLASSES)
    first = fit_survival(records, 2023, _config(), TIMING)
    second = fit_survival(records, 2023, _config(), TIMING)
    assert first.metrics == second.metrics
    assert first.train_events > 0 and first.train_person_months > first.train_records
    for row in first.metrics:
        assert row["evaluable"], row
        assert 0 <= row["concordance_ipcw"] <= 1  # type: ignore[operator]
        assert 0 <= row["integrated_brier"] <= 1  # type: ignore[operator]


def test_the_backtest_scores_the_survival_models_beside_the_others() -> None:
    feats, frame = panel(30, seed=3)
    feature_set = load_feature_set("feature_set_v3").feature_set
    models = [load_classical_model("altman_z2_2000", feature_set)]
    result = run_backtest(feats, frame, _config(), TIMING, models)
    fitted = result.cells.filter(pl.col("fitted"))
    assert {HAZARD, FOREST} <= set(fitted.get_column("model").to_list())
    for horizon in (12, 24):
        scored = result.predictions.filter(
            (pl.col("model") == HAZARD) & (pl.col("horizon_months") == horizon)
        )
        regression = result.predictions.filter(
            (pl.col("model") == "logistic_regression") & (pl.col("horizon_months") == horizon)
        )
        # The same test rows as the binary models (the regression drops none on the panel).
        assert (
            scored.select("run", "krs", "as_of_date")
            .sort(pl.all())
            .equals(regression.select("run", "krs", "as_of_date").sort(pl.all()))
        )
    assert result.survival is not None
    assert set(result.survival.get_column("run").to_list()) == {"main", "no_regime"}
    assert result.survival.height == 2 * 2 * 2  # runs × test years × models
