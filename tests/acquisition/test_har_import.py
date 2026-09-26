"""A3 manual tier: reading RDF sessions from HAR files, and importing them.

HARs here are built from the recorded RDF responses in `tests/fixtures/rdf/`.
The Postgres import tests are `integration` (`make dev-up`); the rest run in
`make check`. `test_recorded_har_*` use the real capture when it is present
locally (it is gitignored) and skip otherwise.
"""

import base64
import io
import json
import uuid
import zipfile
from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import psycopg
import pytest

from distress_radar.acquisition import manifest
from distress_radar.acquisition.base import PermanentSourceError
from distress_radar.acquisition.document_retrieval import (
    API_PREFIX,
    CircuitBreaker,
    RdfShapeError,
    index_filings,
    load_document_types,
)
from distress_radar.acquisition.har_import import (
    FETCH_TIER,
    HarCapture,
    HarFilingBrowser,
    NotCaptured,
    import_har,
    read_har,
)
from distress_radar.acquisition.models import Bir1PkdCode, EntityMasterRow, RawFetchRecord
from distress_radar.acquisition.raw_store import (
    InMemoryObjectStore,
    RawDocumentMeta,
    raw_key,
    sha256_hex,
    sidecar_key,
)
from distress_radar.acquisition.redaction import file_token, redact_download
from distress_radar.settings import Settings

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "rdf"
RECORDED_HAR = FIXTURES / "rdf-przegladarka_ms_gov_pl.cleaned.har"
ENTITY_FOUND = (FIXTURES / "entity_found.json").read_text(encoding="utf-8")
LIST_PAGE0 = json.loads((FIXTURES / "filing_list_page0.json").read_text(encoding="utf-8"))
CORRECTIONS = (FIXTURES / "document_corrections.json").read_text(encoding="utf-8")
DETAIL = json.loads((FIXTURES / "document_detail.json").read_text(encoding="utf-8"))

KRS = "0000209396"
OTHER_KRS = "0000277937"
STATEMENT_2025 = "kQL-7bDLHvl-dIGIeLuLlQ=="
STATEMENT_2024 = "MkKHeXhci9HveHNtSKfMtA=="
AUDITOR_2025 = "B2opwZt-Ik8Yg4luKMAqQA=="
STATEMENT_2025_TOKEN = "kQL-7bDLHvl-dIGIeLuLlQ.xml"  # its nazwaPliku as stored (ADR 0009)
HOST = "https://rdf-przegladarka.ms.gov.pl"
API = HOST + API_PREFIX
T0 = datetime(2026, 9, 16, 9, 0, tzinfo=UTC)


def _zip(text: str) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("sprawozdanie.xml", text)
    return buffer.getvalue()


STATEMENT_ZIP = _zip("<?xml version='1.0'?><Sprawozdanie/>")


def _list(size: int, page: int, items: list[dict[str, Any]], total: int, pages: int) -> str:
    return json.dumps(
        {
            "content": items,
            "metadaneWynikow": {
                "numerStrony": page,
                "rozmiarStrony": size,
                "liczbaStron": pages,
                "calkowitaLiczbaObiektow": total,
            },
        }
    )


def _detail(ref: str, type_id: int, name: str, krs: str = KRS) -> str:
    return json.dumps(
        {
            **DETAIL,
            "identyfikator": ref,
            "nrKRS": krs,
            "nazwaPliku": f"{ref[:3]}.xml",
            "rodzajDokumentu": {**DETAIL["rodzajDokumentu"], "id": type_id, "nazwa": name},
        }
    )


class HarBuilder:
    """Builds a Chrome-style HAR, one entry per call, one second apart."""

    def __init__(self) -> None:
        self.entries: list[dict[str, Any]] = []

    def add(
        self,
        method: str,
        url: str,
        body: str | bytes,
        *,
        post: str | None = None,
        status: int = 200,
        content_type: str = "application/json",
    ) -> "HarBuilder":
        content: dict[str, Any] = {"mimeType": content_type}
        if isinstance(body, bytes):
            content.update(text=base64.b64encode(body).decode(), encoding="base64")
        else:
            content["text"] = body
        request: dict[str, Any] = {
            "method": method,
            "url": url,
            "headers": [{"name": "user-agent", "value": "Mozilla/5.0 Chrome/152.0.0.0"}],
        }
        if post is not None:
            request["postData"] = {"mimeType": "application/json", "text": post}
        started = T0 + timedelta(seconds=len(self.entries))
        self.entries.append(
            {
                "startedDateTime": started.isoformat().replace("+00:00", "Z"),
                "request": request,
                "response": {
                    "status": status,
                    "headers": [
                        {"name": "content-type", "value": content_type},
                        {"name": "set-cookie", "value": "incap_ses_1=secret"},
                    ],
                    "content": content,
                },
            }
        )
        return self

    def filtered_list(self, body: str) -> "HarBuilder":
        post = json.dumps(
            {
                "metadaneStronicowania": {"numerStrony": 0, "rozmiarStrony": 50},
                "nrKRS": "<encrypted>",
                "rodzajDokumentuNazwa": "Roczne sprawozdanie finansowe",
                "nazwa": None,
                "status": None,
            }
        )
        return self.add("POST", API + "dokumenty/wyszukiwanie", body, post=post)

    def search(self, krs: str, *lists: str, found: bool = True) -> "HarBuilder":
        entity = (
            ENTITY_FOUND.replace(KRS, krs)
            if found
            else '{"podmiot":null,"czyPodmiotZnaleziony":false,"komunikatBledu":null}'
        )
        self.add(
            "POST",
            API + "podmioty/wyszukiwanie/dane-podstawowe",
            entity,
            post=json.dumps({"numerKRS": krs}),
        )
        for body in lists:
            self.add("POST", API + "dokumenty/wyszukiwanie", body, post='{"nrKRS":"<encrypted>"}')
        return self

    def expand(
        self, ref: str, detail: str, corrections: dict[str, str] | None = None
    ) -> "HarBuilder":
        """Expand `ref`; the SPA then loads one detail per id in its corrections list."""
        refs = [ref, *(corrections or {})]
        encoded = ref.replace("=", "%3D")
        self.add("GET", API + f"dokumenty/{encoded}/id-dokumentu-i-korekt", json.dumps(refs))
        self.add("GET", API + f"dokumenty/{encoded}", detail)
        for other, other_detail in (corrections or {}).items():
            self.add("GET", API + f"dokumenty/{other.replace('=', '%3D')}", other_detail)
        return self

    def download(self, ref: str | list[str], body: bytes) -> "HarBuilder":
        return self.add(
            "POST",
            API + "dokumenty/tresc",
            body,
            post=json.dumps(ref if isinstance(ref, list) else [ref]),
            content_type="application/octet-stream",
        )

    def build(self) -> bytes:
        page = {"startedDateTime": T0.isoformat(), "id": "page_1", "title": HOST}
        noise = {
            "startedDateTime": T0.isoformat(),
            "request": {"method": "POST", "url": HOST + "/ynine-in-thes?d=x", "headers": []},
            "response": {"status": 200, "headers": [], "content": {"text": '{"token":"x"}'}},
        }
        log = {
            "version": "1.2",
            "creator": {"name": "WebInspector", "version": "537.36"},
            "pages": [page],
            "entries": [noise, *self.entries],
        }
        return json.dumps({"log": log}).encode()


# The recorded rows as they look at the default 10 rows per page (page 1 of 2 of
# a 12-document list), and as one complete page at 50 rows.
TEN_OF_TWELVE = _list(10, 0, LIST_PAGE0["content"], total=12, pages=2)
FULL_AT_50 = _list(50, 0, LIST_PAGE0["content"], total=10, pages=1)


def _session() -> HarBuilder:
    return (
        HarBuilder()
        .search(KRS, TEN_OF_TWELVE, FULL_AT_50)
        .expand(STATEMENT_2025, json.dumps({**DETAIL, "nrKRS": KRS}))
        .download(STATEMENT_2025, STATEMENT_ZIP)
        .expand(AUDITOR_2025, _detail(AUDITOR_2025, 19, "Opinia biegłego rewidenta"))
    )


# --- reading ---------------------------------------------------------------------------------


def test_only_rdf_api_entries_are_read():
    exchanges, recorder = read_har(_session().build())

    assert all(API_PREFIX in e.url for e in exchanges)  # the bot-check entry is ignored
    assert [e.started for e in exchanges] == sorted(e.started for e in exchanges)
    assert recorder == "Mozilla/5.0 Chrome/152.0.0.0"
    assert all("set-cookie" not in e.response.headers for e in exchanges)
    download = next(e for e in exchanges if e.path == "dokumenty/tresc")
    assert download.response.body == STATEMENT_ZIP  # base64 content decoded
    assert download.request_body == json.dumps([STATEMENT_2025]).encode()


def test_not_a_har_is_refused():
    with pytest.raises(PermanentSourceError, match="not a HAR"):
        read_har(b"<html></html>")


def test_capture_groups_exchanges_by_search_and_document():
    data = (
        _session()
        .search(OTHER_KRS, _list(10, 0, [], total=0, pages=0))
        .add("POST", API + "dokumenty/tresc", b"PK", post=json.dumps(["a", "b"]))
        .add("POST", API + "dokumenty/tresc", b"PK", post='{"not": "a list"}')
        .build()
    )

    capture = HarCapture.from_har(data)

    assert capture.krs_numbers == [KRS, OTHER_KRS]
    assert [len(s) for s in capture.searches[KRS]] == [3]  # lookup + both list responses
    assert [len(s) for s in capture.searches[OTHER_KRS]] == [2]
    assert set(capture.details) == {STATEMENT_2025, AUDITOR_2025}  # %3D decoded
    assert set(capture.corrections) == {STATEMENT_2025, AUDITOR_2025}
    assert set(capture.downloads) == {STATEMENT_2025, "a", "b"}  # one file, both documents
    assert capture.downloads["a"] is capture.downloads["b"]
    assert len(capture.skipped) == 1 and "not a list of documents" in capture.skipped[0]


def test_browser_serves_the_largest_complete_list():
    browser = HarFilingBrowser(HarCapture.from_har(_session().build()))

    listing = browser.open_filing_list(KRS)

    assert [p.body.decode() for p in listing.pages] == [FULL_AT_50]
    assert browser.captured_at() == T0 + timedelta(seconds=2)
    assert browser.fetch_tier == FETCH_TIER == "manual_har"


def test_incomplete_list_is_not_captured():
    data = HarBuilder().search(KRS, TEN_OF_TWELVE).build()

    with pytest.raises(NotCaptured, match="largest rows-per-page"):
        HarFilingBrowser(HarCapture.from_har(data)).open_filing_list(KRS)


STATEMENTS_ONLY = _list(
    50, 0, [i for i in LIST_PAGE0["content"] if i["rodzaj"] == "18"], total=2, pages=1
)


def test_filtered_list_is_never_taken_for_the_full_list():
    data = HarBuilder().search(KRS, FULL_AT_50).filtered_list(STATEMENTS_ONLY).build()

    listing = HarFilingBrowser(HarCapture.from_har(data)).open_filing_list(KRS)

    assert [p.body.decode() for p in listing.pages] == [FULL_AT_50]  # not the later, filtered one


def test_only_a_filtered_complete_list_is_not_captured():
    data = HarBuilder().search(KRS, TEN_OF_TWELVE).filtered_list(STATEMENTS_ONLY).build()

    with pytest.raises(NotCaptured, match="unfiltered"):
        HarFilingBrowser(HarCapture.from_har(data)).open_filing_list(KRS)


def test_all_pages_at_the_default_size_are_complete():
    items = LIST_PAGE0["content"]
    data = (
        HarBuilder()
        .search(KRS, _list(5, 0, items[:5], total=10, pages=2), _list(5, 1, items[5:], 10, 2))
        .build()
    )

    listing = HarFilingBrowser(HarCapture.from_har(data)).open_filing_list(KRS)

    assert len(listing.pages) == 2


def test_unknown_entity_has_no_pages_and_missing_search_is_not_captured():
    browser = HarFilingBrowser(HarCapture.from_har(HarBuilder().search(KRS, found=False).build()))

    assert browser.open_filing_list(KRS).pages == []
    with pytest.raises(NotCaptured, match="no search"):
        browser.open_filing_list(OTHER_KRS)


def test_document_of_another_entity_is_refused():
    data = HarBuilder().expand(AUDITOR_2025, _detail(AUDITOR_2025, 19, "x", krs=OTHER_KRS)).build()

    with pytest.raises(RdfShapeError, match="belongs to"):
        HarFilingBrowser(HarCapture.from_har(data)).open_document(KRS, AUDITOR_2025)


CORRECTION_2024 = "korekta-2024=="
CORRECTION_DETAIL = json.dumps(
    {
        **json.loads(_detail(CORRECTION_2024, 18, "Roczne sprawozdanie finansowe")),
        "czyKorekta": True,
        "dataDodania": "2025-11-03",
        "okresSprawozdawczyPoczatek": "2024-01-01",
        "okresSprawozdawczyKoniec": "2024-12-31",
    }
)


def _bundle_zip() -> bytes:
    """The statement and its correction, each named as its detail's `nazwaPliku` says."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for ref in (STATEMENT_2024, CORRECTION_2024):
            archive.writestr(f"{ref[:3]}.xml", f"<?xml version='1.0'?><Sprawozdanie ref='{ref}'/>")
    return buffer.getvalue()


BUNDLE_ZIP = _bundle_zip()
BUNDLE = [STATEMENT_2024, CORRECTION_2024]
BUNDLE_NAMES = {ref: f"{ref[:3]}.xml" for ref in (STATEMENT_2024, CORRECTION_2024)}
# What the store holds: the same files, each renamed to its filing's token (ADR 0009).
STORED_BUNDLE = redact_download(BUNDLE_ZIP, BUNDLE_NAMES).data


def _corrected_session() -> HarBuilder:
    return (
        _session()
        .expand(
            STATEMENT_2024,
            _detail(STATEMENT_2024, 18, "Roczne sprawozdanie finansowe"),
            {CORRECTION_2024: CORRECTION_DETAIL},
        )
        .download([STATEMENT_2024, CORRECTION_2024], BUNDLE_ZIP)
    )


def test_expanded_row_serves_its_corrections():
    browser = HarFilingBrowser(HarCapture.from_har(_corrected_session().build()))

    view = browser.open_document(KRS, STATEMENT_2024)

    assert list(view.related) == [CORRECTION_2024]
    assert browser.download(KRS, CORRECTION_2024).body == BUNDLE_ZIP


def test_expanded_row_without_a_correction_detail_is_not_captured():
    data = (
        HarBuilder()
        .expand(STATEMENT_2024, _detail(STATEMENT_2024, 18, "x"))
        .add(
            "GET",
            API + f"dokumenty/{STATEMENT_2024}/id-dokumentu-i-korekt",
            json.dumps([STATEMENT_2024, CORRECTION_2024]),
        )
        .build()
    )

    with pytest.raises(NotCaptured, match="without details"):
        HarFilingBrowser(HarCapture.from_har(data)).open_document(KRS, STATEMENT_2024)


def test_unexpanded_or_undownloaded_documents_are_not_captured():
    browser = HarFilingBrowser(HarCapture.from_har(_session().build()))

    with pytest.raises(NotCaptured, match="did not expand"):
        browser.open_document(KRS, STATEMENT_2024)
    with pytest.raises(NotCaptured, match="did not download"):
        browser.download(KRS, AUDITOR_2025)


def test_flow_over_a_capture_records_manual_tier_and_capture_time():
    browser = HarFilingBrowser(HarCapture.from_har(_session().build()))
    store = InMemoryObjectStore()

    result = index_filings(
        KRS,
        browser=browser,
        store=store,
        ingestion_run_id="run-1",
        breaker=CircuitBreaker(),
        clock=browser.captured_at,
    )

    assert len(result.entries) == 10
    assert result.entries[0].discovered_at == T0 + timedelta(seconds=2)
    fetch = result.raw_fetches[1]
    assert fetch.sha256 == sha256_hex(FULL_AT_50.encode())
    sidecar = json.loads(store.get(sidecar_key(fetch.sha256)))
    assert sidecar["fetch_tier"] == "manual_har"
    assert sidecar["source_url"] == API + "dokumenty/wyszukiwanie"
    assert sidecar["browser_version"] == "Mozilla/5.0 Chrome/152.0.0.0"
    assert "set-cookie" not in sidecar["http_headers"]


@pytest.mark.skipif(not RECORDED_HAR.exists(), reason="recorded HAR is not kept (ADR 0009)")
def test_recorded_har_has_detail_and_download_but_only_one_list_page():
    browser = HarFilingBrowser(HarCapture.from_har(RECORDED_HAR.read_bytes()))

    with pytest.raises(NotCaptured, match="no complete, unfiltered filing list"):
        browser.open_filing_list(KRS)  # recorded at 10 rows: page 1 of 5 only
    view = browser.open_document(KRS, STATEMENT_2025)
    assert json.loads(view.detail.body)["dataDodania"] == "2026-06-29"
    download = browser.download(KRS, STATEMENT_2025)
    assert download.body[:4] == b"PK\x03\x04"
    assert (
        sha256_hex(download.body)
        == json.loads((FIXTURES / "document_download.meta.json").read_text(encoding="utf-8"))[
            "response"
        ]["sha256"]
    )


# --- importing (live Postgres) ---------------------------------------------------------------

SHA = "ab" * 32


@pytest.fixture
def conn() -> Iterator[psycopg.Connection]:
    schema = f"test_har_import_{uuid.uuid4().hex[:12]}"
    with psycopg.connect(Settings().postgres_conninfo) as connection:
        connection.execute(f'CREATE SCHEMA "{schema}"')  # type: ignore[arg-type]
        connection.execute(f'SET search_path TO "{schema}"')  # type: ignore[arg-type]
        connection.commit()
        try:
            manifest.ensure_schema(connection)
            manifest.insert_raw_fetch(
                connection,
                RawFetchRecord(
                    sha256=SHA,
                    byte_size=1,
                    meta=RawDocumentMeta(
                        source="gus_bir1",
                        source_url="https://bir1.test",
                        content_type="application/xml",
                        fetched_at=T0,
                        http_headers={},
                        ingestion_run_id="a2",
                    ),
                ),
            )
            manifest.insert_entity_master(
                connection,
                EntityMasterRow(
                    krs=KRS,
                    nip=None,
                    regon="932989044",
                    name="PTB",
                    legal_form_code="117",
                    status="active",
                    pkd_codes=[Bir1PkdCode(code="4120Z", version="2007", predominant=True)],
                    pkd_predominant="4120Z",
                    source_document_hash=SHA,
                    pkd_source_document_hash=SHA,
                    known_from=date(2026, 9, 14),
                    ingestion_run_id="a2",
                ),
            )
            connection.commit()
            yield connection
        finally:
            connection.rollback()
            connection.execute(f'DROP SCHEMA "{schema}" CASCADE')  # type: ignore[arg-type]
            connection.commit()


def _import(conn: psycopg.Connection, data: bytes, store: InMemoryObjectStore, run: str):
    return import_har(
        "session.har",
        data,
        conn=conn,
        store=store,
        ingestion_run_id=run,
        document_types=load_document_types(),
        resolved=set(manifest.resolved_entities(conn)),
    )


@pytest.mark.integration
def test_import_indexes_details_downloads_and_reports_what_is_missing(conn: psycopg.Connection):
    store = InMemoryObjectStore()
    data = _session().search(OTHER_KRS, FULL_AT_50).build()

    report = _import(conn, data, store, "run-1")

    assert report.indexed == [KRS]
    assert report.not_resolved == [OTHER_KRS]
    assert (report.details, report.downloads) == (2, 1)
    assert report.missing == {KRS: [STATEMENT_2024]}  # the 2024 statement was not expanded
    assert report.problems == []
    row = conn.execute(
        """
        SELECT submission_date, rdf_type_name, file_name, sha256
        FROM filing_index WHERE document_ref = %s
        """,
        (STATEMENT_2025,),
    ).fetchone()
    stored = redact_download(STATEMENT_ZIP, {STATEMENT_2025: None}).data
    assert row == (
        date(2026, 6, 29),
        "Roczne sprawozdanie finansowe",
        STATEMENT_2025_TOKEN,
        sha256_hex(stored),
    )
    assert zipfile.ZipFile(io.BytesIO(store.get(raw_key(sha256_hex(stored))))).namelist() == [
        STATEMENT_2025_TOKEN
    ]
    sidecar = json.loads(store.get(sidecar_key(sha256_hex(stored))))
    assert (sidecar["fetch_tier"], sidecar["original_filename"]) == (
        "manual_har",
        STATEMENT_2025_TOKEN,
    )
    assert sidecar["fetched_at"].startswith("2026-09-16T09:00:05")


@pytest.mark.integration
def test_reimport_adds_nothing_and_a_later_capture_completes_the_entity(conn: psycopg.Connection):
    store = InMemoryObjectStore()
    _import(conn, _session().build(), store, "run-1")
    counts = manifest.table_counts(conn)
    objects = dict(store.objects)

    again = _import(conn, _session().build(), store, "run-2")

    assert manifest.table_counts(conn) == counts
    assert store.objects == objects
    assert (again.indexed, again.details, again.downloads) == ([], 0, 0)

    later = (
        HarBuilder()
        .expand(STATEMENT_2024, _detail(STATEMENT_2024, 18, "Roczne sprawozdanie finansowe"))
        .download(STATEMENT_2024, _zip("2024"))
        .search(KRS, FULL_AT_50)
        .build()
    )
    completed = _import(conn, later, store, "run-3")

    assert (completed.details, completed.downloads) == (1, 1)
    assert completed.missing == {}


@pytest.mark.integration
def test_problems_are_reported_without_stopping_the_import(conn: psycopg.Connection):
    data = (
        HarBuilder()
        .search(KRS, FULL_AT_50)
        .expand(STATEMENT_2025, '{"not": "a detail"}')
        .expand(AUDITOR_2025, _detail(AUDITOR_2025, 19, "Opinia biegłego rewidenta"))
        .build()
    )

    report = _import(conn, data, InMemoryObjectStore(), "run-1")

    assert report.indexed == [KRS]
    assert report.details == 1
    assert len(report.problems) == 1 and STATEMENT_2025 in report.problems[0]
    assert STATEMENT_2025 in report.missing[KRS]


@pytest.mark.integration
def test_later_capture_adds_documents_the_index_is_missing(conn: psycopg.Connection):
    store = InMemoryObjectStore()
    # An earlier import that only ever saw two of the ten documents.
    partial_capture = HarBuilder().search(KRS, STATEMENTS_ONLY).build()
    first = _import(conn, partial_capture, store, "run-1")
    assert first.indexed == [KRS]
    assert manifest.table_counts(conn)["filing_index"] == 2

    report = _import(conn, _session().build(), store, "run-2")

    assert report.indexed == []
    assert report.topped_up == {KRS: 8}
    assert manifest.table_counts(conn)["filing_index"] == 10
    counts = manifest.table_counts(conn)
    assert _import(conn, _session().build(), store, "run-3").topped_up == {}
    assert manifest.table_counts(conn) == counts


@pytest.mark.integration
def test_import_adds_corrections_and_files_the_bundle_once(conn: psycopg.Connection):
    store = InMemoryObjectStore()

    report = _import(conn, _corrected_session().build(), store, "run-1")

    assert report.problems == []
    assert (report.details, report.downloads) == (4, 2)  # 3 listed + 1 correction; 2 files
    assert report.missing == {}
    rows = conn.execute(
        """
        SELECT document_ref, correction_of, is_correction, submission_date, period_end, sha256
        FROM filing_index WHERE document_ref IN (%s, %s) ORDER BY correction_of NULLS FIRST
        """,
        (STATEMENT_2024, CORRECTION_2024),
    ).fetchall()
    assert rows == [
        (
            STATEMENT_2024,
            None,
            False,
            date(2026, 6, 29),
            date(2024, 12, 31),
            sha256_hex(STORED_BUNDLE),
        ),
        (
            CORRECTION_2024,
            STATEMENT_2024,
            True,
            date(2025, 11, 3),
            date(2024, 12, 31),
            sha256_hex(STORED_BUNDLE),
        ),
    ]
    members = zipfile.ZipFile(io.BytesIO(store.get(raw_key(sha256_hex(STORED_BUNDLE))))).namelist()
    assert members == [
        f"{file_token(STATEMENT_2024, None)}.xml",
        f"{file_token(CORRECTION_2024, None)}.xml",
    ]
    sidecar = json.loads(store.get(sidecar_key(sha256_hex(STORED_BUNDLE))))
    assert sidecar["original_filename"] is None  # several files in one ZIP
    counts = manifest.table_counts(conn)

    again = _import(conn, _corrected_session().build(), store, "run-2")

    assert (again.details, again.downloads, again.missing) == (0, 0, {})
    assert manifest.table_counts(conn) == counts


@pytest.mark.integration
def test_bundle_not_matching_the_corrections_list_is_refused(conn: psycopg.Connection):
    data = (
        HarBuilder()
        .search(KRS, FULL_AT_50)
        .expand(
            STATEMENT_2024,
            _detail(STATEMENT_2024, 18, "Roczne sprawozdanie finansowe"),
            {CORRECTION_2024: CORRECTION_DETAIL},
        )
        .download([STATEMENT_2024], BUNDLE_ZIP)  # the correction left out
        .build()
    )

    report = _import(conn, data, InMemoryObjectStore(), "run-1")

    assert len(report.problems) == 1 and "not attributable" in report.problems[0]
    assert set(report.missing[KRS]) >= {STATEMENT_2024, CORRECTION_2024}


def _expanded_bundle() -> HarBuilder:
    return (
        HarBuilder()
        .search(KRS, FULL_AT_50)
        .expand(
            STATEMENT_2024,
            _detail(STATEMENT_2024, 18, "Roczne sprawozdanie finansowe"),
            {CORRECTION_2024: CORRECTION_DETAIL},
        )
    )


@pytest.mark.integration
def test_a_bundle_expanded_earlier_is_named_from_a_capture_that_expands_it_again(
    conn: psycopg.Connection,
):
    """File names as received are never stored, so a later capture must carry them again."""
    store = InMemoryObjectStore()
    _import(conn, _expanded_bundle().build(), store, "run-1")  # expanded, not downloaded

    report = _import(conn, _expanded_bundle().download(BUNDLE, BUNDLE_ZIP).build(), store, "run-2")

    assert report.problems == [] and report.downloads == 1
    assert store.exists(raw_key(sha256_hex(STORED_BUNDLE)))


@pytest.mark.integration
def test_a_bundle_whose_names_are_not_in_the_capture_is_not_stored(conn: psycopg.Connection):
    store = InMemoryObjectStore()
    _import(conn, _expanded_bundle().build(), store, "run-1")
    stored_before = set(store.objects)

    only_the_file = HarBuilder().search(KRS, FULL_AT_50).download(BUNDLE, BUNDLE_ZIP).build()
    report = _import(conn, only_the_file, store, "run-2")

    assert len(report.problems) == 1 and "not in hand" in report.problems[0]
    assert set(store.objects) == stored_before
    assert STATEMENT_2024 in report.missing[KRS]
