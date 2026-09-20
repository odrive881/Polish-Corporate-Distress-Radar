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
  - Wariant 2 narrowed six income-statement lines from "towary i materiały" to "towary". Sales of materials moved out, so these are distinct facts (`.R2025` codes). **Corrected 2026-09-20 (second addendum): schema 1-3 narrows the same six lines. This bullet said wariant 2 only, and plan 0005 step A shipped `full-2025-v1-3` without the `.R2025` overrides on the strength of it.**
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

---

## Second addendum, 2026-09-20 — the short forms (plan 0005 step B)

Research for plan 0005, which maps `JednostkaMala` and `JednostkaMikro`. Everything below is regenerated by
`notebooks/exploration/short_form_line_classification.py`, which reads only the vendored XSDs and the committed
full-form body. Re-run it when a schema version is added; if its output stops matching this addendum, this
addendum is what is stale.

### 1. Which versions share an element tree

| Form | `Bilans` | `RZiS` | `ZestZmianWKapitale` | `RachPrzeplywow` |
|---|---|---|---|---|
| `JednostkaInna` | 148 | 98 | 55 | 119 |
| `JednostkaMala` | 46 | 63 | **not declared** | **not declared** |
| `JednostkaMikro` | 13 | 16 (1-0E, 1-2) / 13 (1-3, w2) | **not declared** | **not declared** |

- **Small and micro entities file no cash-flow and no equity-changes statement.** The schemas do not declare
  them at all, so their absence is not a filer omission and must never be graded as one.
- **`JednostkaMala` is stable across 1-0E / 1-2 / 1-3 / wariant 2 in structure**, and `Bilans` is stable in
  labels too. Its `RZiS` is relabelled in 1-3 and wariant 2 — see §3. One body serves all four versions.
- **`JednostkaMikro`'s `RZiS` is not stable.** 1-3 and wariant 2 drop `G`, `G/G_I` and `G/G_II`
  ("Wynik finansowy netto ogółem", the variant for entities under UoR art. 3(1a)(2) — non-profit-type micro
  units) and relabel `F`. `Bilans` is identical across all four. This is the one place where a second body,
  or a version-conditional body, is unavoidable.
- `RZiS` keeps both variants (`RZiSKalk`, `RZiSPor`) in the small form. The micro form has neither: its income
  statement is a single layout with no variant choice (see §4).

### 2. Line-by-line classification against the full form

Counts from the notebook, comparing each short-form element path with the full form's at the same path:

| Form | same line | path collides, meaning differs | no such path in the full form |
|---|---|---|---|
| `JednostkaMala` | 30 | 46 | 33 |
| `JednostkaMikro` | 4 | 4 | 21 |

**A shared path is not a shared line.** For `JednostkaMikro/Bilans` all four collisions are the short form
adding a "w tym" qualifier to an aggregate the full form states plainly:

| Path | Full form | Micro form |
|---|---|---|
| `Aktywa/Aktywa_A` | Aktywa trwałe | Aktywa trwałe, **w tym środki trwałe** |
| `Aktywa/Aktywa_B` | Aktywa obrotowe | Aktywa obrotowe, **w tym:** |
| `Pasywa/Pasywa_A` | Kapitał (fundusz) własny | Kapitał (fundusz) własny, **w tym:** |
| `Pasywa/Pasywa_B` | Zobowiązania i rezerwy na zobowiązania | Zobowiązania i rezerwy na zobowiązania, **w tym:** |

The amount is the same concept in each case (total fixed assets, total current assets, total equity, total
liabilities and provisions), so these **reuse** `BS.ASSETS.A`, `BS.ASSETS.B`, `BS.EQUITY_LIABILITIES.A` and
`BS.EQUITY_LIABILITIES.B`. What follows them does not: the micro form's children are `of_which` breakdowns,
not summing components, and must be marked so or `subtotals_consistent` will fail every correct micro filing.

| Micro path | Label | Treatment |
|---|---|---|
| `Aktywa/Aktywa_B/Aktywa_B_1` | – zapasy | `of_which`, reuse `BS.ASSETS.B.I` |
| `Aktywa/Aktywa_B/Aktywa_B_2` | – należności krótkoterminowe | `of_which`, reuse `BS.ASSETS.B.II` |
| `Pasywa/Pasywa_A/Pasywa_A_1` | – kapitał (fundusz) podstawowy | `of_which`, reuse `BS.EQUITY_LIABILITIES.A.I` |
| `Pasywa/Pasywa_B/Pasywa_B_1` | – rezerwy na zobowiązania | `of_which`, reuse `BS.EQUITY_LIABILITIES.B.I` |
| `Pasywa/Pasywa_B/Pasywa_B_2` | – zobowiązania z tytułu kredytów i pożyczek | **new code**: merges the full form's long- and short-term credit/loan lines, which it has no equivalent of |

### 3. The narrowing reaches further than the first addendum said

Schema **1-3 and wariant 2** both narrow income-statement lines from goods *and materials* to goods only. The
element names and positions do not change, so **only the XSD documentation reveals it**:

| Form | Lines narrowed in 1-3 and w2 |
|---|---|
| `JednostkaInna` | `RZiSKalk/A`, `RZiSKalk/A/A_II`, `RZiSKalk/B`, `RZiSKalk/B/B_II`, `RZiSPor/A/A_IV`, `RZiSPor/B/B_VIII` |
| `JednostkaMala` | `RZiSKalk/A`, `RZiSKalk/B`, `RZiSPor/B/B_VI/B_VI_1` |

The first addendum attributed this to wariant 2 alone, and plan 0005 step A mapped `full-2025-v1-3` without
the `.R2025` overrides as a result — 32 facts merged into codes the chart says are distinct, with no identity
check able to see it. Fixed in plan 0005, and guarded by `test_every_code_label_matches_its_xsd_label`, which
asserts each element's mapped chart label against the XSD's own. **The small-form specs for 1-3 and wariant 2
need their own overrides for the three lines above**, and that test is the acceptance criterion.

### 4. The micro income statement is a third layout

All 16 of its `RZiS` elements are absent from the full form. It is neither the comparative nor the calculation
variant: `A` is "Przychody podstawowej działalności operacyjnej i zrównane z nimi", `B` "Koszty podstawowej
działalności operacyjnej" broken into amortisation, materials and energy, wages and social security, and other
costs. It therefore needs its own code family, and `variant` stays `n/a` (AGENT_SPEC §5's enum is unchanged:
the micro form offers no variant to choose, so no new enum value is warranted).

### 5. CRWDE templates

Probing the template range settles what plan 0005 finding 6 flagged as missing:

| Template | Date | Root | System code |
|---|---|---|---|
| `13817` | 2025-08-07 | `JednostkaInna` | `SFJINZ (2)` — mapped |
| `13818` | 2025-08-07 | `JednostkaMala` | `SFJMAZ (2)` — **now catalogued** |
| `13819` | 2025-08-11 | `JednostkaMikroWTys` | `SFJMIT (2)` — **now catalogued** (the thousands twin) |
| `13820` | 2025-08-12 | `JednostkaOp` | public-benefit organisations, outside the v1 `sp. z o.o.` scope |
| `13821` | 2025-08-12 | `JednostkaMikro` | `SFJMIZ (2)` — catalogued |

Both new templates were vendored with their SHA-256; every schema they import was already vendored. No seed
filing uses either, so they are catalogued, not mapped: a filing on one is recorded as `not_yet_mapped` rather
than quarantined as an unknown structure.
