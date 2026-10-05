"""`auditor_reports` (plan 0013 decision 0c): each report's opinion, and whether its audit firm
changed, compared only with what was known at its filing. No NLP: pages are built directly, and
every report, firm number and name below is invented."""

from __future__ import annotations

import hashlib
from collections import Counter
from datetime import date

import polars as pl

from distress_radar.extraction import auditor_reports as ar
from distress_radar.extraction import rules
from distress_radar.extraction import text_signals as ts
from distress_radar.extraction.contracts import AUDITOR_REPORTS

RULES = rules.load_rules("rules_v3")
KRS = "0000000001"


def _hash(ref: str) -> str:
    return hashlib.sha256(ref.encode()).hexdigest()


def _firm(number: int) -> str:
    return (
        f"Firma audytorska wpisana na listę firm audytorskich pod numerem {number}. "
        "Kluczowy biegły rewident: [osoba], nr w rejestrze 10001."
    )


def _report(ref: str, period: int, filed: date, *pages: str, scanned: int = 0) -> ts.StatementNotes:
    statement = ts.StatementFile(
        krs=KRS,
        document_ref=ref,
        period_end=date(period, 12, 31),
        known_from=filed,
        source_document_hash=_hash(ref),
        source_member=f"zip:{ref}.pdf",
        ingestion_run_id="run-1",
        document_kind="auditor_report",
    )
    notes = ts.StatementNotes(statement)
    for n, text in enumerate(pages, start=1):
        notes.pages.append(ts.NotesPage(f"{ref}/{n}", 1, "", n, text, [], ()))
    notes.page_status.update({"text": len(pages), "needs_ocr": scanned})
    return notes


def _opinions(*rows: tuple[str, int, str]) -> pl.DataFrame:
    """`text_signals` rows of `opinion_type`: (report ref, page, value)."""
    return pl.DataFrame(
        [
            {
                "document_kind": "auditor_report",
                "signal_type": "opinion_type",
                "source_document_hash": _hash(ref),
                "source_member": f"zip:{ref}.pdf",
                "page": page,
                "value": value,
                "confidence": "high",
            }
            for ref, page, value in rows
        ],
        schema={
            "document_kind": pl.String,
            "signal_type": pl.String,
            "source_document_hash": pl.String,
            "source_member": pl.String,
            "page": pl.Int32,
            "value": pl.String,
            "confidence": pl.String,
        },
    )


def _build(reports: list[ts.StatementNotes], signals: pl.DataFrame) -> dict[str, dict[str, object]]:
    frame = AUDITOR_REPORTS.validate(ar.build(reports, signals, RULES, "extractor_v3"))
    return {str(r["document_ref"]): r for r in frame.iter_rows(named=True)}


def test_the_opinion_is_the_first_page_that_states_one() -> None:
    rows = _build(
        [
            _report("r20", 2020, date(2021, 6, 30), "Spis treści", "Opinia z zastrzeżeniem"),
            _report("r21", 2021, date(2022, 6, 30), "Opinia"),
            _report("r22", 2022, date(2023, 6, 30), scanned=4),
        ],
        _opinions(("r20", 3, "unqualified"), ("r20", 2, "qualified"), ("r20", 1, "absent")),
    )
    assert (rows["r20"]["opinion"], rows["r20"]["opinion_page"]) == ("qualified", 2)
    assert rows["r20"]["modified_opinion"] is True
    # a report on which no opinion was found has none: null, never unqualified
    assert (rows["r21"]["opinion"], rows["r21"]["modified_opinion"]) == (None, None)
    assert (rows["r22"]["pages_text"], rows["r22"]["pages_needs_ocr"]) == (0, 4)


def test_the_firm_is_compared_with_the_latest_earlier_report_known_at_filing() -> None:
    rows = _build(
        [
            _report("r19", 2019, date(2020, 6, 30), _firm(11)),
            # filed late: after r21, so r21 cannot have been compared with it
            _report("r20", 2020, date(2022, 9, 1), _firm(22)),
            _report("r21", 2021, date(2022, 6, 30), _firm(11)),
            # a correction of 2021, filed later: compared with r20, now known, not with r21
            _report("r21c", 2021, date(2022, 10, 1), _firm(33)),
            _report("r22", 2022, date(2023, 6, 30), "Firma audytorska bez numeru."),
            _report("r23", 2023, date(2024, 6, 30), _firm(22) + " " + _firm(44)),
        ],
        _opinions(),
    )
    assert (rows["r19"]["auditor_changed"], rows["r19"]["compared_with"]) == (None, None)
    assert (rows["r21"]["auditor_changed"], rows["r21"]["compared_with"]) == (False, "r19")
    assert (rows["r20"]["auditor_changed"], rows["r20"]["compared_with"]) == (True, "r19")
    assert (rows["r21c"]["auditor_changed"], rows["r21c"]["compared_with"]) == (True, "r20")
    # no single firm stated, on this report or the one compared with: unknown, never false
    assert (rows["r22"]["firm_stated"], rows["r22"]["auditor_changed"]) == (False, None)
    assert (rows["r23"]["firm_stated"], rows["r23"]["auditor_changed"]) == (False, None)


def test_no_firm_number_reaches_the_dataset() -> None:
    frame = ar.build(
        [_report("r19", 2019, date(2020, 6, 30), _firm(4321))], _opinions(), RULES, "v"
    )
    cells = Counter(str(v) for row in frame.iter_rows() for v in row)
    assert not any("4321" in c or "10001" in c for c in cells)


def test_the_key_auditor_s_number_is_never_read_as_the_firm_s() -> None:
    assert rules.audit_firm_number([_firm(77)], RULES) == "77"
    only_the_auditor = (
        "Kluczowy biegły rewident, wpisany na listę firm audytorskich: [osoba], nr 10001."
    )
    assert rules.audit_firm_number([only_the_auditor], RULES) is None
    dated = "Lista firm audytorskich, ustawa z dnia 11 maja 2017 r., numer 55."
    assert rules.audit_firm_number([dated], RULES) is None
    assert rules.audit_firm_number([_firm(1), _firm(2)], RULES) is None


def test_only_auditor_reports_are_rows() -> None:
    notes = _report("n20", 2020, date(2021, 6, 30), _firm(11))
    statement = ts.StatementFile(
        **{**notes.statement.__dict__, "document_kind": "statement_notes"}  # type: ignore[arg-type]
    )
    assert ar.build([ts.StatementNotes(statement)], _opinions(), RULES, "v").is_empty()
