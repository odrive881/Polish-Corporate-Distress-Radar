"""H1: ASOF assembly of `feature_store` (plan 0010 step E; ADR 0012).

- `build_grid`: the label grid without horizons (plan 0010 decision 1). Month-ends from the label
  grid's start, or the entity's registration if later, to the entity's cutoff: the earliest of the
  latest fetches of each label source (`distress_radar.labels.LABEL_SOURCES`). An entity missing a
  source has no cutoff and no rows, as in `staging.outcome_label_grid`. Rows the labels exclude
  (in a proceeding, deregistered) still get features; Phase 6 filters.
- `assemble`: the families' long rows, pivoted onto the grid, one column per feature and one
  `__known_from` companion each, validated against the contract.
- `build_feature_store`: loads the inputs (warehouse Parquet, the Postgres manifest), assembles,
  and writes `WAREHOUSE_DIR/feature_store/` by `as_of_year`, replacing the whole dataset
  (ADR 0008). Equal inputs write equal bytes.

Features are recomputed in full on every run (plan 0010 decision 8).
"""

# polars's signatures reference types pyright cannot resolve; scoped to this module.
# pyright: reportUnknownMemberType=false

from __future__ import annotations

import calendar
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path

import polars as pl
from psycopg import Connection

from distress_radar.acquisition.document_retrieval import load_document_types
from distress_radar.features import feature_definitions as fd
from distress_radar.features.config import FeatureConfig
from distress_radar.features.contracts import (
    DTYPES,
    KEY_COLUMNS,
    KNOWN_FROM_SUFFIX,
    feature_columns,
    feature_store_contract,
)
from distress_radar.features.panel import FILINGS_SCHEMA, build_panel
from distress_radar.labels import LABEL_SOURCES
from distress_radar.warehouse import read_dataset, write_dataset

DATASET = "feature_store"
PARTITION = "as_of_year"
SORT_KEY = ["krs", "as_of_date"]

FETCHES_SCHEMA: dict[str, pl.DataType | type[pl.DataType]] = {
    "krs": pl.String,
    "source": pl.String,
    "fetched_on": pl.Date,  # the fetch's UTC date
}

# The columns `load_inputs` selects, in its query's order.
_FILING_INDEX_SCHEMA: dict[str, pl.DataType | type[pl.DataType]] = {
    "krs": pl.String,
    "document_ref": pl.String,
    "rdf_type_code": pl.String,
    "period_start": pl.Date,
    "period_end": pl.Date,
    "submission_date": pl.Date,
    "deleted_on": pl.Date,
    "is_correction": pl.Boolean,
    "file_name": pl.String,
}
_PARSE_STATUS_SCHEMA: dict[str, pl.DataType | type[pl.DataType]] = {
    "krs": pl.String,
    "document_ref": pl.String,
    "status": pl.String,
}


def _month_end(day: date) -> date:
    return date(day.year, day.month, calendar.monthrange(day.year, day.month)[1])


def _month_ends(first: date, last: date) -> list[date]:
    """Month-ends from `first`'s month to `last`, both inclusive where they are month-ends."""
    out: list[date] = []
    y, m = first.year, first.month
    while True:
        end = date(y, m, calendar.monthrange(y, m)[1])
        if end > last:
            return out
        if end >= first:
            out.append(end)
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)


def build_grid(
    entities: Sequence[str],
    fetches: pl.DataFrame,
    legal_events: pl.DataFrame,
    *,
    start: date,
    sources: Sequence[str] = LABEL_SOURCES,
) -> pl.DataFrame:
    """(krs, as_of_date) for every entity with a cutoff: the label grid without horizons."""
    latest = (
        fetches.filter(pl.col("source").is_in(list(sources)))
        .group_by(["krs", "source"])
        .agg(pl.col("fetched_on").max())
    )
    cutoffs = (
        latest.group_by("krs")
        .agg(pl.col("fetched_on").min().alias("cutoff"), pl.col("source").n_unique().alias("n"))
        .filter(pl.col("n") == len(set(sources)))
    )
    registered = (
        legal_events.filter(pl.col("event_type") == "registered")
        .group_by("krs")
        .agg(pl.coalesce("event_date", "known_from").min().alias("registered_on"))
    )
    known = set(entities)
    rows: list[tuple[str, date]] = []
    joined = cutoffs.join(registered, on="krs", how="left").sort("krs")
    for krs, cutoff, _, registered_on in joined.iter_rows():
        if krs not in known:
            continue
        first = max(start, registered_on) if registered_on is not None else start
        rows.extend((krs, day) for day in _month_ends(first, cutoff))
    return pl.DataFrame(rows, schema=fd.GRID_SCHEMA, orient="row").sort(SORT_KEY)


def assemble(grid: pl.DataFrame, values: pl.DataFrame, config: FeatureConfig) -> pl.DataFrame:
    """The wide `feature_store` frame: every grid row, every feature, validated."""
    features = config.feature_set.features
    wide = grid.select(SORT_KEY).unique()
    if not values.is_empty():
        by_value = values.pivot(on="feature", index=SORT_KEY, values="value")
        by_known = values.pivot(on="feature", index=SORT_KEY, values="known_from")
        by_known = by_known.rename(
            {c: f"{c}{KNOWN_FROM_SUFFIX}" for c in by_known.columns if c not in SORT_KEY}
        )
        wide = wide.join(by_value, on=SORT_KEY, how="left").join(by_known, on=SORT_KEY, how="left")
    columns = feature_columns(config)
    # A feature with no value anywhere in the grid still gets its (all-null) column.
    missing = [
        pl.lit(None, dtype=dtype).alias(name)
        for name, dtype in columns.items()
        if name not in wide.columns
    ]
    wide = wide.with_columns(missing)
    casts: list[pl.Expr] = []
    for f in features:
        dtype = columns[f.name]
        expr = pl.col(f.name).cast(pl.Float64)  # the families' values are floats
        if dtype == DTYPES["count"]:
            expr = expr.round(0)
        casts.append(expr.cast(dtype))
        casts.append(pl.col(f"{f.name}{KNOWN_FROM_SUFFIX}").cast(pl.Date))
    frame = (
        wide.with_columns(
            pl.col("as_of_date").dt.year().cast(pl.Int32).alias("as_of_year"),
            pl.lit(config.feature_set.feature_set_version).alias("feature_set_version"),
            pl.lit(config.feature_set_hash).alias("feature_set_hash"),
            *casts,
        )
        .select([*KEY_COLUMNS, *columns])
        .sort(SORT_KEY)
    )
    return feature_store_contract(config).validate(frame)


@dataclass(frozen=True)
class FeatureStoreBuild:
    frame: pl.DataFrame
    written: list[Path]
    panel_excluded: pl.DataFrame  # statement files the panel left out, with reasons
    feature_set_hash: str


def load_inputs(
    conn: Connection, warehouse_dir: Path, config: FeatureConfig
) -> tuple[fd.FeatureInputs, pl.DataFrame, list[str], pl.DataFrame]:
    """The families' inputs, the panel's exclusions, the entities and their fetches."""
    canonical = read_dataset(warehouse_dir, "financial_statements_canonical")
    restatements = read_dataset(warehouse_dir, "restatement_events")
    legal_events = read_dataset(warehouse_dir, "legal_events")
    filing_index = pl.DataFrame(
        conn.execute(
            "SELECT krs, document_ref, rdf_type_code, period_start, period_end, submission_date,"
            " deleted_on, is_correction, file_name FROM filing_index ORDER BY krs, document_ref"
        ).fetchall(),
        schema=_FILING_INDEX_SCHEMA,
        orient="row",
    ).with_columns(pl.col("krs").str.strip_chars())
    parse_status = pl.DataFrame(
        conn.execute(
            "SELECT krs, document_ref, status FROM parsed_documents ORDER BY 1, 2, 3"
        ).fetchall(),
        schema=_PARSE_STATUS_SCHEMA,
        orient="row",
    ).with_columns(pl.col("krs").str.strip_chars())
    entities = sorted(
        str(krs).strip() for (krs,) in conn.execute("SELECT krs FROM entity_master").fetchall()
    )
    fetch_rows = conn.execute("SELECT krs, source, fetched_at FROM legal_source_fetches").fetchall()
    fetches = pl.DataFrame(
        [
            (str(krs).strip(), source, fetched_at.astimezone(UTC).date())
            for krs, source, fetched_at in fetch_rows
            if isinstance(fetched_at, datetime)
        ],
        schema=FETCHES_SCHEMA,
        orient="row",
    )

    panel = build_panel(
        canonical,
        filing_index.select(FILINGS_SCHEMA.keys()),
        config.line_items,
        include_quarantined=config.feature_set.include_quarantined_statements,
    )
    statement_codes = [
        code for code, t in load_document_types().types.items() if t.canonical == "statement"
    ]
    filings = fd.statement_filings(filing_index, parse_status, canonical, statement_codes)
    inputs = fd.FeatureInputs(
        panel=panel.frame, filings=filings, restatements=restatements, legal_events=legal_events
    )
    return inputs, panel.excluded, entities, fetches


def build_feature_store(
    conn: Connection, warehouse_dir: Path, config: FeatureConfig, *, grid_start: date
) -> FeatureStoreBuild:
    """Build and write `feature_store` from the warehouse and the manifest."""
    inputs, excluded, entities, fetches = load_inputs(conn, warehouse_dir, config)
    grid = build_grid(entities, fetches, inputs.legal_events, start=_month_end(grid_start))
    frame = assemble(grid, fd.compute_features(grid, inputs, config), config)
    written = write_dataset(frame, warehouse_dir, DATASET, PARTITION)
    return FeatureStoreBuild(frame, written, excluded, config.feature_set_hash)
