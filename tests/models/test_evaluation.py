"""Metrics, the entity bootstrap, reliability, the backtest run and its report (plan 0012 step E)."""

# polars's signatures reference types pyright cannot resolve; scoped to this module.
# pyright: reportUnknownMemberType=false

from __future__ import annotations

import math
from pathlib import Path

import numpy as np
import polars as pl
import pytest
from tests.models.frames import panel

from distress_radar.features.config import load_feature_set
from distress_radar.models.backtest import POOLED, BacktestResult, run_backtest
from distress_radar.models.baselines import load_classical_model
from distress_radar.models.evaluation import (
    auc,
    bootstrap_intervals,
    brier,
    cell,
    log_loss,
    reliability,
    top_decile_precision,
)
from distress_radar.models.report import render_report, write_report
from distress_radar.models.splits import BacktestConfig, BootstrapConfig, load_backtest_config

Y = np.array([True, False, True, False])
P = np.array([0.9, 0.2, 0.6, 0.4])
BOOT = BootstrapConfig(replicates=200, seed=7, level=0.95)


# --- the metrics, by hand ------------------------------------------------------------------------


def test_brier_by_hand() -> None:
    assert brier(Y, P) == pytest.approx((0.01 + 0.04 + 0.16 + 0.16) / 4)


def test_log_loss_by_hand() -> None:
    expected = -(math.log(0.9) + math.log(0.8) + math.log(0.6) + math.log(0.6)) / 4
    assert log_loss(Y, P) == pytest.approx(expected)
    assert math.isfinite(log_loss(np.array([True]), np.array([0.0])))  # clipped, not infinite


def test_auc_by_hand_with_ties_counted_half() -> None:
    assert auc(Y, P) == 1.0
    # Pairs (positive vs negative): 0.5-0.5 tie, 0.5>0.1, 0.8>0.5, 0.8>0.1: 3.5 of 4.
    assert auc(Y, np.array([0.5, 0.5, 0.8, 0.1])) == pytest.approx(0.875)
    assert auc(np.array([True, True]), np.array([0.1, 0.2])) is None


def test_top_decile_is_the_highest_tenth_rounded_up() -> None:
    y = np.array([True, False] + [False] * 10)
    p = np.array([0.95, 0.9] + [0.1] * 10)
    assert top_decile_precision(y, p) == 0.5  # 12 rows: the top 2
    assert top_decile_precision(np.array([], dtype=bool), np.array([])) is None


def test_reliability_bins_by_hand() -> None:
    frame = pl.DataFrame(
        {"distress": [True, False, False, True], "probability": [0.05, 0.08, 0.55, 1.0]}
    )
    rows = reliability(frame, 10).rows(named=True)
    assert [(r["bin_low"], r["rows"], r["distress_rows"]) for r in rows] == [
        (0.0, 2, 1),
        (0.5, 1, 0),
        (0.9, 1, 1),  # 1.0 falls in the last bin
    ]
    assert rows[0]["mean_predicted"] == pytest.approx(0.065)


def _scored(n_entities: int, seed: int) -> pl.DataFrame:
    rng = np.random.default_rng(seed)
    rows = [
        {
            "krs": f"{e:010d}",
            "distress": e % 4 == 0,
            "probability": float(rng.uniform(0.5, 0.9) if e % 4 == 0 else rng.uniform(0.1, 0.6)),
            "outcome_class": "bankruptcy" if e % 4 == 0 else "alive",
            "trigger_event_type": "bankruptcy_declared" if e % 4 == 0 else None,
        }
        for e in range(n_entities)
        for _ in range(3)
    ]
    return pl.DataFrame(rows).with_columns(
        pl.lit(None, dtype=pl.Date).alias("event_date"),
        pl.date(2021, 1, 1).alias("event_known_from"),
    )


def test_the_bootstrap_resamples_entities_from_a_fixed_seed() -> None:
    scored = _scored(20, 1)
    first, again = bootstrap_intervals(scored, BOOT), bootstrap_intervals(scored, BOOT)
    assert first == again
    for interval in first.values():
        assert interval is not None and interval[0] <= interval[1]


def test_a_cell_below_min_events_has_no_numbers() -> None:
    row = cell(_scored(20, 1), fitted=True, train_events=2, min_events=3, bootstrap=BOOT)
    assert row["evaluable"] is False and row["brier"] is None and row["auc_low"] is None
    ok = cell(_scored(20, 1), fitted=True, train_events=5, min_events=3, bootstrap=BOOT)
    assert ok["evaluable"] is True and ok["test_events"] == 5 and isinstance(ok["brier"], float)


# --- the run and the report ----------------------------------------------------------------------


def _config() -> BacktestConfig:
    return load_backtest_config("backtest_v1").model_copy(
        update={"test_years": (2021, 2022, 2023, 2024), "bootstrap": BOOT}
    )


@pytest.fixture(scope="module")
def result() -> BacktestResult:
    features, labels = panel(30, seed=3)
    feature_set = load_feature_set("feature_set_v3").feature_set
    models = [load_classical_model(m, feature_set) for m in ("altman_z2_2000", "poznan_2004")]
    return run_backtest(features, labels, _config(), models)


def test_every_model_horizon_run_and_year_has_a_cell(result: BacktestResult) -> None:
    cells = result.cells
    assert set(cells.get_column("model").unique().to_list()) == {
        "logistic_regression",
        "altman_z2_2000",
        "poznan_2004",
    }
    # 3 models x 2 horizons x 2 runs x (4 years + pooled)
    assert cells.height == 3 * 2 * 2 * 5
    assert cells.filter(pl.col("evaluable")).height > 0  # the panel is big enough to score
    evaluable = cells.filter(pl.col("evaluable"))
    assert (evaluable.get_column("test_events") >= 3).all()
    assert (evaluable.get_column("train_events") >= 3).all()


def test_the_regime_run_leaves_out_the_flagged_rows(result: BacktestResult) -> None:
    scored = result.predictions
    main = scored.filter(pl.col("run") == "main").height
    no_regime = scored.filter(pl.col("run") == "no_regime").height
    assert 0 < no_regime < main


def test_the_report_opens_every_table_with_the_caveat(result: BacktestResult) -> None:
    text = render_report(result, "0" * 40)
    assert text.startswith("# Backtest report: backtest_v1\n\n> **Seed output, not evidence.**")
    sections = text.count("\n### ")
    assert text.count("_Seed output, not evidence:") == sections == 3 * len(result.config.horizons)
    models = set(result.cells.get_column("model").to_list())
    rows = [[c.strip() for c in line.strip("|").split("|")] for line in text.splitlines()]
    # Metric rows: model, year, run, four counts, the verdict, then the four metrics.
    metric_rows = [r for r in rows if len(r) == 12 and r[0] in models]
    assert len(metric_rows) == result.cells.height
    for columns in metric_rows:
        verdict, metrics = columns[7], columns[8:]
        if verdict == "scored":
            assert all(m != "n/a" for m in metrics)
        else:
            assert verdict.startswith("n/a (") and set(metrics) == {"n/a"}
    assert "pooled" in text and "no_regime" in text


def test_the_report_is_reproducible_byte_for_byte(result: BacktestResult, tmp_path: Path) -> None:
    features, labels = panel(30, seed=3)
    feature_set = load_feature_set("feature_set_v3").feature_set
    models = [load_classical_model(m, feature_set) for m in ("altman_z2_2000", "poznan_2004")]
    again = run_backtest(features, labels, _config(), models)
    first = write_report(render_report(result, "a" * 40), tmp_path, "backtest_v1").read_bytes()
    second = write_report(render_report(again, "a" * 40), tmp_path, "backtest_v1").read_bytes()
    assert first == second
    assert result.cells.equals(again.cells)


def test_pooled_cells_speak_only_for_folds_trained_on_enough_events(
    result: BacktestResult,
) -> None:
    for row in result.cells.filter(pl.col("test_year") == POOLED).iter_rows(named=True):
        if row["fitted"]:
            assert row["train_events"] >= 3
