"""A3 — RDF financial document retrieval (AGENT_SPEC.md §6A, ADR 0007 option C).

Every RDF host sits behind an Imperva Incapsula WAF (ADR 0007), so RDF is
fetched through a real browser only — no httpx tier. KRS support confirmed
(informally) 3 documents/minute for a non-invasive automation script;
`RDF_REQUESTS_PER_MINUTE` defaults to that and is spent per RDF API request,
not per entity or per document.

How RDF works (plan 0003 step A: HAR + DOM captures in `tests/fixtures/rdf/`):
`rdf-przegladarka.ms.gov.pl/wyszukaj-podmiot` is an Angular SPA. Typing a KRS
and pressing "Wyszukaj" fires `podmioty/wyszukiwanie/dane-podstawowe` and then
`dokumenty/wyszukiwanie` (the paginated filing list). The SPA encrypts the KRS
in that second request with a key shipped in its own JavaScript, so the list
cannot be requested directly — and must not be: this adapter drives the page
as a person would and reads the SPA's own responses. Expanding a row
("Rozwiń") fires `dokumenty/{id}/id-dokumentu-i-korekt` and `dokumenty/{id}`
(the detail, whose `dataDodania` is `known_from`). "Pobierz dokumenty" in the
expanded row fires `POST dokumenty/tresc`, answered with the document bytes (a
ZIP for XML statements).

Shape, mirroring A2 (`regon_client.py`):

- `FilingBrowser` — the transport Protocol. It returns *raw* responses and
  never parses content, so the flow can `put_raw` them before
  `parse_filing_list` / `parse_document_detail` read them (invariant 2). The
  Playwright implementation peeks at a few list fields only to navigate the
  page (page count, row positions), the same way the SPA itself does.
- `PlaywrightFilingBrowser` — one Chromium browser, one context, one page,
  launched once per run and used serially. Human-paced: randomised think-time
  before every click, plus one limiter token per API request the page sends.
- `index_filings`, `fetch_filing_detail`, `download_filing` — pure flow over any
  `FilingBrowser`, returning manifest-ready records. Tests use a fake browser.

**Explicit limits (ADR 0007, do not relax without a new ADR):** no
CAPTCHA-solving, no stealth/fingerprint plugins, no User-Agent forging, no
proxy rotation, no exporting browser cookies to httpx, no parallel contexts,
no calling RDF's API outside the page. A WAF page is always a failure, never a
document: `detect_rdf_gate` runs on the page load and every captured response.
After `CIRCUIT_BREAKER_THRESHOLD` consecutive blocks the whole run stops
(`RdfCircuitOpen`) rather than continuing entity by entity.

**Natural persons (invariant 6):** the SPA's submission view ("Pokaż
zgłoszenie", endpoint `zgloszenie/{id}`) lists signatories by name. It is never
clicked, and the browser context aborts any request to it.

Setup (new local prerequisite beyond `make install`, under WSL):

    uv run playwright install chromium --with-deps   # needs sudo for system libraries

Re-run `notebooks/exploration/rdf_access_probe.py` before any production-scale
backfill: the WAF posture and the informal rate confirmation can both change.
"""

from __future__ import annotations

import json
import logging
import random
import re
import time
from collections.abc import Callable, Collection, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, NoReturn, Protocol, Self, cast
from urllib.parse import unquote, urlsplit

import yaml
from pydantic import ValidationError
from pyrate_limiter import Limiter
from tenacity import Retrying, retry_if_exception_type, stop_after_attempt, wait_exponential_jitter
from tenacity.wait import WaitBaseT

from distress_radar.acquisition.base import (
    ContentCheckFailed,
    PermanentSourceError,
    SourcePolicy,
    TransientSourceError,
)
from distress_radar.acquisition.models import (
    FilingDetail,
    FilingIndexRow,
    FilingListEntry,
    PendingFilingDocument,
    QuarantineRecord,
    RawFetchRecord,
    RdfDocumentStatus,
    RdfDocumentTypes,
)
from distress_radar.acquisition.raw_store import ObjectStore, RawDocumentMeta, put_raw, sha256_hex
from distress_radar.acquisition.redaction import REDACTION_VERSION, RedactionError, redact_download

if TYPE_CHECKING:
    from playwright.sync_api import (
        Browser,
        BrowserContext,
        Download,
        Locator,
        Page,
        Playwright,
        Request,
    )
    from playwright.sync_api import Response as PageResponse

logger = logging.getLogger(__name__)

SOURCE = "rdf"
STAGE = "A3"
FETCH_TIER = "playwright"
CIRCUIT_BREAKER_THRESHOLD = 3
THINK_TIME_SECONDS = (2.0, 5.0)

# RDF's API, as the SPA calls it (HAR, 2026-09-15). Paths are relative to
# `API_PREFIX` and compared URL-decoded.
API_PREFIX = "/services/rdf/przegladarka-dokumentow-finansowych/"
ENTITY_PATH = "podmioty/wyszukiwanie/dane-podstawowe"
LIST_PATH = "dokumenty/wyszukiwanie"
DOWNLOAD_PATH = "dokumenty/tresc"
CORRECTIONS_SUFFIX = "/id-dokumentu-i-korekt"
BLOCKED_PATH_PREFIXES = ("zgloszenie/",)  # submission view: signatories' names

DOCUMENT_TYPES_PATH = (
    Path(__file__).resolve().parents[3] / "config" / "mappings" / "rdf_document_types.yaml"
)

# Response headers worth keeping in the sidecar. Everything else is dropped —
# in particular `set-cookie`, which carries Incapsula session tokens.
_KEPT_HEADERS = frozenset(
    {"content-type", "content-length", "content-disposition", "etag", "last-modified", "date"}
)
# Bodies that start like a document are never a WAF page, whatever bytes follow.
_DOCUMENT_MAGIC = (b"PK\x03\x04", b"%PDF-", b"<?xml", b"\xef\xbb\xbf<?xml")


def rdf_policy(requests_per_minute: int) -> SourcePolicy:
    return SourcePolicy(name=SOURCE, requests_per_minute=requests_per_minute)


def load_document_types(path: Path = DOCUMENT_TYPES_PATH) -> RdfDocumentTypes:
    return RdfDocumentTypes.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))


def api_path(url: str) -> str | None:
    """`url`'s path below `API_PREFIX`, URL-decoded; `None` for non-API URLs."""
    path = urlsplit(url).path
    index = path.find(API_PREFIX)
    if index < 0:
        return None
    return unquote(path[index + len(API_PREFIX) :])


def detail_path(document_ref: str) -> str:
    return f"dokumenty/{document_ref}"


def corrections_path(document_ref: str) -> str:
    return f"dokumenty/{document_ref}{CORRECTIONS_SUFFIX}"


def is_blocked_url(url: str) -> bool:
    path = api_path(url)
    return path is not None and path.startswith(BLOCKED_PATH_PREFIXES)


# --- Errors and circuit breaker --------------------------------------------------------------


class RdfAccessBlocked(PermanentSourceError):
    """WAF block, challenge, or CAPTCHA. The entity stays unresolved for a later run."""

    reason_code = "rdf_access_blocked"


class RdfCircuitOpen(PermanentSourceError):
    """Too many consecutive blocks: stop the whole run (ADR 0007)."""


class RdfShapeError(PermanentSourceError):
    """An RDF response no longer has the shape observed in plan 0003's capture."""

    reason_code = "rdf_response_shape_changed"


@dataclass
class CircuitBreaker:
    """Counts consecutive `ContentCheckFailed` outcomes across one run."""

    threshold: int = CIRCUIT_BREAKER_THRESHOLD
    consecutive_blocks: int = 0

    @property
    def is_open(self) -> bool:
        return self.consecutive_blocks >= self.threshold

    def ensure_closed(self) -> None:
        if self.is_open:
            raise RdfCircuitOpen(
                f"RDF circuit breaker open after {self.consecutive_blocks} consecutive blocks"
            )

    def record_block(self) -> None:
        self.consecutive_blocks += 1
        self.ensure_closed()

    def record_success(self) -> None:
        self.consecutive_blocks = 0


# --- Content check ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RdfResponse:
    """One RDF network response as the browser saw it. `body` is unmodified."""

    url: str
    status_code: int
    headers: Mapping[str, str]  # lower-case names
    body: bytes
    request_body: bytes | None = None

    @property
    def content_type(self) -> str:
        return self.headers.get("content-type", "application/octet-stream")


def detect_rdf_gate(
    body: bytes, set_cookie: str, *, content_type: str = "", js_executed: bool = False
) -> str | None:
    """Reason string if the response is a WAF/challenge page rather than content.

    Promoted from the probe notebook's `detect_gate` (ADR 0007). Every gated
    RDF response observed was HTTP 200, so status codes alone cannot tell.
    Deliberately fails closed: a false positive leaves an entity unresolved,
    a false negative would store a block page as a filing.

    A body that starts with a document signature (ZIP, PDF, XML) is content:
    compressed bytes can contain any ASCII run, "captcha" included.
    Incapsula sets `incap_ses_*` on ordinary responses inside a browser
    session too, so "session cookie + short body" only counts as a challenge
    for HTML or untyped responses — never for small JSON or documents.
    `js_executed=True` (a page the browser rendered) skips the "enable
    javascript" marker: an SPA shell's `<noscript>` text is not a gate there.
    """
    if not body:
        return "empty_body"
    if body.startswith(_DOCUMENT_MAGIC):
        return None
    head = body[:20_000].lower()
    if b"_incapsula_resource" in head or b"incapsula incident id" in head:
        return "imperva_incapsula_block_page"
    challenge_shaped = not content_type or "html" in content_type.lower()
    if challenge_shaped and "incap_ses" in set_cookie.lower() and len(body) < 2_000:
        return "imperva_incapsula_cookie_challenge"
    if b"captcha" in head:
        return "captcha"
    if not js_executed and b"enable javascript" in head:
        return "javascript_required"
    return None


def _gate_reason(response: RdfResponse) -> str | None:
    return detect_rdf_gate(
        response.body,
        response.headers.get("set-cookie", ""),
        content_type=response.headers.get("content-type", ""),
    )


def _raise_for_status(response: RdfResponse) -> None:
    code = response.status_code
    if code == 429 or code >= 500:
        raise TransientSourceError(f"HTTP {code} from {response.url}")
    if code >= 400:
        raise PermanentSourceError(f"HTTP {code} from {response.url}")


# --- Parsing ---------------------------------------------------------------------------------


def _json(body: bytes, what: str) -> object:
    try:
        return json.loads(body)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RdfShapeError(f"{what}: not JSON ({exc})") from exc


def _mapping(value: object, what: str, keys: Collection[str]) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise RdfShapeError(f"{what}: expected an object, got {type(value).__name__}")
    mapping = cast(dict[str, Any], value)
    missing = [k for k in keys if k not in mapping]
    if missing:
        raise RdfShapeError(f"{what}: missing {missing}")
    return mapping


def _str(value: object, what: str) -> str:
    if not isinstance(value, str) or not value:
        raise RdfShapeError(f"{what}: expected a non-empty string, got {value!r}")
    return value


def _int(value: object, what: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise RdfShapeError(f"{what}: expected an integer, got {value!r}")
    return value


def _bool(value: object, what: str) -> bool:
    if not isinstance(value, bool):
        raise RdfShapeError(f"{what}: expected a boolean, got {value!r}")
    return value


def _optional_bool(value: object, what: str) -> bool | None:
    return None if value is None else _bool(value, what)


def _date(value: object, what: str) -> date:
    try:
        return date.fromisoformat(_str(value, what))
    except ValueError as exc:
        raise RdfShapeError(f"{what}: expected an ISO date, got {value!r}") from exc


def _optional_date(value: object, what: str) -> date | None:
    return None if value is None or value == "" else _date(value, what)


def _status(value: object) -> RdfDocumentStatus:
    if value == "NIEUSUNIETY":
        return "NIEUSUNIETY"
    if value == "USUNIETY":
        return "USUNIETY"
    raise RdfShapeError(f"list item status: unexpected {value!r}")


def _optional_str(value: object, what: str) -> str | None:
    if value is None or value == "":
        return None
    return _str(value, what)


@dataclass(frozen=True)
class ListPage:
    """Paging metadata and entries of one `dokumenty/wyszukiwanie` response."""

    page_number: int
    page_size: int
    page_count: int
    total: int
    items: list[dict[str, Any]]

    @property
    def document_refs(self) -> list[str]:
        return [_str(item.get("id"), "list item id") for item in self.items]


def read_list_page(body: bytes) -> ListPage:
    document = _mapping(_json(body, "filing list"), "filing list", ("content", "metadaneWynikow"))
    meta = _mapping(
        document["metadaneWynikow"],
        "filing list paging",
        ("numerStrony", "rozmiarStrony", "liczbaStron", "calkowitaLiczbaObiektow"),
    )
    content = document["content"]
    if not isinstance(content, list):
        raise RdfShapeError("filing list: `content` is not a list")
    items = [
        _mapping(item, "filing list item", ("id",)) for item in cast(list[object], content)
    ]
    return ListPage(
        page_number=_int(meta["numerStrony"], "numerStrony"),
        page_size=_int(meta["rozmiarStrony"], "rozmiarStrony"),
        page_count=_int(meta["liczbaStron"], "liczbaStron"),
        total=_int(meta["calkowitaLiczbaObiektow"], "calkowitaLiczbaObiektow"),
        items=items,
    )


def parse_entity_lookup(krs: str, body: bytes) -> bool:
    """Whether RDF knows `krs` (`dane-podstawowe`). A found entity must be that KRS."""
    document = _mapping(
        _json(body, "entity lookup"), "entity lookup", ("czyPodmiotZnaleziony", "podmiot")
    )
    found = _bool(document["czyPodmiotZnaleziony"], "czyPodmiotZnaleziony")
    if found:
        entity = _mapping(document["podmiot"], "entity lookup podmiot", ("numerKRS",))
        if entity["numerKRS"] != krs:
            raise RdfShapeError(f"entity lookup for {krs} answered for {entity['numerKRS']!r}")
    return found


def parse_filing_list(krs: str, pages: list[bytes]) -> list[FilingListEntry]:
    """Parse every page of one filing-list lookup into entries.

    The pages must be the complete list at one page size — consecutive page
    numbers, and together exactly `calkowitaLiczbaObiektow` distinct documents —
    otherwise this raises rather than returning a partial list (invariant 4).
    Only the list's own fields are read, never a document's contents.
    """
    if not pages:
        raise RdfShapeError(f"filing list for {krs}: no pages")
    parsed = [read_list_page(body) for body in pages]
    first = parsed[0]
    expected_pages = list(range(first.page_count)) or [0]
    if [p.page_number for p in parsed] != expected_pages:
        raise RdfShapeError(
            f"filing list for {krs}: pages {[p.page_number for p in parsed]}, "
            f"expected {expected_pages}"
        )
    paging = (first.page_size, first.page_count, first.total)
    if any((p.page_size, p.page_count, p.total) != paging for p in parsed):
        raise RdfShapeError(f"filing list for {krs}: paging changed between pages")

    entries: list[FilingListEntry] = []
    for page in parsed:
        for item in page.items:
            try:
                entries.append(
                    FilingListEntry(
                        krs=krs,
                        document_ref=_str(item.get("id"), "id"),
                        rdf_type_code=_str(item.get("rodzaj"), "rodzaj"),
                        status=_status(item.get("status")),
                        period_start=_date(
                            item.get("okresSprawozdawczyPoczatek"), "okresSprawozdawczyPoczatek"
                        ),
                        period_end=_date(
                            item.get("okresSprawozdawczyKoniec"), "okresSprawozdawczyKoniec"
                        ),
                        deleted_on=_optional_date(
                            item.get("dataUsunieciaDokumentu"), "dataUsunieciaDokumentu"
                        ),
                    )
                )
            except ValidationError as exc:
                raise RdfShapeError(f"filing list item for {krs}: {exc}") from exc

    refs = [e.document_ref for e in entries]
    if len(set(refs)) != len(refs):
        raise RdfShapeError(f"filing list for {krs}: duplicate document ids")
    if len(entries) != first.total:
        raise RdfShapeError(
            f"filing list for {krs}: {len(entries)} documents, RDF reports {first.total}"
        )
    return entries


def parse_correction_refs(body: bytes) -> list[str]:
    """`id-dokumentu-i-korekt`: the expanded document and its corrections, in RDF's order."""
    corrections = _json(body, "corrections list")
    if not isinstance(corrections, list):
        raise RdfShapeError("corrections list: expected a list")
    refs = [_str(ref, "correction id") for ref in cast(list[object], corrections)]
    if len(set(refs)) != len(refs):
        raise RdfShapeError(f"corrections list repeats an id: {refs}")
    return refs


def parse_document_detail(
    document_ref: str, corrections_body: bytes, detail_body: bytes
) -> FilingDetail:
    """Parse one document's detail within an expanded row. Metadata only; the file is not opened.

    `document_ref` is the expanded document or one of its corrections.
    """
    refs = parse_correction_refs(corrections_body)
    if document_ref not in refs:
        raise RdfShapeError(f"corrections list for {document_ref} does not contain it: {refs}")

    detail = _mapping(
        _json(detail_body, "document detail"),
        "document detail",
        ("identyfikator", "rodzajDokumentu", "dataDodania", "czyKorekta", "czyMSR"),
    )
    if detail["identyfikator"] != document_ref:
        raise RdfShapeError(
            f"document detail for {document_ref} answered for {detail['identyfikator']!r}"
        )
    doc_type = _mapping(detail["rodzajDokumentu"], "rodzajDokumentu", ("id", "nazwa"))
    return FilingDetail(
        document_ref=document_ref,
        rdf_type_id=str(_int(doc_type["id"], "rodzajDokumentu.id")),
        rdf_type_name=_str(doc_type["nazwa"], "rodzajDokumentu.nazwa"),
        submission_date=_date(detail["dataDodania"], "dataDodania"),
        prepared_date=_optional_date(detail.get("dataSporzadzenia"), "dataSporzadzenia"),
        is_correction=_bool(detail["czyKorekta"], "czyKorekta"),
        is_ifrs=_optional_bool(detail["czyMSR"], "czyMSR"),  # empty on pre-2018 filings
        file_name=_optional_str(detail.get("nazwaPliku"), "nazwaPliku"),
        correction_refs=refs,
        status=None if detail.get("status") is None else _status(detail["status"]),
        period_start=_optional_date(
            detail.get("okresSprawozdawczyPoczatek"), "okresSprawozdawczyPoczatek"
        ),
        period_end=_optional_date(detail.get("okresSprawozdawczyKoniec"), "okresSprawozdawczyKoniec"),
        deleted_on=_optional_date(
            detail.get("dataUsunieciaDokumentuPrzezSad"), "dataUsunieciaDokumentuPrzezSad"
        ),
    )


# --- Transport -------------------------------------------------------------------------------


@dataclass(frozen=True)
class FilingListing:
    """One KRS search: the entity lookup, then every list page (none if not found)."""

    entity: RdfResponse
    pages: list[RdfResponse]

    @property
    def responses(self) -> list[RdfResponse]:
        return [self.entity, *self.pages]


@dataclass(frozen=True)
class DocumentView:
    """What an expanded row loads: the corrections list, then one detail per document in it.

    Corrections are not rows of RDF's list; expanding the document they correct
    is the only way to reach them. `related` holds their details by id.
    """

    corrections: RdfResponse
    detail: RdfResponse
    related: dict[str, RdfResponse] = field(default_factory=dict[str, RdfResponse])

    @property
    def responses(self) -> list[RdfResponse]:
        return [self.corrections, self.detail, *self.related.values()]


class FilingBrowser(Protocol):
    """What the A3 flow needs from RDF. Returns raw responses; never parses content.

    Implementations raise `TransientSourceError` (after their own bounded
    retries), `PermanentSourceError`, or `ContentCheckFailed` (a gated page
    load they detected themselves).
    """

    @property
    def browser_version(self) -> str | None: ...

    @property
    def fetch_tier(self) -> str:
        """Sidecar `fetch_tier` for what this browser returns (e.g. "playwright")."""
        ...

    def open_filing_list(self, krs: str) -> FilingListing: ...

    def open_document(self, krs: str, document_ref: str) -> DocumentView: ...

    def download(self, krs: str, document_ref: str) -> RdfResponse:
        """"Pobierz dokumenty" for a listed document: one file holding it and its corrections.

        `request_body` must carry the JSON list of ids the file covers.
        """
        ...


@dataclass(frozen=True)
class RdfSpaSpec:
    """Where the RDF SPA lives and how to find its controls (DOM capture, 2026-09-16)."""

    entry_url: str
    krs_input: str = 'input[formcontrolname="numerKRS"]'
    search_button: str = 'button[type="submit"]:has-text("Wyszukaj")'
    data_rows: str = "tbody tr:has(td.actions-col)"
    expand_button: str = 'button[aria-label="Rozwiń"]'
    collapse_button: str = 'button[aria-label="Zwiń"]'
    expanded_panel: str = "app-szczegoly-dokumentu"
    download_button: str = 'button:has-text("Pobierz dokumenty")'
    rows_per_page: str = ".p-paginator-rpp-options"
    rows_per_page_option: str = 'li[role="option"]'
    first_page: str = 'button[aria-label="Pierwsza strona"]'
    next_page: str = 'button[aria-label="Następna strona"]'


@dataclass(frozen=True)
class _Marks:
    """Where the page's response, request, and download logs stood before a click."""

    responses: int
    requests: int
    downloads: int


RDF_SPA_SPEC = RdfSpaSpec(entry_url="https://rdf-przegladarka.ms.gov.pl/wyszukaj-podmiot")


class PlaywrightFilingBrowser:
    """Human-paced RDF access through one serial Chromium context. Use as a context manager.

    Chromium runs Imperva's JavaScript challenge like any visitor's browser;
    its session cookies never leave the context. Every RDF API request the
    page sends costs one limiter token: the expected requests are paid before
    each click, and any extra the SPA sends (e.g. one detail per correction
    tab) are paid right after, so the next click waits for them.
    """

    def __init__(
        self,
        spec: RdfSpaSpec,
        *,
        limiter: Limiter,
        policy: SourcePolicy,
        headless: bool = True,
        think_time_seconds: tuple[float, float] = THINK_TIME_SECONDS,
        sleep: Callable[[float], None] = time.sleep,
        rng: random.Random | None = None,
        wait: WaitBaseT | None = None,
    ) -> None:
        self._spec = spec
        self._limiter = limiter
        self._policy = policy
        self._headless = headless
        self._think_time = think_time_seconds
        self._sleep = sleep
        self._rng = rng if rng is not None else random.Random()
        self._wait: WaitBaseT = wait if wait is not None else wait_exponential_jitter(5, 60)
        self._playwright: Playwright | None = None
        self._browser: Browser | None = None
        self._context: BrowserContext | None = None
        self._page: Page | None = None
        self._responses: list[PageResponse] = []
        self._downloads: list[Download] = []
        self.api_requests = 0  # RDF API requests the page sent
        self.tokens_spent = 0
        self._covered = 0  # API requests already paid for
        self._entry_loaded = False
        self._reset_view()

    def __enter__(self) -> Self:
        from playwright.sync_api import sync_playwright

        self._playwright = sync_playwright().start()
        try:
            self._browser = self._playwright.chromium.launch(headless=self._headless)
            # Desktop viewport: the SPA renders one set of row buttons per breakpoint.
            self._context = self._browser.new_context(viewport={"width": 1440, "height": 900})
            self._context.route(is_blocked_url, lambda route: route.abort("blockedbyclient"))
            self._page = self._context.new_page()
            self._page.on("request", self._on_request)
            self._page.on("response", self._on_response)
            self._page.on("download", self._on_download)
        except Exception:
            self.close()
            raise
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def close(self) -> None:
        if self._browser is not None:
            self._browser.close()  # closes its context and page
        if self._playwright is not None:
            self._playwright.stop()
        self._playwright = self._browser = self._context = self._page = None

    @property
    def browser_version(self) -> str | None:
        return self._browser.version if self._browser is not None else None

    @property
    def fetch_tier(self) -> str:
        return FETCH_TIER

    def open_filing_list(self, krs: str) -> FilingListing:
        return self._call(lambda: self._open_filing_list_once(krs))

    def open_document(self, krs: str, document_ref: str) -> DocumentView:
        return self._call(lambda: self._open_document_once(krs, document_ref))

    def download(self, krs: str, document_ref: str) -> RdfResponse:
        return self._call(lambda: self._download_once(krs, document_ref))

    # -- network bookkeeping --

    def _on_request(self, request: Request) -> None:
        if api_path(request.url) is not None:
            self.api_requests += 1

    def _on_response(self, response: PageResponse) -> None:
        if api_path(response.url) is not None:
            self._responses.append(response)

    def _on_download(self, download: Download) -> None:
        self._downloads.append(download)

    def _spend(self, tokens: int) -> None:
        for _ in range(tokens):
            self._limiter.try_acquire(SOURCE)
            self.tokens_spent += 1

    def _settle(self) -> None:
        """Pay for API requests sent but not yet paid for."""
        unpaid = self.api_requests - self._covered
        if unpaid > 0:
            self._spend(unpaid)
            self._covered += unpaid

    def _click(self, target: Locator, expected_requests: int) -> _Marks:
        """Think, pay for `expected_requests`, click. Returns marks for `_await`/`_finish`."""
        self._settle()
        self._sleep(self._rng.uniform(*self._think_time))
        self._spend(expected_requests)
        marks = _Marks(len(self._responses), self.api_requests, len(self._downloads))
        target.click(timeout=self._timeout_ms)
        return marks

    def _finish(self, marks: _Marks, expected_requests: int) -> None:
        sent = self.api_requests - marks.requests
        self._covered += min(sent, expected_requests)
        self._settle()

    def _await(
        self, marks: _Marks, wanted: Callable[[str], bool], description: str
    ) -> PageResponse:
        """First API response since `marks` whose path satisfies `wanted`."""
        return self._poll(
            lambda: next(
                (
                    response
                    for response in self._responses[marks.responses :]
                    if (path := api_path(response.url)) is not None and wanted(path)
                ),
                None,
            ),
            description,
        )

    def _await_download(self, marks: _Marks) -> Download:
        return self._poll(
            lambda: next(iter(self._downloads[marks.downloads :]), None), "a saved download"
        )

    def _poll[T](self, find: Callable[[], T | None], description: str) -> T:
        page = self._require_page()
        deadline = time.monotonic() + self._policy.timeout_seconds
        while (found := find()) is None:
            if time.monotonic() >= deadline:
                raise TransientSourceError(f"RDF did not answer with {description}")
            page.wait_for_timeout(100)
        return found

    @property
    def _timeout_ms(self) -> float:
        return self._policy.timeout_seconds * 1000

    # -- page state --

    def _reset_view(self) -> None:
        self._krs: str | None = None
        self._pages: list[list[str]] = []
        self._page_number = 0
        self._expanded: str | None = None
        self._expanded_refs: list[str] = []

    def _require_page(self) -> Page:
        if self._page is None:
            raise RuntimeError("PlaywrightFilingBrowser used outside its context manager")
        return self._page

    def _ensure_entry(self) -> Page:
        page = self._require_page()
        if self._entry_loaded:
            return page
        self._sleep(self._rng.uniform(*self._think_time))
        navigation = page.goto(
            self._spec.entry_url, wait_until="domcontentloaded", timeout=self._timeout_ms
        )
        status = navigation.status if navigation is not None else 0
        reason = detect_rdf_gate(page.content().encode("utf-8"), "", js_executed=True)
        if reason is not None:
            raise ContentCheckFailed(self._spec.entry_url, status, reason)
        if status >= 400:
            _raise_for_status(RdfResponse(self._spec.entry_url, status, {}, b""))
        page.locator(self._spec.krs_input).wait_for(timeout=self._timeout_ms)
        self._entry_loaded = True
        self._reset_view()
        return page

    def _captured(
        self, response: PageResponse, body: Callable[[], bytes] | None = None
    ) -> RdfResponse:
        """`response` as an `RdfResponse`; `body` supplies the bytes when not `response.body()`."""
        status = RdfResponse(response.url, response.status, {}, b"")
        _raise_for_status(status)
        return RdfResponse(
            url=response.url,
            status_code=response.status,
            headers={k.lower(): v for k, v in response.all_headers().items()},
            body=body() if body is not None else response.body(),
            request_body=response.request.post_data_buffer,
        )

    def _navigation_page(self, response: RdfResponse) -> ListPage:
        """Peek at a list response to steer the page; a gated one is a block."""
        reason = _gate_reason(response)
        if reason is not None:
            raise ContentCheckFailed(response.url, response.status_code, reason)
        return read_list_page(response.body)

    def _collapse(self) -> None:
        if self._expanded is None:
            return
        page = self._require_page()
        toggle = page.locator(self._spec.collapse_button).filter(visible=True)
        if toggle.count() > 0:
            self._sleep(self._rng.uniform(*self._think_time))
            toggle.first.click(timeout=self._timeout_ms)
        self._expanded = None

    def _search(self, krs: str) -> tuple[RdfResponse, RdfResponse | None]:
        page = self._ensure_entry()
        self._collapse()
        self._reset_view()
        page.locator(self._spec.krs_input).fill(krs, timeout=self._timeout_ms)
        search = page.locator(self._spec.search_button)
        # Found entities trigger the list request too: pay for both up front.
        marks = self._click(search, expected_requests=2)
        entity = self._captured(self._await(marks, lambda p: p == ENTITY_PATH, "entity lookup"))
        reason = _gate_reason(entity)
        if reason is not None:
            raise ContentCheckFailed(entity.url, entity.status_code, reason)
        if not parse_entity_lookup(krs, entity.body):
            self._finish(marks, 2)
            return entity, None
        listing = self._captured(self._await(marks, lambda p: p == LIST_PATH, "filing list"))
        self._finish(marks, 2)
        return entity, listing

    def _click_for_list(self, target: Locator, description: str) -> tuple[RdfResponse, ListPage]:
        marks = self._click(target, expected_requests=1)
        response = self._captured(self._await(marks, lambda p: p == LIST_PATH, description))
        self._finish(marks, 1)
        return response, self._navigation_page(response)

    def _largest_page_size(self) -> tuple[int, Locator] | None:
        page = self._require_page()
        dropdown = page.locator(self._spec.rows_per_page).filter(visible=True)
        if dropdown.count() == 0:
            return None
        self._sleep(self._rng.uniform(*self._think_time))
        dropdown.first.click(timeout=self._timeout_ms)  # opens the overlay, no request
        options = page.locator(self._spec.rows_per_page_option).filter(visible=True)
        best: tuple[int, Locator] | None = None
        for index in range(options.count()):
            option = options.nth(index)
            text = option.inner_text().strip()
            if text.isdigit() and (best is None or int(text) > best[0]):
                best = (int(text), option)
        return best

    def _open_filing_list_once(self, krs: str) -> FilingListing:
        entity, first = self._search(krs)
        if first is None:
            return FilingListing(entity=entity, pages=[])
        current = self._navigation_page(first)
        pages = [first]
        if current.page_count > 1:
            largest = self._largest_page_size()
            if largest is not None and largest[0] > current.page_size:
                response, current = self._click_for_list(largest[1], "resized filing list")
                pages = [response]
            elif largest is not None:
                self._require_page().keyboard.press("Escape")
        refs = [current.document_refs]
        next_page = self._require_page().locator(self._spec.next_page).first
        while current.page_number + 1 < current.page_count:
            response, current = self._click_for_list(next_page, "next filing-list page")
            pages.append(response)
            refs.append(current.document_refs)
        self._krs, self._pages, self._page_number = krs, refs, current.page_number
        return FilingListing(entity=entity, pages=pages)

    def _show_row(self, krs: str, document_ref: str) -> int:
        """Bring the page holding `document_ref` into view; returns its row index."""
        if self._krs != krs:
            self._open_filing_list_once(krs)
        position = next(
            (
                (page_index, refs.index(document_ref))
                for page_index, refs in enumerate(self._pages)
                if document_ref in refs
            ),
            None,
        )
        if position is None:
            raise PermanentSourceError(f"document {document_ref} is not in RDF's list for {krs}")
        target, row = position
        if target == self._page_number:
            return row
        self._collapse()
        page = self._require_page()
        shown: list[str] = []
        if target < self._page_number:
            _, current = self._click_for_list(
                page.locator(self._spec.first_page).first, "first filing-list page"
            )
            self._page_number, shown = current.page_number, current.document_refs
        while self._page_number < target:
            _, current = self._click_for_list(
                page.locator(self._spec.next_page).first, "next filing-list page"
            )
            self._page_number, shown = current.page_number, current.document_refs
        if shown != self._pages[target]:
            self._reset_view()
            raise PermanentSourceError(f"RDF's list for {krs} changed during the run")
        return row

    def _expand(self, document_ref: str, row: int) -> DocumentView:
        self._collapse()
        page = self._require_page()
        toggle = (
            page.locator(self._spec.data_rows)
            .nth(row)
            .locator(self._spec.expand_button)
            .filter(visible=True)
            .first
        )
        marks = self._click(toggle, expected_requests=2)
        corrections = self._captured(
            self._await(marks, lambda p: p.endswith(CORRECTIONS_SUFFIX), "corrections list")
        )
        if api_path(corrections.url) != corrections_path(document_ref):
            raise PermanentSourceError(
                f"row {row} opened {corrections.url}, not document {document_ref}"
            )
        refs = parse_correction_refs(corrections.body)  # one detail loads per id
        details = {
            ref: self._captured(
                self._await(marks, lambda p, ref=ref: p == detail_path(ref), f"detail of {ref}")
            )
            for ref in refs
        }
        self._finish(marks, 2)
        if document_ref not in details:
            raise PermanentSourceError(f"document {document_ref} is not in its own row: {refs}")
        self._expanded, self._expanded_refs = document_ref, refs
        detail = details.pop(document_ref)
        return DocumentView(corrections=corrections, detail=detail, related=details)

    def _open_document_once(self, krs: str, document_ref: str) -> DocumentView:
        self._ensure_entry()
        return self._expand(document_ref, self._show_row(krs, document_ref))

    def _download_once(self, krs: str, document_ref: str) -> RdfResponse:
        self._ensure_entry()
        if self._krs != krs or self._expanded != document_ref:
            self._expand(document_ref, self._show_row(krs, document_ref))
        page = self._require_page()
        button = (
            page.locator(self._spec.expanded_panel)
            .locator(self._spec.download_button)
            .filter(visible=True)
            .first
        )
        marks = self._click(button, expected_requests=1)
        answer = self._await(marks, lambda p: p == DOWNLOAD_PATH, "document download")
        # The SPA hands the answer to the browser as a file download. Its bytes
        # are read from that file: for these blob answers `answer.body()` is not
        # reliable, and Chromium may re-request the URL to produce one.
        response = self._captured(answer, lambda: self._saved_bytes(marks))
        self._finish(marks, 1)
        try:
            requested: object = json.loads(response.request_body or b"null")
        except (UnicodeDecodeError, json.JSONDecodeError):
            requested = response.request_body
        if requested != self._expanded_refs:
            raise PermanentSourceError(
                f"download for {document_ref} requested {requested!r}, expected "
                f"{self._expanded_refs!r}; bytes not attributable"
            )
        return response

    def _saved_bytes(self, marks: _Marks) -> bytes:
        download = self._await_download(marks)
        failure = download.failure()
        if failure is not None:
            raise TransientSourceError(f"RDF download did not complete: {failure}")
        try:
            return download.path().read_bytes()
        finally:
            download.delete()

    def _call[T](self, action: Callable[[], T]) -> T:
        from playwright.sync_api import Error as PlaywrightError

        for attempt in Retrying(
            retry=retry_if_exception_type(TransientSourceError),
            stop=stop_after_attempt(self._policy.max_attempts),
            wait=self._wait,
            reraise=True,
        ):
            with attempt:
                try:
                    return action()
                except PlaywrightError as exc:  # includes playwright's TimeoutError
                    self._entry_loaded = False
                    raise TransientSourceError(f"RDF browser error: {exc.message}") from exc
                except TransientSourceError:
                    self._entry_loaded = False  # reload the SPA before retrying
                    raise
        raise AssertionError("unreachable: Retrying reraises")  # pragma: no cover


# --- Flow ------------------------------------------------------------------------------------


@dataclass
class A3IndexResult:
    """One entity's filing-list lookup, ready for the B2 manifest writer."""

    krs: str
    raw_fetches: list[RawFetchRecord] = field(default_factory=list[RawFetchRecord])
    entries: list[FilingIndexRow] = field(default_factory=list[FilingIndexRow])
    quarantine: list[QuarantineRecord] = field(default_factory=list[QuarantineRecord])


@dataclass(frozen=True)
class A3Detail:
    """One expanded row: the listed document's detail, and each correction's."""

    krs: str
    detail: FilingDetail
    corrections_fetch: RawFetchRecord
    detail_fetch: RawFetchRecord
    related: list[tuple[FilingDetail, RawFetchRecord]] = field(
        default_factory=list[tuple[FilingDetail, RawFetchRecord]]
    )


@dataclass(frozen=True)
class A3Download:
    """One downloaded file and every `filing_index` row it holds (a document + corrections)."""

    krs: str
    document_refs: list[str]
    raw_fetch: RawFetchRecord


def _checked[T](
    action: Callable[[], T], responses: Callable[[T], list[RdfResponse]], breaker: CircuitBreaker
) -> T:
    """Run one browser action through the circuit breaker and the gate check."""
    breaker.ensure_closed()
    try:
        result = action()
    except ContentCheckFailed as exc:
        _blocked(exc, breaker)
    for response in responses(result):
        reason = _gate_reason(response)
        if reason is not None:
            _blocked(ContentCheckFailed(response.url, response.status_code, reason), breaker)
    breaker.record_success()
    return result


def _blocked(exc: ContentCheckFailed, breaker: CircuitBreaker) -> NoReturn:
    logger.warning("RDF gated response: %s", exc)
    breaker.record_block()  # raises RdfCircuitOpen at the threshold
    raise RdfAccessBlocked(str(exc)) from exc


def _store_raw(
    store: ObjectStore,
    response: RdfResponse,
    *,
    ingestion_run_id: str,
    fetched_at: datetime,
    browser: FilingBrowser,
    original_filename: str | None = None,
    redact: bool = False,
) -> RawFetchRecord:
    body = response.body
    redaction_version = received_sha256 = None
    if redact:
        try:
            redacted = redact_download(body)
        except RedactionError as exc:
            raise PermanentSourceError(f"{response.url}: cannot redact download: {exc}") from exc
        if redacted.changed:
            redaction_version, received_sha256 = REDACTION_VERSION, sha256_hex(body)
            body = redacted.data
    disposition = response.headers.get("content-disposition", "")
    filename = re.search(r'filename\*?=(?:UTF-8\'\')?"?([^";]+)"?', disposition, re.IGNORECASE)
    meta = RawDocumentMeta(
        source=SOURCE,
        source_url=response.url,
        content_type=response.content_type,
        fetched_at=fetched_at,
        http_headers={k: v for k, v in response.headers.items() if k in _KEPT_HEADERS},
        ingestion_run_id=ingestion_run_id,
        original_filename=filename.group(1) if filename is not None else original_filename,
        fetch_tier=browser.fetch_tier,
        browser_version=browser.browser_version,
        redaction_version=redaction_version,
        received_sha256=received_sha256,
    )
    digest = put_raw(store, body, meta)
    return RawFetchRecord(sha256=digest, byte_size=len(body), meta=meta)


def _quarantine(
    krs: str, reason_code: str, detail: str, source_hash: str, run_id: str, at: datetime
) -> QuarantineRecord:
    return QuarantineRecord(
        stage=STAGE,
        entity_key=krs,
        reason_code=reason_code,
        detail=detail,
        source_document_hash=source_hash,
        ingestion_run_id=run_id,
        created_at=at,
        krs=krs,
        document_ref=None,
    )


def index_filings(
    krs: str,
    *,
    browser: FilingBrowser,
    store: ObjectStore,
    ingestion_run_id: str,
    breaker: CircuitBreaker,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    parse: Callable[[str, list[bytes]], list[FilingListEntry]] = parse_filing_list,
) -> A3IndexResult:
    """Search `krs`: raw-store the entity lookup and every list page, then parse them.

    Raises `RdfAccessBlocked` (entity left unresolved), `RdfCircuitOpen` (stop
    the run), or the browser's source errors. An entity RDF does not know, or
    one with no filings, is a quarantine row.
    """
    result = A3IndexResult(krs=krs)
    listing = _checked(lambda: browser.open_filing_list(krs), lambda r: r.responses, breaker)
    fetched_at = clock()
    records = [
        _store_raw(
            store,
            response,
            ingestion_run_id=ingestion_run_id,
            fetched_at=fetched_at,
            browser=browser,
        )
        for response in listing.responses
    ]
    result.raw_fetches.extend(records)

    if not parse_entity_lookup(krs, listing.entity.body):
        result.quarantine.append(
            _quarantine(
                krs,
                "rdf_entity_not_found",
                "RDF entity lookup found no entity for this KRS",
                records[0].sha256,
                ingestion_run_id,
                clock(),
            )
        )
        return result

    entries = parse(krs, [page.body for page in listing.pages])
    if not entries:
        result.quarantine.append(
            _quarantine(
                krs,
                "no_rdf_filings",
                "RDF filing list is empty",
                records[1].sha256 if len(records) > 1 else records[0].sha256,
                ingestion_run_id,
                clock(),
            )
        )
        return result

    result.entries = [
        FilingIndexRow(
            krs=entry.krs,
            document_ref=entry.document_ref,
            rdf_type_code=entry.rdf_type_code,
            status=entry.status,
            period_start=entry.period_start,
            period_end=entry.period_end,
            deleted_on=entry.deleted_on,
            discovered_at=fetched_at,
            ingestion_run_id=ingestion_run_id,
        )
        for entry in entries
    ]
    return result


def fetch_filing_detail(
    krs: str,
    document_ref: str,
    *,
    browser: FilingBrowser,
    store: ObjectStore,
    ingestion_run_id: str,
    breaker: CircuitBreaker,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> A3Detail:
    """Expand one listed document: raw-store both responses, then parse the detail."""
    view = _checked(
        lambda: browser.open_document(krs, document_ref), lambda r: r.responses, breaker
    )
    fetched_at = clock()

    def store_raw(response: RdfResponse) -> RawFetchRecord:
        return _store_raw(
            store, response, ingestion_run_id=ingestion_run_id, fetched_at=fetched_at, browser=browser
        )

    corrections_fetch = store_raw(view.corrections)
    detail_fetch = store_raw(view.detail)
    related_fetches = {ref: store_raw(response) for ref, response in view.related.items()}
    detail = parse_document_detail(document_ref, view.corrections.body, view.detail.body)
    missing = set(detail.correction_refs) - {document_ref} - set(view.related)
    if missing:
        raise RdfShapeError(f"expanded {document_ref} without the details of {sorted(missing)}")
    related = [
        (parse_document_detail(ref, view.corrections.body, view.related[ref].body), fetch)
        for ref, fetch in related_fetches.items()
    ]
    return A3Detail(
        krs=krs,
        detail=detail,
        corrections_fetch=corrections_fetch,
        detail_fetch=detail_fetch,
        related=related,
    )


def download_filing(
    krs: str,
    document_ref: str,
    *,
    browser: FilingBrowser,
    store: ObjectStore,
    ingestion_run_id: str,
    breaker: CircuitBreaker,
    bundle: Sequence[str] | None = None,
    original_filename: str | None = None,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> A3Download:
    """Download a listed document and store it. Contents are read only to redact.

    Signatures and other natural-person data are removed before the file is
    hashed and stored (invariant 6, ADR 0009); nothing else is changed.

    RDF delivers a document together with its corrections, so the file must
    cover exactly `bundle` (the detail's `correction_refs`; default just the
    document), as the recorded request says — otherwise its bytes are not
    attributable and nothing is stored. RDF sends no `content-disposition`, so
    `original_filename` (the detail's `nazwaPliku`) names a single-document
    file in the sidecar.
    """
    expected = list(bundle) if bundle else [document_ref]
    response = _checked(lambda: browser.download(krs, document_ref), lambda r: [r], breaker)
    requested = _requested_refs(response)
    if requested != expected:
        raise PermanentSourceError(
            f"download for {document_ref} covers {requested!r}, expected {expected!r}; "
            "bytes not attributable"
        )
    record = _store_raw(
        store,
        response,
        ingestion_run_id=ingestion_run_id,
        fetched_at=clock(),
        browser=browser,
        original_filename=original_filename if len(expected) == 1 else None,
        redact=True,
    )
    return A3Download(krs=krs, document_refs=expected, raw_fetch=record)


def _requested_refs(response: RdfResponse) -> object:
    try:
        return json.loads(response.request_body or b"null")
    except (UnicodeDecodeError, json.JSONDecodeError):
        return response.request_body


def retrieve_document(
    pending: PendingFilingDocument,
    *,
    browser: FilingBrowser,
    store: ObjectStore,
    ingestion_run_id: str,
    breaker: CircuitBreaker,
    document_types: RdfDocumentTypes,
    on_detail: Callable[[A3Detail], None],
    on_download: Callable[[A3Download], None],
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> None:
    """Settle one pending document: its detail if owed, then its file if in scope.

    Each step is handed to its callback (which records and commits it) as soon
    as it succeeds, so a failed download never loses the detail fetched before
    it. The detail's own type id decides the download scope; a list code that
    disagrees with it is a shape change, not something to guess around. A
    correction is downloaded through the document it corrects.
    """
    type_id, file_name, bundle = pending.rdf_type_id, pending.file_name, pending.bundle
    if pending.needs_detail:
        fetched = fetch_filing_detail(
            pending.krs,
            pending.document_ref,
            browser=browser,
            store=store,
            ingestion_run_id=ingestion_run_id,
            breaker=breaker,
            clock=clock,
        )
        on_detail(fetched)
        if fetched.detail.rdf_type_id != pending.rdf_type_code:
            raise RdfShapeError(
                f"document {pending.document_ref}: list type {pending.rdf_type_code}, "
                f"detail type {fetched.detail.rdf_type_id}"
            )
        type_id, file_name = fetched.detail.rdf_type_id, fetched.detail.file_name
        bundle = fetched.detail.correction_refs
    if type_id not in document_types.download_codes or pending.downloaded:
        return
    on_download(
        download_filing(
            pending.krs,
            pending.download_ref,
            browser=browser,
            store=store,
            ingestion_run_id=ingestion_run_id,
            breaker=breaker,
            bundle=bundle,
            original_filename=file_name,
            clock=clock,
        )
    )
