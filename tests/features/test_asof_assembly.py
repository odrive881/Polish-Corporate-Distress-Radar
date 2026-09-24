"""ASOF assembly of `feature_store` (plan 0010 step E): grid, pivot, contract, bytes."""

# polars's signatures reference types pyright cannot resolve; scoped to this module.
# pyright: reportUnknownMemberType=false

from __future__ import annotations

from datetime import date
from pathlib import Path

import pandera.errors
import polars as pl
import pytest

from distress_radar.features import feature_definitions as fd
from distress_radar.features.asof_assembly import FETCHES_SCHEMA, assemble, build_grid
from distress_radar.features.config import FeatureConfig, load_feature_set
from distress_radar.features.contracts import KEY_COLUMNS, feature_columns, feature_store_contract
from distress_radar.parsing.legal_events import LEGAL_EVENTS_SCHEMA
from distress_radar.warehouse import write_dataset

A, B = "0000000001", "0000000002"


@pytest.fixture(scope="module")
def config() -> FeatureConfig:
    return load_feature_set("feature_set_v1")


def _fetches(*rows: tuple[str, str, date]) -> pl.DataFrame:
    return pl.DataFrame(list(rows), schema=FETCHES_SCHEMA, orient="row")


def _registered(krs: str, on: date) -> pl.DataFrame:
    return pl.DataFrame(
        [
            {
                "krs": krs,
                "event_type": "registered",
                "event_date": on,
                "known_from": on,
                "ends": [],
                "precludes_silent_exit": False,
            }
        ],
        schema=LEGAL_EVENTS_SCHEMA,
        orient="row",
    )


# --- the grid ------------------------------------------------------------------------------------


def test_the_grid_runs_from_registration_to_the_earliest_last_fetch() -> None:
    fetches = _fetches(
        (A, "KRS", date(2026, 9, 23)),
        (A, "KRS", date(2026, 5, 2)),  # an older fetch: only the latest counts
        (A, "MSiG", date(2026, 7, 15)),  # the earlier source bounds the cutoff
    )
    grid = build_grid([A], fetches, _registered(A, date(2026, 3, 10)), start=date(2012, 1, 31))
    assert grid.get_column("as_of_date").to_list() == [
        date(2026, 3, 31),
        date(2026, 4, 30),
        date(2026, 5, 31),
        date(2026, 6, 30),
    ]


def test_the_grid_starts_at_its_start_for_an_older_entity() -> None:
    fetches = _fetches((A, "KRS", date(2012, 3, 31)), (A, "MSiG", date(2012, 3, 31)))
    grid = build_grid([A], fetches, _registered(A, date(2003, 11, 27)), start=date(2012, 1, 31))
    assert grid.get_column("as_of_date").to_list() == [
        date(2012, 1, 31),
        date(2012, 2, 29),
        date(2012, 3, 31),  # a cutoff on a month-end includes it
    ]


def test_an_entity_missing_a_source_or_the_master_has_no_rows() -> None:
    fetches = _fetches(
        (A, "KRS", date(2026, 9, 23)),  # no MSiG fetch: no cutoff
        (B, "KRS", date(2026, 9, 23)),
        (B, "MSiG", date(2026, 9, 23)),
    )
    events = pl.concat([_registered(A, date(2020, 1, 1)), _registered(B, date(2020, 1, 1))])
    assert build_grid([A], fetches, events, start=date(2012, 1, 31)).is_empty()
    assert build_grid([B], fetches, events, start=date(2012, 1, 31)).height > 0


# --- assembly ------------------------------------------------------------------------------------


def _grid(*days: date) -> pl.DataFrame:
    return pl.DataFrame([(A, d) for d in days], schema=fd.GRID_SCHEMA, orient="row")


def _values(*rows: tuple[date, str, float, date]) -> pl.DataFrame:
    return pl.DataFrame([(A, *r) for r in rows], schema=fd.FEATURE_VALUES_SCHEMA, orient="row")


def test_every_feature_has_a_typed_column_and_a_companion(config: FeatureConfig) -> None:
    grid = _grid(date(2022, 5, 31), date(2022, 7, 31))
    values = _values(
        (date(2022, 7, 31), "current_ratio", 2.0, date(2022, 6, 30)),
        (date(2022, 7, 31), "art233_triggered", 1.0, date(2022, 6, 30)),
        (date(2022, 7, 31), "board_changes_12m", 2.0, date(2022, 2, 1)),
    )
    frame = assemble(grid, values, config)
    assert frame.columns == [*KEY_COLUMNS, *feature_columns(config)]
    assert frame.schema["current_ratio"] == pl.Float64
    assert frame.schema["art233_triggered"] == pl.Boolean
    assert frame.schema["board_changes_12m"] == pl.Int32
    july = frame.filter(pl.col("as_of_date") == date(2022, 7, 31)).row(0, named=True)
    assert (july["current_ratio"], july["current_ratio__known_from"]) == (2.0, date(2022, 6, 30))
    assert july["art233_triggered"] is True and july["board_changes_12m"] == 2
    assert july["as_of_year"] == 2022
    assert july["feature_set_version"] == "feature_set_v1"
    assert july["feature_set_hash"] == config.feature_set_hash
    # A grid row with no values is still a row, every feature null.
    may = frame.filter(pl.col("as_of_date") == date(2022, 5, 31)).row(0, named=True)
    assert all(may[name] is None for name in feature_columns(config))


def test_the_contract_rejects_a_value_known_after_its_row(config: FeatureConfig) -> None:
    values = _values((date(2022, 5, 31), "current_ratio", 2.0, date(2022, 6, 30)))
    with pytest.raises(pandera.errors.SchemaError, match="current_ratio_known_by_as_of_date"):
        assemble(_grid(date(2022, 5, 31)), values, config)


def test_the_contract_pairs_a_value_with_its_date(config: FeatureConfig) -> None:
    frame = assemble(_grid(date(2022, 7, 31)), _values(), config).with_columns(
        pl.lit(1.5).alias("roa")
    )
    with pytest.raises(pandera.errors.SchemaError, match="roa_iff_known_from"):
        feature_store_contract(config).validate(frame)


def test_equal_inputs_write_equal_bytes(config: FeatureConfig, tmp_path: Path) -> None:
    grid = _grid(date(2021, 12, 31), date(2022, 7, 31))
    values = _values(
        (date(2022, 7, 31), "current_ratio", 2.0, date(2022, 6, 30)),
        (date(2021, 12, 31), "curators_ever", 0.0, date(2015, 3, 1)),
    )
    for run in ("a", "b"):
        write_dataset(assemble(grid, values, config), tmp_path / run, "feature_store", "as_of_year")

    def files(run: str) -> dict[str, bytes]:
        root = tmp_path / run
        return {str(p.relative_to(root)): p.read_bytes() for p in sorted(root.rglob("*.parquet"))}

    assert files("a") == files("b")
    assert len(files("a")) == 2  # one partition per as_of_year
