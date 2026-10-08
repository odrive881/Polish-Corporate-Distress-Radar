"""A3, scripted downloads tier — import what the owner's Power Automate Desktop script hands over.

ADR 0013 chose a PAD script reading RDF's public page as the route at scale; plan 0014 builds its
importer. The script records no HAR, so there is no detail response: what a tab of an expanded row
shows is written to a listing instead, and the listing is the source of each row's detail columns,
`known_from` included (its "Data dodania"). The listing is stored raw before it is read and every
row it fills points back to it (`filing_index.listing_sha256`).

Inbox layout (`RDF_SCRIPT_INBOX`, default `.cache/rdf_script_inbox`, gitignored), as specified to
the owner (plan 0014, decision 2 and § "Progress"):

- `documents.csv`: one row per tab of an expanded row, appended, never rewritten (`DOCUMENT_COLUMNS`).
- `entities.csv`: one row per search (`ENTITY_COLUMNS`); `complete` is `Nie` on any early stop.
- `<krs>/<row_document_id>.zip`: each expanded row's download exactly as "Pobierz dokumenty"
  delivered it, named in the rows' `file` column.

Both files are `;`-separated UTF-8 (a BOM allowed), flags `Tak` / `Nie` as RDF shows them, dates
`YYYY-MM-DD` (or `DD.MM.YYYY`), a time without an offset read as Warsaw time.

Keys (decision 1): a row is matched by `rdf_document_id` (RDF's numeric `idDokumentu`), then by
(KRS, type, period end, original or correction) when that names exactly one row with no id yet,
else it becomes a new row keyed `id-<idDokumentu>`. Type codes come from the type's name and the
period (decision 5); a name no code fits is indexed with no code and reported.

Each ZIP is redacted (ADR 0009) and checked before it is stored. A correction group's ZIP holds a
member per document, and the page shows no file names to pair them by (decision 3): unless
`PAIR_BY_PREPARED_DATE` is on, its members are stored unpaired (`unmatched-<n>`), which parsing
quarantines, and a HAR capture completes them.

Nothing is dropped silently: every row, ZIP or entity that cannot be used is in the report. Only
rows with nothing recorded are written, so importing the same inbox twice adds nothing.
"""

from __future__ import annotations

import csv
import io
import json
import re
import zipfile
from collections.abc import Collection, Iterable, Mapping
from dataclasses import dataclass, field, replace
from datetime import UTC, date, datetime
from pathlib import Path, PurePosixPath
from typing import Any, cast
from zoneinfo import ZoneInfo

from lxml import etree
from psycopg import Connection
from psycopg.types.json import Jsonb

from distress_radar.acquisition import manifest
from distress_radar.acquisition.document_retrieval import SOURCE, A3Download
from distress_radar.acquisition.models import RawFetchRecord, RdfDocumentStatus, RdfDocumentTypes
from distress_radar.acquisition.raw_store import (
    ObjectStore,
    RawDocumentMeta,
    put_raw,
    raw_key,
    sha256_hex,
)
from distress_radar.acquisition.redaction import (
    REDACTION_VERSION,
    RedactionError,
    file_token,
    personal_data_markers,
    redact_download,
)
from distress_radar.acquisition.report_import import DATES_FILE, auditor_codes, read_dates

FETCH_TIER = "pad_script"
DOCUMENTS_FILE = "documents.csv"
ENTITIES_FILE = "entities.csv"
DOCUMENT_COLUMNS = (
    "krs",
    "document_id",
    "row_document_id",
    "type_name",
    "period_start",
    "period_end",
    "prepared_date",
    "is_ifrs",
    "is_correction",
    "submission_date",
    "status",
    "deleted_on",
    "file",
    "captured_at",
    "tab",
    "language",
)
ENTITY_COLUMNS = ("krs", "searched_at", "found", "list_rows", "complete")
SCRIPT_KEY_PREFIX = "id-"
LISTING_TIMEZONE = ZoneInfo("Europe/Warsaw")
# Decision 3: pair a correction group's members with its tabs by the statement header's
# `DataSporzadzenia` against each tab's "Data sporządzenia dokumentu". Off until step A shows the
# rule pairs all of the seed's groups exactly as the HAR import did.
PAIR_BY_PREPARED_DATE = False

_EMPTY = frozenset({"", "-", "brak danych"})
_FLAGS = {"tak": True, "nie": False}
_STATUSES: dict[str, RdfDocumentStatus] = {
    "NIEUSUNIĘTY": "NIEUSUNIETY",
    "NIEUSUNIETY": "NIEUSUNIETY",
    "USUNIĘTY": "USUNIETY",
    "USUNIETY": "USUNIETY",
}
_TAB = re.compile(r"\s*(\d+)\s*/\s*(\d+)\s*")
_KRS = re.compile(r"\d{10}")


class ListingFileError(ValueError):
    """A listing that cannot be read as a whole (its header); nothing from it is used."""


class _RowError(ValueError):
    pass


@dataclass(frozen=True)
class ListedTab:
    """One tab of an expanded row, as the script read it."""

    line: int
    krs: str
    document_id: str
    row_document_id: str
    type_name: str
    period_start: date
    period_end: date
    prepared_date: date | None
    is_ifrs: bool | None
    is_correction: bool
    submission_date: date
    status: RdfDocumentStatus
    deleted_on: date | None
    file: str | None
    captured_at: datetime
    tab: int
    tabs: int
    language: str | None

    def facts(self) -> tuple[object, ...]:
        """What cannot change between two readings of one document."""
        return (
            self.krs,
            self.row_document_id,
            " ".join(self.type_name.split()).casefold(),
            self.period_start,
            self.period_end,
            self.prepared_date,
            self.is_ifrs,
            self.is_correction,
            self.submission_date,
        )


@dataclass(frozen=True)
class ListedSearch:
    """One search of one KRS number, as the script recorded it."""

    line: int
    krs: str
    searched_at: datetime
    found: bool
    list_rows: int | None
    complete: bool


# --- Reading the listings ------------------------------------------------------------------


def _value(raw: str) -> str | None:
    text = raw.strip()
    return None if text.casefold() in _EMPTY else text


def _required(row: Mapping[str, str], column: str) -> str:
    text = _value(row[column])
    if text is None:
        raise _RowError(f"{column} is empty")
    return text


def _date(text: str | None, column: str) -> date | None:
    if text is None:
        return None
    try:
        if re.fullmatch(r"\d{2}\.\d{2}\.\d{4}", text):
            day, month, year = text.split(".")
            return date(int(year), int(month), int(day))
        return date.fromisoformat(text)
    except ValueError as exc:
        raise _RowError(f"{column} {text!r}: {exc}") from exc


def _required_date(row: Mapping[str, str], column: str) -> date:
    found = _date(_required(row, column), column)
    assert found is not None
    return found


def _datetime(text: str, column: str) -> datetime:
    """A time as the script wrote it, in UTC; without an offset it is Warsaw time."""
    found = re.fullmatch(r"(\d{2})\.(\d{2})\.(\d{4})([ T].*)?", text)
    iso = f"{found[3]}-{found[2]}-{found[1]}{found[4] or ''}" if found else text
    try:
        value = datetime.fromisoformat(iso)
    except ValueError as exc:
        raise _RowError(f"{column} {text!r}: {exc}") from exc
    if value.tzinfo is None:
        value = value.replace(tzinfo=LISTING_TIMEZONE)
    return value.astimezone(UTC)


def _flag(text: str | None, column: str) -> bool | None:
    if text is None:
        return None
    try:
        return _FLAGS[text.casefold()]
    except KeyError:
        raise _RowError(f"{column} {text!r}: expected Tak or Nie") from None


def _required_flag(row: Mapping[str, str], column: str) -> bool:
    found = _flag(_required(row, column), column)
    assert found is not None
    return found


def _digits(row: Mapping[str, str], column: str) -> str:
    text = _required(row, column)
    if not text.isdigit():
        raise _RowError(f"{column} {text!r}: expected digits")
    return text


def _krs(row: Mapping[str, str]) -> str:
    text = _required(row, "krs")
    if not _KRS.fullmatch(text):
        raise _RowError(f"krs {text!r}")
    return text


def _file(text: str | None) -> str | None:
    if text is None:
        return None
    path = PurePosixPath(text.replace("\\", "/"))
    if path.is_absolute() or ".." in path.parts or path.suffix.lower() != ".zip":
        raise _RowError(f"file {text!r}: expected a ZIP's path inside the inbox")
    return path.as_posix()


def _read(data: bytes, columns: tuple[str, ...], name: str) -> list[tuple[int, dict[str, str]]]:
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ListingFileError(f"{name}: not UTF-8: {exc}") from exc
    reader = csv.reader(io.StringIO(text, newline=""), delimiter=";")
    header = [h.strip() for h in next(reader, list[str]())]
    if sorted(header) != sorted(columns):
        missing = sorted(set(columns) - set(header))
        extra = sorted(set(header) - set(columns))
        raise ListingFileError(
            f"{name}: header {header!r}: missing {missing}, unexpected {extra}, "
            f"or a column twice; expected {';'.join(columns)}"
        )
    rows: list[tuple[int, dict[str, str]]] = []
    for line, values in enumerate(reader, start=2):
        if not any(v.strip() for v in values):
            continue
        if len(values) != len(header):
            rows.append((line, {}))  # refused by the caller, with its line
            continue
        rows.append((line, dict(zip(header, values, strict=True))))
    return rows


def read_documents(data: bytes) -> tuple[list[ListedTab], list[str]]:
    """Parse `documents.csv`: the rows, and each refused row's reason.

    Raises `ListingFileError` on a bad header. A row is refused when a value cannot be read or
    its dates are out of order: period end < submission ≤ the time it was read, and the document
    prepared no later than it was submitted (which catches "Data sporządzenia" read into the
    submission column whenever the two differ).
    """
    tabs: list[ListedTab] = []
    problems: list[str] = []
    for line, row in _read(data, DOCUMENT_COLUMNS, DOCUMENTS_FILE):
        try:
            if not row:
                raise _RowError(f"not {len(DOCUMENT_COLUMNS)} fields")
            tabs.append(_tab(line, row))
        except _RowError as exc:
            problems.append(f"{DOCUMENTS_FILE} line {line}: {exc}; not used")
    return tabs, problems


def _tab(line: int, row: Mapping[str, str]) -> ListedTab:
    status_text = _required(row, "status").upper()
    if status_text not in _STATUSES:
        raise _RowError(f"status {status_text!r}")
    tab = _TAB.fullmatch(_required(row, "tab"))
    if tab is None or not 1 <= int(tab[1]) <= int(tab[2]):
        raise _RowError(f"tab {row['tab']!r}: expected n / m")
    listed = ListedTab(
        line=line,
        krs=_krs(row),
        document_id=_digits(row, "document_id"),
        row_document_id=_digits(row, "row_document_id"),
        type_name=_required(row, "type_name"),
        period_start=_required_date(row, "period_start"),
        period_end=_required_date(row, "period_end"),
        prepared_date=_date(_value(row["prepared_date"]), "prepared_date"),
        is_ifrs=_flag(_value(row["is_ifrs"]), "is_ifrs"),
        is_correction=_required_flag(row, "is_correction"),
        submission_date=_required_date(row, "submission_date"),
        status=_STATUSES[status_text],
        deleted_on=_date(_value(row["deleted_on"]), "deleted_on"),
        file=_file(_value(row["file"])),
        captured_at=_datetime(_required(row, "captured_at"), "captured_at"),
        tab=int(tab[1]),
        tabs=int(tab[2]),
        language=_value(row["language"]),
    )
    captured_on = listed.captured_at.astimezone(LISTING_TIMEZONE).date()
    if listed.period_start > listed.period_end:
        raise _RowError(f"period {listed.period_start}..{listed.period_end} runs backwards")
    if not listed.period_end < listed.submission_date <= captured_on:
        raise _RowError(
            f"submitted {listed.submission_date}, outside ({listed.period_end}, {captured_on}]"
        )
    if listed.prepared_date is not None and listed.prepared_date > listed.submission_date:
        raise _RowError(f"prepared {listed.prepared_date} after submitted {listed.submission_date}")
    if listed.deleted_on is not None and listed.deleted_on < listed.submission_date:
        raise _RowError(f"deleted {listed.deleted_on} before submitted {listed.submission_date}")
    if listed.is_correction == (listed.document_id == listed.row_document_id):
        raise _RowError(
            "is_correction disagrees with the ids: the expanded row's own tab is the original"
        )
    return listed


def read_entities(data: bytes) -> tuple[list[ListedSearch], list[str]]:
    """Parse `entities.csv`: the searches, and each refused row's reason."""
    searches: list[ListedSearch] = []
    problems: list[str] = []
    for line, row in _read(data, ENTITY_COLUMNS, ENTITIES_FILE):
        try:
            if not row:
                raise _RowError(f"not {len(ENTITY_COLUMNS)} fields")
            list_rows = _value(row["list_rows"])
            if list_rows is not None and not list_rows.isdigit():
                raise _RowError(f"list_rows {list_rows!r}")
            search = ListedSearch(
                line=line,
                krs=_krs(row),
                searched_at=_datetime(_required(row, "searched_at"), "searched_at"),
                found=_required_flag(row, "found"),
                list_rows=None if list_rows is None else int(list_rows),
                complete=_required_flag(row, "complete"),
            )
            if search.found and search.list_rows is None:
                raise _RowError("found, but list_rows is empty")
            searches.append(search)
        except _RowError as exc:
            problems.append(f"{ENTITIES_FILE} line {line}: {exc}; not used")
    return searches, problems


def current_documents(tabs: Iterable[ListedTab]) -> tuple[dict[str, ListedTab], list[str]]:
    """One reading per document id: the latest, with the latest file any reading saved.

    Readings of one id that disagree on what cannot change (its entity, row, type, period,
    dates, flags) are refused together. Status and deletion may change: the latest wins. A
    correction whose original tab was not read is refused too.
    """
    by_id: dict[str, list[ListedTab]] = {}
    for listed in tabs:
        by_id.setdefault(listed.document_id, []).append(listed)
    problems: list[str] = []
    current: dict[str, ListedTab] = {}
    for document_id, readings in sorted(by_id.items()):
        lines = ", ".join(str(r.line) for r in readings)
        if len({r.facts() for r in readings}) > 1:
            problems.append(
                f"{DOCUMENTS_FILE} lines {lines}: document {document_id} read differently; "
                "none is used"
            )
            continue
        readings.sort(key=lambda r: (r.captured_at, r.line))
        latest = readings[-1]
        files = [r.file for r in readings if r.file is not None]
        if files and latest.file != files[-1]:
            latest = replace(latest, file=files[-1])
        current[document_id] = latest
    for document_id, listed in list(current.items()):
        original = current.get(listed.row_document_id)
        if original is None or original.krs != listed.krs:
            problems.append(
                f"{DOCUMENTS_FILE} line {listed.line}: document {document_id}'s row "
                f"{listed.row_document_id} is not a document of {listed.krs} in the listing; "
                "not used"
            )
            del current[document_id]
    return current, problems


# --- The report ----------------------------------------------------------------------------


@dataclass
class ScriptImportReport:
    """What one import stored, and everything it could not use (nothing is dropped silently)."""

    listings: int = 0
    searches: int = 0
    indexed: int = 0  # new rows
    completed: int = 0  # existing rows filled from the listing
    updated: int = 0  # deletions seen in a later listing
    downloads: int = 0
    unpaired_groups: int = 0  # correction groups stored with unpaired members
    backfilled: int = 0
    not_in_entity_master: list[str] = field(default_factory=list[str])
    unknown_types: list[str] = field(default_factory=list[str])
    rows_without_zip: list[str] = field(default_factory=list[str])
    zips_without_row: list[str] = field(default_factory=list[str])
    incomplete_entities: list[str] = field(default_factory=list[str])
    disagreements: list[str] = field(default_factory=list[str])  # listing vs an earlier source
    problems: list[str] = field(default_factory=list[str])

    def counts(self) -> dict[str, int]:
        return {
            "listings": self.listings,
            "searches": self.searches,
            "indexed": self.indexed,
            "completed": self.completed,
            "updated": self.updated,
            "downloads": self.downloads,
            "unpaired_groups": self.unpaired_groups,
            "backfilled": self.backfilled,
            "not_in_entity_master": len(self.not_in_entity_master),
            "unknown_types": len(self.unknown_types),
            "rows_without_zip": len(self.rows_without_zip),
            "zips_without_row": len(self.zips_without_row),
            "incomplete_entities": len(self.incomplete_entities),
            "disagreements": len(self.disagreements),
            "problems": len(self.problems),
        }


# --- Storing -------------------------------------------------------------------------------


def _utc_mtime(path: Path) -> datetime:
    return datetime.fromtimestamp(path.stat().st_mtime, UTC)


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


def _store_listing(
    conn: Connection, store: ObjectStore, path: Path, ingestion_run_id: str
) -> tuple[bytes, RawFetchRecord]:
    data = path.read_bytes()
    known = conn.execute(
        "SELECT 1 FROM raw_documents WHERE sha256 = %s", (sha256_hex(data),)
    ).fetchone()
    record = _store(
        store,
        data,
        source_url=f"{FETCH_TIER}:{path.name}",
        content_type="text/csv",
        fetched_at=_utc_mtime(path),
        ingestion_run_id=ingestion_run_id,
    )
    if known is None:  # the same listing read again is not a new fetch (invariant 5)
        manifest.insert_raw_fetch(conn, record)
    return data, record


# --- Decision 1: the backfill of `rdf_document_id` ---------------------------------------


def backfill_document_ids(
    conn: Connection, store: ObjectStore, document_types: RdfDocumentTypes
) -> tuple[int, list[str]]:
    """Set `rdf_document_id` on rows that have a stored source for it; (rows set, problems).

    From each stored detail (`idDokumentu`), then from the owner's stored dates lists
    (`filing_dates.csv`, plan 0013 decision 6 amended) for the auditor-report rows they dated,
    matched as `report_import` matched them. An id another row of the entity already holds is
    reported, never set twice. Rows already set are left alone, so a second run sets nothing.
    """
    set_rows = 0
    problems: list[str] = []

    def assign(krs: str, ref: str, document_id: str, source: str) -> None:
        nonlocal set_rows
        holder = conn.execute(
            "SELECT document_ref FROM filing_index WHERE krs = %s AND rdf_document_id = %s",
            (krs, document_id),
        ).fetchone()
        if holder is not None:
            if holder[0] != ref:
                problems.append(
                    f"{source}: id {document_id} of {krs} {ref} is already {holder[0]}'s; not set"
                )
            return
        conn.execute(
            """
            UPDATE filing_index SET rdf_document_id = %s
            WHERE krs = %s AND document_ref = %s AND rdf_document_id IS NULL
            """,
            (document_id, krs, ref),
        )
        set_rows += 1

    for krs, ref, detail_sha in conn.execute(
        """
        SELECT krs, document_ref, detail_sha256 FROM filing_index
        WHERE rdf_document_id IS NULL AND detail_sha256 IS NOT NULL
        ORDER BY krs, document_ref
        """
    ).fetchall():
        try:
            detail = json.loads(store.get(raw_key(str(detail_sha))))
        except (KeyError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            problems.append(f"detail {detail_sha} of {ref}: unreadable ({exc}); not set")
            continue
        value = (
            cast(dict[str, Any], detail).get("idDokumentu") if isinstance(detail, dict) else None
        )
        if isinstance(value, int) and not isinstance(value, bool):
            value = str(value)
        if not isinstance(value, str) or not value.isdigit():
            problems.append(f"detail {detail_sha} of {ref}: no idDokumentu; not set")
            continue
        assign(str(krs).strip(), str(ref), value, f"detail {detail_sha}")

    codes = auditor_codes(document_types)
    lists = conn.execute(
        """
        SELECT DISTINCT sha256 FROM raw_document_fetches
        WHERE source_url = %s ORDER BY sha256
        """,
        (f"manual:{DATES_FILE}",),
    ).fetchall()
    for (list_sha,) in lists:
        try:
            listed = read_dates(store.get(raw_key(str(list_sha))))
        except (KeyError, ValueError) as exc:
            problems.append(f"{DATES_FILE} {list_sha}: unreadable ({exc}); not used")
            continue
        ids: dict[tuple[str, date], set[str]] = {}
        for row in listed:
            if row.document_id is not None:
                ids.setdefault((row.krs, row.period_end), set()).add(row.document_id)
        for (krs, period_end), found in sorted(ids.items()):
            if len(found) != 1:
                problems.append(
                    f"{DATES_FILE} {list_sha}: {krs} {period_end} has ids {sorted(found)}; not set"
                )
                continue
            rows = conn.execute(
                """
                SELECT document_ref, rdf_document_id FROM filing_index
                WHERE krs = %s AND period_end = %s AND rdf_type_code = ANY(%s)
                  AND correction_of IS NULL AND deleted_on IS NULL
                """,
                (krs, period_end, codes),
            ).fetchall()
            if len(rows) != 1:
                continue  # `report_import` did not use this line either
            [(ref, current)] = rows
            [document_id] = found
            if current is None:
                assign(krs, str(ref), document_id, f"{DATES_FILE} {list_sha}")
            elif current != document_id:
                problems.append(
                    f"{DATES_FILE} {list_sha}: {krs} {period_end} lists id {document_id}, "
                    f"its row holds {current}"
                )
    return set_rows, problems


# --- Decision 3: pairing a correction group's members ---------------------------------------


def header_prepared_dates(member: bytes) -> list[date]:
    """Every `Naglowek/DataSporzadzenia` in a statement file (inline in a signature too)."""
    data = member.removeprefix(b"\xef\xbb\xbf").lstrip()
    if not data.startswith(b"<"):
        return []
    parser = etree.XMLParser(huge_tree=True, resolve_entities=False, no_network=True)
    try:
        root = etree.fromstring(data, parser)
    except etree.XMLSyntaxError:
        return []
    found: list[date] = []
    for element in root.iter(etree.Element):
        if etree.QName(element).localname != "DataSporzadzenia":
            continue
        parent = element.getparent()
        if parent is None or etree.QName(parent).localname != "Naglowek":
            continue
        try:
            found.append(date.fromisoformat((element.text or "").strip()))
        except ValueError:
            continue
    return found


def pair_by_prepared_date(raw: bytes, prepared: Mapping[str, date | None]) -> dict[str, str] | None:
    """`{document_ref: member name}` when every content member pairs with exactly one tab by
    its header's prepared date and no tab takes two; None otherwise (the group stays unpaired).

    Members that carry no statement header (a detached signature) are left out.
    """
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            members = [(i.filename, archive.read(i)) for i in archive.infolist() if not i.is_dir()]
    except (zipfile.BadZipFile, zipfile.LargeZipFile):
        return None
    pairs: dict[str, str] = {}
    for name, data in members:
        dates = set(header_prepared_dates(data))
        if not dates:
            continue
        if len(dates) != 1:
            return None
        [when] = dates
        refs = [ref for ref, day in prepared.items() if day == when]
        if len(refs) != 1 or refs[0] in pairs:
            return None
        pairs[refs[0]] = name
    bases = [PurePosixPath(name).name for name in pairs.values()]
    if not pairs or len(set(bases)) != len(bases):
        return None
    return {ref: PurePosixPath(name).name for ref, name in pairs.items()}


# --- The import ----------------------------------------------------------------------------


@dataclass(frozen=True)
class _Row:
    document_ref: str
    rdf_document_id: str | None
    detail_sha256: str | None
    listing_sha256: str | None
    sha256: str | None
    status: str
    submission_date: date | None


def _existing(conn: Connection, krs: str, document_id: str) -> _Row | None:
    row = conn.execute(
        """
        SELECT document_ref, rdf_document_id, detail_sha256, listing_sha256, sha256, status,
               submission_date
        FROM filing_index WHERE krs = %s AND rdf_document_id = %s
        """,
        (krs, document_id),
    ).fetchone()
    return None if row is None else _Row(*row)


def _candidates(conn: Connection, listed: ListedTab, code: str) -> list[_Row]:
    return [
        _Row(*row)
        for row in conn.execute(
            """
            SELECT document_ref, rdf_document_id, detail_sha256, listing_sha256, sha256, status,
                   submission_date
            FROM filing_index
            WHERE krs = %s AND rdf_type_code = %s AND period_end = %s
              AND (correction_of IS NOT NULL) = %s AND rdf_document_id IS NULL
            """,
            (listed.krs, code, listed.period_end, listed.is_correction),
        ).fetchall()
    ]


def _index(
    conn: Connection,
    listed: ListedTab,
    code: str | None,
    refs: Mapping[str, str],
    listing: RawFetchRecord,
    ingestion_run_id: str,
    report: ScriptImportReport,
) -> str:
    """The `document_ref` of the listed document's row, creating or completing it."""
    row = _existing(conn, listed.krs, listed.document_id)
    if row is None and code is not None:
        found = _candidates(conn, listed, code)
        if len(found) == 1:
            row = found[0]
            conn.execute(
                "UPDATE filing_index SET rdf_document_id = %s WHERE krs = %s AND document_ref = %s",
                (listed.document_id, listed.krs, row.document_ref),
            )
    columns = {
        "krs": listed.krs,
        "type_name": listed.type_name,
        "submitted": listed.submission_date,
        "prepared": listed.prepared_date,
        "is_correction": listed.is_correction,
        "is_ifrs": listed.is_ifrs,
        "listing": listing.sha256,
    }
    if row is None:
        ref = SCRIPT_KEY_PREFIX + listed.document_id
        conn.execute(
            """
            INSERT INTO filing_index
                (krs, document_ref, rdf_type_code, status, period_start, period_end, deleted_on,
                 rdf_type_name, submission_date, prepared_date, is_correction, is_ifrs,
                 correction_of, rdf_document_id, listing_sha256, discovered_at, ingestion_run_id)
            VALUES (%(krs)s, %(ref)s, %(code)s, %(status)s, %(start)s, %(end)s, %(deleted)s,
                    %(type_name)s, %(submitted)s, %(prepared)s, %(is_correction)s, %(is_ifrs)s,
                    %(correction_of)s, %(document_id)s, %(listing)s, %(at)s, %(run)s)
            ON CONFLICT DO NOTHING
            """,
            columns
            | {
                "ref": ref,
                "code": code,
                "status": listed.status,
                "start": listed.period_start,
                "end": listed.period_end,
                "deleted": listed.deleted_on,
                "correction_of": refs.get(listed.row_document_id) if listed.is_correction else None,
                "document_id": listed.document_id,
                "at": listed.captured_at,
                "run": ingestion_run_id,
            },
        )
        report.indexed += 1
        return ref
    ref = row.document_ref
    if row.detail_sha256 is None and row.listing_sha256 is None:
        if row.submission_date not in (None, listed.submission_date):
            report.disagreements.append(
                f"{listed.krs} {ref}: submitted {row.submission_date} in the dates list, "
                f"{listed.submission_date} in the listing; the listing's is kept"
            )
        conn.execute(
            """
            UPDATE filing_index SET
                rdf_type_name = %(type_name)s, submission_date = %(submitted)s,
                prepared_date = %(prepared)s, is_correction = %(is_correction)s,
                is_ifrs = %(is_ifrs)s, listing_sha256 = %(listing)s,
                submission_date_sha256 = NULL
            WHERE krs = %(krs)s AND document_ref = %(ref)s
              AND detail_sha256 IS NULL AND listing_sha256 IS NULL
            """,
            columns | {"ref": ref},
        )
        report.completed += 1
    elif row.detail_sha256 is not None and row.submission_date != listed.submission_date:
        report.disagreements.append(
            f"{listed.krs} {ref}: submitted {row.submission_date} in the detail, "
            f"{listed.submission_date} in the listing; the detail's is kept"
        )
    if row.status == "NIEUSUNIETY" and listed.status == "USUNIETY":
        conn.execute(
            """
            UPDATE filing_index SET status = %s, deleted_on = COALESCE(deleted_on, %s)
            WHERE krs = %s AND document_ref = %s
            """,
            (listed.status, listed.deleted_on, listed.krs, ref),
        )
        report.updated += 1
    return ref


def _group_refs(
    conn: Connection, krs: str, group: list[ListedTab], refs: Mapping[str, str]
) -> None:
    """A listed original's `correction_refs`: its tabs' rows in tab order (the download bundle)."""
    ordered = [refs[t.document_id] for t in sorted(group, key=lambda t: t.tab)]
    original = next(t for t in group if not t.is_correction)
    conn.execute(
        """
        UPDATE filing_index SET correction_refs = %s
        WHERE krs = %s AND document_ref = %s AND detail_sha256 IS NULL
          AND listing_sha256 IS NOT NULL
          AND correction_refs IS DISTINCT FROM %s
        """,
        (Jsonb(ordered), krs, refs[original.document_id], Jsonb(ordered)),
    )


def _downloaded(conn: Connection, krs: str, refs: Collection[str]) -> bool:
    row = conn.execute(
        """
        SELECT count(*) FILTER (WHERE sha256 IS NULL) FROM filing_index
        WHERE krs = %s AND document_ref = ANY(%s)
        """,
        (krs, list(refs)),
    ).fetchone()
    return row is not None and row[0] == 0


def _import_zip(
    inbox: Path,
    file: str,
    group: list[ListedTab],
    refs: Mapping[str, str],
    *,
    conn: Connection,
    store: ObjectStore,
    ingestion_run_id: str,
    report: ScriptImportReport,
) -> None:
    krs = group[0].krs
    covered = [refs[t.document_id] for t in sorted(group, key=lambda t: t.tab)]
    if _downloaded(conn, krs, covered):
        return
    path = inbox / file
    if not path.is_file():
        report.rows_without_zip.extend(f"{krs} {ref}: {file} not in the inbox" for ref in covered)
        return
    raw = path.read_bytes()
    if not zipfile.is_zipfile(io.BytesIO(raw)):
        report.problems.append(f"{file}: not a ZIP; not stored")
        return
    names: dict[str, str | None] = dict.fromkeys(covered)
    paired = len(covered) == 1
    if not paired and PAIR_BY_PREPARED_DATE:
        prepared = {refs[t.document_id]: t.prepared_date for t in group}
        pairs = pair_by_prepared_date(raw, prepared)
        if pairs is not None and set(pairs) == set(covered):
            names.update(pairs)
            paired = True
    try:
        redacted = redact_download(raw, names)
    except RedactionError as exc:
        report.problems.append(f"{file}: cannot redact: {exc}; not stored")
        return
    markers = personal_data_markers(redacted.data, covered)
    if markers:
        report.problems.append(f"{file}: personal data left after redaction: {markers}")
        return
    with zipfile.ZipFile(io.BytesIO(redacted.data)) as archive:
        stored_names = [i.filename for i in archive.infolist()]
    record = _store(
        store,
        redacted.data,
        source_url=f"{FETCH_TIER}:{file}",
        content_type="application/zip",
        fetched_at=_utc_mtime(path),
        ingestion_run_id=ingestion_run_id,
        original_filename=stored_names[0] if len(stored_names) == 1 else None,
        received_sha256=sha256_hex(raw) if redacted.changed else None,
    )
    manifest.record_a3_download(conn, A3Download(krs=krs, document_refs=covered, raw_fetch=record))
    # Parsing pairs a bundle's members by `file_name` (`containers.match_members`).
    tokens = {PurePosixPath(n).stem: n for n in stored_names}
    for ref in covered:
        token = tokens.get(file_token(ref, None))
        if token is not None:
            conn.execute(
                """
                UPDATE filing_index SET file_name = %s
                WHERE krs = %s AND document_ref = %s AND file_name IS NULL
                """,
                (token, krs, ref),
            )
    report.downloads += 1
    if not paired:
        report.unpaired_groups += 1


def import_listing(
    inbox: Path,
    *,
    conn: Connection,
    store: ObjectStore,
    ingestion_run_id: str,
    document_types: RdfDocumentTypes,
    resolved: Collection[str],
) -> ScriptImportReport:
    """Import the script's inbox into the manifest; commits per step."""
    report = ScriptImportReport()
    report.backfilled, problems = backfill_document_ids(conn, store, document_types)
    report.problems.extend(problems)
    conn.commit()

    searches: list[ListedSearch] = []
    entities_path = inbox / ENTITIES_FILE
    if entities_path.is_file():
        data, entities_record = _store_listing(conn, store, entities_path, ingestion_run_id)
        conn.commit()
        report.listings += 1
        try:
            searches, problems = read_entities(data)
            report.problems.extend(problems)
        except ListingFileError as exc:
            report.problems.append(str(exc))
        for search in searches:
            if search.krs not in resolved:
                continue
            conn.execute(
                """
                INSERT INTO rdf_listed_entities
                    (krs, searched_at, found, list_rows, complete, listing_sha256,
                     ingestion_run_id)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT DO NOTHING
                """,
                (
                    search.krs,
                    search.searched_at,
                    search.found,
                    search.list_rows,
                    search.complete,
                    entities_record.sha256,
                    ingestion_run_id,
                ),
            )
            report.searches += 1
        conn.commit()
    else:
        report.problems.append(f"{ENTITIES_FILE}: not in {inbox}")

    current: dict[str, ListedTab] = {}
    documents_path = inbox / DOCUMENTS_FILE
    documents_record: RawFetchRecord | None = None
    if documents_path.is_file():
        data, documents_record = _store_listing(conn, store, documents_path, ingestion_run_id)
        conn.commit()
        report.listings += 1
        try:
            tabs, problems = read_documents(data)
            report.problems.extend(problems)
            current, problems = current_documents(tabs)
            report.problems.extend(problems)
        except ListingFileError as exc:
            report.problems.append(str(exc))
    else:
        report.problems.append(f"{DOCUMENTS_FILE}: not in {inbox}")

    listed_krs = {t.krs for t in current.values()} | {s.krs for s in searches}
    report.not_in_entity_master = sorted(k for k in listed_krs if k not in resolved)
    groups: dict[tuple[str, str], list[ListedTab]] = {}
    for listed in current.values():
        if listed.krs in resolved:
            groups.setdefault((listed.krs, listed.row_document_id), []).append(listed)

    refs: dict[str, str] = {}
    if documents_record is not None:
        for (krs, _), group in sorted(groups.items()):
            for listed in sorted(group, key=lambda t: (t.is_correction, t.tab)):
                code = document_types.code_for(listed.type_name, listed.period_end)
                if code is None:
                    report.unknown_types.append(
                        f"{krs} {listed.document_id}: {listed.type_name!r}, period ending "
                        f"{listed.period_end}: no code; indexed without one"
                    )
                refs[listed.document_id] = _index(
                    conn, listed, code, refs, documents_record, ingestion_run_id, report
                )
            if all(t.document_id in refs for t in group):
                _group_refs(conn, krs, group, refs)
            conn.commit()

    by_file: dict[str, list[ListedTab]] = {}
    for (krs, _), group in sorted(groups.items()):
        for listed in group:
            if listed.document_id not in refs:
                continue
            if listed.file is None:
                if listed.status == "NIEUSUNIETY":
                    report.rows_without_zip.append(f"{krs} {refs[listed.document_id]}: no file")
                continue
            by_file.setdefault(listed.file, []).append(listed)
    for file, group in sorted(by_file.items()):
        if len({t.krs for t in group}) != 1 or len({t.row_document_id for t in group}) != 1:
            report.problems.append(f"{file}: named by rows of more than one expanded row; not used")
            continue
        _import_zip(
            inbox,
            file,
            group,
            refs,
            conn=conn,
            store=store,
            ingestion_run_id=ingestion_run_id,
            report=report,
        )
        conn.commit()

    named = set(by_file)
    for path in sorted(inbox.glob("*/*.zip")):
        relative = path.relative_to(inbox).as_posix()
        if _KRS.fullmatch(path.parent.name) and relative not in named:
            report.zips_without_row.append(relative)

    latest: dict[str, ListedSearch] = {}
    for search in sorted(searches, key=lambda s: s.searched_at):
        latest[search.krs] = search
    for krs in sorted({k for k, _ in groups} | set(latest)):
        search = latest.get(krs)
        if krs in resolved and (search is None or not search.complete):
            report.incomplete_entities.append(
                f"{krs}: {'no search recorded' if search is None else 'listing incomplete'}"
            )
    return report
