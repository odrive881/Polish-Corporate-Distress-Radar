"""Derived datasets as Parquet under `WAREHOUSE_DIR` (ADR 0008).

`write_dataset` rebuilds a whole dataset: rows are sorted by the caller, each
partition is written with fixed settings (so equal input gives equal bytes),
and the new directory replaces the old one only once it is complete. Partition
columns stay inside the files too, so readers never depend on Hive parsing.
"""

# polars's signatures reference types pyright cannot resolve; scoped to this module.
# pyright: reportUnknownMemberType=false

from __future__ import annotations

import shutil
import uuid
from pathlib import Path

import polars as pl

COMPRESSION = "zstd"
COMPRESSION_LEVEL = 3


def dataset_dir(warehouse_dir: Path, name: str) -> Path:
    return warehouse_dir / name


def write_dataset(
    frame: pl.DataFrame, warehouse_dir: Path, name: str, partition_by: str
) -> list[Path]:
    """Replace dataset `name` with `frame`, one file per `partition_by` value."""
    target = dataset_dir(warehouse_dir, name)
    staging = warehouse_dir / f".{name}.staging-{uuid.uuid4().hex}"
    staging.mkdir(parents=True)
    written: list[Path] = []
    try:
        values = sorted(frame.get_column(partition_by).unique().to_list())
        for value in values:
            part_dir = staging / f"{partition_by}={value}"
            part_dir.mkdir()
            path = part_dir / "part-0.parquet"
            frame.filter(pl.col(partition_by) == value).write_parquet(
                path, compression=COMPRESSION, compression_level=COMPRESSION_LEVEL, statistics=True
            )
            written.append(target / part_dir.name / path.name)
        if not values:
            # Keep an empty dataset readable: one file with the schema and no rows.
            frame.write_parquet(staging / "empty.parquet", compression=COMPRESSION)
        retired = warehouse_dir / f".{name}.retired-{uuid.uuid4().hex}"
        if target.exists():
            target.rename(retired)
        staging.rename(target)
        shutil.rmtree(retired, ignore_errors=True)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return written


def read_dataset(warehouse_dir: Path, name: str) -> pl.DataFrame:
    target = dataset_dir(warehouse_dir, name)
    if not target.exists():
        raise FileNotFoundError(f"dataset {name!r} has not been materialized under {warehouse_dir}")
    return pl.read_parquet(target / "**" / "*.parquet", hive_partitioning=False)
