"""The `auditor_reports` dataset (plan 0013 decision 0c; AGENT_SPEC §5): one row per auditor report
the text stage read, with what the features need from it as a whole. No I/O: the `text_signals`
asset writes it beside `text_signals` and `text_coverage`.

- **The opinion**: the first page of the report on which the opinion rule (`opinion_type`, read by
  `rules_v2`'s headings or later) states one, with that page's confidence. A report with no such
  page has no opinion, which is not an unqualified one: `opinion` and `modified_opinion` are null.
- **The auditor change**: whether the report's audit firm differs from the firm on the entity's
  latest earlier report, of an earlier period, known when this one was filed (its `known_from`),
  so the comparison uses nothing filed later. The firm is told by its number on the list of audit
  firms (`rules.audit_firm_number`), read from the masked pages and never stored: a firm can be a
  sole practitioner (owner, 2026-10-05). Only `firm_stated` and the change are kept. The change is
  null when either report states no single firm, or there is no earlier report.

Dated like the report: `known_from` is its own submission date (decision 6, amended).
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date
from typing import Any

import polars as pl

from distress_radar.extraction import rules
from distress_radar.extraction.text_signals import StatementNotes

DATASET = "auditor_reports"
MODIFIED = frozenset({"qualified", "adverse", "disclaimer"})

COLUMNS: dict[str, pl.DataType | type[pl.DataType]] = {
    "krs": pl.String,
    "fiscal_year": pl.Int32,
    "period_end": pl.Date,
    "known_from": pl.Date,
    "document_ref": pl.String,
    "source_document_hash": pl.String,
    "source_member": pl.String,
    "pages_text": pl.Int32,
    "pages_needs_ocr": pl.Int32,
    "opinion": pl.String,  # unqualified | qualified | adverse | disclaimer; null: none stated
    "opinion_page": pl.Int32,
    "opinion_confidence": pl.String,
    "modified_opinion": pl.Boolean,  # null exactly when `opinion` is
    "firm_stated": pl.Boolean,  # the report states exactly one audit-firm number
    "auditor_changed": pl.Boolean,
    "compared_with": pl.String,  # the earlier report's document_ref; null when there is none
    "extractor_version": pl.String,
    "ingestion_run_id": pl.String,
}
SORT_KEY = ["krs", "period_end", "known_from", "document_ref", "source_member"]


def empty() -> pl.DataFrame:
    return pl.DataFrame(schema=COLUMNS)


@dataclass(frozen=True)
class _Report:
    notes: StatementNotes
    firm: str | None  # in memory only

    @property
    def order(self) -> tuple[date, date, str, str]:
        st = self.notes.statement
        return (st.period_end, st.known_from, st.document_ref, st.source_member)


def _previous(report: _Report, others: Iterable[_Report]) -> _Report | None:
    """The entity's latest report of an earlier period known when `report` was filed."""
    st = report.notes.statement
    earlier = [
        o
        for o in others
        if o.notes.statement.krs == st.krs
        and o.notes.statement.period_end < st.period_end
        and o.notes.statement.known_from <= st.known_from
    ]
    return max(earlier, key=lambda o: o.order, default=None)


def build(
    reports: Iterable[StatementNotes],
    signals: pl.DataFrame,
    firm_rules: rules.Rules,
    extractor_version: str,
) -> pl.DataFrame:
    """One row per auditor report, sorted. `signals` is the run's `text_signals` frame."""
    read = [
        _Report(n, rules.audit_firm_number((p.text for p in n.pages), firm_rules))
        for n in reports
        if n.statement.document_kind == "auditor_report"
    ]
    stated = (
        signals.filter(
            (pl.col("document_kind") == "auditor_report")
            & (pl.col("signal_type") == "opinion_type")
            & (pl.col("value") != "absent")
        )
        .sort("page")
        .unique(["source_document_hash", "source_member"], keep="first", maintain_order=True)
    )
    opinions: dict[tuple[str, str], tuple[str, int, str]] = {
        (r["source_document_hash"], r["source_member"]): (r["value"], r["page"], r["confidence"])
        for r in stated.iter_rows(named=True)
    }
    rows: list[dict[str, Any]] = []
    for report in read:
        st = report.notes.statement
        opinion, page, confidence = opinions.get(
            (st.source_document_hash, st.source_member), (None, None, None)
        )
        previous = _previous(report, read)
        changed = (
            None
            if previous is None or report.firm is None or previous.firm is None
            else report.firm != previous.firm
        )
        rows.append(
            {
                "krs": st.krs,
                "fiscal_year": st.period_end.year,
                "period_end": st.period_end,
                "known_from": st.known_from,
                "document_ref": st.document_ref,
                "source_document_hash": st.source_document_hash,
                "source_member": st.source_member,
                "pages_text": report.notes.page_status["text"],
                "pages_needs_ocr": report.notes.page_status["needs_ocr"],
                "opinion": opinion,
                "opinion_page": page,
                "opinion_confidence": confidence,
                "modified_opinion": None if opinion is None else opinion in MODIFIED,
                "firm_stated": report.firm is not None,
                "auditor_changed": changed,
                "compared_with": None
                if previous is None
                else previous.notes.statement.document_ref,
                "extractor_version": extractor_version,
                "ingestion_run_id": st.ingestion_run_id,
            }
        )
    if not rows:
        return empty()
    return pl.DataFrame(rows, schema=COLUMNS).select(list(COLUMNS)).sort(SORT_KEY)
