"""The modelling population: entities whose acquisition is complete (plan 0015 decision 10)."""

# polars's signatures reference types pyright cannot resolve; scoped to this module.
# pyright: reportUnknownMemberType=false

from __future__ import annotations

from datetime import UTC, datetime

import polars as pl
import pytest
from tests.models.frames import TIMING, panel

from distress_radar.features.config import load_feature_set
from distress_radar.models.backtest import BacktestResult, run_backtest
from distress_radar.models.baselines import load_classical_model
from distress_radar.models.population import (
    FILINGS_SCHEMA,
    SEARCHES_SCHEMA,
    SOURCES_SCHEMA,
    Population,
    PopulationConfig,
    classify,
    entities_hash,
)
from distress_radar.models.report import render_report
from distress_radar.models.splits import BacktestConfig, BootstrapConfig, load_backtest_config

STATEMENT = "18"


def _searches(rows: list[tuple[str, int, bool, bool]]) -> pl.DataFrame:
    """(krs, day of October 2026, found, complete)."""
    return pl.DataFrame(
        [(k, datetime(2026, 10, d, tzinfo=UTC), f, c) for k, d, f, c in rows],
        schema=SEARCHES_SCHEMA,
        orient="row",
    )


def _filings(rows: list[tuple[str, str, bool]]) -> pl.DataFrame:
    return pl.DataFrame(rows, schema=FILINGS_SCHEMA, orient="row")


def _sources(rows: list[tuple[str, str]]) -> pl.DataFrame:
    return pl.DataFrame(rows, schema=SOURCES_SCHEMA, orient="row")


def _classify(
    entities: list[str],
    searches: pl.DataFrame,
    filings: pl.DataFrame,
    sources: pl.DataFrame,
) -> Population:
    return classify(
        entities,
        searches,
        filings,
        sources,
        complete_by_capture=["manual_seed"],
        download_codes=[STATEMENT],
    )


def test_each_entity_gets_the_first_reason_that_applies() -> None:
    searches = _searches(
        [
            ("ok", 1, True, True),
            ("gone", 1, False, False),
            ("partial", 1, True, False),
            ("young", 1, True, True),
            ("held", 1, True, True),
        ]
    )
    filings = _filings(
        [
            ("ok", STATEMENT, True),
            ("ok", "19", False),  # an auditor report, not downloaded: not held back
            ("partial", STATEMENT, True),
            ("held", STATEMENT, True),
            ("held", STATEMENT, False),
        ]
    )
    sources = _sources([(k, "rejestr_io_v1") for k in ("ok", "gone", "partial", "young", "held")])
    population = _classify(
        ["ok", "unsearched", "gone", "partial", "young", "held"], searches, filings, sources
    )
    assert population.included == {"ok"}
    assert population.excluded == {
        "unsearched": "rdf_not_searched",
        "gone": "rdf_not_found",
        "partial": "rdf_search_incomplete",
        "young": "no_statement_imported",
        "held": "statements_held_back",
    }
    assert population.reasons() == {
        "rdf_not_searched": 1,
        "rdf_not_found": 1,
        "rdf_search_incomplete": 1,
        "no_statement_imported": 1,
        "statements_held_back": 1,
    }


def test_the_latest_search_decides() -> None:
    searches = _searches([("a", 1, True, False), ("a", 5, True, True), ("b", 5, True, False)])
    searches = pl.concat([searches, _searches([("b", 1, True, True)])])  # out of order
    filings = _filings([("a", STATEMENT, True), ("b", STATEMENT, True)])
    population = _classify(["a", "b"], searches, filings, _sources([]))
    assert population.included == {"a"}
    assert population.excluded == {"b": "rdf_search_incomplete"}


def test_a_source_complete_by_capture_needs_no_search_but_can_be_held_back() -> None:
    filings = _filings(
        [("seed", STATEMENT, True), ("seed2", STATEMENT, True), ("seed2", STATEMENT, False)]
    )
    sources = _sources(
        [("seed", "manual_seed"), ("seed2", "manual_seed"), ("seed2", "rejestr_io_v1")]
    )
    population = _classify(["seed", "seed2"], _searches([]), filings, sources)
    assert population.included == {"seed"}
    assert population.excluded == {"seed2": "statements_held_back"}


def test_the_hash_is_of_the_sorted_included_entities() -> None:
    filings = _filings([("b", STATEMENT, True), ("a", STATEMENT, True)])
    population = _classify(
        ["b", "a"], _searches([("a", 1, True, True), ("b", 1, True, True)]), filings, _sources([])
    )
    assert population.entities_hash == entities_hash(["a", "b"]) == entities_hash(["b", "a"])


# --- the backtest ---------------------------------------------------------------------------------


def _config(population: PopulationConfig | None) -> BacktestConfig:
    return load_backtest_config("backtest_v1").model_copy(
        update={
            "test_years": (2021, 2022, 2023, 2024),
            "bootstrap": BootstrapConfig(replicates=100, seed=7, level=0.95),
            "population": population,
        }
    )


def _run(
    config: BacktestConfig, population: Population | None
) -> tuple[pl.DataFrame, BacktestResult]:
    features, labels = panel(30, seed=3)
    feature_set = load_feature_set("feature_set_v3").feature_set
    models = [load_classical_model(m, feature_set) for m in ("altman_z2_2000", "poznan_2004")]
    return labels, run_backtest(features, labels, config, TIMING, models, population)


def test_the_backtest_models_only_the_included_entities_and_reports_the_rest() -> None:
    _, labels = panel(30, seed=3)
    entities = sorted(labels.get_column("krs").unique().to_list())
    kept, left = entities[:24], entities[24:]
    population = Population(
        included=frozenset(kept), excluded=dict.fromkeys(left, "rdf_not_searched")
    )
    rule = PopulationConfig(
        rule="acquisition_complete",
        complete_by_capture=("manual_seed",),
        entities_hash=entities_hash(kept),
    )
    labels, result = _run(_config(rule), population)
    assert result.entities == 24
    assert set(result.predictions.get_column("krs").unique().to_list()) <= set(kept)
    distress = labels.filter(
        pl.col("krs").is_in(left) & pl.col("outcome_class").is_in(result.config.distress_classes)
    )
    assert result.excluded_distress_entities == distress.get_column("krs").n_unique() > 0
    report = render_report(result, "0" * 40)
    assert "## Population" in report
    assert f"`{entities_hash(kept)}`" in report
    assert "| `rdf_not_searched` | 6 |" in report


def test_a_population_that_does_not_match_its_pin_is_refused() -> None:
    population = Population(included=frozenset({"0000000001"}), excluded={})
    rule = PopulationConfig(
        rule="acquisition_complete", complete_by_capture=(), entities_hash="0" * 64
    )
    with pytest.raises(ValueError, match="new backtest version"):
        _run(_config(rule), population)


def test_a_population_is_given_exactly_when_the_config_names_a_rule() -> None:
    population = Population(included=frozenset({"0000000001"}), excluded={})
    with pytest.raises(ValueError, match="exactly when"):
        _run(_config(None), population)
    rule = PopulationConfig(
        rule="acquisition_complete", complete_by_capture=(), entities_hash="0" * 64
    )
    with pytest.raises(ValueError, match="exactly when"):
        _run(_config(rule), None)


def test_a_config_without_a_rule_reports_no_population() -> None:
    _, result = _run(_config(None), None)
    assert result.population is None
    assert "## Population" not in render_report(result, "0" * 40)
