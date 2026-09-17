"""`PlaywrightFilingBrowser` driving real Chromium. Never against RDF.

Two kinds of check, both local:

- The adapter's selectors against the DOM recorded from the real SPA
  (`tests/fixtures/rdf/dom_*.html`, loaded with `page.set_content`).
- The whole search → paginate → expand → download flow against a stand-in SPA
  on 127.0.0.1 that mimics RDF's markup and API paths.

Marked integration because it needs `uv run playwright install chromium --with-deps`.
"""

import io
import json
import threading
import zipfile
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import unquote

import pytest
from tenacity import wait_none

from distress_radar.acquisition.base import (
    ContentCheckFailed,
    PermanentSourceError,
    in_memory_limiter,
)
from distress_radar.acquisition.document_retrieval import (
    API_PREFIX,
    RDF_SPA_SPEC,
    PlaywrightFilingBrowser,
    RdfSpaSpec,
    parse_document_detail,
    parse_filing_list,
    rdf_policy,
)

pytestmark = pytest.mark.integration

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "rdf"
DETAIL = json.loads((FIXTURES / "document_detail.json").read_text(encoding="utf-8"))
WAF = (FIXTURES / "waf_block_page_synthetic.html").read_bytes()

BIG_KRS = "0000209396"  # 12 documents: two pages even at the largest page size (10)
SMALL_KRS = "0000277937"  # 3 documents: no paginator
UNKNOWN_KRS = "0000000009"
CORRECTED = "doc-03=="  # has a correction: expanding it loads two details
GONE = "doc-gone=="  # listed, but its download answers 404


def _refs(krs: str) -> list[str]:
    if krs == SMALL_KRS:
        return ["small-1==", "small-2==", "small-3=="]
    return [f"doc-{i:02d}==" for i in range(1, 12)] + [GONE]


def _zip(ref: str) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("statement.xml", f"<?xml version='1.0'?><Doc id='{ref}'/>")
    return buffer.getvalue()


def _item(ref: str) -> dict[str, Any]:
    return {
        "id": ref,
        "rodzaj": "18",
        "status": "NIEUSUNIETY",
        "statusBezpieczenstwa": None,
        "nazwa": None,
        "okresSprawozdawczyPoczatek": "2025-01-01",
        "okresSprawozdawczyKoniec": "2025-12-31",
        "dataUsunieciaDokumentu": "",
    }


def _list_page(krs: str, page: int, size: int) -> bytes:
    refs = _refs(krs)
    count = -(-len(refs) // size)
    return json.dumps(
        {
            "content": [_item(r) for r in refs[page * size : (page + 1) * size]],
            "metadaneWynikow": {
                "numerStrony": page,
                "rozmiarStrony": size,
                "liczbaStron": count,
                "calkowitaLiczbaObiektow": len(refs),
            },
        }
    ).encode()


def _detail(ref: str) -> bytes:
    return json.dumps({**DETAIL, "identyfikator": ref, "nazwaPliku": f"{ref}.xml"}).encode()


def _corrections(ref: str) -> bytes:
    return json.dumps([ref, "doc-03-korekta=="] if ref == CORRECTED else [ref]).encode()


# A vanilla-JS stand-in with RDF's markup hooks (checked against the real DOM
# in `test_selectors_match_the_recorded_dom`) and RDF's API paths. Like the
# real SPA, the page size survives a new search.
SPA = """<!doctype html><html><body><app-root>
<form id="search"><input formcontrolname="numerKRS" maxlength="10">
<button type="submit"><span>Wyszukaj</span></button></form>
<div id="paginator"></div>
<table><tbody id="rows"></tbody></table>
</app-root><script>
const API = "__API__";
let krs = null, page = 0, size = 5, meta = null;
const post = (path, body) => fetch(API + path, {
  method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(body)});
async function load() {
  const data = await (await post("dokumenty/wyszukiwanie", {krs, page, size})).json();
  meta = data.metadaneWynikow;
  render(data.content);
}
function setLabel(tr, label) {
  tr.querySelectorAll("td.actions-col button").forEach(b => b.setAttribute("aria-label", label));
}
async function toggle(tr, id) {
  const next = tr.nextElementSibling;
  if (next && next.classList.contains("expansion")) { next.remove(); setLabel(tr, "Rozwiń"); return; }
  const enc = encodeURIComponent(id);
  const ids = await (await fetch(API + "dokumenty/" + enc + "/id-dokumentu-i-korekt")).json();
  for (const other of ids) { await fetch(API + "dokumenty/" + encodeURIComponent(other)); }
  const row = document.createElement("tr");
  row.className = "expansion";
  row.innerHTML = '<td colspan="2"><app-szczegoly-dokumentu>' +
    '<button type="button" class="download"><span>Pobierz dokumenty</span></button>' +
    '<button type="button" class="submission">Pokaż zgłoszenie</button>' +
    '</app-szczegoly-dokumentu></td>';
  row.querySelector(".download").onclick = async () => {
    const blob = await (await post("dokumenty/tresc", ids)).blob();  // with its corrections
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob); a.download = "document.zip"; a.click();
  };
  row.querySelector(".submission").onclick = () => fetch(API + "zgloszenie/" + enc);
  tr.after(row);
  setLabel(tr, "Zwiń");
}
function render(items) {
  const rows = document.getElementById("rows");
  rows.innerHTML = "";
  for (const item of items) {
    const tr = document.createElement("tr");
    tr.innerHTML = "<td>" + item.rodzaj + "</td><td class='actions-col'>" +
      "<div><button type='button' aria-label='Rozwiń'>v</button></div>" +
      "<div style='display:none'><button type='button' aria-label='Rozwiń'>Rozwiń szczegółowe dane</button></div></td>";
    tr.querySelectorAll("button").forEach(b => { b.onclick = () => toggle(tr, item.id); });
    rows.appendChild(tr);
  }
  const paginator = document.getElementById("paginator");
  paginator.innerHTML = "";
  if (meta.calkowitaLiczbaObiektow <= 5) return;
  paginator.innerHTML = "<button type='button' aria-label='Pierwsza strona'>«</button>" +
    "<button type='button' aria-label='Następna strona'>›</button>" +
    "<div class='p-paginator-rpp-options'><span>" + size + "</span>" +
    "<ul style='display:none'><li role='option'>5</li><li role='option'>10</li></ul></div>";
  paginator.querySelector("[aria-label='Pierwsza strona']").onclick = () => { page = 0; load(); };
  paginator.querySelector("[aria-label='Następna strona']").onclick = () => {
    if (page + 1 < meta.liczbaStron) { page += 1; load(); }
  };
  const dropdown = paginator.querySelector(".p-paginator-rpp-options");
  dropdown.onclick = () => { dropdown.querySelector("ul").style.display = "block"; };
  dropdown.querySelectorAll("li").forEach(li => {
    li.onclick = (event) => {
      event.stopPropagation(); size = parseInt(li.textContent, 10); page = 0; load();
    };
  });
}
document.getElementById("search").onsubmit = async (event) => {
  event.preventDefault();
  const value = document.querySelector("[formcontrolname='numerKRS']").value;
  const found = await (await post("podmioty/wyszukiwanie/dane-podstawowe", {numerKRS: value})).json();
  document.getElementById("rows").innerHTML = "";
  if (!found.czyPodmiotZnaleziony) return;
  krs = value; page = 0;
  await load();
};
</script></body></html>""".replace("__API__", API_PREFIX)


SEEN: list[str] = []  # every request the stand-in server received, in order


class _Handler(BaseHTTPRequestHandler):

    def _send(self, status: int, content_type: str, body: bytes) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        path = unquote(self.path.split("?")[0])
        SEEN.append(f"GET {path}")
        if path == "/wyszukaj-podmiot":
            return self._send(200, "text/html; charset=utf-8", SPA.encode())
        if path == "/blocked":
            return self._send(200, "text/html", WAF)
        api = path.removeprefix(API_PREFIX)
        if api.endswith("/id-dokumentu-i-korekt"):
            ref = api.removeprefix("dokumenty/").removesuffix("/id-dokumentu-i-korekt")
            return self._send(200, "application/json", _corrections(ref))
        if api.startswith("dokumenty/"):
            return self._send(200, "application/json", _detail(api.removeprefix("dokumenty/")))
        return self._send(200, "application/json", b'{"osobyPodpisujace": []}')

    def do_POST(self) -> None:
        path = unquote(self.path.split("?")[0])
        SEEN.append(f"POST {path}")
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        api = path.removeprefix(API_PREFIX)
        if api == "podmioty/wyszukiwanie/dane-podstawowe":
            krs = body["numerKRS"]
            found = krs != UNKNOWN_KRS
            answer = {
                "podmiot": {"numerKRS": krs} if found else None,
                "czyPodmiotZnaleziony": found,
                "komunikatBledu": None,
            }
            return self._send(200, "application/json", json.dumps(answer).encode())
        if api == "dokumenty/wyszukiwanie":
            return self._send(
                200, "application/json", _list_page(body["krs"], body["page"], body["size"])
            )
        if api == "dokumenty/tresc":
            if body == [GONE]:
                return self._send(404, "text/plain", b"not found")
            return self._send(200, "application/octet-stream", _zip(body[0]))
        return self._send(404, "text/plain", b"unknown")

    def log_message(self, format: str, *args: object) -> None:
        pass


@pytest.fixture(scope="module")
def base_url() -> Iterator[str]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()


def _browser(base_url: str, entry_path: str = "/wyszukaj-podmiot") -> PlaywrightFilingBrowser:
    policy = rdf_policy(requests_per_minute=10_000)
    return PlaywrightFilingBrowser(
        RdfSpaSpec(entry_url=base_url + entry_path),
        limiter=in_memory_limiter(policy),
        policy=policy,
        think_time_seconds=(0.0, 0.0),
        wait=wait_none(),
    )


def _api_calls(since: int) -> list[str]:
    return [c.replace(API_PREFIX, "") for c in SEEN[since:] if API_PREFIX in c]


def test_selectors_match_the_recorded_dom():
    from playwright.sync_api import sync_playwright

    spec = RDF_SPA_SPEC
    results = (FIXTURES / "dom_results.html").read_text(encoding="utf-8")
    expanded = (FIXTURES / "dom_expanded_row.html").read_text(encoding="utf-8")
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        try:
            page = browser.new_page()
            page.set_content(results)
            assert page.locator(spec.krs_input).count() == 1
            assert page.locator(spec.search_button).count() == 1
            assert page.locator(spec.data_rows).count() == 10
            # one toggle per breakpoint; the adapter clicks the visible one
            assert page.locator(spec.data_rows).nth(3).locator(spec.expand_button).count() == 2
            assert page.locator(spec.next_page).count() == 1
            assert page.locator(spec.first_page).count() == 1
            assert page.locator(spec.rows_per_page).count() == 1
            assert page.locator(spec.expanded_panel).count() == 0

            page.set_content(expanded)
            assert page.locator(spec.data_rows).count() == 10  # the expansion is not a data row
            assert page.locator(spec.data_rows).nth(3).locator(spec.collapse_button).count() == 2
            panel = page.locator(spec.expanded_panel)
            assert panel.count() == 1
            assert panel.locator(spec.download_button).count() == 1
            assert "Pokaż zgłoszenie" not in panel.locator(spec.download_button).inner_text()
        finally:
            browser.close()


def test_listing_resizes_then_pages_and_pays_for_every_request(base_url: str):
    since = len(SEEN)
    with _browser(base_url) as browser:
        listing = browser.open_filing_list(BIG_KRS)
        assert browser.api_requests == 4
        assert browser.tokens_spent == 4
        assert browser.browser_version

    # search (entity + list at 5), resize to 10 (page 0), next page (page 1)
    assert _api_calls(since) == [
        "POST podmioty/wyszukiwanie/dane-podstawowe",
        "POST dokumenty/wyszukiwanie",
        "POST dokumenty/wyszukiwanie",
        "POST dokumenty/wyszukiwanie",
    ]
    assert json.loads(listing.entity.body)["czyPodmiotZnaleziony"] is True
    entries = parse_filing_list(BIG_KRS, [p.body for p in listing.pages])
    assert [e.document_ref for e in entries] == _refs(BIG_KRS)
    assert listing.pages[0].content_type == "application/json"


def test_small_and_unknown_entities(base_url: str):
    with _browser(base_url) as browser:
        small = browser.open_filing_list(SMALL_KRS)
        unknown = browser.open_filing_list(UNKNOWN_KRS)
        # an unknown entity sends one request, not the two paid for up front
        assert browser.api_requests == 3
        assert browser.tokens_spent == 4

    assert len(small.pages) == 1
    assert len(parse_filing_list(SMALL_KRS, [small.pages[0].body])) == 3
    assert unknown.pages == []
    assert json.loads(unknown.entity.body)["czyPodmiotZnaleziony"] is False


def test_expand_navigates_pages_and_download_reuses_the_expanded_row(base_url: str):
    with _browser(base_url) as browser:
        browser.open_filing_list(BIG_KRS)  # leaves page 1 (documents 11-12) showing
        since = len(SEEN)

        view = browser.open_document(BIG_KRS, CORRECTED)  # on page 0
        document = browser.download(BIG_KRS, CORRECTED)
        assert browser.api_requests == browser.tokens_spent

    assert _api_calls(since) == [
        "POST dokumenty/wyszukiwanie",  # first page
        f"GET dokumenty/{CORRECTED}/id-dokumentu-i-korekt",
        f"GET dokumenty/{CORRECTED}",
        "GET dokumenty/doc-03-korekta==",  # the extra tab's detail, paid after the click
        "POST dokumenty/tresc",
    ]
    detail = parse_document_detail(CORRECTED, view.corrections.body, view.detail.body)
    assert detail.correction_refs == [CORRECTED, "doc-03-korekta=="]
    assert detail.file_name == f"{CORRECTED}.xml"
    assert document.body == _zip(CORRECTED)
    assert list(view.related) == ["doc-03-korekta=="]
    assert json.loads(view.related["doc-03-korekta=="].body)["identyfikator"] == "doc-03-korekta=="
    assert document.request_body is not None
    assert json.loads(document.request_body) == [CORRECTED, "doc-03-korekta=="]


def test_download_without_expanding_first_expands_the_row(base_url: str):
    with _browser(base_url) as browser:
        since = len(SEEN)
        document = browser.download(SMALL_KRS, "small-2==")

    assert _api_calls(since) == [
        "POST podmioty/wyszukiwanie/dane-podstawowe",
        "POST dokumenty/wyszukiwanie",
        "GET dokumenty/small-2==/id-dokumentu-i-korekt",
        "GET dokumenty/small-2==",
        "POST dokumenty/tresc",
    ]
    assert document.body == _zip("small-2==")


def test_submission_view_never_reaches_the_server(base_url: str):
    with _browser(base_url) as browser:
        browser.open_document(SMALL_KRS, "small-1==")
        page = browser._require_page()  # pyright: ignore[reportPrivateUsage]
        since = len(SEEN)
        page.locator("button.submission").click()
        page.wait_for_timeout(500)

    assert not [c for c in SEEN[since:] if "zgloszenie" in c]


def test_document_missing_from_the_list_is_permanent(base_url: str):
    with _browser(base_url) as browser, pytest.raises(PermanentSourceError, match="not in RDF"):
        browser.open_document(SMALL_KRS, "doc-01==")


def test_client_error_on_download_is_permanent(base_url: str):
    with _browser(base_url) as browser, pytest.raises(PermanentSourceError, match="HTTP 404"):
        browser.download(BIG_KRS, GONE)


def test_gated_page_load_raises_content_check_failed(base_url: str):
    blocked = pytest.raises(ContentCheckFailed, match="imperva_incapsula_block_page")
    with _browser(base_url, entry_path="/blocked") as browser, blocked:
        browser.open_filing_list(BIG_KRS)
