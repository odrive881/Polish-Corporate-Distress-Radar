"""E1: Pandera contracts for the C2 output tables (AGENT_SPEC §5, §6E1).

A contract failure means a bug in this package, not bad input data (bad input
is quarantined before it gets here), so callers let the error fail the run.
"""

# pandera's signatures reference types pyright cannot resolve; scoped to this module.
# pyright: reportUnknownMemberType=false

from __future__ import annotations

import pandera.polars as pa
import polars as pl

from distress_radar.parsing.accounting_identities import RESTATEMENT_SCHEMA
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
