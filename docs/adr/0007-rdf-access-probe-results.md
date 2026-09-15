# 0007 — RDF access probe results

- **Status:** proposed. The decision below needs a human call on terms of use before plan 0003 is written.
- **Date:** 2026-09-14
- **Follows up:** 0004

## Context

ADR 0004 confirmed from documentation that RDF offers free, per-entity, unauthenticated lookup by KRS number. It also flagged that the search UI is described as "protected against robot downloading activity". It deferred to Phase 1 the question of whether A3 can use plain `httpx`, needs the Playwright fallback tier, or is blocked outright. Plan 0002 §G ran that probe.

**Method:** `notebooks/exploration/rdf_access_probe.py`, run 2026-09-14 from a residential connection in Poland. It used the shared `distress_radar.acquisition.base` client (hishel cache, pyrate-limiter at 3 requests/minute, tenacity) with the project's honest User-Agent (`distress-radar/0.1 (research; low-rate per-entity lookups)`). Targets were three seed KRS numbers (0000163893, 0000507997, 0000277937) plus the RDF entry points. Response bodies went through `put_raw`. MinIO was not running, so they went to the in-memory store. Hosts were taken from ADR 0004 and from the Portal Rejestrów Sądowych's own `env.js` (`rdfUrl`, `rdfSearchUrl`). The probe deliberately made no attempt to get past anti-bot controls: no browser User-Agent spoofing, no cookie replay, no headless browser.

## Findings

| Target | Result |
|---|---|
| `https://ekrs.ms.gov.pl/rdf/rd/` (ADR 0004's endpoint) | `302` → `https://prs.ms.gov.pl/rdf-informacja`, an Angular SPA *information* page (200, 24 kB shell). **The old lookup endpoint no longer serves search.** |
| `https://rdf.ms.gov.pl/` | `200`, 5.8 kB **Imperva Incapsula block page** (`x-iinfo` header, `visid_incap_*` / `incap_ses_*` cookies, `_Incapsula_Resource` script) |
| `https://rdf-przegladarka.ms.gov.pl/` (current public search, per PRS `env.js`) | `200`, ~1 kB Incapsula block page: *"Request unsuccessful. Incapsula incident ID: …"* inside a challenge iframe |
| `…/wyszukaj-podmiot?krs=<KRS>` ×3 and an API-shaped path under `/services/rdf/…` | Identical Incapsula block page for every path, including non-UI paths: the whole host is behind the WAF |

- **Status codes:** every gated response is **HTTP 200**. A status-code check alone would accept the block page as content. The `ContentCheck` seam in `base.py` detects it: `ContentCheckFailed(imperva_incapsula_block_page)` on every RDF target.
- **Challenge type:** Imperva Incapsula bot management. The response is an interstitial, not a rate limit. It was served on the first request from a fresh client, with no prior traffic. Whether the iframe resolves to a JavaScript proof-of-work or a CAPTCHA could not be observed without executing it, which the probe did not do.
- **Document list shape (JSON/XML/HTML), download URL shape, submission date (`known_from`) exposure:** **not observable.** No request reached the application behind the WAF.
- **Official alternatives seen:** the PRS config also advertises the KRS open API (`api-krs.ms.gov.pl`, e.g. `OdpisAktualny`). It serves registry extracts, not financial statements, so it does not replace RDF for A3. It may matter for A4/registry dynamics later.

## Decision

1. **Plain `httpx` does not work for RDF.** A3 cannot be built as an httpx-only adapter.
2. **Whether to add a Playwright tier is not an engineering routing decision. It needs a human decision on terms of use.** ADR 0004 anticipated a JS gate that a browser tier would simply render. What the probe found is a commercial bot-management WAF that blocks automated clients on every path. RDF's own description says it is protected against robot downloading. Driving a headless browser to pass that challenge would deliberately defeat an access control, which is more than "routing on failure". AGENT_SPEC §11.3 and §12 require acquisition to stay within the source's terms. So:
   - **Plan 0003 is on hold** until someone decides between these options (in order of preference):
     - **a.** Ask the Ministry of Justice (RDF operator) for sanctioned programmatic or research access, or an allow-listed client, at the project's modest rate. Record the answer.
     - **b.** Look for a sanctioned bulk or partner channel for financial statements, e.g. licensed data from a registry aggregator that obtains them legitimately. Record licence terms.
     - **c.** The Playwright tier ADR 0004 described, human-paced, per entity.
3. **The `ContentCheck` → `ContentCheckFailed` seam stays as built.** Whichever option is chosen, A3 must treat an HTTP-200 WAF page as a failure, never as a document. The probe's `detect_gate` is the reference check to promote into `document_retrieval.py`.

## Option C in detail: a human-paced Playwright tier

This section says what option C would involve, so it can be weighed against a and b. It is not a decision. Status stays `proposed`.

### What it is

- A real Chromium browser, driven through Playwright's Python async API, uses the public `rdf-przegladarka.ms.gov.pl` UI the way a person would:
  1. open the page;
  2. search by KRS;
  3. open the filing list;
  4. download each document.
- The browser runs Imperva's JavaScript challenge as it would for any visitor. The session cookies it gets (`visid_incap_*`, `incap_ses_*`) stay inside that browser context.
- Playwright is already in the locked stack (AGENT_SPEC "Browser fallback: Playwright"). This is not a stack substitution.

### Explicit limits: what option C does *not* include

- No CAPTCHA-solving services, and no manual-solve relays built into the pipeline.
- No stealth or fingerprint-spoofing plugins, no User-Agent forging, no proxy or IP rotation.
- No exporting browser cookies into `httpx` (cookie replay) to skip the browser.
- No parallel browser contexts to get around pacing.

If an honest, real browser at human pace is still blocked or gets a CAPTCHA, option C has **failed**. The run stops, and the project falls back to option a or b. It does not escalate.

### How it would fit A3 (shape for plan 0003)

- **Adapter.** `acquisition/document_retrieval.py` gets an RDF adapter behind a small `FilingBrowser` Protocol, with a Playwright implementation and a fake for tests. This mirrors `ObjectStore` (`raw_store.py`) and `Bir1Service` (`regon_client.py`).
- **Browser-only for RDF.** Every RDF host is gated, so RDF is fetched through the browser only, not "httpx first, browser on failure". The `ContentCheck` → `ContentCheckFailed` seam (`base.py`) still runs on every page and every download, with the probe's `detect_gate` as the reference check. A WAF page is always a failure, never a document.
- **Filing list.** Capture the SPA's own XHR/JSON responses (`page.on("response")`) rather than scraping the DOM:
  - they are sturdier than CSS selectors;
  - the submission date (`known_from`) should appear there.

  The list response itself is raw-stored through `put_raw`.
- **Documents.** Download via `page.expect_download()` or the context's `APIRequestContext`, which shares the browser session.
  - Bytes go to `put_raw` unmodified.
  - The sidecar records `fetch_tier: playwright`, the browser version, and the document URL.
  - Manifest rows follow the existing `raw_documents` / `raw_document_fetches` pattern.
- **Pacing.**
  - One serial browser with one context.
  - A `pyrate-limiter` `PostgresBucket` keyed per *entity*, using `RDF_REQUESTS_PER_MINUTE` (e.g. 1–2 entities/minute).
  - Randomised think-time between UI actions, and a daily cap.
- **Circuit breaker.** After N consecutive `ContentCheckFailed` or CAPTCHA results, stop the whole run instead of retrying entity by entity.
- **Failure taxonomy.**
  - Timeouts → `TransientSourceError`, with bounded retries.
  - Unresolved challenge, CAPTCHA, or block → `PermanentSourceError` (`rdf_access_blocked`). The entity stays unresolved for a later run, like A2 source errors.
  - "Entity has no filings in RDF" → a reason-coded `quarantine` row.

### Operational cost

- **Memory.** Chromium uses about 300–500 MB of RAM per context.
- **Install.** `playwright install chromium --with-deps` pulls system libraries under WSL. A later Dagster container would build on the `mcr.microsoft.com/playwright/python` base image.
- **Throughput.** At about 1 entity/minute, a v1 universe of a few thousand entities is roughly 2–4 days of wall-clock backfill, spread over sessions. Incremental runs are small afterwards, because statements arrive annually.

### Testing

- `make check` stays network-free: tests use the fake `FilingBrowser` with recorded list JSON and document fixtures.
- An optional `@pytest.mark.integration` test drives Playwright against local HTML served through `page.route`, never against RDF.

### Risks

- Imperva may flag automated Chromium even at low rates with honest behaviour. If so, option C does not work.
- Terms and WAF posture can change. Re-run the probe notebook before each backfill.
- The SPA's structure is fragile. That is why capture relies on its XHR responses first.
- A browser tier is slower and harder to debug than `httpx`.

### A no-automation next step, useful for every option

A human opens `rdf-przegladarka.ms.gov.pl` in an ordinary browser, looks up one seed KRS, and saves a DevTools HAR. That is normal manual use of the public UI. It answers the probe's "not observable" findings:
- the list endpoint shape;
- the download URL shape;
- whether a submission date is exposed.

It de-risks plan 0003 whichever option is chosen. Store the HAR sanitised, with cookies and session tokens stripped, under `tests/fixtures/`.

## Consequences

- Phase 1's deliverable ("raw documents in MinIO") is **blocked for financial statements**. A1/A2 (identity, BIR1 payloads in MinIO, manifest in Postgres) are unaffected. This is exactly the early discovery AGENT_SPEC §10 orders Phase 1 before Phase 2 to surface.
- `CLAUDE.md` § "Known moving targets" and AGENT_SPEC §11.1 said the `ekrs.ms.gov.pl/rdf/rd/` lookup "still works as A3 assumes". That is no longer true. `CLAUDE.md` now points here. ADR 0004 remains as the documentary record and is not superseded, since its documentary findings still hold.
- Re-run the probe notebook when the access question is answered, or if the WAF posture changes. It is cheap, low-rate, and stores what it sees.
- The C2 parsing work (Phase 2) can still proceed against `tests/fixtures/` golden documents obtained by hand through the public browser UI, as `neobis_001.xml` was.
