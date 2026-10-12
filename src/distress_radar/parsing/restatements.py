"""`restatement_events` from the canonical facts (AGENT_SPEC §4.3, §5).

One function builds the dataset, for the asset that writes it; the other tells whether a stored
copy is still the one the current canonical facts give. `restatement_events` is a pure function
of `financial_statements_canonical`, so a stored copy that differs was built from older facts:
the feature store refuses it rather than reading restatements that predate the statements beside
them (found 2026-10-12: the canonical facts rebuilt over list v1, the restatements not).
"""

# polars's signatures reference types pyright cannot resolve; scoped to this module.
# pyright: reportUnknownMemberType=false

from __future__ import annotations

from decimal import Decimal

import polars as pl

from distress_radar.parsing.accounting_identities import prior_year_consistency
from distress_radar.parsing.contracts import RESTATEMENT_EVENTS


class StaleInputError(RuntimeError):
    """A stored dataset was built from older inputs than the ones beside it."""


SORT = [
    "krs",
    "period_end",
    "restating_document_ref",
    "restating_source_member",
    "restated_column",
    "line_item",
]


def restatement_events(canonical: pl.DataFrame, tolerance: Decimal) -> pl.DataFrame:
    """Every prior-year figure that differs from the one filed before, from the files graded
    `pass` or `warn` (a quarantined file's figures are not trusted)."""
    facts = canonical.filter(pl.col("quality_grade") != "quarantined")
    return RESTATEMENT_EVENTS.validate(prior_year_consistency(facts, tolerance).sort(SORT))


def stale_restatements(stored: pl.DataFrame, canonical: pl.DataFrame, tolerance: Decimal) -> str:
    """Why `stored` is not what `canonical` gives, or "" when it is."""
    expected = restatement_events(canonical, tolerance)
    columns = list(expected.columns)
    if set(stored.columns) != set(columns):
        return f"its columns differ: {sorted(set(stored.columns) ^ set(columns))}"
    left = stored.select(columns).sort(columns, nulls_last=True)
    right = expected.sort(columns, nulls_last=True)
    if left.equals(right):
        return ""
    return (
        f"it holds {stored.height} rows where the current canonical facts give {expected.height}; "
        "rebuild restatement_events first"
    )
