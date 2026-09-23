"""E1: Pandera contracts for the C2 and E2 output tables (AGENT_SPEC §5, §6E1).

A contract failure means a bug in this package, not bad input data (bad input
is quarantined before it gets here), so callers let the error fail the run.
"""

# pandera's signatures reference types pyright cannot resolve; scoped to this module.
# pyright: reportUnknownMemberType=false

from __future__ import annotations

import pandera.polars as pa
import polars as pl

from distress_radar.parsing.accounting_identities import (
    CHECK_STATUSES,
    IDENTITY_CHECK_RESULTS_SCHEMA,
    IDENTITY_CHECKS,
    RESTATEMENT_SCHEMA,
    SEVERITIES,
)
from distress_radar.parsing.legal_events import LEGAL_EVENTS_SCHEMA
from distress_radar.parsing.legal_taxonomy import EVENT_OUTCOME_CLASSES
from distress_radar.parsing.mapping_engine import CANONICAL_COLUMNS

_SHA256 = r"^[0-9a-f]{64}$"
_KRS = r"^[0-9]{10}$"


def _column(name: str, dtype: pl.DataType | type[pl.DataType], nullable: bool) -> pa.Column:
    checks: list[pa.Check] = []
    if name == "krs":
        checks.append(pa.Check.str_matches(_KRS))
    elif name in ("source_document_hash", "original_document_hash", "restating_document_hash"):
        checks.append(pa.Check.str_matches(_SHA256))
    elif name == "statement_type":
        checks.append(
            pa.Check.isin(["balance_sheet", "income_statement", "cash_flow", "equity_changes"])
        )
    elif name == "variant":
        checks.append(pa.Check.isin(["comparative", "calculation", "direct", "indirect", "n/a"]))
    elif name == "column":
        checks.append(pa.Check.isin(["current_year", "prior_year", "prior_year_restated"]))
    elif name == "restated_column":
        # §5: only a comparative column can be restated, never the current year.
        checks.append(pa.Check.isin(["prior_year", "prior_year_restated"]))
    elif name == "quality_grade":
        checks.append(pa.Check.isin(["pass", "warn", "quarantined"]))
    elif name == "check":
        checks.append(pa.Check.isin(list(IDENTITY_CHECKS)))
    elif name == "status":
        checks.append(pa.Check.isin(list(CHECK_STATUSES)))
    elif name == "severity":
        checks.append(pa.Check.isin(list(SEVERITIES)))
    return pa.Column(dtype, checks=checks, nullable=nullable)


# Only the entity identifiers may be null; every lineage column is required (invariant 3).
FINANCIAL_STATEMENTS_CANONICAL = pa.DataFrameSchema(
    {
        name: _column(name, dtype, nullable=name in ("nip", "regon"))
        for name, dtype in CANONICAL_COLUMNS.items()
    },
    strict=True,
    ordered=True,
    unique=["source_document_hash", "source_member", "line_item", "column"],
    name="financial_statements_canonical",
)

RESTATEMENT_EVENTS = pa.DataFrameSchema(
    {name: _column(name, dtype, nullable=False) for name, dtype in RESTATEMENT_SCHEMA.items()},
    strict=True,
    ordered=True,
    name="restatement_events",
)


def _severity_iff_failed(data: pa.PolarsData) -> pl.LazyFrame:
    return data.lazyframe.select(
        pl.col("severity").is_not_null() == (pl.col("status") == "fail")
    )


def _figures_iff_evaluated(data: pa.PolarsData) -> pl.LazyFrame:
    return data.lazyframe.select(
        pl.col("difference").is_null() == (pl.col("status") == "not_applicable")
    )


# `expected`/`actual` are null when a side of the identity was not reported, and
# `difference` exactly when nothing was evaluated; `severity` only on failures.
IDENTITY_CHECK_RESULTS = pa.DataFrameSchema(
    {
        name: _column(
            name, dtype, nullable=name in ("expected", "actual", "difference", "severity")
        )
        for name, dtype in IDENTITY_CHECK_RESULTS_SCHEMA.items()
    },
    checks=[
        pa.Check(_severity_iff_failed, name="severity_iff_failed"),
        pa.Check(_figures_iff_evaluated, name="difference_iff_evaluated"),
    ],
    strict=True,
    ordered=True,
    unique=["source_document_hash", "source_member", "column", "check", "line_item"],
    name="identity_check_results",
)


# --- legal_events (plan 0008 step F) -------------------------------------------------------------

_LEGAL_NULLABLE = {"outcome_class", "event_date", "removed_on", "case_signature", "proceeding_id"}


def _legal_column(name: str, dtype: pl.DataType | type[pl.DataType]) -> pa.Column:
    checks: list[pa.Check] = []
    if name == "krs":
        checks.append(pa.Check.str_matches(_KRS))
    elif name in ("source_document_hash", "dedup_group_id"):
        checks.append(pa.Check.str_matches(_SHA256))
    elif name == "source":
        checks.append(pa.Check.isin(["KRS", "KRZ", "MSiG"]))
    elif name == "outcome_class":
        checks.append(pa.Check.isin(list(EVENT_OUTCOME_CLASSES)))
    elif name == "stage":
        checks.append(pa.Check.isin(["petition", "opening", "closing", "exit", "signal"]))
    return pa.Column(dtype, checks=checks, nullable=name in _LEGAL_NULLABLE)


def _event_year_matches(data: pa.PolarsData) -> pl.LazyFrame:
    return data.lazyframe.select(
        pl.col("event_year") == pl.coalesce("event_date", "known_from").dt.year().cast(pl.Int32)
    )


def _group_within_one_entity(data: pa.PolarsData) -> pl.LazyFrame:
    return data.lazyframe.select(pl.col("krs").n_unique().over("dedup_group_id") == 1)


# Every lineage column is required (invariant 3); `event_date` may be null (no decision date,
# plan 0008), `known_from` never is (AGENT_SPEC §4.7).
LEGAL_EVENTS = pa.DataFrameSchema(
    {name: _legal_column(name, dtype) for name, dtype in LEGAL_EVENTS_SCHEMA.items()},
    checks=[
        pa.Check(_event_year_matches, error="event_year is the year of event_date, else known_from"),
        pa.Check(_group_within_one_entity, error="a dedup group spans entities"),
    ],
    strict=True,
    ordered=True,
    unique=["source_document_hash", "source_element_path"],
    name="legal_events",
)
