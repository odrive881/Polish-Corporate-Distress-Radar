"""The explorer's frames (plan 0016 step E): out-of-time history (decision 3), the newer of score
and exclusion, peers as distributions, documents fetched by hash."""

from __future__ import annotations

from dataclasses import replace
from datetime import date
from pathlib import Path

import polars as pl
import pytest
from tests.explorer.sources import DAY, DOCUMENT, make_sources

from distress_radar.explorer import data


@pytest.fixture
def sources(tmp_path: Path) -> data.Sources:
    return make_sources(tmp_path)


def test_only_the_served_versions_main_backtest_rows_are_kept(sources: data.Sources) -> None:
    # Of five synthetic rows, the `no_regime` run and the older backtest are dropped.
    assert sources.predictions.height == 3
    assert sources.predictions.get_column("probability").max() < 0.9  # type: ignore[operator]


def test_history_is_out_of_time_before_the_eve_and_served_after(sources: data.Sources) -> None:
    history = data.score_history(sources, "0000000001")
    rows = [
        (r["as_of_date"], r["horizon_months"], r["source"]) for r in history.iter_rows(named=True)
    ]
    assert rows == [
        (date(2024, 6, 30), 12, "out-of-time backtest, fold 2024"),
        (date(2025, 6, 30), 12, "out-of-time backtest, fold 2025"),
        (DAY, 12, "served model v4"),
        (date(2025, 6, 30), 24, "out-of-time backtest, fold 2025"),
        (DAY, 24, "served model v4"),
    ]


def test_an_incomplete_row_is_no_point_of_the_history(sources: data.Sources) -> None:
    history = data.score_history(sources, "0000000002")
    assert history.get_column("horizon_months").to_list() == [12]


def test_current_status_says_why_a_company_is_not_scored(sources: data.Sources) -> None:
    incomplete = data.current_status(sources, "0000000002").row(1, named=True)
    assert incomplete["reason"] == "inputs incomplete"
    assert incomplete["missing_inputs"] == ["working_capital_to_assets"]
    excluded = data.current_status(sources, "0000000003")
    assert excluded.get_column("reason").to_list() == ["rdf_not_searched"] * 2
    assert excluded.get_column("probability").null_count() == 2


def test_a_newer_exclusion_replaces_an_older_score(sources: data.Sources) -> None:
    import polars as pl

    later = sources.exclusions.with_columns(
        pl.lit("0000000001").alias("krs"), pl.lit(date(2026, 10, 31)).alias("as_of_date")
    ).head(1)
    moved = data.Sources(**{**sources.__dict__, "exclusions": later})
    status = data.current_status(moved, "0000000001")
    assert status.get_column("reason").to_list() == ["rdf_not_searched", None]


def test_the_month_end_summary_counts_every_status(sources: data.Sources) -> None:
    summary = {
        (r["horizon_months"], r["status"]): r["entities"]
        for r in data.month_end_summary(sources).iter_rows(named=True)
    }
    assert summary == {
        (12, "scored"): 2,
        (12, "rdf_not_searched"): 1,
        (24, "scored"): 1,
        (24, "inputs incomplete"): 1,
        (24, "rdf_not_searched"): 1,
    }


def test_marks_hold_filings_and_distress_events_only(sources: data.Sources) -> None:
    marks = data.history_marks(sources, "0000000001")
    assert marks.get_column("kind").to_list() == ["legal event", "filing"]
    assert "registered" not in " ".join(marks.get_column("label").to_list())


def test_peers_are_distributions_by_division(sources: data.Sources) -> None:
    peers = data.peer_distribution(sources, 12)
    assert peers.columns == ["division", "companies", "q1", "median", "q3"]
    assert peers.get_column("division").to_list() == ["41"]
    assert peers.get_column("companies").to_list() == [2]
    assert data.peer_percentile(sources, "0000000001", 12) == 50.0
    assert data.peer_percentile(sources, "0000000003", 12) is None


def test_statements_carry_their_document_and_element(sources: data.Sources) -> None:
    facts = data.statement_facts(sources, "0000000001")
    assert facts.height == 1
    assert facts.row(0, named=True)["source_document_hash"] == DOCUMENT
    assert facts.row(0, named=True)["source_element_path"] == "/Bilans/Aktywa"


def test_a_stored_document_is_fetched_by_hash(sources: data.Sources) -> None:
    content = data.stored_document(sources, DOCUMENT)
    assert data.extension(content) == ".zip"
    assert data.extension(b"%PDF-1.7") == ".pdf"
    assert data.extension(b"  <?xml") == ".xml"
    assert data.division("43.99.Z") == "43" and data.division(None) is None


def test_each_alert_type_is_described_from_its_detail() -> None:
    """Plan 0018 step E: the Alerts page's one line per alert, from `alerts.detail`."""
    score = {
        "horizon_months": 12,
        "model": "distress_radar_12m v5",
        "probability": 0.31,
        "previous_probability": 0.08,
        "previous_as_of_date": "2026-09-30",
        "level": 0.25,
    }
    assert data.describe("threshold_crossed", score) == (
        "12-month probability 31.0%, from 8.0% at 2026-09-30: crossed 25% (distress_radar_12m v5)"
    )
    assert data.describe("score_jump", score).endswith("at 2026-09-30 (distress_radar_12m v5)")
    assert data.describe(
        "going_concern_flagged", {"feature": "modified_opinion", "known_from": "2026-07-01"}
    ) == ("the auditor's opinion is modified (known from 2026-07-01)")
    overdue = {
        "expected_period_end": "2025-12-31",
        "deadline": "2026-07-15",
        "searched_at": "2026-10-08T09:12:00+00:00",
    }
    assert data.describe("filing_overdue", overdue) == (
        "no statement for the year ending 2025-12-31 (due 2026-07-15) at the RDF search of "
        "2026-10-08"
    )


def test_a_score_shows_its_interval(sources: data.Sources) -> None:
    status = data.current_status(sources, "0000000001").row(0, named=True)
    assert (status["probability_low"], status["probability_high"]) == (0.01, 0.06)


def test_drivers_move_only_against_the_same_versions_previous_score(sources: data.Sources) -> None:
    """Plan 0021: no movement for a version's first score; against its previous one after."""
    assert data.drivers_since_last(sources, "0000000001", 12, DAY, "4") is None
    earlier = sources.scores.filter(
        (pl.col("krs") == "0000000001") & (pl.col("horizon_months") == 12)
    ).with_columns(
        pl.lit(date(2026, 8, 31)).alias("as_of_date"),
        pl.lit('[{"feature": "roa", "contribution": 0.1}]').alias("top_drivers"),
    )
    moved = replace(sources, scores=pl.concat([sources.scores, earlier]))
    (row,) = data.drivers_since_last(moved, "0000000001", 12, DAY, "4") or []
    assert row["feature"] == "roa" and row["previous_contribution"] == 0.1
    assert abs(row["change"] - 0.3) < 1e-12
    other = replace(
        sources,
        scores=pl.concat(
            [sources.scores, earlier.with_columns(pl.lit("3").alias("model_version"))]
        ),
    )
    assert data.drivers_since_last(other, "0000000001", 12, DAY, "4") is None
