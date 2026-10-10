"""Explanations (plan 0015 step E): SHAP per fold, global importance, per-row values kept local."""

# polars's signatures reference types pyright cannot resolve; scoped to this module.
# pyright: reportUnknownMemberType=false

from __future__ import annotations

from datetime import date
from pathlib import Path

import numpy as np
import polars as pl
import pytest
from tests.models.frames import PANEL_RATIOS, TIMING, panel

from distress_radar.features.config import load_feature_set
from distress_radar.models.backtest import BacktestResult, run_backtest
from distress_radar.models.baselines import load_classical_model
from distress_radar.models.dataset import modelling_dataset
from distress_radar.models.explain import (
    BASE,
    DATASET,
    explanation_frame,
    importance,
    shap_values,
    top_features,
    write_explanations,
)
from distress_radar.models.gbm import (
    CALIBRATED,
    RAW,
    fit_booster,
    fit_predict_gbm,
    hyperparameters,
    raw_score,
)
from distress_radar.models.report import render_report
from distress_radar.models.splits import (
    BacktestConfig,
    BootstrapConfig,
    load_backtest_config,
    purged_folds,
)
from distress_radar.warehouse import read_dataset

CLASSES = ("bankruptcy", "restructuring", "liquidation", "silent_exit")


def _config() -> BacktestConfig:
    config = load_backtest_config("backtest_v5")
    return config.model_copy(
        update={
            "population": None,
            "survival": None,
            "logistic_regression": load_backtest_config("backtest_v1").logistic_regression,
            "test_years": (2022, 2023),
            "bootstrap": BootstrapConfig(replicates=100, seed=7, level=0.95),
        }
    )


def test_a_rows_values_sum_to_its_raw_score_nulls_included() -> None:
    rng = np.random.default_rng(0)
    x = rng.normal(size=(300, 3))
    x[::3, 1] = np.nan
    y = x[:, 0] + rng.normal(0, 0.5, 300) > 0.5
    config = load_backtest_config("backtest_v5").lightgbm
    assert config is not None
    booster = fit_booster(x, y, config, hyperparameters(config))
    values, base = shap_values(booster, x)
    assert values.shape == x.shape
    np.testing.assert_allclose(values.sum(axis=1) + base, raw_score(booster, x), atol=1e-9)


def test_the_calibrated_model_carries_its_test_rows_values() -> None:
    features, labels = panel(30, seed=3)
    fold = purged_folds(modelling_dataset(features, labels, 12, CLASSES), (2023,), TIMING)[0]
    raw, calibrated = fit_predict_gbm(fold, _config(), TIMING)
    assert raw.summary.model == RAW and raw.explanation is None
    assert calibrated.summary.model == CALIBRATED
    explanation = calibrated.explanation
    assert explanation is not None
    assert explanation.columns == ["krs", "as_of_date", BASE, *PANEL_RATIOS]
    assert explanation.select("krs", "as_of_date").equals(fold.test.select("krs", "as_of_date"))


def _explanations() -> pl.DataFrame:
    keys = {
        "backtest": "b",
        "run": "main",
        "horizon_months": 12,
        "model": CALIBRATED,
        "test_year": 2023,
    }
    rows = pl.DataFrame(
        {"krs": ["0000000001", "0000000002"], "as_of_date": [date(2023, 1, 31)] * 2}
    )
    frame = explanation_frame(
        rows, ["a", "b", "c"], np.array([[1.0, -2.0, 0.0], [-3.0, 2.0, 0.5]]), 0.1
    )
    return frame.select(*(pl.lit(v).alias(k) for k, v in keys.items()), pl.all()).with_columns(
        pl.col("horizon_months").cast(pl.Int32), pl.col("test_year").cast(pl.Int32)
    )


def test_importance_is_the_mean_absolute_value_per_fold_and_feature() -> None:
    table = importance(_explanations())
    assert table.select("feature", "mean_abs_shap", "rows").rows() == [
        ("a", 2.0, 2),
        ("b", 2.0, 2),
        ("c", 0.25, 2),
    ]


def test_the_reports_table_is_the_top_features_and_the_rest_summed() -> None:
    fold = pl.DataFrame(
        {
            "feature": [f"f{i:02d}" for i in range(20)],
            "mean_abs_shap": [float(i) for i in range(20)],
        }
    )
    table = top_features(fold, top=15)
    assert table.height == 16
    assert table.get_column("feature").to_list()[:2] == ["f19", "f18"]
    assert table.row(15) == ("5 other features", 0.0 + 1 + 2 + 3 + 4)
    assert top_features(fold.head(3), top=15).height == 3


def _run() -> BacktestResult:
    features, labels = panel(30, seed=3)
    feature_set = load_feature_set("feature_set_v3").feature_set
    models = [load_classical_model("altman_z2_2000", feature_set)]
    return run_backtest(features, labels, _config(), TIMING, models)


@pytest.fixture(scope="module")
def result() -> BacktestResult:
    return _run()


def test_the_backtest_gathers_values_and_importance_per_fold(result: BacktestResult) -> None:
    assert result.explanations is not None and result.importance is not None
    scored = result.predictions.filter(pl.col("model") == CALIBRATED)
    assert result.explanations.height == scored.height
    folds = result.importance.select("run", "horizon_months", "test_year").unique()
    assert folds.height == 2 * 2 * 2  # runs × horizons × test years
    assert set(result.importance.get_column("feature").to_list()) == set(PANEL_RATIOS)


def test_explanations_stay_out_of_the_report(result: BacktestResult) -> None:
    report = render_report(result, "0" * 40)
    assert result.explanations is not None
    for krs in result.explanations.get_column("krs").unique().to_list():
        assert krs not in report
    value = float(result.explanations.get_column(PANEL_RATIOS[0]).abs().max())  # type: ignore[arg-type]
    assert f"{value:.4f}" not in report


def test_two_runs_give_identical_explanations(result: BacktestResult, tmp_path: Path) -> None:
    again = _run()
    assert result.explanations is not None and again.explanations is not None
    assert result.explanations.equals(again.explanations)
    assert result.importance is not None and again.importance is not None
    assert result.importance.equals(again.importance)
    first = write_explanations(result.explanations, tmp_path / "a")
    second = write_explanations(again.explanations, tmp_path / "b")
    assert [p.relative_to(tmp_path / "a") for p in first] == [
        p.relative_to(tmp_path / "b") for p in second
    ]
    for a, b in zip(first, second, strict=True):
        assert a.read_bytes() == b.read_bytes()
    stored = read_dataset(tmp_path / "a", DATASET)
    assert stored.height == result.explanations.height


def test_no_explanations_without_generation_3() -> None:
    features, labels = panel(30, seed=3)
    config = load_backtest_config("backtest_v1").model_copy(
        update={
            "test_years": (2022,),
            "bootstrap": BootstrapConfig(replicates=100, seed=7, level=0.95),
        }
    )
    result = run_backtest(features, labels, config, TIMING, [])
    assert result.explanations is None and result.importance is None
