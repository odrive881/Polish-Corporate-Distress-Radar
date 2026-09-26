"""H2: the leakage checks (AGENT_SPEC §9.1; plan 0010 step F). Invariant 1.

Two checks, independent of each other:
- `known_from_violations`, §9.1 as written: on every row of `feature_store`, every non-null
  feature has a `__known_from`, and it is on or before the row's `as_of_date`. It trusts the
  companion columns, so a family that mis-dates its facts passes it.
- `truncation_differences`, the per-family variant, which trusts nothing the families report:
  each family is recomputed for each `as_of_date` from the sources cut to what was public by then
  (`known_on`), and must give exactly what it gave from the full sources. A family that reads a
  fact published after `as_of_date`, by any path, gives a different answer.

The blocking test (`tests/features/test_leakage.py`) runs both on a synthetic warehouse with
traps, and shows that deliberately leaky families fail the second. The Dagster asset check
runs them on the live store (plan 0010 step G).
"""

# polars's signatures reference types pyright cannot resolve; scoped to this module.
# pyright: reportUnknownMemberType=false

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import date

import polars as pl

from distress_radar.features import feature_definitions as fd
from distress_radar.features.asof_assembly import FeatureSources, feature_inputs
from distress_radar.features.config import Family, FeatureConfig
from distress_radar.features.contracts import KNOWN_FROM_SUFFIX

FamilyFn = Callable[[pl.DataFrame, fd.FeatureInputs, FeatureConfig], pl.DataFrame]

VIOLATIONS_SCHEMA: dict[str, pl.DataType | type[pl.DataType]] = {
    "krs": pl.String,
    "as_of_date": pl.Date,
    "feature": pl.String,
    "known_from": pl.Date,
    "reason": pl.String,  # no_known_from | known_after_as_of
}

DIFFERENCES_SCHEMA: dict[str, pl.DataType | type[pl.DataType]] = {
    "family": pl.String,
    "krs": pl.String,
    "as_of_date": pl.Date,
    "feature": pl.String,
    "value": pl.Float64,  # from the full sources
    "known_from": pl.Date,
    "value_then": pl.Float64,  # from the sources as known on as_of_date
    "known_from_then": pl.Date,
}
_KEY = ["krs", "as_of_date", "feature"]
_VALUE_COLUMNS = ["value", "known_from", "value_then", "known_from_then"]


def known_on(sources: FeatureSources, day: date) -> FeatureSources:
    """The sources as an observer had them at the end of `day`.

    A fact is kept when its `known_from` (for `filing_index`, the submission date) is on or
    before `day`; a deletion or removal after `day` had not happened yet. A filing with no
    submission date cannot be dated, so it is not known on any day.
    """
    filing_index = sources.filing_index.filter(
        pl.col("submission_date").is_not_null() & (pl.col("submission_date") <= day)
    ).with_columns(_until("deleted_on", day))
    return FeatureSources(
        canonical=sources.canonical.filter(pl.col("known_from") <= day),
        restatements=sources.restatements.filter(pl.col("known_from") <= day),
        legal_events=sources.legal_events.filter(pl.col("known_from") <= day).with_columns(
            _until("removed_on", day)
        ),
        filing_index=filing_index,
        parse_status=sources.parse_status.join(
            filing_index.select("krs", "document_ref").unique(),
            on=["krs", "document_ref"],
            how="semi",
        ),
    )


def _until(column: str, day: date) -> pl.Expr:
    """A later date is a fact from after `day`: null it."""
    return pl.when(pl.col(column) > day).then(None).otherwise(pl.col(column)).alias(column)


def known_from_violations(frame: pl.DataFrame, config: FeatureConfig) -> pl.DataFrame:
    """§9.1 on a wide `feature_store` frame: one row per offending (row, feature)."""
    found: list[pl.DataFrame] = []
    for f in config.feature_set.features:
        known = pl.col(f"{f.name}{KNOWN_FROM_SUFFIX}")
        found.append(
            frame.filter(
                (pl.col(f.name).is_not_null() & known.is_null()) | (known > pl.col("as_of_date"))
            ).select(
                "krs",
                "as_of_date",
                pl.lit(f.name).alias("feature"),
                known.alias("known_from"),
                pl.when(known.is_null())
                .then(pl.lit("no_known_from"))
                .otherwise(pl.lit("known_after_as_of"))
                .alias("reason"),
            )
        )
    if not found:
        return pl.DataFrame(schema=VIOLATIONS_SCHEMA)
    return pl.concat(found).cast(VIOLATIONS_SCHEMA).sort(_KEY)  # pyright: ignore[reportArgumentType]


def truncation_differences(
    grid: pl.DataFrame,
    sources: FeatureSources,
    config: FeatureConfig,
    families: Mapping[Family, FamilyFn] = fd.FAMILIES,
) -> pl.DataFrame:
    """Each family, from the full sources and from the sources known on each `as_of_date`.

    Returns one row per (family, row, feature) where the two disagree, on the value or on its
    `known_from`; empty when nothing leaks. The cost is one `feature_inputs` per distinct date.
    """
    inputs, _ = feature_inputs(sources, config)
    full = {name: compute(grid, inputs, config) for name, compute in families.items()}
    found: list[pl.DataFrame] = []
    for (day,) in grid.select("as_of_date").unique().sort("as_of_date").iter_rows():
        rows = grid.filter(pl.col("as_of_date") == day)
        then, _ = feature_inputs(known_on(sources, day), config)
        for name, compute in families.items():
            now = full[name].filter(pl.col("as_of_date") == day)
            past = compute(rows, then, config).rename(
                {"value": "value_then", "known_from": "known_from_then"}
            )
            joined = now.join(past, on=_KEY, how="full", coalesce=True)
            found.append(
                joined.filter(
                    pl.col("value").ne_missing(pl.col("value_then"))
                    | pl.col("known_from").ne_missing(pl.col("known_from_then"))
                ).select(pl.lit(name).alias("family"), *_KEY, *_VALUE_COLUMNS)
            )
    if not found:
        return pl.DataFrame(schema=DIFFERENCES_SCHEMA)
    return (
        pl.concat(found)
        .select(DIFFERENCES_SCHEMA.keys())
        .cast(DIFFERENCES_SCHEMA)  # pyright: ignore[reportArgumentType]
        .sort(["family", *_KEY])
    )
