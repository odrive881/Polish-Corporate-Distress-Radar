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
     - **c.** Only with explicit written confirmation that automated browser access is acceptable: the Playwright tier ADR 0004 described, human-paced, per entity.
   - Until then, no code path automates past the Incapsula challenge.
3. **The `ContentCheck` → `ContentCheckFailed` seam stays as built.** Whichever option is chosen, A3 must treat an HTTP-200 WAF page as a failure, never as a document. The probe's `detect_gate` is the reference check to promote into `document_retrieval.py`.

## Consequences

- Phase 1's deliverable ("raw documents in MinIO") is **blocked for financial statements**. A1/A2 (identity, BIR1 payloads in MinIO, manifest in Postgres) are unaffected. This is exactly the early discovery AGENT_SPEC §10 orders Phase 1 before Phase 2 to surface.
- `CLAUDE.md` § "Known moving targets" and AGENT_SPEC §11.1 said the `ekrs.ms.gov.pl/rdf/rd/` lookup "still works as A3 assumes". That is no longer true. `CLAUDE.md` now points here. ADR 0004 remains as the documentary record and is not superseded, since its documentary findings still hold.
- Re-run the probe notebook when the access question is answered, or if the WAF posture changes. It is cheap, low-rate, and stores what it sees.
- The C2 parsing work (Phase 2) can still proceed against `tests/fixtures/` golden documents obtained by hand through the public browser UI, as `neobis_001.xml` was.
