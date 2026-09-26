"""Feature coverage: the share of non-null values, by family and form (plan 0010 step G).

Report-only (plan 0010 owner decision 3 for steps F–H): many nulls are structural. A micro form
carries no liability split or equity breakdown, and a period that is not a year long nulls every
length-dependent feature (owner decision 7). A threshold would fail on correct data, so this
measures and Phase 6 reads it.

A row's **form** is that of the statement speaking for the latest period known at its
`as_of_date` (the one the financial families read), from its `structure_version`: `full`,
`small` or `micro`, or `none` before any statement is known. A period filled from a later
filing's prior-year column takes that filing's form.
"""

# polars's signatures reference types pyright cannot resolve; scoped to this module.
# pyright: reportUnknownMemberType=false

from __future__ import annotations

from bisect import bisect_right
from datetime import date

import polars as pl

from distress_radar.features.config import FeatureConfig

NO_STATEMENT = "none"

FORMS_SCHEMA: dict[str, pl.DataType | type[pl.DataType]] = {
    "krs": pl.String,
    "as_of_date": pl.Date,
    "form": pl.String,
}
COVERAGE_SCHEMA: dict[str, pl.DataType | type[pl.DataType]] = {
    "family": pl.String,
    "form": pl.String,
    "rows": pl.Int64,  # grid rows of that form
    "values": pl.Int64,  # rows x features in the family
    "non_null": pl.Int64,
    "share": pl.Float64,  # non_null / values
}


def latest_forms(grid: pl.DataFrame, panel: pl.DataFrame, canonical: pl.DataFrame) -> pl.DataFrame:
    """(krs, as_of_date, form) for every grid row (`FORMS_SCHEMA`)."""
    forms = dict(
        canonical.select("document_ref", pl.col("structure_version").str.split("-").list.first())
        .unique()
        .iter_rows()
    )
    # Each entity's panel versions, one per (known_from, period_end), in known_from order.
    versions: dict[str, list[tuple[date, date, str, bool]]] = {}
    for krs, known_from, period_end, ref, kind in (
        panel.select("krs", "known_from", "period_end", "document_ref", "source_kind")
        .unique(["krs", "known_from", "period_end"])
        .sort(["krs", "known_from", "period_end"])
        .iter_rows()
    ):
        versions.setdefault(krs, []).append((known_from, period_end, ref, kind == "withdrawn"))

    rows: list[tuple[str, date, str]] = []
    for krs, days in (
        grid.select("krs", "as_of_date")
        .unique()
        .sort(["krs", "as_of_date"])
        .group_by("krs", maintain_order=True)
        .agg("as_of_date")
        .iter_rows()
    ):
        history = versions.get(krs, [])
        known_days = [v[0] for v in history]
        for day in days:
            speaking: dict[date, tuple[str, bool]] = {}
            for _, period_end, ref, withdrawn in history[: bisect_right(known_days, day)]:
                speaking[period_end] = (ref, withdrawn)
            live = {end: ref for end, (ref, withdrawn) in speaking.items() if not withdrawn}
            form = forms.get(live[max(live)], NO_STATEMENT) if live else NO_STATEMENT
            rows.append((krs, day, form))
    return pl.DataFrame(rows, schema=FORMS_SCHEMA, orient="row")


def coverage(frame: pl.DataFrame, forms: pl.DataFrame, config: FeatureConfig) -> pl.DataFrame:
    """Non-null shares of a `feature_store` frame by family and form (`COVERAGE_SCHEMA`)."""
    by_family: dict[str, list[str]] = {}
    for f in config.feature_set.features:
        by_family.setdefault(f.family, []).append(f.name)
    tagged = frame.join(forms, on=["krs", "as_of_date"], how="left").with_columns(
        pl.col("form").fill_null(NO_STATEMENT)
    )
    parts: list[pl.DataFrame] = []
    for family, names in by_family.items():
        parts.append(
            tagged.group_by("form")
            .agg(
                pl.len().alias("rows"),
                pl.sum_horizontal([pl.col(n).is_not_null() for n in names]).sum().alias("non_null"),
            )
            .with_columns(
                pl.lit(family).alias("family"),
                (pl.col("rows") * len(names)).alias("values"),
            )
        )
    if not parts:
        return pl.DataFrame(schema=COVERAGE_SCHEMA)
    return (
        pl.concat(parts)
        .with_columns((pl.col("non_null") / pl.col("values")).alias("share"))
        .select(COVERAGE_SCHEMA.keys())
        .cast(COVERAGE_SCHEMA)  # pyright: ignore[reportArgumentType]
        .sort(["family", "form"])
    )
