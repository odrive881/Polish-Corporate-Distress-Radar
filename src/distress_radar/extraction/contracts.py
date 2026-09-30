"""Pandera contracts for `text_signals` and `text_coverage` (plan 0013 step H; AGENT_SPEC §5).

As in `parsing.contracts`: a failure means a bug in this package, not bad input (bad input is
quarantined before it gets here), so callers let it fail the run.
"""

# pandera's signatures reference types pyright cannot resolve; scoped to this module.
# pyright: reportUnknownMemberType=false

from __future__ import annotations

from typing import get_args

import pandera.polars as pa
import polars as pl

from distress_radar.extraction.preprocessing import SIGNAL_TYPES
from distress_radar.extraction.schemas import Confidence, OpinionValue
from distress_radar.extraction.text_signals import COVERAGE_COLUMNS, SIGNAL_COLUMNS, CoverageStatus

_SHA256 = r"^[0-9a-f]{64}$"
_KRS = r"^[0-9]{10}$"
_VALUES = ["present", "absent", *get_args(OpinionValue)]


def _column(
    name: str, dtype: pl.DataType | type[pl.DataType], nullable: frozenset[str]
) -> pa.Column:
    checks: list[pa.Check] = []
    if name == "krs":
        checks.append(pa.Check.str_matches(_KRS))
    elif name in ("source_document_hash", "response_key"):
        checks.append(pa.Check.str_matches(_SHA256))
    elif name == "signal_type":
        checks.append(pa.Check.isin(list(SIGNAL_TYPES)))
    elif name == "value":
        checks.append(pa.Check.isin(_VALUES))
    elif name == "extraction_method":
        checks.append(pa.Check.isin(["llm", "rule"]))
    elif name == "confidence":
        checks.append(pa.Check.isin(list(get_args(Confidence))))
    elif name == "status":
        checks.append(pa.Check.isin(list(get_args(CoverageStatus))))
    elif dtype == pl.Int32 and name != "fiscal_year":
        checks.append(pa.Check.ge(0))
    return pa.Column(dtype, checks=checks, nullable=name in nullable)


def _evidence_when_present(data: pa.PolarsData) -> pl.LazyFrame:
    return data.lazyframe.select((pl.col("value") == "absent") == pl.col("evidence_span").is_null())


def _response_for_llm(data: pa.PolarsData) -> pl.LazyFrame:
    return data.lazyframe.select(
        (pl.col("extraction_method") == "llm") == pl.col("response_key").is_not_null()
    )


def _opinion_values(data: pa.PolarsData) -> pl.LazyFrame:
    return data.lazyframe.select(
        (pl.col("signal_type") == "opinion_type") | pl.col("value").is_in(["present", "absent"])
    )


# One row per kept extraction; every lineage column is required (invariant 3).
TEXT_SIGNALS = pa.DataFrameSchema(
    {
        name: _column(name, dtype, frozenset({"evidence_span", "response_key"}))
        for name, dtype in SIGNAL_COLUMNS.items()
    },
    checks=[
        pa.Check(_evidence_when_present, error="evidence exactly when a signal is present"),
        pa.Check(_response_for_llm, error="a model's extraction names its stored response"),
        pa.Check(_opinion_values, error="only opinion_type takes an opinion as its value"),
    ],
    strict=True,
    ordered=True,
    unique=["source_document_hash", "source_member", "attachment", "page", "signal_type"],
    name="text_signals",
)


def _counts_add_up(data: pa.PolarsData) -> pl.LazyFrame:
    return data.lazyframe.select(
        (
            pl.col("pages_text") + pl.col("pages_needs_ocr") + pl.col("pages_sparse")
            == pl.col("pages")
        )
        & (
            pl.col("kept_present")
            + pl.col("kept_absent")
            + pl.col("discarded")
            + pl.col("unanswered")
            <= pl.col("pages_selected")
        )
        & (pl.col("pages_selected") <= pl.col("pages_text"))
    )


# One row per (statement file, signal_type): what was read.
TEXT_COVERAGE = pa.DataFrameSchema(
    {name: _column(name, dtype, frozenset()) for name, dtype in COVERAGE_COLUMNS.items()},
    checks=[pa.Check(_counts_add_up, error="page and extraction counts add up")],
    strict=True,
    ordered=True,
    unique=["source_document_hash", "source_member", "signal_type"],
    name="text_coverage",
)
