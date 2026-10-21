"""E2: statement files that look filed in the wrong unit (plan 0023; PROJECT_OVERVIEW stage 5).

The unit is fixed by the structure (AGENT_SPEC §4.2), so C multiplies a thousands form by 1,000
whatever the filer typed. A filer who types złoty into a thousands form, or thousands into a złoty
one, makes every amount 1,000 times off, and the identities still hold. What gives it away is the
same balance sheet reported twice: a filing's current year and a later filing's prior-year
column for that period. When most lines both carry differ by about 1,000, one of the two is in
the wrong unit.

Which one: the share capital (`BS.EQUITY_LIABILITIES.A.I`) rarely moves, so the side whose share
capital sits about 1,000 from the entity's other filings' is the suspect. When that cannot tell
them apart (no share capital, or no other filings), both are.

A suspect is flagged, never corrected: the caller caps its grade at `warn` and records it. A
correction is the owner's, after review, in `unit_corrections_v<n>.yaml`; a corrected file then
agrees with its neighbours and is no longer a suspect. Pure: no I/O.
"""

# polars's signatures reference types pyright cannot resolve; scoped to this module.
# pyright: reportUnknownMemberType=false

from __future__ import annotations

import polars as pl

from distress_radar.parsing.canonical_schema import UnitCheck

SHARE_CAPITAL = "BS.EQUITY_LIABILITIES.A.I"
PRIOR = ("prior_year", "prior_year_restated")
FILE = ["source_document_hash", "source_member"]
LINE = ["statement_type", "variant", "line_item"]
SUSPECTS_SCHEMA: dict[str, pl.DataType | type[pl.DataType]] = {
    "source_document_hash": pl.String,
    "source_member": pl.String,
    "krs": pl.String,
    "document_ref": pl.String,
    "detail": pl.String,
}


def _near_thousand(log_ratio: pl.Expr, check: UnitCheck) -> pl.Expr:
    return (log_ratio.abs() - 3).abs() <= check.tolerance_log10


def _logged(facts: pl.DataFrame) -> pl.DataFrame:
    return facts.filter(pl.col("value").is_not_null() & (pl.col("value") != 0)).with_columns(
        pl.col("value").cast(pl.Float64).abs().log10().alias("log_value")
    )


def scaled_pairs(facts: pl.DataFrame, check: UnitCheck) -> pl.DataFrame:
    """Each (filing A's current year, later filing B's prior year for the same period) pair whose
    shared lines are mostly about 1,000 apart: `doc_a`/`member_a`, `doc_b`/`member_b`, `krs`,
    `period_end`, `shared_lines`, `share_scaled`, `log_ratio` (A over B, median over the lines)."""
    logged = _logged(facts)
    current = logged.filter(pl.col("column") == "current_year")
    # A prior-year column carries its filing's own period; its period is the entity's
    # previous fiscal year, whose end the current-year rows give.
    periods = (
        current.select("krs", "fiscal_year", "period_end")
        .unique()
        .sort("krs", "fiscal_year", "period_end")
        .unique(["krs", "fiscal_year"], keep="first")
    )
    a = current.select(
        "krs",
        "period_end",
        *LINE,
        pl.col("source_document_hash").alias("doc_a"),
        pl.col("source_member").alias("member_a"),
        pl.col("known_from").alias("known_a"),
        pl.col("log_value").alias("log_a"),
    )
    b = (
        logged.filter(pl.col("column").is_in(PRIOR))
        .sort("column")  # `prior_year` before `prior_year_restated`
        .unique([*FILE, *LINE], keep="first")
        .with_columns((pl.col("fiscal_year") - 1).alias("_prior_year"))
        .join(
            periods.rename({"fiscal_year": "_prior_year", "period_end": "_prior_end"}),
            on=["krs", "_prior_year"],
            how="inner",
        )
        .select(
            "krs",
            pl.col("_prior_end").alias("period_end"),
            *LINE,
            pl.col("source_document_hash").alias("doc_b"),
            pl.col("source_member").alias("member_b"),
            pl.col("known_from").alias("known_b"),
            pl.col("log_value").alias("log_b"),
        )
    )
    pairs = (
        a.join(b, on=["krs", "period_end", *LINE], how="inner")
        .filter(
            ((pl.col("doc_a") != pl.col("doc_b")) | (pl.col("member_a") != pl.col("member_b")))
            & (pl.col("known_b") >= pl.col("known_a"))
        )
        .with_columns((pl.col("log_a") - pl.col("log_b")).alias("_lr"))
        .group_by("krs", "period_end", "doc_a", "member_a", "doc_b", "member_b")
        .agg(
            pl.len().alias("shared_lines"),
            _near_thousand(pl.col("_lr"), check).mean().alias("share_scaled"),
            pl.col("_lr").median().alias("log_ratio"),
        )
    )
    return pairs.filter(
        (pl.col("shared_lines") >= check.min_shared_lines)
        & (pl.col("share_scaled") >= check.min_share_scaled)
    ).sort("krs", "period_end", "doc_a", "member_a", "doc_b", "member_b")


def suspects(facts: pl.DataFrame, check: UnitCheck) -> pl.DataFrame:
    """The files `scaled_pairs` points at (`SUSPECTS_SCHEMA`), one row each, sorted: the side
    whose share capital sits about 1,000 from the entity's other filings', else both sides."""
    pairs = scaled_pairs(facts, check)
    files = facts.select(*FILE, "krs", "document_ref", "fiscal_year").unique(FILE)
    capital = {
        (str(h), str(m)): float(v)
        for h, m, v in _logged(facts)
        .filter((pl.col("line_item") == SHARE_CAPITAL) & (pl.col("column") == "current_year"))
        .filter(pl.col("value") > 0)
        .select(*FILE, "log_value")
        .iter_rows()
    }
    entity_files: dict[str, list[tuple[str, str]]] = {}
    for h, m, k in files.select(*FILE, "krs").iter_rows():
        entity_files.setdefault(str(k), []).append((str(h), str(m)))
    info = {(str(r[0]), str(r[1])): r for r in files.iter_rows()}
    found: dict[tuple[str, str], str] = {}
    for row in pairs.iter_rows(named=True):
        sides = [(row["doc_a"], row["member_a"]), (row["doc_b"], row["member_b"])]
        others = [capital[f] for f in entity_files[row["krs"]] if f not in sides and f in capital]
        median = sorted(others)[len(others) // 2] if others else None
        off = [
            side
            for side in sides
            if median is not None
            and side in capital
            and abs(abs(capital[side] - median) - 3) <= check.tolerance_log10
        ]
        flagged = off if len(off) == 1 else sides
        years = "/".join(str(info[s][4]) for s in sides)
        for side in flagged:
            other = sides[1] if side == sides[0] else sides[0]
            found.setdefault(
                side,
                f"period {row['period_end']} reported twice (FY {years}), "
                f"{row['share_scaled']:.0%} of {row['shared_lines']} lines about 1,000 apart; "
                f"against {info[other][3]}"
                + ("" if len(off) == 1 else "; share capital cannot tell which"),
            )
    rows = [
        {
            "source_document_hash": h,
            "source_member": m,
            "krs": info[(h, m)][2],
            "document_ref": info[(h, m)][3],
            "detail": detail,
        }
        for (h, m), detail in sorted(found.items())
    ]
    return pl.DataFrame(rows, schema=SUSPECTS_SCHEMA)
