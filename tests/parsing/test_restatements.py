"""`restatement_events` built from the canonical facts, and a stored copy built from older facts
recognised as stale (found 2026-10-12: the facts rebuilt over list v1, the restatements not)."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import polars as pl
from tests.parsing.test_accounting_identities import TOLERANCE, _frame

from distress_radar.parsing.canonical_schema import MappingConfig
from distress_radar.parsing.restatements import restatement_events, stale_restatements
from distress_radar.warehouse import read_dataset, write_dataset


def _canonical(config: MappingConfig, *years: int) -> pl.DataFrame:
    """Total assets of 100 filed for 2022; the 2023 filing restates them as 110."""
    filings = {
        2022: ({("BS.ASSETS", "current_year"): "100.00"}, "por_2022"),
        2023: (
            {("BS.ASSETS", "current_year"): "130.00", ("BS.ASSETS", "prior_year"): "110.00"},
            "por_2023",
        ),
    }
    frames = [
        _frame(
            filings[year][0],
            config,
            ref=filings[year][1],
            period=(date(year, 1, 1), date(year, 12, 31)),
            known_from=date(year + 1, 6, 1),
        )
        for year in years
    ]
    return pl.concat(frames).with_columns(pl.lit("pass").alias("quality_grade"))


def _stored(events: pl.DataFrame, warehouse: Path) -> pl.DataFrame:
    """Through Parquet and back, as the feature store reads it."""
    write_dataset(events, warehouse, "restatement_events", "fiscal_year")
    return read_dataset(warehouse, "restatement_events")


def test_a_copy_built_from_the_current_facts_is_current(
    mapping_config: MappingConfig, tmp_path: Path
) -> None:
    canonical = _canonical(mapping_config, 2022, 2023)
    events = restatement_events(canonical, TOLERANCE)
    assert not events.is_empty()
    assert stale_restatements(_stored(events, tmp_path), canonical, TOLERANCE) == ""


def test_a_copy_built_before_the_facts_changed_is_stale(mapping_config: MappingConfig) -> None:
    older = _canonical(mapping_config, 2022)  # one filing: nothing restated yet
    current = _canonical(mapping_config, 2022, 2023)
    stored = restatement_events(older, TOLERANCE)
    reason = stale_restatements(stored, current, TOLERANCE)
    assert "rebuild restatement_events first" in reason
    assert f"holds {stored.height} rows" in reason


def test_a_quarantined_files_figures_restate_nothing(mapping_config: MappingConfig) -> None:
    canonical = _canonical(mapping_config, 2022, 2023).with_columns(
        pl.when(pl.col("document_ref") == "por_2023")
        .then(pl.lit("quarantined"))
        .otherwise(pl.col("quality_grade"))
        .alias("quality_grade")
    )
    assert restatement_events(canonical, TOLERANCE).is_empty()
