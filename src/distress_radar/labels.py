"""Outcome labels: their parameters (plan 0008 step B) and the label-set freeze (step G).

`config/labels/<label_version>.yaml` holds the engineering choices behind `outcome_labels`:
horizons, the `as_of_date` grid, precedence, the cutoff policy. The file name is the version,
so a changed value is a new file and a new `label_version`, never an edit in place.

The labels themselves are built in SQLMesh (`transform/models/marts/outcome_labels.sql`).
`freeze_label_set` then hashes the rows and writes the set once, under its hash, to
`WAREHOUSE_DIR/outcome_labels/`, and `record_label_set` adds it to `label_sets` in Postgres. A
model trains on a hash, never on "the latest labels" (AGENT_SPEC §4.6, decision 7).
"""

# polars's signatures reference types pyright cannot resolve; scoped to this module.
# pyright: reportUnknownMemberType=false

from __future__ import annotations

import calendar
import hashlib
import shutil
import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Literal

import polars as pl
import yaml
from psycopg import Connection
from pydantic import BaseModel, ConfigDict, Field, model_validator

from distress_radar.parsing.canonical_schema import CONFIG_DIR
from distress_radar.parsing.legal_taxonomy import EVENT_OUTCOME_CLASSES, EventOutcomeClass

# The sources whose last complete fetch bounds what is known: an entity's cutoff is the earliest
# of their latest fetches (`cutoff: earliest_last_complete_fetch`). Read by the SQL label grid and
# by the feature grid (plan 0010 step E), which must agree.
LABEL_SOURCES: tuple[str, ...] = ("KRS", "MSiG")


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class AsOfGrid(_Frozen):
    frequency: Literal["month_end"]
    start: date

    @model_validator(mode="after")
    def _month_end(self) -> AsOfGrid:
        last = calendar.monthrange(self.start.year, self.start.month)[1]
        if self.start.day != last:
            raise ValueError(f"as_of_grid.start {self.start} is not a month end")
        return self


class LabelConfig(_Frozen):
    label_version: str
    horizons_months: tuple[Literal[12, 24], ...]
    as_of_grid: AsOfGrid
    precedence: tuple[EventOutcomeClass, ...]
    cutoff: Literal["earliest_last_complete_fetch"]
    exclude_in_proceeding: bool
    undated_event: Literal["on_or_before_known_from"]
    # Version 2 (plan 0009). Absent from version 1, whose labels they leave unchanged.
    alive_lag_months: int = Field(default=0, ge=0)
    petition_expiry_months: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def _consistent(self) -> LabelConfig:
        if not self.horizons_months or len(set(self.horizons_months)) != len(self.horizons_months):
            raise ValueError(
                f"horizons_months must be non-empty and distinct: {self.horizons_months}"
            )
        if sorted(self.precedence) != sorted(EVENT_OUTCOME_CLASSES):
            raise ValueError(
                f"precedence must rank each of {list(EVENT_OUTCOME_CLASSES)} once: {self.precedence}"
            )
        return self


def load_label_config(label_version: str, config_dir: Path = CONFIG_DIR) -> LabelConfig:
    path = config_dir / "labels" / f"{label_version}.yaml"
    config = LabelConfig.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))
    if config.label_version != path.stem:
        raise ValueError(f"{path}: label_version must match the file name")
    return config


# --- freezing a label set (plan 0008 decision 7) -------------------------------------------------

OUTCOME_LABELS_SCHEMA: dict[str, pl.DataType | type[pl.DataType]] = {
    "krs": pl.String,
    "as_of_date": pl.Date,
    "horizon_months": pl.Int32,
    "outcome_class": pl.String,
    "censored": pl.Boolean,
    "event_date": pl.Date,
    "event_known_from": pl.Date,
    "trigger_event_type": pl.String,
    "proceeding_id": pl.String,
    "proceeding_id_note": pl.String,
    "regime_flag": pl.Boolean,
    "source_era": pl.String,
    "cutoff_date": pl.Date,
    "label_version": pl.String,
}
LABEL_SORT_KEY = ["krs", "as_of_date", "horizon_months"]
DATASET = "outcome_labels"

LABEL_SETS_DDL = """
CREATE TABLE IF NOT EXISTS label_sets (
    label_set_hash     text PRIMARY KEY,
    label_version      text NOT NULL,
    cutoff_date        date NOT NULL,
    row_count          integer NOT NULL,
    parquet_path       text NOT NULL,  -- relative to WAREHOUSE_DIR
    created_at         timestamptz NOT NULL,
    ingestion_run_id   text NOT NULL
)
"""


@dataclass(frozen=True)
class FrozenLabelSet:
    label_set_hash: str
    label_version: str
    cutoff_date: date
    row_count: int
    path: Path  # relative to WAREHOUSE_DIR
    written: bool  # False when this exact set was already frozen


def label_set_hash(labels: pl.DataFrame) -> str:
    """SHA-256 of the label rows: fixed columns, fixed order, CSV with ISO dates.

    Hashes the rows, not the Parquet bytes, so a library upgrade that changes the file
    encoding does not change the identity of the set.
    """
    frame = labels.select(list(OUTCOME_LABELS_SCHEMA)).sort(LABEL_SORT_KEY)
    return hashlib.sha256(frame.write_csv(date_format="%Y-%m-%d").encode("utf-8")).hexdigest()


def freeze_label_set(labels: pl.DataFrame, warehouse_dir: Path) -> FrozenLabelSet:
    """Write `labels` once under its hash; never overwrite a frozen set.

    Layout: `outcome_labels/label_set_hash=<hash>/part-0.parquet`, rows sorted, the hash as a
    column. Re-freezing identical rows finds the directory and writes nothing.
    """
    frame = (
        labels.select(list(OUTCOME_LABELS_SCHEMA))
        .cast(pl.Schema(OUTCOME_LABELS_SCHEMA))
        .sort(LABEL_SORT_KEY)
    )
    versions = frame["label_version"].unique().to_list()
    cutoffs = frame["cutoff_date"].unique().to_list()
    if len(versions) != 1:
        raise ValueError(f"a label set has exactly one label_version, got {versions}")
    digest = label_set_hash(frame)
    path = Path(DATASET) / f"label_set_hash={digest}" / "part-0.parquet"
    target = warehouse_dir / path.parent
    written = False
    if not (warehouse_dir / path).exists():
        staging = warehouse_dir / DATASET / f".staging-{uuid.uuid4().hex}"
        staging.mkdir(parents=True)
        try:
            frame.with_columns(pl.lit(digest).alias("label_set_hash")).write_parquet(
                staging / "part-0.parquet", compression="zstd", compression_level=3, statistics=True
            )
            staging.rename(target)
            written = True
        except BaseException:
            shutil.rmtree(staging, ignore_errors=True)
            raise
    return FrozenLabelSet(
        label_set_hash=digest,
        label_version=str(versions[0]),
        cutoff_date=max(cutoffs),
        row_count=frame.height,
        path=path,
        written=written,
    )


def record_label_set(conn: Connection, frozen: FrozenLabelSet, ingestion_run_id: str) -> None:
    """Add the set to `label_sets` (Postgres); a set frozen before keeps its first row."""
    conn.execute(LABEL_SETS_DDL)
    conn.execute(
        """
        INSERT INTO label_sets
            (label_set_hash, label_version, cutoff_date, row_count, parquet_path, created_at,
             ingestion_run_id)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT DO NOTHING
        """,
        (
            frozen.label_set_hash,
            frozen.label_version,
            frozen.cutoff_date,
            frozen.row_count,
            frozen.path.as_posix(),
            datetime.now(UTC),
            ingestion_run_id,
        ),
    )
