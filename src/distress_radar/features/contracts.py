"""E1: the Pandera contract for `feature_store` (plan 0010 step E; AGENT_SPEC §5, §9.1).

The columns depend on the feature set, so the contract is built from it. Beyond the types, it
holds the point-in-time rules every row must satisfy (plan 0010 decision 2):
- a feature is non-null exactly when its `__known_from` is;
- no `__known_from` is after its row's `as_of_date` (the leakage bound, row by row).

A contract failure means a bug in this package, not bad input, so callers let it fail the run.
"""

# pandera's signatures reference types pyright cannot resolve; scoped to this module.
# pyright: reportUnknownMemberType=false

from __future__ import annotations

from collections.abc import Callable

import pandera.polars as pa
import polars as pl

from distress_radar.features.config import FeatureConfig, feature_dtype

_KRS = r"^[0-9]{10}$"
_SHA256 = r"^[0-9a-f]{64}$"

KNOWN_FROM_SUFFIX = "__known_from"

KEY_COLUMNS: dict[str, pl.DataType | type[pl.DataType]] = {
    "krs": pl.String,
    "as_of_date": pl.Date,
    "as_of_year": pl.Int32,  # the partition column (plan 0010 decision 7)
    "feature_set_version": pl.String,
    "feature_set_hash": pl.String,
}

DTYPES: dict[str, pl.DataType | type[pl.DataType]] = {
    "boolean": pl.Boolean,
    "count": pl.Int32,
    "float": pl.Float64,
}


def feature_columns(config: FeatureConfig) -> dict[str, pl.DataType | type[pl.DataType]]:
    """Every feature and its companion, in the feature set's order."""
    out: dict[str, pl.DataType | type[pl.DataType]] = {}
    for f in config.feature_set.features:
        out[f.name] = DTYPES[feature_dtype(f)]
        out[f"{f.name}{KNOWN_FROM_SUFFIX}"] = pl.Date
    return out


def _paired(name: str) -> Callable[[pa.PolarsData], pl.LazyFrame]:
    def check(data: pa.PolarsData) -> pl.LazyFrame:
        return data.lazyframe.select(
            pl.col(name).is_null() == pl.col(f"{name}{KNOWN_FROM_SUFFIX}").is_null()
        )

    return check


def _not_after_as_of(name: str) -> Callable[[pa.PolarsData], pl.LazyFrame]:
    def check(data: pa.PolarsData) -> pl.LazyFrame:
        known = pl.col(f"{name}{KNOWN_FROM_SUFFIX}")
        return data.lazyframe.select(known.is_null() | (known <= pl.col("as_of_date")))

    return check


def _year_matches(data: pa.PolarsData) -> pl.LazyFrame:
    return data.lazyframe.select(
        pl.col("as_of_year") == pl.col("as_of_date").dt.year().cast(pl.Int32)
    )


def feature_store_contract(config: FeatureConfig) -> pa.DataFrameSchema:
    columns: dict[str, pa.Column] = {
        "krs": pa.Column(pl.String, checks=[pa.Check.str_matches(_KRS)]),
        "as_of_date": pa.Column(pl.Date),
        "as_of_year": pa.Column(pl.Int32),
        "feature_set_version": pa.Column(
            pl.String, checks=[pa.Check.eq(config.feature_set.feature_set_version)]
        ),
        "feature_set_hash": pa.Column(
            pl.String,
            checks=[pa.Check.str_matches(_SHA256), pa.Check.eq(config.feature_set_hash)],
        ),
    }
    checks: list[pa.Check] = [pa.Check(_year_matches, name="as_of_year_matches")]
    for name, dtype in feature_columns(config).items():
        columns[name] = pa.Column(dtype, nullable=True)
        if not name.endswith(KNOWN_FROM_SUFFIX):
            checks.append(pa.Check(_paired(name), name=f"{name}_iff_known_from"))
            checks.append(pa.Check(_not_after_as_of(name), name=f"{name}_known_by_as_of_date"))
    return pa.DataFrameSchema(
        columns,
        checks=checks,
        strict=True,
        ordered=True,
        unique=["krs", "as_of_date"],
        name="feature_store",
    )
