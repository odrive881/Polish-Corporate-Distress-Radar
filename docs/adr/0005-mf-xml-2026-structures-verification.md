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

## Addendum, 2026-09-17: the structures as vendored (plan 0004 step A)

The official XSDs for every structure the Phase 1 seed uses were downloaded, with everything they import, and vendored under `config/xsd/` (51 files; `catalog.yaml` records each URL and SHA-256). What they settle:

- **`JednostkaInna` is the full form.** Its root documentation reads "ZAKRES INFORMACJI WYKAZYWANYCH W SPRAWOZDANIU FINANSOWYM, O KTÓRYM MOWA W ART. 45 USTAWY, DLA JEDNOSTEK INNYCH NIŻ BANKI, ZAKŁADY UBEZPIECZEŃ I ZAKŁADY REASEKURACJI": the UoR Annex 1 statement for ordinary companies. It is not an atypical case. It is what 13 of the 17 seed entities file.
- **A version is identified by `kodSystemowy` + `wersjaSchemy`, not by namespace.** The header fixes both, e.g. `SFJINZ (1)` / `1-2`. Schemas 1-0 (October 2018) and 1-2 share the namespace dated `2018/07/09`. Schema 1-3, used for most FY2024 statements, has its own namespace dated `2025/01/01`. The CRWDE templates (13817 full, 13821 micro) are `SFJINZ (2)` / `SFJMIZ (2)`, `1-0E`. In total the MF publishes 18 `Jednostka{Inna,Mala,Mikro}` × {złoty, thousands} × {1-0, 1-2, 1-3} schemas, plus the CRWDE templates.
- **The unit is part of the structure.** `…WZlotych` and `…WTysiacach` are separate schemas (`SFJINZ` vs `SFJINT`), with root `JednostkaInna` vs `JednostkaInnaWTys`. Thousands use integer amounts. The statement bodies are otherwise identical.
- **The full-form statutory line items are identical in every version** (1-0, 1-2, 1-3, wariant 2, and the thousands twins): 148 balance-sheet, 98 income-statement, 55 equity-changes and 119 cash-flow elements with the same paths. The differences:
  - Wariant 2 narrowed six income-statement lines from "towary i materiały" to "towary". Sales of materials moved out, so these are distinct facts (`.R2025` codes).
  - Schema 1-0 gave the cash-flow section headings A/B/C amounts that the UoR template does not have. Most filers wrote 0.00 there.
- **Amount columns:**
  - `KwotaA`: at the end of the current year.
  - `KwotaB`: at the end of the prior year.
  - `KwotaB1`: restated comparatives for the prior year ("przekształcone dane porównawcze").
  - `KwotaC` appears only in the additional tax information, not in the four statements.
- **Filers can add their own lines** (`PozycjaUszczegolawiajaca_N`) between the statutory children of most elements. Seed filers use them for real extra components, for "of which" breakdowns, and (in one case) for a whole old-format profit chain. Plan 0004 captures them as one `….USER` fact per element.
- **Not in the XML:**
  - Average employment has no structured field.
  - The notes are attached files (`Plik`).

This closes the open question in the Consequences above. Mapping specs are named by form, namespace year and schema version (`full-2018-v1-2`, `full-2025-w2-v1-0`), not `full-2026-v1`.
