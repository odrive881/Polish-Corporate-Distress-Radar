# 0004 — RDF 2026 platform verification

- **Status:** accepted
- **Date:** 2026-09-13

## Context

`CLAUDE.md` and `AGENT_SPEC.md` §11 flag that RDF (Repozytorium Dokumentów Finansowych) was rebuilt in February 2026, and require confirming current access patterns before building stage A3 (document retrieval) against it, rather than assuming continuity with the previous portal. This must happen before Phase 1 acquisition work starts.

## Decision

Findings from live verification (web search + page fetches, September 2026):

- **The new RDF has been live since 23 February 2026.** It is a full rebuild of the filing/storage/access system for financial documents of KRS-registered entities, described by the Ministry of Justice as a new IT architecture for security and stability. It also now accepts new document types (ESG reports, tax documents) that are out of scope for this project.
- **Filing** happens through the Portal Rejestrów Sądowych (`prs.ms.gov.pl`, "RDF – Bezpłatne zgłaszanie dokumentów finansowych" tab). Not relevant to this project — we only read.
- **Public lookup/download — the access pattern this project actually needs — is confirmed per-entity and confirmed free:**
  - Search endpoint: `https://ekrs.ms.gov.pl/rdf/rd/` (also mirrored as a browser UI at `rdf-przegladarka.ms.gov.pl`).
  - Lookup is by KRS number, one entity at a time. No bulk listing or bulk export was found anywhere in official documentation — this matches the existing "not a bulk API" assumption in the glossary and `AGENT_SPEC.md` §6 A3, no design change needed there.
  - **No account or authentication is required** to search and download an entity's filed documents. This is good news for A3: no session/token handling is needed for read access (contrast with A2/GUS BIR1, which does require a SOAP session).
  - Documents are served in **XML and PDF** formats, as already assumed.
  - The search UI is explicitly **"protected against robot downloading activity"** (zabezpieczone przed pobieraniem przez roboty). This is the one real finding that changes a design assumption: A3 cannot necessarily be pure `httpx` + `hishel` + `pyrate-limiter` against a stable JSON/XML endpoint. It may require the same tiered fallback A1 already uses (`httpx` first, `Playwright` routed on failure) if the anti-bot measure turns out to be a challenge page rather than a simple rate limit. **This must be confirmed empirically against the live endpoint in Phase 1**, with `Playwright` as the documented fallback per the existing A1 pattern — no architectural surprise, just a routing decision to make with real traffic rather than in the abstract.
- No terms-of-use document specific to programmatic/automated access was found distinct from the general free-public-search framing (Ministry of Justice press materials describe it as free and open to anyone). Absent a published rate limit, this project keeps its existing default posture: polite request pacing via `pyrate-limiter`, `hishel` caching so re-runs never re-fetch unchanged documents, and `tenacity` retries with backoff distinguishing transient failures — unchanged from the AGENT_SPEC §6 A3 design.

## Consequences

- **A3's per-entity design stands as specified.** No change to `AGENT_SPEC.md` §6 A3 is needed.
- **One addition for A3's implementation (Phase 1), not a Phase 0 blocker:** detect anti-bot responses (challenge pages, unexpected non-200s, or content that fails a "did we get real content" check) and route to `Playwright` on failure, mirroring A1's existing tiering rather than inventing a new pattern.
- No authentication/session handling is needed for A3 read access, simplifying that adapter relative to A2 (GUS BIR1 SOAP session/token handling).
- This ADR should be revisited once Phase 1 actually drives real traffic against `ekrs.ms.gov.pl/rdf/rd/` — if the anti-bot measure proves stricter than a simple check (e.g. CAPTCHA), that finding gets its own follow-up ADR rather than silently changing A3's behavior.

**Sources:** [gov.pl — Repozytorium Dokumentów Finansowych](https://www.gov.pl/web/sprawiedliwosc/innowacyjne-i-bezpieczne-dziala-juz-nowe-repozytorium-dokumentow-finansowych), [gov.pl — Bezpłatne wyszukanie i pobranie dokumentu finansowego](https://www.gov.pl/web/sprawiedliwosc/bezplatne-wyszukanie-i-pobranie-dokumentu-finansowego-podmiotu-wpisanego-do-rejestru-przedsiebiorcow-krajowego-rejestru-sadowego), [PIT.pl — Nowy system RDF](https://www.pit.pl/aktualnosci/nowy-system-rdf-ale-zasady-skladania-dokumentow-finansowych-do-krs-bez-zmian), [Infor.pl — Nowe RDF w praktyce](https://ksiegowosc.infor.pl/obrot-gospodarczy/finanse-i-inwestycje/7606638,nowe-repozytorium-dokumentow-finansowych-w-praktyce-najczestsze-problemy-techniczne-przy-skladaniu-dokumentow-w-2026-roku.html), [taxpoint.pl — Nowe struktury logiczne a realia systemu RDF](https://www.taxpoint.pl/blog/nowe-struktury-logiczne-sprawozdan-finansowych-a-realia-systemu-rdf).
