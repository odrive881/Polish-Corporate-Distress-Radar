"""A3, manual tier — import RDF documents from a person's browser session (HAR).

RDF answers an automated browser with an hCaptcha challenge (ADR 0007, live
result 2026-09-16), so for now documents are fetched by a person, in an
ordinary browser, with DevTools recording. The saved HAR holds exactly what the
SPA received: the entity lookup, the filing list, each expanded row's detail,
and the bytes of every "Pobierz dokumenty" download. This module reads those
API responses back and runs them through the same A3 flow as the Playwright
tier (`document_retrieval`), so lineage, raw-first storage, gate checks, and
parsing are shared. Sidecars record `fetch_tier: manual_har`, the capture time
as `fetched_at`, and the recorded request URL.

Only RDF API entries are read. The HAR's other entries (scripts, fonts,
Imperva's bot check, cookies) are ignored and never stored. The procedure for
the person capturing is in `README.md` § "Manual RDF capture".
"""

from __future__ import annotations

import base64
import json
import logging
from collections.abc import Callable, Collection, Iterable
from dataclasses import dataclass, field
from datetime import datetime
from functools import partial
from typing import Any, cast

from psycopg import Connection

from distress_radar.acquisition import manifest
from distress_radar.acquisition.base import PermanentSourceError, SourceError
from distress_radar.acquisition.document_retrieval import (
    CORRECTIONS_SUFFIX,
    DOWNLOAD_PATH,
    ENTITY_PATH,
    LIST_PATH,
    CircuitBreaker,
    DocumentView,
    FilingListing,
    RdfResponse,
    RdfShapeError,
    api_path,
    download_filing,
    fetch_filing_detail,
    index_filings,
    read_list_page,
)
from distress_radar.acquisition.models import RdfDocumentTypes
from distress_radar.acquisition.raw_store import ObjectStore

logger = logging.getLogger(__name__)

FETCH_TIER = "manual_har"


class NotCaptured(PermanentSourceError):
    """The HAR does not contain what this step needs."""

    reason_code = "rdf_not_in_capture"


@dataclass(frozen=True)
class HarExchange:
    """One recorded RDF API request and its response."""

    started: datetime
    method: str
    url: str
    path: str  # below `API_PREFIX`, URL-decoded
    request_body: bytes | None
    response: RdfResponse


def _body(content: dict[str, Any]) -> bytes:
    text = content.get("text")
    if text is None:
        return b""
    if content.get("encoding") == "base64":
        return base64.b64decode(text)
    return str(text).encode("utf-8")


def read_har(data: bytes) -> tuple[list[HarExchange], str | None]:
    """RDF API exchanges in the HAR, in capture order, plus the recording browser.

    Raises `PermanentSourceError` if the file is not a HAR.
    """
    try:
        document = json.loads(data)
        entries = cast(list[dict[str, Any]], document["log"]["entries"])
    except (UnicodeDecodeError, json.JSONDecodeError, KeyError, TypeError) as exc:
        raise PermanentSourceError(f"not a HAR file: {exc}") from exc

    exchanges: list[HarExchange] = []
    user_agent: str | None = None
    for entry in entries:
        request, response = entry["request"], entry["response"]
        path = api_path(request["url"])
        if path is None:
            continue
        request_headers = {h["name"].lower(): h["value"] for h in request.get("headers", [])}
        user_agent = user_agent or request_headers.get("user-agent")
        post = request.get("postData")
        request_body = str(post["text"]).encode("utf-8") if post and "text" in post else None
        headers: dict[str, str] = {}
        for header in response.get("headers", []):
            name = header["name"].lower()
            if name != "set-cookie":
                headers[name] = header["value"]
        exchanges.append(
            HarExchange(
                started=datetime.fromisoformat(entry["startedDateTime"]),
                method=request["method"],
                url=request["url"],
                path=path,
                request_body=request_body,
                response=RdfResponse(
                    url=request["url"],
                    status_code=int(response["status"]),
                    headers=headers,
                    body=_body(response.get("content", {})),
                    request_body=request_body,
                ),
            )
        )
    exchanges.sort(key=lambda e: e.started)
    creator = cast(dict[str, Any], document["log"].get("creator") or {})
    recorder = user_agent or " ".join(
        str(creator[k]) for k in ("name", "version") if creator.get(k)
    )
    return exchanges, recorder or None


def _json_or_none(body: bytes | None) -> Any:
    if body is None:
        return None
    try:
        return json.loads(body)
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None


def _document_ref(path: str) -> str | None:
    """`{ref}` for a detail path `dokumenty/{ref}`; `None` for other paths."""
    if not path.startswith("dokumenty/"):
        return None
    ref = path.removeprefix("dokumenty/")
    if ref in {"wyszukiwanie", "tresc", "rodzajeDokWyszukiwanie", "szczegolyDokumentu"}:
        return None
    return ref if "/" not in ref else None


@dataclass
class HarCapture:
    """What one HAR recorded, organised by KRS and document id."""

    recorder: str | None
    searches: dict[str, list[list[HarExchange]]] = field(
        default_factory=dict[str, list[list[HarExchange]]]
    )  # krs -> [entity lookup, list responses...] per search
    details: dict[str, HarExchange] = field(default_factory=dict[str, HarExchange])
    corrections: dict[str, HarExchange] = field(default_factory=dict[str, HarExchange])
    downloads: dict[str, HarExchange] = field(default_factory=dict[str, HarExchange])
    skipped: list[str] = field(default_factory=list[str])

    @classmethod
    def from_har(cls, data: bytes) -> HarCapture:
        exchanges, recorder = read_har(data)
        capture = cls(recorder=recorder)
        current: list[HarExchange] | None = None
        for exchange in exchanges:
            path = exchange.path
            if path == ENTITY_PATH:
                lookup = _json_or_none(exchange.request_body)
                krs: object = (
                    cast(dict[str, Any], lookup).get("numerKRS") if isinstance(lookup, dict) else None
                )
                if not isinstance(krs, str):
                    capture.skipped.append(f"entity lookup without a KRS at {exchange.started}")
                    current = None
                    continue
                current = [exchange]
                capture.searches.setdefault(krs, []).append(current)
            elif path == LIST_PATH:
                if current is None:
                    capture.skipped.append(f"filing list before any search at {exchange.started}")
                else:
                    current.append(exchange)
            elif path.endswith(CORRECTIONS_SUFFIX):
                ref = path.removeprefix("dokumenty/").removesuffix(CORRECTIONS_SUFFIX)
                capture.corrections[ref] = exchange  # latest wins
            elif path == DOWNLOAD_PATH:
                requested = _json_or_none(exchange.request_body)
                if isinstance(requested, list) and len(cast(list[object], requested)) == 1:
                    ref = cast(list[object], requested)[0]
                    if isinstance(ref, str):
                        capture.downloads[ref] = exchange
                        continue
                capture.skipped.append(
                    f"download of {requested!r} at {exchange.started}: not one document"
                )
            elif (ref := _document_ref(path)) is not None:
                capture.details[ref] = exchange
        return capture

    @property
    def krs_numbers(self) -> list[str]:
        return sorted(self.searches)


# Keys of an unfiltered `dokumenty/wyszukiwanie` request. Anything else with a
# value (`rodzajDokumentuNazwa`, `nazwa`, `okresSprawozdawczyOd`, `status`, ...)
# is a filter from the SPA's filter panel, and its answer is not the full list.
_UNFILTERED_LIST_KEYS = frozenset({"metadaneStronicowania", "nrKRS"})


def is_filtered_list(exchange: HarExchange) -> bool:
    """Whether a list request narrowed the list. Unreadable requests count as filtered."""
    request = _json_or_none(exchange.request_body)
    if not isinstance(request, dict):
        return True
    return any(
        value not in (None, "", [])
        for key, value in cast(dict[str, Any], request).items()
        if key not in _UNFILTERED_LIST_KEYS
    )


def _complete_pages(krs: str, lists: list[HarExchange]) -> list[HarExchange]:
    """The largest page size whose unfiltered pages were all captured, one response per page.

    Filtered answers are skipped: a complete-looking filtered list would
    silently drop every document the filter hid.
    """
    by_size: dict[int, dict[int, HarExchange]] = {}
    counts: dict[int, int] = {}
    for exchange in lists:
        if is_filtered_list(exchange):
            continue
        try:
            page = read_list_page(exchange.response.body)
        except RdfShapeError:
            continue  # the flow's gate check and parser report bad pages when they matter
        by_size.setdefault(page.page_size, {})[page.page_number] = exchange  # latest wins
        counts[page.page_size] = page.page_count
    for size in sorted(by_size, reverse=True):
        wanted = range(max(counts[size], 1))
        if all(n in by_size[size] for n in wanted):
            return [by_size[size][n] for n in wanted]
    raise NotCaptured(
        f"the capture has no complete, unfiltered filing list for {krs}: set the largest "
        "rows-per-page (or open every page) before using the filter or exporting the HAR"
    )


class HarFilingBrowser:
    """A `FilingBrowser` that answers from a recorded session instead of the network."""

    def __init__(self, capture: HarCapture) -> None:
        self._capture = capture
        self._last: datetime | None = None

    @property
    def browser_version(self) -> str | None:
        return self._capture.recorder

    @property
    def fetch_tier(self) -> str:
        return FETCH_TIER

    def captured_at(self) -> datetime:
        """When the exchanges returned by the latest call were recorded (a flow `clock`)."""
        if self._last is None:
            raise RuntimeError("no exchange returned yet")
        return self._last

    def _returned(self, exchanges: Iterable[HarExchange]) -> None:
        self._last = max(e.started for e in exchanges)

    def open_filing_list(self, krs: str) -> FilingListing:
        searches = self._capture.searches.get(krs)
        if not searches:
            raise NotCaptured(f"the capture has no search for {krs}")
        # Prefer the latest search that found the entity; else the latest one.
        found = [s for s in searches if len(s) > 1]
        search = found[-1] if found else searches[-1]
        entity = search[0]
        pages = _complete_pages(krs, search[1:]) if len(search) > 1 else []
        self._returned([entity, *pages])
        return FilingListing(entity=entity.response, pages=[p.response for p in pages])

    def open_document(self, krs: str, document_ref: str) -> DocumentView:
        corrections = self._capture.corrections.get(document_ref)
        detail = self._capture.details.get(document_ref)
        if corrections is None or detail is None:
            raise NotCaptured(f"the capture did not expand document {document_ref}")
        answer = _json_or_none(detail.response.body)
        if isinstance(answer, dict):
            owner = cast(dict[str, Any], answer).get("nrKRS")
            if owner is not None and owner != krs:
                raise RdfShapeError(f"document {document_ref} belongs to {owner}, not {krs}")
        self._returned([corrections, detail])
        return DocumentView(corrections=corrections.response, detail=detail.response)

    def has_detail(self, document_ref: str) -> bool:
        return document_ref in self._capture.details and document_ref in self._capture.corrections

    def has_download(self, document_ref: str) -> bool:
        return document_ref in self._capture.downloads

    def download(self, krs: str, document_ref: str) -> RdfResponse:
        exchange = self._capture.downloads.get(document_ref)
        if exchange is None:
            raise NotCaptured(f"the capture did not download document {document_ref}")
        self._returned([exchange])
        return exchange.response


def _listed_refs(browser: HarFilingBrowser, krs: str) -> set[str]:
    """Document ids in the capture's complete list for `krs` (empty if RDF did not know it)."""
    listing = browser.open_filing_list(krs)
    return {ref for page in listing.pages for ref in read_list_page(page.body).document_refs}


@dataclass
class HarImportReport:
    """What one HAR contributed, and what it left for the next capture."""

    name: str
    indexed: list[str] = field(default_factory=list[str])
    topped_up: dict[str, int] = field(default_factory=dict[str, int])  # krs -> new documents
    quarantined: list[str] = field(default_factory=list[str])
    details: int = 0
    downloads: int = 0
    not_resolved: list[str] = field(default_factory=list[str])  # searched, not in entity_master
    missing: dict[str, list[str]] = field(default_factory=dict[str, list[str]])  # krs -> refs
    problems: list[str] = field(default_factory=list[str])


def import_har(
    name: str,
    data: bytes,
    *,
    conn: Connection,
    store: ObjectStore,
    ingestion_run_id: str,
    document_types: RdfDocumentTypes,
    resolved: Collection[str],
) -> HarImportReport:
    """Import one HAR into the manifest; commits after every recorded step.

    Only work still pending is done — unindexed entities, listed documents not
    yet in `filing_index` (a later capture, or an earlier import that missed
    them), missing details, missing downloads — so importing the same HAR twice
    adds nothing.
    `missing` lists, per searched entity, the in-scope documents this capture
    did not complete (not expanded, or not downloaded).
    """
    report = HarImportReport(name=name)
    capture = HarCapture.from_har(data)
    report.problems.extend(f"skipped {s}" for s in capture.skipped)
    browser = HarFilingBrowser(capture)
    breaker = CircuitBreaker(threshold=len(capture.krs_numbers) + 1)  # no live source to protect
    scope = set(document_types.download_codes)
    flow: dict[str, Any] = {
        "browser": browser,
        "store": store,
        "ingestion_run_id": ingestion_run_id,
        "breaker": breaker,
        "clock": browser.captured_at,
    }

    def attempt[T](what: str, step: Callable[[], T]) -> T | None:
        try:
            return step()
        except SourceError as exc:
            conn.rollback()
            report.problems.append(f"{what}: {exc}")
            return None

    unindexed = set(manifest.unindexed_entities(conn))
    for krs in capture.krs_numbers:
        if krs not in resolved:
            report.not_resolved.append(krs)
            continue
        known = {row.document_ref for row in manifest.filing_documents(conn, [krs])}
        if krs not in unindexed:
            listed = attempt(f"filing list for {krs}", partial(_listed_refs, browser, krs))
            if listed is None or listed <= known:
                continue
        result = attempt(f"filing list for {krs}", partial(index_filings, krs, **flow))
        if result is None:
            continue
        manifest.record_a3_index_result(conn, result)
        conn.commit()
        if krs in unindexed:
            (report.indexed if result.entries else report.quarantined).append(krs)
        else:
            report.topped_up[krs] = len({e.document_ref for e in result.entries} - known)

    searched = [krs for krs in capture.krs_numbers if krs in resolved]
    for row in manifest.filing_documents(conn, searched):
        ref = row.document_ref
        type_id, file_name, downloaded = row.rdf_type_id, row.file_name, row.downloaded
        if type_id is None and browser.has_detail(ref):
            fetched = attempt(
                f"detail of {ref} ({row.krs})", partial(fetch_filing_detail, row.krs, ref, **flow)
            )
            if fetched is not None:
                manifest.record_a3_detail(conn, fetched)
                conn.commit()
                report.details += 1
                type_id, file_name = fetched.detail.rdf_type_id, fetched.detail.file_name
        if type_id is not None and not downloaded and browser.has_download(ref):
            saved = attempt(
                f"download of {ref} ({row.krs})",
                partial(download_filing, row.krs, ref, original_filename=file_name, **flow),
            )
            if saved is not None:
                manifest.record_a3_download(conn, saved)
                conn.commit()
                report.downloads += 1
                downloaded = True
        in_scope = (type_id if type_id is not None else row.rdf_type_code) in scope
        if row.status == "NIEUSUNIETY" and in_scope and not downloaded:
            report.missing.setdefault(row.krs, []).append(ref)
    return report
