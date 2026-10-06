"""`text_signals` and `text_coverage` (plan 0013 step H), from synthetic statements with synthetic
PDF notes. No API call: model signals run against a fake transport or not at all. All text is
invented."""

# PyMuPDF's signatures reference types pyright cannot resolve.
# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false
# pyright: reportUnknownArgumentType=false

from __future__ import annotations

import base64
import json
from collections.abc import Mapping
from dataclasses import replace
from datetime import date
from typing import Any

import polars as pl
import pymupdf
import pytest

from distress_radar.acquisition.raw_store import InMemoryObjectStore
from distress_radar.extraction import extractor as ex
from distress_radar.extraction import masking, preprocessing
from distress_radar.extraction import text_signals as ts
from distress_radar.extraction.contracts import TEXT_COVERAGE, TEXT_SIGNALS
from distress_radar.extraction.response_store import ResponseStore

# Without diacritics: the PDF's built-in font has no Polish glyphs, and the terms these sentences
# need (`pozew`; `opinia` + `negatywny`) lemmatise without them.
LITIGATION = "Bank wniosl pozew przeciwko Spolce o zaplate 120 tys. zl."
OPINION = "Biegly rewident wydal opinie negatywna."
FILLER = (
    " Zapasy materialow wyceniono w cenach nabycia, a naleznosci w kwocie wymaganej zaplaty." * 4
)


def _pdf(*pages: str | None) -> bytes:
    """One page per entry: its text, or None for a scanned page (an image, no text layer)."""
    doc = pymupdf.open()
    for text in pages:
        page = doc.new_page()
        if text is None:
            pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 20, 20), False)
            pix.set_rect(pix.irect, (200, 200, 200))
            page.insert_image(pymupdf.Rect(50, 50, 250, 250), pixmap=pix)
        else:
            page.insert_textbox(pymupdf.Rect(50, 50, 550, 800), text, fontname="helv")
    data = doc.tobytes()
    doc.close()
    return data


def _statement(*attachments: bytes) -> bytes:
    parts = "".join(
        f"<Plik><Nazwa>plik-{i}.pdf</Nazwa><Zawartosc>{base64.b64encode(a).decode()}</Zawartosc></Plik>"
        for i, a in enumerate(attachments, start=1)
    )
    return f"<Sprawozdanie><Informacja>{parts}</Informacja></Sprawozdanie>".encode()


def _file(n: int) -> ts.StatementFile:
    return ts.StatementFile(
        krs=f"{n:010d}",
        document_ref=f"ref{n}",
        period_end=date(2023, 12, 31),
        known_from=date(2024, 6, 30),
        source_document_hash=f"{n:x}" * 64 if n < 16 else "a" * 64,
        source_member=f"zip:{n}.xml",
        ingestion_run_id="run-1",
    )


class FakeTransport:
    def __init__(self, answer: dict[str, Any]) -> None:
        self.body = json.dumps(
            {
                "content": [{"type": "text", "text": json.dumps(answer, ensure_ascii=False)}],
                "stop_reason": "end_turn",
            },
            ensure_ascii=False,
        ).encode()
        self.calls = 0

    def run(self, requests: Mapping[str, dict[str, Any]]) -> tuple[dict[str, bytes], set[str]]:
        self.calls += len(requests)
        return {k: self.body for k in requests}, set()


@pytest.fixture(scope="module")
def models() -> tuple[Any, Any]:
    return masking.load_model(), preprocessing.load_model()


@pytest.fixture(scope="module")
def extractor() -> ex.Extractor:
    return ex.load_extractor("extractor_v1")


@pytest.fixture(scope="module")
def notes(models: tuple[Any, Any]) -> list[ts.StatementNotes]:
    mask_nlp, lemma_nlp = models
    prefilter = preprocessing.load_prefilter("prefilter_v1")
    statements = [
        # 1: a text page with a lawsuit and an auditor's opinion, and a scanned page
        (_file(1), _statement(_pdf(f"{LITIGATION} {OPINION}{FILLER}", None))),
        # 2: figures only, on text pages
        (_file(2), _statement(_pdf(FILLER.strip()))),
        # 3: scans only, and an attachment that is not a PDF at all
        (_file(3), _statement(_pdf(None), b"PK\x03\x04 not a pdf")),
        # 4: an encrypted-looking broken PDF beside a readable one
        (_file(4), _statement(b"%PDF-1.4 broken", _pdf(FILLER.strip()))),
    ]
    return [ts.read_notes(f, x, prefilter, mask_nlp, lemma_nlp) for f, x in statements]


def _build(
    notes: list[ts.StatementNotes],
    extractor: ex.Extractor,
    runnable: set[preprocessing.SignalType],
    transport: ex.Transport,
) -> tuple[pl.DataFrame, pl.DataFrame, list[ts.Detection]]:
    return ts.build(
        notes, extractor, runnable, ResponseStore(InMemoryObjectStore()), transport, ts.TextStats()
    )


def _coverage(frame: pl.DataFrame, n: int, signal: str) -> dict[str, Any]:
    [row] = frame.filter(
        (pl.col("krs") == f"{n:010d}") & (pl.col("signal_type") == signal)
    ).to_dicts()
    return row


def test_before_the_owner_confirms_only_rules_run_and_coverage_says_so(
    notes: list[ts.StatementNotes], extractor: ex.Extractor
) -> None:
    signals, coverage, detections = _build(notes, extractor, {"opinion_type"}, ex.NoCalls())
    TEXT_SIGNALS.validate(signals)
    TEXT_COVERAGE.validate(coverage)
    [row] = signals.to_dicts()
    assert (row["signal_type"], row["value"], row["extraction_method"]) == (
        "opinion_type",
        "adverse",
        "rule",
    )
    assert OPINION in row["evidence_span"] and row["response_key"] is None
    assert (row["known_from"], row["fiscal_year"], row["page"]) == (date(2024, 6, 30), 2023, 1)
    assert row["source_element_path"].endswith("/Plik") and row["ingestion_run_id"] == "run-1"
    assert row["document_kind"] == "statement_notes"
    assert coverage.height == 4 * len(preprocessing.SIGNAL_TYPES)
    # statement 1: the lawsuit was selected but not read; the opinion was read, a page was scanned
    assert _coverage(coverage, 1, "litigation")["status"] == "not_run"
    assert _coverage(coverage, 1, "opinion_type")["status"] == "partial"
    # statement 2: nothing selected on readable pages: read, and absent
    assert {_coverage(coverage, 2, s)["status"] for s in preprocessing.SIGNAL_TYPES} == {"read"}
    # statement 3: scans and an office file only
    three = _coverage(coverage, 3, "litigation")
    assert (three["status"], three["pages_needs_ocr"], three["attachments_unsupported"]) == (
        "no_text",
        1,
        1,
    )
    # statement 4: one attachment unreadable, quarantined G1
    four = _coverage(coverage, 4, "litigation")
    assert (four["status"], four["attachment_errors"]) == ("partial", ["pdf_unreadable"])
    assert [(d.stage, d.reason_code, d.statement.krs) for d in detections] == [
        ("G1", "pdf_unreadable", "0000000004")
    ]


def test_model_signals_run_once_confirmed_and_discards_are_quarantined(
    notes: list[ts.StatementNotes], extractor: ex.Extractor
) -> None:
    runnable = set(preprocessing.SIGNAL_TYPES)
    kept, coverage, _d = _build(
        notes,
        extractor,
        runnable,
        FakeTransport({"present": True, "evidence": "Bank wniosl pozew", "confidence": "high"}),
    )
    lit = kept.filter(pl.col("signal_type") == "litigation").to_dicts()
    assert [(r["value"], r["extraction_method"]) for r in lit] == [("present", "llm")]
    assert lit[0]["response_key"] is not None
    assert _coverage(coverage, 1, "litigation")["kept_present"] == 1

    _s, coverage, detections = _build(
        notes,
        extractor,
        runnable,
        FakeTransport({"present": True, "evidence": "not on the page", "confidence": "high"}),
    )
    row = _coverage(coverage, 1, "litigation")
    assert (row["status"], row["discarded"], row["discard_reasons"]) == (
        "partial",
        1,
        ["evidence_not_on_page"],
    )
    assert ("G2", "evidence_not_on_page", "litigation") in {
        (d.stage, d.reason_code, d.signal_type) for d in detections
    }


def test_a_dated_valued_signal_gets_its_statement_s_date_and_keeps_its_kind() -> None:
    """Decision 9: `post_balance_sheet_event` under extractor_v4."""
    event = "Po dniu bilansowym Spolka zlozyla wniosek o otwarcie postepowania sanacyjnego."
    page = ts.NotesPage("p5", 1, "Informacja/Plik", 1, event, [], ("post_balance_sheet_event",))
    notes = ts.StatementNotes(statement=_file(5), pages=[page], attachments=1)
    notes.page_status["text"] = 1
    seen: list[dict[str, Any]] = []

    class Capture(FakeTransport):
        def run(self, requests: Mapping[str, dict[str, Any]]) -> tuple[dict[str, bytes], set[str]]:
            seen.extend(requests.values())
            return super().run(requests)

    answer = {"present": True, "evidence": event, "confidence": "high", "value": "adverse"}
    kept, _c, _d = _build(
        [notes],
        ex.load_extractor("extractor_v4"),
        {"post_balance_sheet_event"},
        Capture(answer),
    )
    [request] = seen
    assert request["messages"][0]["content"].startswith(
        "<balance_sheet_date>2023-12-31</balance_sheet_date>"
    )
    assert kept["value"].to_list() == ["adverse"]
    TEXT_SIGNALS.validate(kept)


def _report(n: int) -> ts.StatementFile:
    return replace(
        _file(n),
        document_ref=f"report{n}",
        source_member=f"zip:{n}.pdf",
        known_from=date(2024, 7, 15),
        document_kind="auditor_report",
    )


def test_an_auditor_report_is_read_as_its_own_document(
    models: tuple[Any, Any], extractor: ex.Extractor
) -> None:
    mask_nlp, lemma_nlp = models
    prefilter = preprocessing.load_prefilter("prefilter_v1")
    reports = [
        ts.read_report(_report(5), _pdf(f"{OPINION}{FILLER}"), prefilter, mask_nlp, lemma_nlp),
        ts.read_report(_report(6), _pdf(None, None), prefilter, mask_nlp, lemma_nlp),
    ]
    signals, coverage, detections = _build(reports, extractor, {"opinion_type"}, ex.NoCalls())
    TEXT_SIGNALS.validate(signals)
    TEXT_COVERAGE.validate(coverage)
    [row] = signals.to_dicts()
    assert (row["document_kind"], row["value"], row["document_ref"]) == (
        "auditor_report",
        "adverse",
        "report5",
    )
    # dated by the report's own filing, and the page is the report's own: no element path
    assert (row["known_from"], row["attachment"], row["page"]) == (date(2024, 7, 15), 1, 1)
    assert row["source_element_path"] == ""
    assert set(coverage.get_column("document_kind").to_list()) == {"auditor_report"}
    assert _coverage(coverage, 5, "opinion_type")["status"] == "read"
    # a scanned report is no text, never "no warning"
    six = _coverage(coverage, 6, "opinion_type")
    assert (six["status"], six["pages_needs_ocr"]) == ("no_text", 2)
    assert detections == []


def test_an_unreadable_report_is_quarantined(
    models: tuple[Any, Any], extractor: ex.Extractor
) -> None:
    mask_nlp, lemma_nlp = models
    prefilter = preprocessing.load_prefilter("prefilter_v1")
    report = ts.read_report(_report(7), b"%PDF-1.4 broken", prefilter, mask_nlp, lemma_nlp)
    _s, coverage, detections = _build([report], extractor, {"opinion_type"}, ex.NoCalls())
    assert _coverage(coverage, 7, "opinion_type")["status"] == "no_text"
    assert [(d.stage, d.reason_code, d.detail) for d in detections] == [
        ("G1", "pdf_unreadable", "the document")
    ]


def test_only_an_auditor_report_is_read_as_one(models: tuple[Any, Any]) -> None:
    prefilter = preprocessing.load_prefilter("prefilter_v1")
    with pytest.raises(ValueError, match="not an auditor report"):
        ts.read_report(_file(8), _pdf(FILLER.strip()), prefilter, *models)


def test_the_same_input_gives_the_same_frames(
    notes: list[ts.StatementNotes], extractor: ex.Extractor
) -> None:
    first = _build(notes, extractor, {"opinion_type"}, ex.NoCalls())
    second = _build(notes, extractor, {"opinion_type"}, ex.NoCalls())
    assert first[0].equals(second[0]) and first[1].equals(second[1])


def test_the_pipeline_hash_changes_with_what_runs(extractor: ex.Extractor) -> None:
    rules_only = ts.pipeline_hash(extractor, {"opinion_type"})
    assert rules_only == ts.pipeline_hash(extractor, {"opinion_type"})
    assert rules_only != ts.pipeline_hash(extractor, set(preprocessing.SIGNAL_TYPES))


def test_the_masking_check_finds_a_name_in_evidence(models: tuple[Any, Any]) -> None:
    frame = pl.DataFrame(
        {
            "evidence_span": [
                "Umowę podpisał Jan Kowalski, PESEL 90010112345.",
                "[osoba] złożył pozew.",
                None,
            ]
        },
        schema={"evidence_span": pl.String},
    )
    found = ts.masking_findings(frame, models[0])
    assert found.get("pesel") == 1 and found.get("person", 0) >= 1
