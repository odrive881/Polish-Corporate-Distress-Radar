"""A3 parsing and flow over a fake `FilingBrowser`. No network, no browser.

Fixtures are RDF's own responses recorded in plan 0003 step A (see
`tests/fixtures/rdf/README.md`); multi-page and edge-case lists are derived
from the recorded page by rewriting only its paging metadata.
"""

import io
import json
import zipfile
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import pytest

from distress_radar.acquisition.base import PermanentSourceError
from distress_radar.acquisition.document_retrieval import (
    API_PREFIX,
    FETCH_TIER,
    A3Detail,
    A3Download,
    CircuitBreaker,
    DocumentView,
    FilingListing,
    RdfAccessBlocked,
    RdfCircuitOpen,
    RdfResponse,
    RdfShapeError,
    api_path,
    corrections_path,
    detail_path,
    detect_rdf_gate,
    download_filing,
    fetch_filing_detail,
    index_filings,
    is_blocked_url,
    load_document_types,
    parse_document_detail,
    parse_entity_lookup,
    parse_filing_list,
    retrieve_document,
)
from distress_radar.acquisition.models import FilingListEntry, PendingFilingDocument
from distress_radar.acquisition.raw_store import (
    InMemoryObjectStore,
    raw_key,
    sha256_hex,
    sidecar_key,
)

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "rdf"
WAF_PAGE = (FIXTURES / "waf_block_page_synthetic.html").read_bytes()
ENTITY_FOUND = (FIXTURES / "entity_found.json").read_bytes()
ENTITY_NOT_FOUND = (FIXTURES / "entity_not_found_synthetic.json").read_bytes()
LIST_PAGE0 = (FIXTURES / "filing_list_page0.json").read_bytes()
LIST_EMPTY = (FIXTURES / "filing_list_empty_synthetic.json").read_bytes()
CORRECTIONS = (FIXTURES / "document_corrections.json").read_bytes()
DETAIL = (FIXTURES / "document_detail.json").read_bytes()
DOWNLOAD_META = json.loads((FIXTURES / "document_download.meta.json").read_text(encoding="utf-8"))

NOW = datetime(2026, 9, 15, 12, 0, tzinfo=UTC)
KRS = "0000209396"  # the recorded entity
EMPTY_KRS = "0000277937"
UNKNOWN_KRS = "0000000009"
STATEMENT_REF = "kQL-7bDLHvl-dIGIeLuLlQ=="  # 2025 annual statement, rodzaj 18
AUDITOR_REF = "B2opwZt-Ik8Yg4luKMAqQA=="  # 2025 auditor report, rodzaj 19
# A statement with a correction, shaped like KRS 0000153402's 2022 filing: the
# correction is not a list row, only a second id in the original's expanded row.
ORIGINAL_REF = "_OdJUXJvvyacsRf0ul442g=="
CORRECTION_REF = "m7XcfLIW7RJVBLSp8XsIMg=="
API = "https://rdf.test" + API_PREFIX


def _zip(members: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_STORED) as archive:
        for name, data in members.items():
            archive.writestr(name, data)
    return buffer.getvalue()


# Stored uncompressed, so the text scan would see "captcha" if it ran.
DOCUMENT_ZIP = _zip({"sprawozdanie.xml": b"<?xml version='1.0'?><Uwagi>captcha</Uwagi>"})


def _recorded_items() -> list[dict[str, Any]]:
    return json.loads(LIST_PAGE0)["content"]


def _list_page(
    items: list[dict[str, Any]], *, page: int = 0, size: int = 50, pages: int = 1, total: int
) -> bytes:
    paging = {
        "numerStrony": page,
        "rozmiarStrony": size,
        "liczbaStron": pages,
        "calkowitaLiczbaObiektow": total,
    }
    return json.dumps({"content": items, "metadaneWynikow": paging}).encode()


FULL_LIST = _list_page(_recorded_items(), total=10)  # the recorded 10 rows as a complete list


def _detail_for(ref: str, type_id: int, type_name: str) -> bytes:
    detail = json.loads(DETAIL)
    detail["identyfikator"] = ref
    detail["rodzajDokumentu"] = {**detail["rodzajDokumentu"], "id": type_id, "nazwa": type_name}
    return json.dumps(detail).encode()


def _correction_detail() -> bytes:
    detail = json.loads(_detail_for(CORRECTION_REF, 18, "Roczne sprawozdanie finansowe"))
    detail.update(czyKorekta=True, dataDodania="2024-01-04", nazwaPliku="eSPR_report.xml")
    detail.update(okresSprawozdawczyPoczatek="2022-01-01", okresSprawozdawczyKoniec="2022-12-31")
    return json.dumps(detail).encode()


BUNDLE = [ORIGINAL_REF, CORRECTION_REF]


def _ok(url: str, body: bytes, **headers: str) -> RdfResponse:
    return RdfResponse(url=url, status_code=200, headers=headers, body=body)


def _json(path: str, body: bytes) -> RdfResponse:
    return _ok(API + path, body, **{"content-type": "application/json"})


def _waf(url: str) -> RdfResponse:
    return _ok(url, WAF_PAGE, **{"content-type": "text/html", "set-cookie": "incap_ses_1=x"})


class FakeFilingBrowser:
    """Serves recorded responses; `blocked` KRS / document refs get the WAF page."""

    browser_version = "fake-chromium-1.0"
    fetch_tier = FETCH_TIER

    def __init__(self, blocked: set[str] | None = None) -> None:
        self.blocked = blocked or set()
        self.calls: list[tuple[str, ...]] = []
        self.details = {
            STATEMENT_REF: (CORRECTIONS, DETAIL),
            AUDITOR_REF: (
                json.dumps([AUDITOR_REF]).encode(),
                _detail_for(AUDITOR_REF, 19, "Opinia biegłego rewidenta"),
            ),
            ORIGINAL_REF: (
                json.dumps(BUNDLE).encode(),
                _detail_for(ORIGINAL_REF, 18, "Roczne sprawozdanie finansowe"),
            ),
        }
        self.related = {CORRECTION_REF: _correction_detail()}
        self.bundles = {ORIGINAL_REF: BUNDLE}

    def open_filing_list(self, krs: str) -> FilingListing:
        self.calls.append(("list", krs))
        if krs in self.blocked:
            return FilingListing(entity=_waf(API + "podmioty/wyszukiwanie/dane-podstawowe"), pages=[])
        if krs == UNKNOWN_KRS:
            return FilingListing(entity=_json("podmioty", ENTITY_NOT_FOUND), pages=[])
        entity = ENTITY_FOUND.replace(KRS.encode(), krs.encode())
        body = LIST_EMPTY if krs == EMPTY_KRS else FULL_LIST
        return FilingListing(
            entity=_json("podmioty/wyszukiwanie/dane-podstawowe", entity),
            pages=[_json("dokumenty/wyszukiwanie", body)],
        )

    def open_document(self, krs: str, document_ref: str) -> DocumentView:
        self.calls.append(("document", krs, document_ref))
        if document_ref in self.blocked:
            return DocumentView(corrections=_waf(API + "x"), detail=_waf(API + "y"))
        corrections, detail = self.details[document_ref]
        others = [r for r in json.loads(corrections) if r != document_ref and r in self.related]
        return DocumentView(
            corrections=_json(corrections_path(document_ref), corrections),
            detail=_json(detail_path(document_ref), detail),
            related={r: _json(detail_path(r), self.related[r]) for r in others},
        )

    def download(self, krs: str, document_ref: str) -> RdfResponse:
        self.calls.append(("download", krs, document_ref))
        url = API + "dokumenty/tresc"
        if document_ref in self.blocked:
            return _waf(url)
        return RdfResponse(
            url=url,
            status_code=200,
            headers={
                "content-type": "application/octet-stream",
                "set-cookie": "visid_incap_1=secret; incap_ses_1=secret",
            },
            body=DOCUMENT_ZIP,
            request_body=json.dumps(self.bundles.get(document_ref, [document_ref])).encode(),
        )


def _index(krs: str, browser: FakeFilingBrowser | None = None, **kwargs: Any):
    store = InMemoryObjectStore()
    result = index_filings(
        krs,
        browser=browser or FakeFilingBrowser(),
        store=store,
        ingestion_run_id="run-1",
        breaker=kwargs.pop("breaker", CircuitBreaker()),
        clock=lambda: NOW,
        **kwargs,
    )
    return result, store


# --- gate check ------------------------------------------------------------------------------


def test_waf_block_page_fixture_is_detected():
    assert detect_rdf_gate(WAF_PAGE, "") == "imperva_incapsula_block_page"


@pytest.mark.parametrize(
    ("body", "cookie", "content_type", "js_executed", "reason"),
    [
        (b"", "", "", False, "empty_body"),
        (
            b"<html>short</html>",
            "incap_ses_9=abc",
            "text/html",
            False,
            "imperva_incapsula_cookie_challenge",
        ),
        (b"<html>short</html>", "incap_ses_9=abc", "", False, "imperva_incapsula_cookie_challenge"),
        (CORRECTIONS, "incap_ses_9=abc", "application/json", False, None),  # tiny real JSON
        (DOCUMENT_ZIP, "incap_ses_9=abc", "application/octet-stream", False, None),
        (DOCUMENT_ZIP, "", "", False, None),  # "captcha" inside a document is not a gate
        (b"%PDF-1.7 captcha", "", "application/pdf", False, None),
        (b"<div class='g-recaptcha'>CAPTCHA</div>", "", "text/html", False, "captcha"),
        (
            b"<noscript>Please enable JavaScript</noscript>",
            "",
            "text/html",
            False,
            "javascript_required",
        ),
        (b"<noscript>Please enable JavaScript</noscript><app-root>", "", "text/html", True, None),
    ],
)
def test_gate_reasons(
    body: bytes, cookie: str, content_type: str, js_executed: bool, reason: str | None
):
    assert (
        detect_rdf_gate(body, cookie, content_type=content_type, js_executed=js_executed) == reason
    )


def test_recorded_download_is_a_zip_the_gate_lets_through():
    assert DOWNLOAD_META["response"]["magic_hex"].startswith(b"PK\x03\x04".hex())
    assert DOWNLOAD_META["request"]["body"] == json.dumps([STATEMENT_REF])


# --- URLs ------------------------------------------------------------------------------------


def test_api_paths_are_compared_decoded():
    recorded = (
        "https://rdf-przegladarka.ms.gov.pl" + API_PREFIX + "dokumenty/kQL-7bDLHvl-dIGIeLuLlQ%3D%3D"
    )
    assert api_path(recorded) == detail_path(STATEMENT_REF)
    assert api_path(recorded + "/id-dokumentu-i-korekt") == corrections_path(STATEMENT_REF)
    assert api_path("https://rdf-przegladarka.ms.gov.pl/main.js") is None


def test_submission_view_is_blocked():
    assert is_blocked_url(API + "zgloszenie/R3IkLYvVBLqXYJJIgpIa_A%3D%3D")
    assert not is_blocked_url(API + detail_path(STATEMENT_REF))
    assert not is_blocked_url("https://rdf.test/zgloszenie/1")  # outside the API


# --- parsing ---------------------------------------------------------------------------------


def test_entity_lookup():
    assert parse_entity_lookup(KRS, ENTITY_FOUND) is True
    assert parse_entity_lookup(UNKNOWN_KRS, ENTITY_NOT_FOUND) is False
    with pytest.raises(RdfShapeError, match="answered for"):
        parse_entity_lookup(EMPTY_KRS, ENTITY_FOUND)


def test_complete_list_parses_every_row():
    entries = parse_filing_list(KRS, [FULL_LIST])

    assert len(entries) == 10
    assert entries[3] == FilingListEntry(
        krs=KRS,
        document_ref=STATEMENT_REF,
        rdf_type_code="18",
        status="NIEUSUNIETY",
        period_start=date(2025, 1, 1),
        period_end=date(2025, 12, 31),
        deleted_on=None,  # "" in the list
    )
    assert [e.rdf_type_code for e in entries] == ["4", "3", "20", "18", "19", "3", "4", "19", "20", "18"]


def test_recorded_first_page_alone_is_refused_as_incomplete():
    # The recorded page is page 0 of 5: parsing it alone would silently drop 39 documents.
    with pytest.raises(RdfShapeError, match="pages"):
        parse_filing_list(KRS, [LIST_PAGE0])


def test_multi_page_list_parses_in_page_order():
    items = _recorded_items()
    pages = [
        _list_page(items[:5], page=0, size=5, pages=2, total=10),
        _list_page(items[5:], page=1, size=5, pages=2, total=10),
    ]

    entries = parse_filing_list(KRS, pages)

    assert [e.document_ref for e in entries] == [i["id"] for i in items]


@pytest.mark.parametrize(
    ("pages", "match"),
    [
        ([], "no pages"),
        (
            [
                _list_page(_recorded_items()[:5], page=0, size=5, pages=2, total=10),
                _list_page(_recorded_items()[:5], page=0, size=5, pages=2, total=10),
            ],
            "pages",
        ),
        ([_list_page(_recorded_items() + _recorded_items()[:1], total=11)], "duplicate"),
        ([_list_page(_recorded_items()[:9], total=10)], "RDF reports 10"),
        ([_list_page([{**_recorded_items()[0], "status": "ARCHIWALNY"}], total=1)], "status"),
        ([_list_page([{**_recorded_items()[0], "okresSprawozdawczyKoniec": "31.12.2025"}], total=1)], "ISO date"),
        ([b"<html>not json</html>"], "not JSON"),
        ([json.dumps({"items": []}).encode()], "missing"),
    ],
)
def test_list_shape_changes_are_refused(pages: list[bytes], match: str):
    with pytest.raises(RdfShapeError, match=match):
        parse_filing_list(KRS, pages)


def test_shape_error_is_permanent_and_reason_coded():
    assert issubclass(RdfShapeError, PermanentSourceError)
    assert RdfShapeError.reason_code == "rdf_response_shape_changed"


def test_empty_list_parses_to_nothing():
    assert parse_filing_list(EMPTY_KRS, [LIST_EMPTY]) == []


def test_recorded_detail_carries_known_from():
    detail = parse_document_detail(STATEMENT_REF, CORRECTIONS, DETAIL)

    assert detail.submission_date == date(2026, 6, 29)
    assert (detail.rdf_type_id, detail.rdf_type_name) == ("18", "Roczne sprawozdanie finansowe")
    assert detail.prepared_date == date(2026, 5, 28)
    assert (detail.is_correction, detail.is_ifrs) == (False, False)
    assert detail.file_name == "sprawozdanie finansowe za rok 2025 korekta.xml"
    assert detail.correction_refs == [STATEMENT_REF]


def test_detail_for_another_document_is_refused():
    with pytest.raises(RdfShapeError, match="does not contain"):
        parse_document_detail(AUDITOR_REF, CORRECTIONS, DETAIL)
    with pytest.raises(RdfShapeError, match="answered for"):
        parse_document_detail(STATEMENT_REF, CORRECTIONS, _detail_for(AUDITOR_REF, 19, "x"))


def test_pre_2018_detail_without_ifrs_flag_parses():
    detail = json.loads(DETAIL)
    detail["czyMSR"] = None  # as RDF sends it for 2017 filings
    detail["rodzajDokumentu"] = {**detail["rodzajDokumentu"], "id": 1}

    parsed = parse_document_detail(STATEMENT_REF, CORRECTIONS, json.dumps(detail).encode())

    assert (parsed.rdf_type_id, parsed.is_ifrs) == ("1", None)
    with pytest.raises(RdfShapeError, match="czyMSR"):
        detail["czyMSR"] = "Nie"
        parse_document_detail(STATEMENT_REF, CORRECTIONS, json.dumps(detail).encode())


def test_corrections_list_must_not_repeat_ids():
    with pytest.raises(RdfShapeError, match="repeats"):
        parse_document_detail(
            ORIGINAL_REF,
            json.dumps([ORIGINAL_REF, ORIGINAL_REF]).encode(),
            _detail_for(ORIGINAL_REF, 18, "x"),
        )


def test_detail_without_submission_date_is_refused_not_imputed():
    detail = json.loads(DETAIL)
    detail["dataDodania"] = None
    with pytest.raises(RdfShapeError, match="dataDodania"):
        parse_document_detail(STATEMENT_REF, CORRECTIONS, json.dumps(detail).encode())


def test_document_type_config_matches_the_capture():
    types = load_document_types()

    assert types.download_codes == ["18"]  # pre-2018 statements (code 1) are out of v1 scope
    assert types.types["1"].name == types.types["18"].name
    assert types.types["18"].name == json.loads(DETAIL)["rodzajDokumentu"]["nazwa"]
    listed_codes = {item["rodzaj"] for item in _recorded_items()}
    assert listed_codes <= set(types.types)
    dom = (FIXTURES / "dom_results.html").read_text(encoding="utf-8")
    assert all(t.name in dom for t in types.types.values())


# --- filing list flow ------------------------------------------------------------------------


def test_filing_list_becomes_index_rows_without_detail_columns():
    result, store = _index(KRS)

    assert result.quarantine == []
    assert len(result.entries) == 10
    row = result.entries[3]
    assert (row.document_ref, row.rdf_type_code, row.period_end) == (
        STATEMENT_REF,
        "18",
        date(2025, 12, 31),
    )
    assert all(r.ingestion_run_id == "run-1" and r.discovered_at == NOW for r in result.entries)
    # lineage: the entity lookup and every list page are raw documents
    assert [f.sha256 for f in result.raw_fetches] == [sha256_hex(ENTITY_FOUND), sha256_hex(FULL_LIST)]
    assert store.get(raw_key(sha256_hex(FULL_LIST))) == FULL_LIST
    fetch = result.raw_fetches[1]
    assert (fetch.meta.source, fetch.meta.fetch_tier) == ("rdf", FETCH_TIER)
    assert fetch.meta.browser_version == "fake-chromium-1.0"


def test_raw_write_happens_before_list_parse():
    store = InMemoryObjectStore()
    seen: list[bool] = []

    def recording_parse(krs: str, pages: list[bytes]) -> list[FilingListEntry]:
        seen.extend(
            store.exists(raw_key(sha256_hex(body))) and store.exists(sidecar_key(sha256_hex(body)))
            for body in [ENTITY_FOUND, *pages]
        )
        return parse_filing_list(krs, pages)

    index_filings(
        KRS,
        browser=FakeFilingBrowser(),
        store=store,
        ingestion_run_id="run-1",
        breaker=CircuitBreaker(),
        clock=lambda: NOW,
        parse=recording_parse,
    )

    assert seen == [True, True]


def test_unknown_entity_is_quarantined_not_raised():
    def must_not_parse(krs: str, pages: list[bytes]) -> list[FilingListEntry]:
        raise AssertionError("no list to parse")

    result, store = _index(UNKNOWN_KRS, parse=must_not_parse)

    assert result.entries == []
    [row] = result.quarantine
    assert (row.stage, row.entity_key, row.reason_code) == ("A3", UNKNOWN_KRS, "rdf_entity_not_found")
    assert row.source_document_hash == sha256_hex(ENTITY_NOT_FOUND)
    assert store.exists(raw_key(row.source_document_hash))


def test_no_filings_is_quarantined_not_raised():
    result, store = _index(EMPTY_KRS)

    assert result.entries == []
    [row] = result.quarantine
    assert (row.stage, row.entity_key, row.reason_code) == ("A3", EMPTY_KRS, "no_rdf_filings")
    assert row.source_document_hash == sha256_hex(LIST_EMPTY)
    assert store.exists(raw_key(row.source_document_hash))


def test_waf_page_on_list_is_blocked_and_nothing_is_stored():
    breaker = CircuitBreaker()

    with pytest.raises(RdfAccessBlocked, match="imperva_incapsula_block_page") as info:
        _index(KRS, FakeFilingBrowser(blocked={KRS}), breaker=breaker)

    assert info.value.reason_code == "rdf_access_blocked"
    assert breaker.consecutive_blocks == 1


def test_index_is_idempotent():
    first, store = _index(KRS)
    objects = dict(store.objects)

    second = index_filings(
        KRS,
        browser=FakeFilingBrowser(),
        store=store,
        ingestion_run_id="run-1",
        breaker=CircuitBreaker(),
        clock=lambda: NOW,
    )

    assert store.objects == objects
    assert second == first


# --- details and downloads -------------------------------------------------------------------


def test_detail_is_stored_raw_then_parsed():
    store = InMemoryObjectStore()

    fetched = fetch_filing_detail(
        KRS,
        STATEMENT_REF,
        browser=FakeFilingBrowser(),
        store=store,
        ingestion_run_id="run-1",
        breaker=CircuitBreaker(),
        clock=lambda: NOW,
    )

    assert fetched.detail.submission_date == date(2026, 6, 29)
    assert fetched.corrections_fetch.sha256 == sha256_hex(CORRECTIONS)
    assert fetched.detail_fetch.sha256 == sha256_hex(DETAIL)
    assert store.get(raw_key(sha256_hex(DETAIL))) == DETAIL


def test_unparseable_detail_is_still_stored():
    browser = FakeFilingBrowser()
    browser.details[STATEMENT_REF] = (CORRECTIONS, b'{"unexpected": true}')
    store = InMemoryObjectStore()

    with pytest.raises(RdfShapeError):
        fetch_filing_detail(
            KRS,
            STATEMENT_REF,
            browser=browser,
            store=store,
            ingestion_run_id="run-1",
            breaker=CircuitBreaker(),
            clock=lambda: NOW,
        )

    assert store.exists(raw_key(sha256_hex(b'{"unexpected": true}')))


def test_download_stores_bytes_unmodified_without_session_cookies():
    store = InMemoryObjectStore()

    download = download_filing(
        KRS,
        STATEMENT_REF,
        browser=FakeFilingBrowser(),
        store=store,
        ingestion_run_id="run-1",
        breaker=CircuitBreaker(),
        original_filename="sprawozdanie.xml",
        clock=lambda: NOW,
    )

    assert (download.krs, download.document_refs) == (KRS, [STATEMENT_REF])
    digest = download.raw_fetch.sha256
    assert digest == sha256_hex(DOCUMENT_ZIP)
    assert store.get(raw_key(digest)) == DOCUMENT_ZIP
    sidecar = json.loads(store.get(sidecar_key(digest)))
    assert sidecar["source"] == "rdf"
    assert sidecar["source_url"].endswith(API_PREFIX + "dokumenty/tresc")
    assert sidecar["fetch_tier"] == "playwright"
    assert sidecar["content_type"] == "application/octet-stream"
    assert sidecar["original_filename"] == "sprawozdanie.xml"  # RDF sends no content-disposition
    assert "set-cookie" not in sidecar["http_headers"]
    assert "incap" not in json.dumps(sidecar)


def test_waf_page_on_download_is_blocked():
    store = InMemoryObjectStore()

    with pytest.raises(RdfAccessBlocked):
        download_filing(
            KRS,
            STATEMENT_REF,
            browser=FakeFilingBrowser(blocked={STATEMENT_REF}),
            store=store,
            ingestion_run_id="run-1",
            breaker=CircuitBreaker(),
            clock=lambda: NOW,
        )

    assert store.objects == {}


def _pending(
    ref: str,
    code: str,
    *,
    type_id: str | None = None,
    downloaded: bool = False,
    correction_of: str | None = None,
    bundle: list[str] | None = None,
) -> PendingFilingDocument:
    return PendingFilingDocument(
        krs=KRS,
        document_ref=ref,
        rdf_type_code=code,
        status="NIEUSUNIETY",
        rdf_type_id=type_id,
        file_name=None if type_id is None else "known.xml",
        downloaded=downloaded,
        needs_detail=type_id is None,
        correction_of=correction_of,
        bundle=bundle if bundle is not None else ([] if type_id is None else [ref]),
    )


def _retrieve(pending: PendingFilingDocument, browser: FakeFilingBrowser) -> list[object]:
    events: list[object] = []
    retrieve_document(
        pending,
        browser=browser,
        store=InMemoryObjectStore(),
        ingestion_run_id="run-1",
        breaker=CircuitBreaker(),
        document_types=load_document_types(),
        on_detail=events.append,
        on_download=events.append,
        clock=lambda: NOW,
    )
    return events


def test_statement_gets_detail_then_download():
    browser = FakeFilingBrowser()

    events = _retrieve(_pending(STATEMENT_REF, "18"), browser)

    assert [type(e) for e in events] == [A3Detail, A3Download]
    download = events[1]
    assert isinstance(download, A3Download)
    assert download.raw_fetch.meta.original_filename == (
        "sprawozdanie finansowe za rok 2025 korekta.xml"
    )
    assert [c[0] for c in browser.calls] == ["document", "download"]


def test_out_of_scope_type_gets_detail_only():
    browser = FakeFilingBrowser()

    events = _retrieve(_pending(AUDITOR_REF, "19"), browser)

    assert [type(e) for e in events] == [A3Detail]
    assert [c[0] for c in browser.calls] == ["document"]


def test_known_detail_skips_straight_to_download():
    browser = FakeFilingBrowser()

    events = _retrieve(_pending(STATEMENT_REF, "18", type_id="18"), browser)

    assert [type(e) for e in events] == [A3Download]
    assert [c[0] for c in browser.calls] == ["download"]


def test_list_and_detail_type_disagreeing_is_refused_after_recording_the_detail():
    browser = FakeFilingBrowser()
    events: list[object] = []

    with pytest.raises(RdfShapeError, match="list type 19, detail type 18"):
        retrieve_document(
            _pending(STATEMENT_REF, "19"),
            browser=browser,
            store=InMemoryObjectStore(),
            ingestion_run_id="run-1",
            breaker=CircuitBreaker(),
            document_types=load_document_types(),
            on_detail=events.append,
            on_download=events.append,
            clock=lambda: NOW,
        )

    assert [type(e) for e in events] == [A3Detail]
    assert "download" not in [c[0] for c in browser.calls]


def test_failed_download_keeps_the_detail():
    browser = FakeFilingBrowser()
    events: list[object] = []

    def blocked_download(krs: str, document_ref: str) -> RdfResponse:
        return _waf(API + "dokumenty/tresc")

    browser.download = blocked_download  # type: ignore[method-assign]
    with pytest.raises(RdfAccessBlocked):
        retrieve_document(
            _pending(STATEMENT_REF, "18"),
            browser=browser,
            store=InMemoryObjectStore(),
            ingestion_run_id="run-1",
            breaker=CircuitBreaker(),
            document_types=load_document_types(),
            on_detail=events.append,
            on_download=events.append,
            clock=lambda: NOW,
        )

    assert [type(e) for e in events] == [A3Detail]


# --- circuit breaker -------------------------------------------------------------------------


def test_circuit_breaker_trips_after_consecutive_blocks_and_stops_the_run():
    browser = FakeFilingBrowser(blocked={"0000000001", "0000000002", "0000000003"})
    breaker = CircuitBreaker(threshold=3)

    for krs in ("0000000001", "0000000002"):
        with pytest.raises(RdfAccessBlocked):
            _index(krs, browser, breaker=breaker)
    with pytest.raises(RdfCircuitOpen):
        _index("0000000003", browser, breaker=breaker)

    # once open, nothing more reaches RDF — not even an entity that would succeed
    calls_before = len(browser.calls)
    with pytest.raises(RdfCircuitOpen):
        _index(KRS, browser, breaker=breaker)
    assert len(browser.calls) == calls_before


def test_circuit_breaker_counts_only_consecutive_blocks():
    browser = FakeFilingBrowser(blocked={"0000000001", "0000000002"})
    breaker = CircuitBreaker(threshold=3)

    for krs in ("0000000001", "0000000002"):
        with pytest.raises(RdfAccessBlocked):
            _index(krs, browser, breaker=breaker)
    _index(KRS, browser, breaker=breaker)  # a real response resets the count

    assert breaker.consecutive_blocks == 0
    with pytest.raises(RdfAccessBlocked):
        _index("0000000001", browser, breaker=breaker)
    assert not breaker.is_open


def test_blocked_detail_counts_towards_the_breaker():
    breaker = CircuitBreaker(threshold=1)

    with pytest.raises(RdfCircuitOpen):
        fetch_filing_detail(
            KRS,
            STATEMENT_REF,
            browser=FakeFilingBrowser(blocked={STATEMENT_REF}),
            store=InMemoryObjectStore(),
            ingestion_run_id="run-1",
            breaker=breaker,
            clock=lambda: NOW,
        )


# --- corrections -----------------------------------------------------------------------------


def test_expanded_row_yields_the_corrections_details():
    store = InMemoryObjectStore()

    fetched = fetch_filing_detail(
        KRS,
        ORIGINAL_REF,
        browser=FakeFilingBrowser(),
        store=store,
        ingestion_run_id="run-1",
        breaker=CircuitBreaker(),
        clock=lambda: NOW,
    )

    assert fetched.detail.correction_refs == BUNDLE
    assert fetched.detail.is_correction is False
    [(correction, fetch)] = fetched.related
    assert (correction.document_ref, correction.is_correction) == (CORRECTION_REF, True)
    assert correction.submission_date == date(2024, 1, 4)  # its own known_from
    assert (correction.period_start, correction.period_end) == (date(2022, 1, 1), date(2022, 12, 31))
    assert fetch.sha256 == sha256_hex(_correction_detail())
    assert store.exists(raw_key(fetch.sha256))


def test_expanded_row_missing_a_correction_detail_is_refused():
    browser = FakeFilingBrowser()
    browser.related = {}

    with pytest.raises(RdfShapeError, match="without the details"):
        fetch_filing_detail(
            KRS,
            ORIGINAL_REF,
            browser=browser,
            store=InMemoryObjectStore(),
            ingestion_run_id="run-1",
            breaker=CircuitBreaker(),
            clock=lambda: NOW,
        )


def test_bundle_download_covers_the_document_and_its_corrections():
    download = download_filing(
        KRS,
        ORIGINAL_REF,
        browser=FakeFilingBrowser(),
        store=InMemoryObjectStore(),
        ingestion_run_id="run-1",
        breaker=CircuitBreaker(),
        bundle=BUNDLE,
        original_filename="original.xml",
        clock=lambda: NOW,
    )

    assert download.document_refs == BUNDLE
    assert download.raw_fetch.meta.original_filename is None  # the ZIP holds several files


def test_download_covering_other_documents_than_expected_stores_nothing():
    store = InMemoryObjectStore()

    with pytest.raises(PermanentSourceError, match="not attributable"):
        download_filing(
            KRS,
            ORIGINAL_REF,  # RDF bundles its correction, but the caller expected it alone
            browser=FakeFilingBrowser(),
            store=store,
            ingestion_run_id="run-1",
            breaker=CircuitBreaker(),
            clock=lambda: NOW,
        )

    assert store.objects == {}


def test_correction_is_downloaded_through_the_document_it_corrects():
    browser = FakeFilingBrowser()
    pending = _pending(
        CORRECTION_REF, "18", type_id="18", correction_of=ORIGINAL_REF, bundle=BUNDLE
    )

    events = _retrieve(pending, browser)

    assert browser.calls == [("download", KRS, ORIGINAL_REF)]
    [download] = events
    assert isinstance(download, A3Download) and download.document_refs == BUNDLE


def test_statement_with_a_correction_gets_detail_then_bundle():
    browser = FakeFilingBrowser()

    events = _retrieve(_pending(ORIGINAL_REF, "18"), browser)

    assert [type(e) for e in events] == [A3Detail, A3Download]
    detail, download = events
    assert isinstance(detail, A3Detail) and len(detail.related) == 1
    assert isinstance(download, A3Download) and download.document_refs == BUNDLE
