"""The layout-independent digest `make reproduce` compares (plan 0021 step A)."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

import polars as pl

from distress_radar.reproducibility import compare, dataset_digest, warehouse_digests
from distress_radar.warehouse import write_dataset

FRAME = pl.DataFrame(
    {
        "krs": ["0000000001", "0000000002", "0000000003"],
        "fiscal_year": [2022, 2023, 2023],
        "value": [Decimal("1.50"), None, Decimal("-2.00")],
        "known_from": [date(2023, 6, 30), date(2024, 6, 30), None],
        "ends": [["bankruptcy"], [], None],
    },
    schema={
        "krs": pl.String,
        "fiscal_year": pl.Int32,
        "value": pl.Decimal(20, 2),
        "known_from": pl.Date,
        "ends": pl.List(pl.String),
    },
)


def test_row_and_column_order_do_not_change_the_digest() -> None:
    shuffled = FRAME.reverse().select(list(reversed(FRAME.columns)))
    assert dataset_digest(shuffled) == dataset_digest(FRAME)


def test_one_changed_value_changes_it() -> None:
    changed = FRAME.with_columns(
        pl.when(pl.col("krs") == "0000000002")
        .then(pl.lit(Decimal("0.01"), dtype=pl.Decimal(20, 2)))
        .otherwise(pl.col("value"))
        .alias("value")
    )
    assert dataset_digest(changed) != dataset_digest(FRAME)


def test_a_duplicated_row_changes_it() -> None:
    assert dataset_digest(pl.concat([FRAME, FRAME.head(1)])) != dataset_digest(FRAME)


def test_a_changed_type_changes_it() -> None:
    assert dataset_digest(FRAME.with_columns(pl.col("fiscal_year").cast(pl.Int64))) != (
        dataset_digest(FRAME)
    )


def test_the_file_layout_does_not_change_it(tmp_path: Path) -> None:
    """The same rows written by year or by entity read back to the same digest."""
    one, two = tmp_path / "one", tmp_path / "two"
    write_dataset(FRAME, one, "restatement_events", "fiscal_year")
    write_dataset(FRAME, two, "restatement_events", "krs")
    first, second = warehouse_digests(one), warehouse_digests(two)
    assert first["restatement_events"] == second["restatement_events"] is not None
    assert first["feature_store"] is None  # not materialized there
    row = next(r for r in compare(first, second, first) if r.name == "restatement_events")
    assert row.reproduced and row.stored_is_current
