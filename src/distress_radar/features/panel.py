"""The point-in-time financial panel (plan 0010 step C; AGENT_SPEC §4.1, §4.7).

For each entity and statement period, which figures an observer had, and from when. The panel is a
list of **versions**: a period's figures change only when a new filing changes which statement
speaks for it, and each version starts at that filing's `known_from`. The ASOF join in step D then
takes, for an `as_of_date`, the latest version known by then, and needs no rule of its own.

Which statement speaks for a period (plan 0010 owner decision 2):
- **the statement filed for the period wins.** A later filing for the same period (a correction,
  or a second original) replaces it from its own `known_from`;
- **a later filing's prior-year column only fills a period with no usable statement.** It never
  replaces a filed one, even when it restates it: restatements are features of their own. It is
  dated by the later filing, and replaced by a filed statement once one is known;
- between two prior-year columns, the later one wins.

A fill reads the `prior_year` column as the later filing published it. The `prior_year_restated`
column is never read here: a restatement is what `restatement_events` records, and the filing
behaviour features count it.

A statement file is **usable** unless it is graded `quarantined` (owner decision 3). The feature
set's `include_quarantined_statements` switch makes quarantined files usable too; their grade stays
on every row they supply. Files left out are returned in `excluded`, with a reason, never dropped
silently (invariant 4).

**Periods, not fiscal years.** An entity can split a year into two statements (a liquidation
opening closes one period and starts the next, both in one calendar year), so the panel is keyed
by `period_end`. A prior-year column describes the period that ended the day before its own
statement began. A prior-year column whose total assets are missing or zero describes no period of
the entity's life (the one before its first year) and fills nothing.

An input the source statement does not carry has a null value in that version, so an older
version's figure never shows through a newer one (invariant 4: null, never imputed).
"""

# polars's signatures reference types pyright cannot resolve; scoped to this module.
# pyright: reportUnknownMemberType=false

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from typing import Literal

import polars as pl

from distress_radar.features.config import LineItems
from distress_radar.parsing.mapping_engine import VALUE_DTYPE

SourceKind = Literal["filed", "correction", "comparative"]

# The input that tells whether a prior-year column describes a period the entity lived through.
TOTAL_ASSETS = "total_assets"

FILINGS_SCHEMA: dict[str, pl.DataType | type[pl.DataType]] = {
    "krs": pl.String,
    "document_ref": pl.String,
    "is_correction": pl.Boolean,
}

PANEL_SCHEMA: dict[str, pl.DataType | type[pl.DataType]] = {
    "krs": pl.String,
    "period_end": pl.Date,
    "period_start": pl.Date,  # null for a period known only from a prior-year column
    "fiscal_year": pl.Int32,
    "input": pl.String,
    "value": VALUE_DTYPE,
    "line_item": pl.String,  # the chart code that carried the value; null when none did
    "known_from": pl.Date,
    "source_kind": pl.String,
    "document_ref": pl.String,
    "source_member": pl.String,
    "source_document_hash": pl.String,
    "quality_grade": pl.String,
}
PANEL_SORT_KEY = ["krs", "period_end", "known_from", "input"]

EXCLUDED_SCHEMA: dict[str, pl.DataType | type[pl.DataType]] = {
    "krs": pl.String,
    "document_ref": pl.String,
    "source_member": pl.String,
    "role": pl.String,  # filed | comparative: which use of the file was refused
    "period_end": pl.Date,
    "known_from": pl.Date,
    "reason": pl.String,
}
EXCLUDED_SORT_KEY = ["krs", "period_end", "known_from", "document_ref", "role"]


class PanelError(Exception):
    """The input breaks an assumption the loaders prove; a bug upstream, not bad data."""


@dataclass(frozen=True)
class _Statement:
    krs: str
    document_ref: str
    source_member: str
    source_document_hash: str
    fiscal_year: int
    period_start: date
    period_end: date
    known_from: date
    quality_grade: str
    is_correction: bool


@dataclass(frozen=True)
class _Candidate:
    statement: _Statement
    kind: SourceKind

    @property
    def column(self) -> str:
        return "prior_year" if self.kind == "comparative" else "current_year"

    @property
    def period_end(self) -> date:
        s = self.statement
        return s.period_start - timedelta(days=1) if self.kind == "comparative" else s.period_end

    @property
    def rank(self) -> int:
        """On one day: filed before corrected, statements before prior-year columns."""
        s = self.statement
        if self.kind == "comparative":
            return 3 if s.is_correction else 2
        return 1 if s.is_correction else 0


@dataclass(frozen=True)
class Panel:
    frame: pl.DataFrame  # PANEL_SCHEMA
    excluded: pl.DataFrame  # EXCLUDED_SCHEMA


def _statements(canonical: pl.DataFrame, filings: pl.DataFrame) -> list[_Statement]:
    files = (
        canonical.select(
            "krs",
            "document_ref",
            "source_member",
            "source_document_hash",
            "fiscal_year",
            "period_start",
            "period_end",
            "known_from",
            "quality_grade",
        )
        .unique()
        .join(
            filings.select(FILINGS_SCHEMA.keys()).with_columns(pl.lit(True).alias("_indexed")),
            on=["krs", "document_ref"],
            how="left",
        )
    )
    if files.select(["krs", "document_ref", "source_member"]).is_duplicated().any():
        raise PanelError("a statement file carries more than one period, date or grade")
    unknown = files.filter(pl.col("_indexed").is_null()).get_column("document_ref").to_list()
    if unknown:
        raise PanelError(f"statement files with no filing_index row: {sorted(unknown)}")
    return [
        _Statement(
            krs=row["krs"],
            document_ref=row["document_ref"],
            source_member=row["source_member"],
            source_document_hash=row["source_document_hash"],
            fiscal_year=row["fiscal_year"],
            period_start=row["period_start"],
            period_end=row["period_end"],
            known_from=row["known_from"],
            quality_grade=row["quality_grade"],
            # Null only on a row never expanded, and a parsed statement's row always is.
            is_correction=bool(row["is_correction"]),
        )
        for row in files.sort(["krs", "document_ref", "source_member"]).iter_rows(named=True)
    ]


def _values(
    canonical: pl.DataFrame, line_items: LineItems
) -> dict[tuple[str, str, str], dict[str, tuple[str, Decimal]]]:
    """(document_ref, source_member, column) → input → (chart code, value)."""
    code_input = {code: name for name, codes in line_items.inputs.items() for code in codes}
    rows = canonical.filter(
        pl.col("line_item").is_in(list(code_input))
        & pl.col("column").is_in(["current_year", "prior_year"])
    ).select("document_ref", "source_member", "column", "line_item", "value")
    out: dict[tuple[str, str, str], dict[str, tuple[str, Decimal]]] = {}
    for row in rows.sort(["document_ref", "source_member", "column", "line_item"]).iter_rows(
        named=True
    ):
        key = (row["document_ref"], row["source_member"], row["column"])
        name = code_input[row["line_item"]]
        found = out.setdefault(key, {})
        if name in found:
            raise PanelError(
                f"{key}: {name} carried by both {found[name][0]} and {row['line_item']}"
            )
        found[name] = (row["line_item"], row["value"])
    return out


def build_panel(
    canonical: pl.DataFrame,
    filings: pl.DataFrame,
    line_items: LineItems,
    *,
    include_quarantined: bool,
) -> Panel:
    """The versioned panel from `financial_statements_canonical` and `filing_index`.

    `filings` needs `FILINGS_SCHEMA`'s columns for every statement file in `canonical`.
    """
    values = _values(canonical, line_items)
    excluded: list[dict[str, object]] = []

    def exclude(c: _Candidate, reason: str) -> None:
        s = c.statement
        excluded.append(
            {
                "krs": s.krs,
                "document_ref": s.document_ref,
                "source_member": s.source_member,
                "role": "comparative" if c.kind == "comparative" else "filed",
                "period_end": c.period_end,
                "known_from": s.known_from,
                "reason": reason,
            }
        )

    by_period: dict[tuple[str, date], list[_Candidate]] = {}
    for s in _statements(canonical, filings):
        own = _Candidate(s, "correction" if s.is_correction else "filed")
        prior = _Candidate(s, "comparative")
        if s.quality_grade == "quarantined" and not include_quarantined:
            exclude(own, "quarantined")
            exclude(prior, "quarantined")
            continue
        by_period.setdefault((s.krs, own.period_end), []).append(own)
        assets = values.get((s.document_ref, s.source_member, "prior_year"), {}).get(TOTAL_ASSETS)
        if assets is None or assets[1] == 0:
            exclude(prior, "comparative_without_assets")
        else:
            by_period.setdefault((s.krs, prior.period_end), []).append(prior)

    rows: list[dict[str, object]] = []
    for (krs, period_end), candidates in sorted(by_period.items()):
        # Two sources of one rank for one period on one day: nothing says which one speaks.
        same_day: dict[tuple[date, int], list[_Candidate]] = {}
        for c in candidates:
            same_day.setdefault((c.statement.known_from, c.rank), []).append(c)
        clashing = {id(c) for group in same_day.values() if len(group) > 1 for c in group}
        for c in candidates:
            if id(c) in clashing:
                exclude(c, "same_day_filings")

        current: _Candidate | None = None
        versions: dict[date, _Candidate] = {}  # the source in force at the end of each day
        for c in sorted(
            (c for c in candidates if id(c) not in clashing),
            key=lambda c: (c.statement.known_from, c.rank),
        ):
            if c.kind == "comparative" and current is not None and current.kind != "comparative":
                continue  # the filed statement wins its period
            current = versions[c.statement.known_from] = c

        for c in versions.values():
            s = c.statement
            carried = values.get((s.document_ref, s.source_member, c.column), {})
            for name in sorted(line_items.inputs):
                code, value = carried.get(name, (None, None))
                rows.append(
                    {
                        "krs": krs,
                        "period_end": period_end,
                        "period_start": None if c.kind == "comparative" else s.period_start,
                        "fiscal_year": period_end.year,
                        "input": name,
                        "value": value,
                        "line_item": code,
                        "known_from": s.known_from,
                        "source_kind": c.kind,
                        "document_ref": s.document_ref,
                        "source_member": s.source_member,
                        "source_document_hash": s.source_document_hash,
                        "quality_grade": s.quality_grade,
                    }
                )

    frame = pl.DataFrame(rows, schema=PANEL_SCHEMA, orient="row").sort(PANEL_SORT_KEY)
    # Two versions of one period on one day would leave the ASOF join nothing to choose by.
    if frame.select(["krs", "period_end", "known_from", "input"]).is_duplicated().any():
        raise PanelError("two versions of one period share a known_from")
    return Panel(
        frame=frame,
        excluded=pl.DataFrame(excluded, schema=EXCLUDED_SCHEMA, orient="row").sort(
            EXCLUDED_SORT_KEY
        ),
    )
