"""The `text_signals` and `text_coverage` datasets (plan 0013 step H; AGENT_SPEC §5, §6G). No I/O:
the `text_signals` asset (`dagster_defs/assets/extraction.py`) reads the stores and writes the
Parquet.

Two kinds of document are read (`document_kind`): the notes embedded in every stored statement
file (`statement_notes`), and every stored auditor report (`auditor_report`, plan 0013 decision
0c), a separately filed PDF. Each is read page by page (`extraction.page_text`), each text page
masked (`extraction.masking`, ADR 0009 third addendum), split into sentences and lemmas,
prefiltered, and every (page, signal) the prefilter selects is extracted
(`extraction.extractor`). Masked page text is not stored: it is re-derived from the raw bytes,
deterministically, on every run, and only the evidence spans a signal quotes are kept. Below, a
"statement file" is either: the file a document was read from.

- **`text_signals`**: one row per kept extraction, present or absent, with its evidence (masked)
  and its lineage: the stored file, the statement in it, the attachment's element path and page,
  the ingestion run. Dated by the filing: `known_from` is its submission date (§4.7; for an
  auditor report its own, decision 6), `fiscal_year` its period end's year.
- **`text_coverage`**: one row per (statement file, signal_type), saying what was read, so a missing
  signal is never read as "no warning" (invariant 4; plan 0013 constraint 2): text pages, scanned
  pages, attachment errors, pages the prefilter selected, extractions kept and discarded, and a
  `status`:
  - `no_text`: no page with a text layer (no notes attached, or scans only);
  - `not_run`: the prefilter selected pages for a model signal before the owner confirmed the
    provider's terms (decision 2), so they were not read;
  - `partial`: read, but some of the notes were not: a scanned page, an unreadable attachment, a
    discarded or unanswered extraction;
  - `read`: every page of the notes has a text layer and every selected page was answered.
  A page the prefilter did not select for a signal is read for it, with the answer "absent".

Discarded extractions (stage G2) and unreadable attachments (stage G1) go to `quarantine_events`;
the current set is recomputed from `text_coverage` by the `quarantine` model.
"""

from __future__ import annotations

import hashlib
from collections import Counter
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Literal

import polars as pl

from distress_radar.extraction import extractor as ex
from distress_radar.extraction import masking, page_text, preprocessing
from distress_radar.extraction.preprocessing import (
    SIGNAL_TYPES,
    DocumentKind,
    Sentence,
    SignalType,
)
from distress_radar.extraction.response_store import ResponseStore
from distress_radar.extraction.schemas import SCHEMA_VERSION, Discarded, Extraction
from distress_radar.parsing.canonical_schema import CONFIG_DIR

DATASET = "text_signals"
COVERAGE = "text_coverage"
CoverageStatus = Literal["read", "partial", "not_run", "no_text"]

SIGNAL_COLUMNS: dict[str, pl.DataType | type[pl.DataType]] = {
    # AGENT_SPEC §5, in its order
    "krs": pl.String,
    "fiscal_year": pl.Int32,
    "signal_type": pl.String,
    "value": pl.String,  # "present" | "absent"; for `opinion_type` the opinion, or "absent"
    "evidence_span": pl.String,  # masked; null when absent
    "source_document_hash": pl.String,
    "page": pl.Int32,
    "extraction_method": pl.String,  # "llm" | "rule"
    "confidence": pl.String,  # "high" | "medium" | "low"
    "known_from": pl.Date,
    # lineage and versions (invariant 3)
    "period_end": pl.Date,
    "document_ref": pl.String,
    "source_member": pl.String,
    "document_kind": pl.String,  # "statement_notes" | "auditor_report"
    "source_element_path": pl.String,  # the attachment's element in the statement; "" for a report
    "attachment": pl.Int32,
    "extractor_version": pl.String,
    "masking_version": pl.String,
    "response_key": pl.String,  # the stored model response it was read from; null for a rule
    "ingestion_run_id": pl.String,
}
SIGNAL_SORT_KEY = [
    "krs",
    "period_end",
    "document_ref",
    "source_member",
    "attachment",
    "page",
    "signal_type",
]

COVERAGE_COLUMNS: dict[str, pl.DataType | type[pl.DataType]] = {
    "krs": pl.String,
    "fiscal_year": pl.Int32,
    "signal_type": pl.String,
    "status": pl.String,
    "known_from": pl.Date,
    "period_end": pl.Date,
    "document_ref": pl.String,
    "source_document_hash": pl.String,
    "source_member": pl.String,
    "document_kind": pl.String,
    "attachments": pl.Int32,  # embedded documents with content; 1 for an auditor report
    "attachments_unsupported": pl.Int32,  # not PDF: counted, not opened
    "attachment_errors": pl.List(pl.String),  # reason codes of PDFs that could not be read
    "pages": pl.Int32,
    "pages_text": pl.Int32,
    "pages_needs_ocr": pl.Int32,
    "pages_sparse": pl.Int32,
    "pages_selected": pl.Int32,  # by the prefilter, for this signal
    "kept_present": pl.Int32,
    "kept_absent": pl.Int32,
    "discarded": pl.Int32,
    "discard_reasons": pl.List(pl.String),
    "unanswered": pl.Int32,  # no response yet (api_error): read again on the next run
    "extractor_version": pl.String,
    "masking_version": pl.String,
    "ingestion_run_id": pl.String,
}
COVERAGE_SORT_KEY = ["krs", "period_end", "document_ref", "source_member", "signal_type"]


def empty_signals() -> pl.DataFrame:
    return pl.DataFrame(schema=SIGNAL_COLUMNS)


def empty_coverage() -> pl.DataFrame:
    return pl.DataFrame(schema=COVERAGE_COLUMNS)


def pipeline_hash(
    extractor: ex.Extractor,
    runnable: Iterable[SignalType],
    config_dir: Path = CONFIG_DIR,
    prompts_dir: Path = ex.PROMPTS_DIR,
) -> str:
    """SHA-256 of what shapes the rows: configs, prompts, schema, masking, which signals run.
    A change is a new manifest row, hence a new `ingestion_run_id` (invariant 5)."""
    cfg = extractor.config
    extraction = config_dir / "extraction"
    files = [
        extraction / f"{cfg.extractor_version}.yaml",
        extraction / f"{cfg.rules_version}.yaml",
        extraction / f"{cfg.prefilter_version}.yaml",
        *(prompts_dir / f"{m.prompt}.md" for m in cfg.signals.values() if m.prompt),
    ]
    digest = hashlib.sha256()
    for path in sorted(files):
        digest.update(path.name.encode() + b"\0" + path.read_bytes() + b"\0")
    parts = [
        f"schema={SCHEMA_VERSION}",
        f"masking={masking.MASKING_VERSION}",
        f"text_page_chars={page_text.TEXT_PAGE_CHARS}",
        "runnable=" + ",".join(s for s in SIGNAL_TYPES if s in set(runnable)),
    ]
    digest.update("\n".join(parts).encode())
    return digest.hexdigest()


# --- one statement's notes -----------------------------------------------------------------------


@dataclass(frozen=True)
class StatementFile:
    krs: str
    document_ref: str
    period_end: date
    known_from: date
    source_document_hash: str
    source_member: str
    ingestion_run_id: str
    document_kind: DocumentKind = "statement_notes"


@dataclass(frozen=True)
class NotesPage:
    page_id: str  # unique across the run
    attachment: int
    element_path: str
    page: int
    text: str  # masked
    sentences: list[Sentence]
    selected: tuple[SignalType, ...]


@dataclass
class StatementNotes:
    statement: StatementFile
    pages: list[NotesPage] = field(default_factory=list[NotesPage])
    attachments: int = 0
    attachments_unsupported: int = 0
    attachment_errors: list[tuple[str, str]] = field(
        default_factory=list[tuple[str, str]]
    )  # (reason, path)
    page_status: Counter[str] = field(default_factory=Counter[str])


def read_notes(
    statement: StatementFile,
    xml: bytes,
    prefilter: preprocessing.Prefilter,
    mask_nlp: Any,
    lemma_nlp: Any,
) -> StatementNotes:
    """Every page of the notes embedded in one statement: masked, analysed, prefiltered."""
    return _read(statement, page_text.attachments(xml), prefilter, mask_nlp, lemma_nlp)


def read_report(
    report: StatementFile,
    pdf: bytes,
    prefilter: preprocessing.Prefilter,
    mask_nlp: Any,
    lemma_nlp: Any,
) -> StatementNotes:
    """Every page of a separately filed auditor report, read as one attachment with no element
    path: the same masking, prefilter and coverage as a statement's notes."""
    if report.document_kind != "auditor_report":
        raise ValueError(f"{report.document_ref} is {report.document_kind}, not an auditor report")
    return _read(report, [page_text.Attachment(1, "", "pdf", pdf)], prefilter, mask_nlp, lemma_nlp)


def _read(
    statement: StatementFile,
    attachments: Iterable[page_text.Attachment],
    prefilter: preprocessing.Prefilter,
    mask_nlp: Any,
    lemma_nlp: Any,
) -> StatementNotes:
    notes = StatementNotes(statement)
    for attachment in attachments:
        notes.attachments += 1
        if attachment.kind != "pdf":
            notes.attachments_unsupported += 1
            continue
        try:
            pages = list(page_text.pages(attachment.data))
        except page_text.AttachmentError as exc:
            notes.attachment_errors.append((exc.reason_code, attachment.element_path))
            continue
        for page in pages:
            notes.page_status[page.status] += 1
            if page.status != "text":
                continue
            text = masking.mask(page.text, mask_nlp).text
            sentences = preprocessing.analyse(text, lemma_nlp)
            notes.pages.append(
                NotesPage(
                    page_id=f"{statement.source_document_hash}/{statement.source_member}/{attachment.index}/{page.number}",
                    attachment=attachment.index,
                    element_path=attachment.element_path,
                    page=page.number,
                    text=text,
                    sentences=sentences,
                    selected=tuple(preprocessing.candidates(sentences, prefilter)),
                )
            )
    return notes


# --- the datasets --------------------------------------------------------------------------------


@dataclass
class TextStats:
    statements: int = 0
    pages: Counter[str] = field(default_factory=Counter[str])
    coverage: Counter[str] = field(default_factory=Counter[str])  # "<signal>:<status>"
    extraction: ex.ExtractionStats = field(default_factory=ex.ExtractionStats)


@dataclass(frozen=True)
class Detection:
    """A quarantine detection: G1 an unreadable attachment, G2 a discarded extraction."""

    stage: Literal["G1", "G2"]
    statement: StatementFile
    reason_code: str
    detail: str
    signal_type: SignalType | None = None


def _value(result: Extraction) -> str:
    if not result.present:
        return "absent"
    return result.value if result.value is not None else "present"


def build(
    all_notes: Iterable[StatementNotes],
    extractor: ex.Extractor,
    runnable: set[SignalType],
    store: ResponseStore,
    transport: ex.Transport,
    stats: TextStats,
) -> tuple[pl.DataFrame, pl.DataFrame, list[Detection]]:
    """`text_signals`, `text_coverage` and the quarantine detections, sorted."""
    notes_list = list(all_notes)
    inputs = [
        ex.PageInput(p.page_id, p.text, p.sentences, tuple(s for s in p.selected if s in runnable))
        for notes in notes_list
        for p in notes.pages
    ]
    results = ex.extract(inputs, extractor, store, transport, stats.extraction)
    version = extractor.config.extractor_version
    signal_rows: list[dict[str, Any]] = []
    coverage_rows: list[dict[str, Any]] = []
    detections: list[Detection] = []
    for notes in notes_list:
        st = notes.statement
        stats.statements += 1
        stats.pages.update(notes.page_status)
        for reason, path in notes.attachment_errors:
            where = f"attachment at {path}" if path else "the document"
            detections.append(Detection("G1", st, reason, where))
        common = {
            "krs": st.krs,
            "fiscal_year": st.period_end.year,
            "known_from": st.known_from,
            "period_end": st.period_end,
            "document_ref": st.document_ref,
            "source_document_hash": st.source_document_hash,
            "source_member": st.source_member,
            "document_kind": st.document_kind,
            "extractor_version": version,
            "masking_version": masking.MASKING_VERSION,
            "ingestion_run_id": st.ingestion_run_id,
        }
        for signal in SIGNAL_TYPES:
            kept: Counter[bool] = Counter()
            discards: Counter[str] = Counter()
            unanswered = selected = 0
            for page in notes.pages:
                if signal not in page.selected:
                    continue
                selected += 1
                result = results.get((page.page_id, signal))
                if result is None:
                    continue  # not run: a model signal before the owner's confirmation
                if isinstance(result, Discarded):
                    if result.reason_code == "api_error":
                        unanswered += 1
                    else:
                        discards[result.reason_code] += 1
                        detections.append(
                            Detection(
                                "G2",
                                st,
                                result.reason_code,
                                f"page {page.attachment}.{page.page}",
                                signal,
                            )
                        )
                    continue
                kept[result.present] += 1
                signal_rows.append(
                    {
                        **common,
                        "signal_type": signal,
                        "value": _value(result),
                        "evidence_span": result.evidence,
                        "page": page.page,
                        "extraction_method": result.method,
                        "confidence": result.confidence,
                        "source_element_path": page.element_path,
                        "attachment": page.attachment,
                        "response_key": result.response_key,
                    }
                )
            text_pages = notes.page_status["text"]
            status: CoverageStatus
            if text_pages == 0:
                status = "no_text"
            elif selected and signal not in runnable:
                status = "not_run"
            elif (
                notes.page_status["needs_ocr"] or notes.attachment_errors or discards or unanswered
            ):
                status = "partial"
            else:
                status = "read"
            stats.coverage[f"{signal}:{status}"] += 1
            coverage_rows.append(
                {
                    **common,
                    "signal_type": signal,
                    "status": status,
                    "attachments": notes.attachments,
                    "attachments_unsupported": notes.attachments_unsupported,
                    "attachment_errors": sorted({r for r, _p in notes.attachment_errors}),
                    "pages": sum(notes.page_status.values()),
                    "pages_text": text_pages,
                    "pages_needs_ocr": notes.page_status["needs_ocr"],
                    "pages_sparse": notes.page_status["sparse"],
                    "pages_selected": selected,
                    "kept_present": kept[True],
                    "kept_absent": kept[False],
                    "discarded": sum(discards.values()),
                    "discard_reasons": sorted(discards),
                    "unanswered": unanswered,
                }
            )
    signals = (
        pl.DataFrame(signal_rows, schema=SIGNAL_COLUMNS)
        .select(list(SIGNAL_COLUMNS))
        .sort(SIGNAL_SORT_KEY)
        if signal_rows
        else empty_signals()
    )
    coverage = (
        pl.DataFrame(coverage_rows, schema=COVERAGE_COLUMNS)
        .select(list(COVERAGE_COLUMNS))
        .sort(COVERAGE_SORT_KEY)
        if coverage_rows
        else empty_coverage()
    )
    return signals, coverage, detections


# --- the masking check ---------------------------------------------------------------------------


def masking_findings(signals: pl.DataFrame, nlp: Any) -> Mapping[str, int]:
    """What the masker would still replace in the stored evidence spans, by kind; never the text.
    Evidence is quoted from masked pages, so anything here is a name the first pass missed."""
    found: Counter[str] = Counter()
    for span in signals.get_column("evidence_span").drop_nulls().to_list():
        found.update(masking.mask(str(span), nlp).counts)
    return dict(sorted(found.items()))
