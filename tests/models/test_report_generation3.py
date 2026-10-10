"""The backtest with generation 3 (plan 0015 step F): the entity-disjoint run, events per list, and
the report's new sections."""

# polars's signatures reference types pyright cannot resolve; scoped to this module.
# pyright: reportUnknownMemberType=false

from __future__ import annotations

from datetime import date

import polars as pl
import pytest
from tests.models.frames import TIMING, panel

from distress_radar.features.config import load_feature_set
from distress_radar.models.backtest import BacktestResult, run_backtest
from distress_radar.models.baselines import load_classical_model
from distress_radar.models.dataset import modelling_dataset
from distress_radar.models.report import render_report
from distress_radar.models.splits import (
    BacktestConfig,
    BootstrapConfig,
    load_backtest_config,
    purged_folds,
)

CLASSES = ("bankruptcy", "restructuring", "liquidation", "silent_exit")
# Entities 21-30 enter the panel in 2022, so a 2023 fold has not trained on them.
LATE = {f"{i:010d}" for i in range(21, 31)}


def _late_panel() -> tuple[pl.DataFrame, pl.DataFrame]:
    features, labels = panel(30, seed=3)
    early = pl.col("krs").is_in(sorted(LATE)) & (pl.col("as_of_date") < date(2022, 1, 1))
    return features.filter(~early), labels.filter(~early)


def _config() -> BacktestConfig:
    config = load_backtest_config("backtest_v5")
    assert config.survival is not None
    return config.model_copy(
        update={
            "population": None,
            "logistic_regression": load_backtest_config("backtest_v1").logistic_regression,
            "test_years": (2022, 2023),
            "bootstrap": BootstrapConfig(replicates=100, seed=7, level=0.95),
            "survival": config.survival.model_copy(
                update={
                    "comparison": config.survival.comparison.model_copy(update={"n_estimators": 10})
                }
            ),
        }
    )


def _run(config: BacktestConfig) -> BacktestResult:
    features, labels = _late_panel()
    feature_set = load_feature_set("feature_set_v3").feature_set
    models = [load_classical_model("altman_z2_2000", feature_set)]
    lists = {
        "manual_seed": frozenset(f"{i:010d}" for i in range(1, 11)),
        "rejestr_io_v1": frozenset(f"{i:010d}" for i in range(6, 31)),
    }
    return run_backtest(features, labels, config, TIMING, models, None, lists)


@pytest.fixture(scope="module")
def result() -> BacktestResult:
    return _run(_config())


def test_the_entity_disjoint_run_scores_only_entities_no_model_trained_on(
    result: BacktestResult,
) -> None:
    features, labels = _late_panel()
    disjoint = result.predictions.filter(pl.col("run") == "entity_disjoint")
    for horizon in (12, 24):
        dataset = modelling_dataset(features, labels, horizon, CLASSES)
        for fold in purged_folds(dataset, (2022, 2023), TIMING):
            seen = set(fold.train.get_column("krs").to_list())
            scored = disjoint.filter(
                (pl.col("horizon_months") == horizon) & (pl.col("test_year") == fold.test_year)
            )
            assert not set(scored.get_column("krs").to_list()) & seen
    # The late entrants: on the 2023 fold's eve their 2022 windows are not yet settled (the
    # `alive` lag), so no model has trained on them, survival records included.
    late = disjoint.filter(pl.col("test_year") == 2023)
    assert not late.is_empty()
    assert set(late.get_column("krs").to_list()) <= LATE


def test_events_are_counted_per_list(result: BacktestResult) -> None:
    labelled = set(_late_panel()[1].get_column("krs").to_list())
    assert result.lists == {
        "manual_seed": len({f"{i:010d}" for i in range(1, 11)} & labelled),
        "rejestr_io_v1": len({f"{i:010d}" for i in range(6, 31)} & labelled),
    }
    for horizon, counts in result.events_by_list.items():
        assert set(counts) == {"manual_seed", "rejestr_io_v1"}
        assert max(counts.values()) <= result.events[horizon] <= sum(counts.values())


def test_the_report_holds_generation_3s_sections(result: BacktestResult) -> None:
    text = render_report(result, "0" * 40)
    assert (
        f"> **Purposive lists, not evidence.** {result.entities} entities (manual_seed "
        f"{result.lists['manual_seed']}, rejestr_io_v1 {result.lists['rejestr_io_v1']})"
    ) in text
    for horizon in (12, 24):
        counts = result.events_by_list[horizon]
        assert (
            f"_Purposive lists, not evidence: {result.events[horizon]} distinct events at "
            f"{horizon} months (manual_seed {counts['manual_seed']}, rejestr_io_v1 "
            f"{counts['rejestr_io_v1']})"
        ) in text
    for heading in (
        "### Generation 3 per fold",
        "### Feature importance (generation 3)",
        "## Survival",
        "## Champion rule",
    ):
        assert heading in text
    assert "`entity_disjoint`: `main`'s models" in text
    assert "| lightgbm | pooled | entity_disjoint |" in text
    assert "| survival_forest | 2023 | main |" in text
    # The panel cannot reach the rule's 50 events.
    assert "**Nothing is promoted: `logistic_regression` stays the champion.**" in text
    assert "not evaluable: " in text
    assert "defaults: inner year " in text


def test_the_report_is_reproducible(result: BacktestResult) -> None:
    again = _run(_config())
    assert render_report(result, "0" * 40) == render_report(again, "0" * 40)
    assert result.champion == again.champion
