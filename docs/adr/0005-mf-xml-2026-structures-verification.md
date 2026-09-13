# 0005 — Ministry of Finance 2026-generation XML structures verification

- **Status:** accepted
- **Date:** 2026-09-13

## Context

`CLAUDE.md` and `AGENT_SPEC.md` §11 flag a new generation of Ministry of Finance financial-statement XML structures and require confirming the published XSDs and their entity-type coverage before writing stage C2 mappings for that generation. This must happen before Phase 1 acquisition/parsing work is built on an assumption about which structures exist.

## Decision

Findings from live verification (web search + page fetches, September 2026):

- **New logical structures ("struktury logiczne") for e-financial statements have been published in the Centralne Repozytorium Wzorów Dokumentów Elektronicznych (CRWDE)**, the Ministry of Finance's electronic-document-template repository. This is the authoritative publication point going forward, not the older BIP page some search results still surface (that page's visible content dates to the 2018 rollout and schema 1.2 — stale, not the current source).
- **Correction to the trigger condition already stated in `CLAUDE.md` / `AGENT_SPEC.md` §11:** the new structures apply to financial statements for **fiscal years beginning 1 January 2025 or later** — not "fiscal year 2026" / "statements prepared from 1 January 2026" as both documents currently say. In practice this means: a calendar-year filer's FY2025 statement (prepared and filed in 2026) already uses the new structure. The two framings found in different sources ("applies to statements prepared from 2026" vs. "applies to fiscal years starting 2025-01-01") describe the same practical population once "prepared from 2026" is read as "for the fiscal year whose statement gets prepared during 2026" — but the precise, unambiguous trigger is the **fiscal year start date**, and it is one year earlier than the wording in both root documents implies. **This ADR supersedes that wording; `CLAUDE.md` and `AGENT_SPEC.md` §11 are updated as part of this change to say "fiscal years beginning 1 January 2025 or later."**
- **The version identifier is "wariant 2, wersja 1-0E."** This matters directly: the repo's one existing golden fixture, `tests/fixtures/neobis_001.xml`, is a 2026-generation document — its header carries `wersjaSchemy="1-0E"`, `WariantSprawozdania=2`, `OkresOd=2025-01-01`, and a `tns:` namespace dated `2025/08/07` under `crd.gov.pl` (CRWDE's namespace root). The prototype parser and its test were written without this being flagged; it means the repo's only fixture is **not** representative of the pre-2026 structures (`full-2018-v1`, `small-2018-v1`, `micro-2018-v1` in `DIRECTORY_STRUCTURE.md` §1's example filenames) and stage C2 will need fixtures for both generations, not just this one.
- The fixture's root filing type is `SprFinJednostkaInnaWZlotych` ("JednostkaInna" / other-unit structure), one of several parallel structures alongside the ones keyed to UoR Załącznik 1 (full), 4 (small), and 5 (micro). Confirming exactly which annex-structures apply to `sp. z o.o.` size classes in v1 scope, and whether `JednostkaInna` is one of them or the fixture is an atypical example, is **stage C2 work**, not resolved here — flagged as a follow-up rather than assumed.
- No definitive single-page list of every published structure version (with a full version-to-XSD-URL table) was retrievable through search; the CRWDE catalog is the canonical source and should be browsed directly at the start of C2, not re-derived from secondary commentary.

## Consequences

- `CLAUDE.md`'s "Known moving targets" entry and `AGENT_SPEC.md` §11 item 2 are corrected in this change to state the fiscal-year-2025 trigger precisely, citing this ADR.
- Stage C2 (`config/mappings/structures/`) must plan for at least two structure generations from the start: pre-2026 (e.g. `full-2018-v1.yaml`) and the new `wariant 2 / wersja 1-0E` generation — the existing `neobis_001.xml` fixture already covers the latter, but a pre-2026-generation fixture still needs to be added for coverage, and the `JednostkaInna` vs. Załącznik 1/4/5 question needs resolving before mapping specs are written.
- No change to A3 (acquisition) design — this ADR is about structure *content*, not the retrieval mechanism (see ADR 0004 for that).

**Sources:** [ksiegowosc.blog — Nowe struktury logiczne sprawozdań finansowych sporządzanych od stycznia 2026 roku](https://ksiegowosc.blog/2025/11/17/nowe-struktury-logiczne-sprawozdan-finansowych-sporzadzanych-od-stycznia-2026-roku/), [taxpoint.pl — Nowe struktury logiczne a realia systemu RDF](https://www.taxpoint.pl/blog/nowe-struktury-logiczne-sprawozdan-finansowych-a-realia-systemu-rdf), [PIBR — Ważna zmiana - nowe struktury logiczne](https://www.pibr.org.pl/pl/aktualnosci/2601,Wazna-zmiana-nowe-struktury-logiczne), [podatki.gov.pl — e-Sprawozdania Finansowe](https://www.podatki.gov.pl/e-sprawozdania-finansowe), and direct inspection of `tests/fixtures/neobis_001.xml`.
