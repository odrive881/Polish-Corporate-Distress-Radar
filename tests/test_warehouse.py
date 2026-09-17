"""Derived-dataset writer (ADR 0008): deterministic bytes, whole-dataset replacement."""

from datetime import date
from decimal import Decimal
from pathlib import Path

import polars as pl

from distress_radar.warehouse import read_dataset, write_dataset


def _frame(years: list[int]) -> pl.DataFrame:
    return pl.DataFrame(
        {
            "fiscal_year": years,
            "value": [Decimal("1.50") * i for i in range(len(years))],
            "period_end": [date(y, 12, 31) for y in years],
        },
        schema={"fiscal_year": pl.Int32, "value": pl.Decimal(20, 2), "period_end": pl.Date},
    )


def _bytes(root: Path) -> dict[str, bytes]:
    return {str(p.relative_to(root)): p.read_bytes() for p in sorted(root.rglob("*.parquet"))}


def test_same_input_gives_identical_files(tmp_path: Path) -> None:
    frame = _frame([2021, 2022, 2022])
    write_dataset(frame, tmp_path / "a", "facts", "fiscal_year")
    write_dataset(frame, tmp_path / "b", "facts", "fiscal_year")
    first, second = _bytes(tmp_path / "a"), _bytes(tmp_path / "b")
    assert first == second
    assert sorted(first) == [
        "facts/fiscal_year=2021/part-0.parquet",
        "facts/fiscal_year=2022/part-0.parquet",
    ]


def test_rewrite_replaces_the_whole_dataset(tmp_path: Path) -> None:
    write_dataset(_frame([2020, 2021]), tmp_path, "facts", "fiscal_year")
    write_dataset(_frame([2022]), tmp_path, "facts", "fiscal_year")
    assert sorted(_bytes(tmp_path)) == ["facts/fiscal_year=2022/part-0.parquet"]
    assert not [p for p in tmp_path.iterdir() if p.name.startswith(".")]
    back = read_dataset(tmp_path, "facts")
    assert back.to_dicts() == _frame([2022]).to_dicts()
    assert back.schema == _frame([2022]).schema


def test_empty_dataset_stays_readable(tmp_path: Path) -> None:
    write_dataset(_frame([]), tmp_path, "facts", "fiscal_year")
    back = read_dataset(tmp_path, "facts")
    assert back.is_empty()
    assert back.schema == _frame([]).schema
