"""A3, manual files tier — auditor reports downloaded by hand, dated from a hand-collected list.

The owner downloaded the seed's auditor reports from RDF at a human pace with Power Automate
Desktop, which records no HAR (plan 0013, decision 0c). So there is no detail response for these
rows; their submission dates ("Data dodania", the detail's `dataDodania`) were collected into a
CSV instead. Decision 6 is amended to accept that list as the source of `known_from` (owner,
2026-10-02), on two conditions this module keeps: the list is stored raw like any capture, and
every date it gives points back to it (`filing_index.submission_date_sha256`).

Inbox layout (`RDF_REPORT_INBOX`, default `.cache/rdf_auditor_reports`, gitignored):

- `filing_dates.csv`: `doc_name;krs;filing_date;period;document_id`, `;`-separated, a UTF-8 BOM
  allowed. `filing_date` and `document_id` may be `unknown`.
- `<krs>/**/<krs>_<period end>.zip`: each file exactly as RDF's "Pobierz dokumenty" delivered it.
  Files extracted from them by hand are not read: the ZIP is what was received.

A file or a date is matched to its `filing_index` row by (KRS, period end, a type marked
`canonical: auditor_report` in `config/mappings/rdf_document_types.yaml`), and only when that
names exactly one listed document. Each ZIP is redacted (ADR 0009: signatures, PDF metadata, the
filer's file name) before it is hashed and stored, the same way as a captured download; a ZIP
that does not hold exactly one PDF is not an auditor report and is not stored.

A detail captured later wins: `record_a3_detail` replaces the date and clears its source.
Only rows with nothing recorded are written, so importing the same inbox twice adds nothing.
"""

from __future__ import annotations

import csv
import io
import re
import zipfile
from collections.abc import Collection
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path

from psycopg import Connection

from distress_radar.acquisition import manifest
from distress_radar.acquisition.document_retrieval import SOURCE, A3Download
from distress_radar.acquisition.models import RawFetchRecord, RdfDocumentTypes
from distress_radar.acquisition.raw_store import ObjectStore, RawDocumentMeta, put_raw, sha256_hex
from distress_radar.acquisition.redaction import (
    REDACTION_VERSION,
    RedactionError,
    file_token,
    personal_data_markers,
    redact_download,
)

FETCH_TIER = "manual_files"
DATES_FILE = "filing_dates.csv"
DATES_COLUMNS = ("doc_name", "krs", "filing_date", "period", "document_id")
UNKNOWN = "unknown"
AUDITOR_REPORT = "auditor_report"

_ZIP_NAME = re.compile(r"(?P<krs>\d{10})_(?P<period>\d{4}-\d{2}-\d{2})\.zip")
_DOC_NAME = re.compile(r"(?P<krs>\d{10})_(?P<period>\d{4}-\d{2}-\d{2})\.[a-z]{3,4}")


class DatesFileError(ValueError):
    """The dates list cannot be read as a whole; nothing from it is used."""


@dataclass(frozen=True)
class ListedDate:
    """One row of the dates list."""

    krs: str
    period_end: date
    filing_date: date | None  # None: the list says `unknown`
    line: int


@dataclass
class ReportImportReport:
    """What one import stored, and what it could not use (nothing is dropped silently)."""

    downloads: int = 0
    dated: int = 0
    unmatched: list[str] = field(default_factory=list[str])  # no single auditor-report row
    not_a_report: list[str] = field(default_factory=list[str])
    undated: list[str] = field(default_factory=list[str])  # rows still with no submission date
    problems: list[str] = field(default_factory=list[str])


def _utc_mtime(path: Path) -> datetime:
    return datetime.fromtimestamp(path.stat().st_mtime, UTC)


def read_dates(data: bytes) -> list[ListedDate]:
    """Parse the dates list. Raises `DatesFileError` on a bad header or row."""
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise DatesFileError(f"not UTF-8: {exc}") from exc
    reader = csv.reader(io.StringIO(text, newline=""), delimiter=";")
    header = next(reader, None)
    if header is None or tuple(h.strip() for h in header) != DATES_COLUMNS:
        raise DatesFileError(f"header is {header!r}, expected {';'.join(DATES_COLUMNS)}")
    rows: list[ListedDate] = []
    for line, values in enumerate(reader, start=2):
        if not any(v.strip() for v in values):
            continue
        if len(values) != len(DATES_COLUMNS):
            raise DatesFileError(f"line {line}: {len(values)} fields")
        doc_name, krs, filing_date, period, document_id = (v.strip() for v in values)
        name = _DOC_NAME.fullmatch(doc_name)
        if name is None or name["krs"] != krs or name["period"] != period:
            raise DatesFileError(f"line {line}: doc_name does not match its KRS and period")
        if not re.fullmatch(r"\d{10}", krs):
            raise DatesFileError(f"line {line}: KRS {krs!r}")
        if document_id != UNKNOWN and not document_id.isdigit():
            raise DatesFileError(f"line {line}: document_id {document_id!r}")
        try:
            period_end = date.fromisoformat(period)
            filed = None if filing_date == UNKNOWN else date.fromisoformat(filing_date)
        except ValueError as exc:
            raise DatesFileError(f"line {line}: {exc}") from exc
        rows.append(ListedDate(krs=krs, period_end=period_end, filing_date=filed, line=line))
    return rows


def auditor_codes(document_types: RdfDocumentTypes) -> list[str]:
    return sorted(c for c, t in document_types.types.items() if t.canonical == AUDITOR_REPORT)


def _report_rows(
    conn: Connection, codes: Collection[str]
) -> dict[tuple[str, date], list[tuple[str, str | None, date | None, str | None]]]:
    """(krs, period end) -> listed auditor-report rows: (ref, sha256, submission date, detail)."""
    rows: dict[tuple[str, date], list[tuple[str, str | None, date | None, str | None]]] = {}
    for krs, ref, period_end, sha256, submitted, detail in conn.execute(
        """
        SELECT krs, document_ref, period_end, sha256, submission_date, detail_sha256
        FROM filing_index
        WHERE rdf_type_code = ANY(%s) AND correction_of IS NULL AND deleted_on IS NULL
        ORDER BY krs, period_end, document_ref
        """,
        (list(codes),),
    ):
        rows.setdefault((str(krs).strip(), period_end), []).append((ref, sha256, submitted, detail))
    return rows


def _pdf_only(raw: bytes) -> str | None:
    """The single member's name when the ZIP holds exactly one PDF; else None."""
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            members = [i for i in archive.infolist() if not i.is_dir()]
            if len(members) != 1:
                return None
            head = archive.read(members[0])[:1024]
    except (zipfile.BadZipFile, zipfile.LargeZipFile):
        return None
    return members[0].filename if b"%PDF-" in head else None


def _store(
    store: ObjectStore,
    data: bytes,
    *,
    source_url: str,
    content_type: str,
    fetched_at: datetime,
    ingestion_run_id: str,
    original_filename: str | None = None,
    received_sha256: str | None = None,
) -> RawFetchRecord:
    meta = RawDocumentMeta(
        source=SOURCE,
        source_url=source_url,
        content_type=content_type,
        fetched_at=fetched_at,
        http_headers={},
        ingestion_run_id=ingestion_run_id,
        original_filename=original_filename,
        fetch_tier=FETCH_TIER,
        redaction_version=REDACTION_VERSION if received_sha256 is not None else None,
        received_sha256=received_sha256,
    )
    return RawFetchRecord(sha256=put_raw(store, data, meta), byte_size=len(data), meta=meta)


def import_reports(
    inbox: Path,
    *,
    conn: Connection,
    store: ObjectStore,
    ingestion_run_id: str,
    document_types: RdfDocumentTypes,
) -> ReportImportReport:
    """Store the inbox's auditor reports and dates against `filing_index`; commits per step."""
    report = ReportImportReport()
    codes = auditor_codes(document_types)
    rows = _report_rows(conn, codes)

    def single(krs: str, period_end: date, what: str) -> tuple[str, str | None, date | None]:
        found = rows.get((krs, period_end), [])
        if len(found) != 1:
            report.unmatched.append(
                f"{what}: {len(found)} auditor-report rows (types {codes}) for {krs} {period_end}"
            )
            return ("", None, None)
        ref, sha256, submitted, _ = found[0]
        return (ref, sha256, submitted)

    for path in sorted(inbox.glob("*/**/*.zip")):
        name = _ZIP_NAME.fullmatch(path.name)
        if name is None or path.relative_to(inbox).parts[0] != name["krs"]:
            report.problems.append(f"{path.relative_to(inbox)}: not <krs>/…/<krs>_<period>.zip")
            continue
        krs, period_end = name["krs"], date.fromisoformat(name["period"])
        ref, sha256, _ = single(krs, period_end, path.name)
        if not ref or sha256 is not None:
            continue
        raw = path.read_bytes()
        member = _pdf_only(raw)
        if member is None:
            report.not_a_report.append(f"{path.name}: does not hold exactly one PDF; not stored")
            continue
        try:
            redacted = redact_download(raw, {ref: None})
        except RedactionError as exc:
            report.problems.append(f"{path.name}: cannot redact: {exc}")
            continue
        markers = personal_data_markers(redacted.data, [ref])
        if markers:
            report.problems.append(f"{path.name}: personal data left after redaction: {markers}")
            continue
        record = _store(
            store,
            redacted.data,
            source_url=f"manual:{krs}/{path.name}",
            content_type="application/zip",
            fetched_at=_utc_mtime(path),
            ingestion_run_id=ingestion_run_id,
            original_filename=file_token(ref, member),
            received_sha256=sha256_hex(raw) if redacted.changed else None,
        )
        manifest.record_a3_download(
            conn, A3Download(krs=krs, document_refs=[ref], raw_fetch=record)
        )
        conn.commit()
        report.downloads += 1

    dates_path = inbox / DATES_FILE
    if dates_path.exists():
        data = dates_path.read_bytes()
        try:
            listed = read_dates(data)
        except DatesFileError as exc:
            report.problems.append(f"{DATES_FILE}: {exc}")
            listed = []
        seen: dict[tuple[str, date], ListedDate] = {}
        for row in listed:
            key = (row.krs, row.period_end)
            if key in seen and seen[key].filing_date != row.filing_date:
                report.problems.append(
                    f"{DATES_FILE}: lines {seen[key].line} and {row.line} disagree on "
                    f"{row.krs} {row.period_end}; neither is used"
                )
            seen.setdefault(key, row)
        conflicting = {
            (r.krs, r.period_end)
            for r in listed
            if seen[(r.krs, r.period_end)].filing_date != r.filing_date
        }
        fetched_at = _utc_mtime(dates_path)
        record: RawFetchRecord | None = None
        for key, row in seen.items():
            if key in conflicting or row.filing_date is None:
                continue
            ref, _, submitted = single(row.krs, row.period_end, f"{DATES_FILE} line {row.line}")
            if not ref or submitted is not None:
                continue
            if not row.period_end < row.filing_date <= fetched_at.date():
                report.problems.append(
                    f"{DATES_FILE} line {row.line}: filed {row.filing_date}, outside "
                    f"({row.period_end}, {fetched_at.date()}]; not used"
                )
                continue
            if record is None:
                record = _store(
                    store,
                    data,
                    source_url=f"manual:{DATES_FILE}",
                    content_type="text/csv",
                    fetched_at=fetched_at,
                    ingestion_run_id=ingestion_run_id,
                )
                manifest.insert_raw_fetch(conn, record)
            conn.execute(
                """
                UPDATE filing_index SET submission_date = %s, submission_date_sha256 = %s
                WHERE krs = %s AND document_ref = %s
                  AND submission_date IS NULL AND detail_sha256 IS NULL
                """,
                (row.filing_date, record.sha256, row.krs, ref),
            )
            report.dated += 1
        conn.commit()
    else:
        report.problems.append(f"{DATES_FILE}: not in {inbox}")

    for (krs, period_end), found in sorted(_report_rows(conn, codes).items()):
        for ref, sha256, submitted, _ in found:
            if sha256 is not None and submitted is None:
                report.undated.append(f"{krs} {period_end} ({ref})")
    return report
