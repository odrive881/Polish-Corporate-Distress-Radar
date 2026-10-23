"""E2: statement files with a balance-sheet line below zero that the forms allow none on
(2026-10-23; PROJECT_OVERVIEW stage 5, "sign errors").

The balance-sheet forms carry amounts: an asset, a liability, a capital or a provision is never
negative, and a sp. z o.o.'s share capital is at least 5,000 zł. `sign_check_v<n>.yaml` lists the
lines that may be (the results, the revaluation reserve, the write-offs, total equity); a file with
any other balance-sheet line below zero, in any column, is a suspect.

A suspect is flagged, never corrected: the caller caps its grade at `warn` and records it. What
the filer meant is not knowable from the file. The most common case in the store (2026-10-23) is a
share capital carrying the accumulated losses, with the prior years' result left at zero: the
equity total still adds up, but the line is misfiled, not its sign flipped. Pure: no I/O.
"""

# polars's signatures reference types pyright cannot resolve; scoped to this module.
# pyright: reportUnknownMemberType=false

from __future__ import annotations

import polars as pl

from distress_radar.parsing.canonical_schema import SignCheck
from distress_radar.parsing.unit_plausibility import FILE, SUSPECTS_SCHEMA

# How many of a file's negative lines its detail names.
NAMED_LINES = 3


def suspects(facts: pl.DataFrame, check: SignCheck) -> pl.DataFrame:
    """The files with a balance-sheet line below zero that `check` does not allow
    (`SUSPECTS_SCHEMA`), one row each, sorted; the detail names the lines and columns."""
    negative = facts.filter(
        (pl.col("statement_type") == "balance_sheet")
        & (pl.col("value") < 0)
        & ~pl.col("line_item").is_in(list(check.may_be_negative))
    )
    if negative.is_empty():
        return pl.DataFrame(schema=SUSPECTS_SCHEMA)
    by_line = (
        negative.group_by(*FILE, "krs", "document_ref", "line_item")
        .agg(pl.col("column").unique().sort().str.join(", ").alias("columns"))
        .sort(*FILE, "line_item")
    )
    rows: list[dict[str, object]] = []
    for (h, m, krs, ref), lines in by_line.group_by(
        *FILE, "krs", "document_ref", maintain_order=True
    ):
        named = [f"{li} ({cols})" for li, cols in lines.select("line_item", "columns").iter_rows()]
        more = len(named) - NAMED_LINES
        rows.append(
            {
                "source_document_hash": h,
                "source_member": m,
                "krs": krs,
                "document_ref": ref,
                "detail": f"below zero where the form allows none: {'; '.join(named[:NAMED_LINES])}"
                + (f"; and {more} more" if more > 0 else ""),
            }
        )
    return pl.DataFrame(rows, schema=SUSPECTS_SCHEMA).sort(*FILE)
