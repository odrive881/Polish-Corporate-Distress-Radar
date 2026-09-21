# 0005 — Phase 3a: the remaining structure versions (small, micro, full 1-3)

**Stage:** Phase 3 (AGENT_SPEC.md §10), first of three plans: C1 and C2 (§6C) for every structure version left in the seed, and E2 (§4.3) grading over the forms that file no cash-flow statement. Phase 3's other two parts are the C3 PDF tier (plan 0006) and SQLMesh `quarantine` + `dq_mart` (plan 0007).

**Order:** this plan first, then 0006, then 0007. Plan 0006's single in-scope PDF turned out to be a rendered **small-form** statement (`SprFinJednostkaMalaWZlotych`, schema 1-2), so its extraction target is the `jednostka_mala` body and chart codes this plan introduces. Plan 0007 aggregates the canonical table and wants its final shape. **Superseded 2026-09-21:** plan 0006 is deferred (its one document's figures are already in the warehouse as the next year's comparative column, and both filings post-date the entity's bankruptcy), so 0007 follows this plan directly.

## Status: complete — steps A–F (2026-09-20), G–H (2026-09-21)

## Why

Phase 2 (plan 0004) mapped the four full-form versions and parses 81 of the seed's 131 statement files. 49 files are recorded `not_yet_mapped` and one `needs_pdf_tier`. The unmapped files are not an edge case:

**Four of the 17 seed entities have no canonical facts at all** — `0000041651`, `0000225354`, `0000277937`, `0000507997`. They file small or micro forms exclusively, so today they are invisible to every downstream stage. A fifth, `0000181328`, has one parsed file against nine unmapped and one PDF. Until this plan lands, the v1 universe is effectively 13 entities, not 17, and the "≥3 filed years" criterion is being judged on a partial view.

## What is left to map (survey, 2026-09-20)

From `parsed_documents`, every file with status `not_yet_mapped`:

| Form | Schema | Files | Entities |
|---|---|---|---|
| `JednostkaMala` (2018 ns) | 1-2 | 19 | 4 |
| `JednostkaMala` (2018 ns) | 1-0E | 4 | 4 |
| `JednostkaMala` (2025/01 ns) | 1-3 | 7 | 3 |
| `JednostkaMikro` (2018 ns) | 1-2 | 7 | 2 |
| `JednostkaMikro` (2025/01 ns) | 1-3 | 3 | 2 |
| `JednostkaMikro` (2018 ns) | 1-0E | 1 | 1 |
| CRWDE Mikro `13821` | 1-0E | 1 | 1 |
| `JednostkaInna` (2025/01 ns) | 1-3 | 7 | 7 |
| **Total** | | **49** | **13 distinct** |

Fiscal years 2018–2025, weighted to 2024 (13 files) and the 2018–2022 span (28 files).

### Findings that drive the design

1. **Full-form 1-3 is element-for-element identical to 1-2.** Checked with `xsd_inventory` across all four statements: `Bilans` 148/148, `RZiS` 98/98, `ZestZmianWKapitale` 55/55, `RachPrzeplywow` 119/119 — identical paths, identical `has_amounts` and `user_slots` flags. It is a one-file spec on the existing `jednostka_inna` body, and it unlocks 7 entities. Do it first; it is the cheapest coverage in the plan.

2. **Small and micro are genuinely different statements, not subsets.**

   | | `Bilans` | `RZiS` | `ZestZmianWKapitale` | `RachPrzeplywow` |
   |---|---|---|---|---|
   | `JednostkaInna` | 148 | 98 | 55 | 119 |
   | `JednostkaMala` | 46 | 63 | **absent** | **absent** |
   | `JednostkaMikro` | 13 | 16 | **absent** | **absent** |

   Small and micro entities are exempt from the cash-flow and equity-changes statements, and the schemas do not declare them. `RZiS` keeps both variants (`RZiSKalk`, `RZiSPor`) in the small form.

3. **Element paths collide across forms while meaning differs.** 36 of the 46 small-form `Bilans` paths and 8 of the 13 micro-form paths also occur in the full form, but they are not the same lines. `Aktywa/Aktywa_A` is "Aktywa trwałe" in the full and small forms, and "Aktywa trwałe, **w tym środki trwałe**" in the micro form. `Aktywa/Aktywa_B/Aktywa_B_1` is "– zapasy", an of-which breakdown, where the full form's `Aktywa_B_I` "Zapasy" is a summing component. **Sharing one body across forms would mis-map silently and pass every identity check.** Each form needs its own body file.

4. **The existing architecture already absorbs this.** Plan 0004 made the chart vocabulary only (554 codes, no hierarchy) and put parents, of-which flags and formulas in the per-structure body. So the small and micro bodies reuse the chart codes for lines that mean the same thing, mark their own of-which lines, and declare their own (shorter) subtotal trees. Only genuinely different aggregates need new codes.

5. **A few micro lines have no full-form equivalent.** `Pasywa/Pasywa_B/Pasywa_B_2` is "zobowiązania z tytułu kredytów i pożyczek" across both maturities, where the full form splits credits and loans under long- and short-term liabilities separately. These need their own codes. The `code_overrides` mechanism built for wariant 2's `.R2025` codes covers the pattern.

6. **The CRWDE small template is not catalogued.** Of the three wariant-2 templates, the full form (`13817`) is mapped and the micro form (`13821`, `SFJMIZ (2)`) is catalogued, but there is no `SFJMAZ (2)` entry anywhere. No seed file uses it, so nothing is broken today; a CRWDE small filing would quarantine as `unknown_structure_version`. Add it while the catalogue is open.

## Decisions this plan makes (flag any you disagree with before step C)

1. **Two new bodies, eight new specs.** Bodies: `jednostka_mala.yaml` and `jednostka_mikro.yaml`; full 1-3 reuses the existing `jednostka_inna.yaml`. Specs: `full-2025-v1-3`, `small-2018-v1-0`, `small-2018-v1-2`, `small-2025-v1-3`, `micro-2018-v1-0`, `micro-2018-v1-2`, `micro-2025-v1-3`, `micro-2025-w2-v1-0`. Names follow the `DIRECTORY_STRUCTURE.md` §5 rule as corrected in commit `aa574e1`: the year is the namespace year, with no month segment. The MF 2025/01/01 generation and the CRWDE 2025 one are told apart by the `-w2` marker, not by the date.
   - The `…WTysiacach` twins of each are **catalogued, not mapped**, exactly as `full-2018-v1-2-tys` was mapped only to exercise unit normalisation. One thousands spec in the project is enough to keep that test honest.

2. **Chart codes are reused wherever the line means the same thing**, and new codes are added only where the aggregation differs. Each new code states in its `label_pl` which form it belongs to, and carries `replaces` where it merges full-form lines, following the `.R2025` precedent. A line present in a short form but absent from the full form never borrows a full-form code "close enough" to it.

3. **Absent statements are absent, not empty.** A small or micro filing produces no `cash_flow` or `equity_changes` rows at all. It is not graded down for that, and `cashflow_ties` already treats a missing cash-flow statement as not-applicable rather than a failure (plan 0004 step F). No zero rows are written (invariant 4).

4. **The form a company files is evidence, not a size classification.** §4.4 requires size class to be computed from balance-sheet total, net revenue and average employment, never taken from a label. A filer choosing the micro form is asserting a size class, and filers get this wrong. This plan records the filed form as `structure_version` (it already does) and does **not** derive `entity_size_class_history` from it. Size classification stays blocked on the average-employment decision (`docs/data_inventory.md` §8).

5. **Grading rules are unchanged.** The §4.3 checks that apply — `balance_sheet_balances`, `subtotals_consistent`, `profit_ties`, `prior_year_consistency` — run as they do today. Materiality is still measured against the file's own total assets, which is what makes the rule transfer to a micro balance sheet without retuning.

6. **No re-parse of the full-form files.** Adding specs changes the mapping-config hash, which by design gives every file a new `spec_hash` row in `parsed_documents` and a new `ingestion_run_id`. The canonical **values** must not move: step H checks a column-wise hash before and after, as the ADR 0009 migration did.

## Out of scope

- The C3 PDF tier (plan 0006) and the one `needs_pdf_tier` file.
- SQLMesh, `quarantine` as a model, `dq_mart` (plan 0007).
- Size classification and `entity_size_class_history` (§4.4) — blocked on average employment.
- The notes (`Plik` attachments) and single-line breakdowns — stage G, Phase 7.
- Any new acquisition. The 49 files are already stored.
- The `…WTysiacach` twins as mapped specs, and the 3 pre-2018 PDFs (out of v1 scope).

## Steps

### A. Full-form 1-3 (smallest useful increment, land it on its own)

- Add `config/mappings/structures/full-2025-v1-3.yaml`: the `jednostka_inna` body, the `2025/01/01/JednostkaInnaWZlotych` namespace, `kod_systemowy: "SFJINZ (1)"`, `wersja_schemy: "1-3"`, the already-vendored XSD, and the same `columns` map as 1-2.
- Remove that entry from `structure_catalog.yaml`.
- Extend the `test_body_lists_exactly_the_statutory_elements` parametrisation to the new version. That test is what proves finding 1 holds; it must pass with no body edits.
- Materialize and confirm 7 files move from `not_yet_mapped` to `valid`, covering 7 entities.

**Done, 2026-09-20.** `full-2025-v1-3` mapped (not `full-2025-01-v1-3`: the §5 naming rule takes the namespace
year with no month segment, and `-w2` is what separates this generation from the CRWDE one).

- **Finding 1 held.** `test_body_lists_exactly_the_statutory_elements` passes for the new version against the 1-3
  XSD with no body edits, so the shared `jednostka_inna` body is confirmed through the real test path, not just
  the ad-hoc comparison in the survey.
- **Namespaces:** `jin` moves to `…/2025/01/01/JednostkaInnaStruktury`, but `dtsf` (the amount types) stays on
  the 2018/07/09 namespace, which the 1-3 schema still imports. Header XPaths are unchanged.
- **Golden fixture:** `full_2025_v1_3_por_2024.xml`, KRS 0000498679 FY2024 (293,907 → 35,190 bytes; no
  signatures present, one notes attachment replaced, personal-data scan clean). Chosen because that entity
  already has 2018/2022/2023 fixtures, so the set now carries a `prior_year_consistency` pair that crosses
  from schema 1-2 to 1-3.
- **Cross-generation verification:** the FY2024 file's `prior_year` column and the FY2023 file's `current_year`
  column share **117 line items and differ on none** — the old and new specs agree exactly on the same
  company's same figures, parsed through different namespaces. (117, not the 119 first recorded here: the two
  narrowed comparative lines correctly stopped sharing a code once the correction below landed.)

**Correction, 2026-09-20 — step A shipped `full-2025-v1-3` with `code_overrides: {}`, which was wrong.**
Found while doing step B's label diff for the short forms. Schema 1-3 narrows the *same six* income-statement
lines wariant 2 narrows, from goods **and materials** to goods only: `RZiSKalk/A`, `RZiSKalk/A/A_II`,
`RZiSKalk/B`, `RZiSKalk/B/B_II`, `RZiSPor/A/A_IV`, `RZiSPor/B/B_VIII`. Element names and positions are
unchanged, so **only the XSD documentation gives it away**, and
`test_body_lists_exactly_the_statutory_elements` compares paths and flags but not labels — as did the ad-hoc
check behind finding 1. The result was 32 facts across 6 entities merged into the pre-2025 codes that
`canonical_chart.yaml` says must stay distinct. Grades did not move (3/2/2), because no identity check can see
the difference: this is finding 3's silent-mis-map risk, realised on the full form rather than the short ones.

- **Fixed:** the spec now carries the same eight `code_overrides` as `full-2025-w2-v1-0`. The 32 facts move to
  `IS.CALC.A.R2025`, `IS.CALC.A.II.R2025`, `IS.CALC.B.R2025`, `IS.CALC.B.II.R2025`, `IS.COMP.A.IV.R2025`,
  `IS.COMP.B.VIII.R2025`. Row count, file count and the Phase 2 hash are unchanged.
- **Guarded:** new test `test_every_code_label_matches_its_xsd_label` asserts that each element's mapped chart
  label is the XSD's own label, for every mapped version (the six `header: true` cash-flow headings are skipped,
  since plan 0004 gave them a note of ours). Verified to fail on the original spec and pass on the fixed one.
  **Step C must treat this as the acceptance test for the short-form bodies**, because Mala has the same
  narrowing in 1-3 and wariant 2.
- **Seed result:** 7 files `not_yet_mapped` → `valid`; canonical table 81 → 88 files, 33,490 → 36,466 rows.
  Current-config status is now 88 `valid`, 42 `not_yet_mapped`, 1 `needs_pdf_tier`.
- **Decision 6 held:** the column-wise hash over every Phase 2 full-form row
  (`7d6d0dee0b7f392d88f736a30733ed2ade82c9914c344bf5e4d70a4bcc4c46b2`, 33,490 rows) is unchanged.
- **Grades:** 3 `pass`, 2 `warn`, 2 `quarantined`. Both quarantines traced to the filed XML, neither a mapping error:
  - **0000198429:** `B_I` = 0.00 and `B_II` = 767,269.38, so investing net flow `B_III` must be −767,269.38; it is
    filed as **+767,269.38**. The filer's own `D` (−364,245.22) equals A.III + (−767,269.38) + C.III exactly, so
    the sign was correct everywhere except the line itself.
  - **0000440028:** the income statement reports a net loss of 2,457.10 (`L`) while the balance sheet's net-result
    line is 0.00; and revenue `A` is filed as 520.00 with every component `A_I`–`A_IV` at 0.00.

  The two `warn` files are the same immaterial gaps these entities showed in Phase 2 (0000188883's 158.32 split,
  0000209396's 49.49 cash-flow rounding).
- **Idempotence:** re-materializing left all 14 Parquet files byte-identical. `make check` 278 passed,
  `make test-integration` 28 passed.

### B. Structure research for the short forms (no pipeline code)

- From the vendored small and micro XSDs, produce for each form and schema version the full statutory element list with labels, via `xsd_inventory`. Confirm across 1-0E / 1-2 / 1-3 whether the short forms are as stable as the full form is (finding 1 checked only the full form).
- Diff each short-form element against the full form's and classify every line: **same meaning** (reuse the code), **of-which where the full form sums** (reuse the code, set `of_which`), **merged aggregate** (new code, with `replaces`), **no equivalent** (new code).
- Read the CRWDE `13821` (micro wariant 2) schema the same way and record whether it narrows lines the way the full-form wariant 2 did (the `.R2025` case).
- Record the classification in an **ADR 0005 second addendum**, as the table that justifies every new chart code. Nothing in step C should need a judgement call that is not written down here first.
- Add `SFJMAZ (2)` (CRWDE small) to `structure_catalog.yaml` with its XSD, vendored alongside.

**Done, 2026-09-20.** Findings are in the **ADR 0005 second addendum**, regenerated by
`notebooks/exploration/short_form_line_classification.py` (read-only: vendored XSDs and the committed body,
no database, no network). Headlines, all of which change step C:

- **Small and micro declare no cash-flow and no equity-changes statement at all.** Their absence is structural,
  never a filer omission. Decision 3 holds and now has a schema-level reason.
- **`JednostkaMala` needs one body for all four versions**; `Bilans` (46 lines) is identical everywhere.
- **`JednostkaMikro` needs two**, or a version-conditional one: its `RZiS` has 16 lines in 1-0E/1-2 and 13 in
  1-3/wariant 2, which drop the `G` "Wynik finansowy netto ogółem" block (UoR art. 3(1a)(2) units). `Bilans`
  (13 lines) is identical everywhere. This is the risk the plan flagged as "one body per schema version, not
  per form" — it has materialised, but only for micro `RZiS`.
- **Classification counts** (short-form element vs the full form's at the same path): Mala 30 same / 46 collide
  / 33 absent; Mikro 4 / 4 / 21. The collisions are real: the micro form adds a "w tym" qualifier to four
  aggregates and hangs `of_which` children off them where the full form has summing components. The addendum
  tables every one.
- **The micro income statement is a third layout**, neither comparative nor calculation, so it gets its own
  code family with `variant: "n/a"`. AGENT_SPEC §5's enum needs no new value.
- **Only one genuinely new balance-sheet code** is required for micro:
  `Pasywa/Pasywa_B/Pasywa_B_2` ("zobowiązania z tytułu kredytów i pożyczek") merges the full form's long- and
  short-term credit and loan lines.
- **The narrowing is wider than the first addendum said** — see the step A correction above. `JednostkaMala`
  narrows three `RZiS` lines in 1-3 and wariant 2, so **those two small-form specs need `code_overrides` too**,
  and `test_every_code_label_matches_its_xsd_label` is step C's acceptance test.
- **CRWDE templates settled** (finding 6): `13818` = small `SFJMAZ (2)`, `13819` = micro thousands
  `SFJMIT (2)`, `13820` = `JednostkaOp` (out of v1 scope). 13818 and 13819 are vendored with their SHA-256 and
  catalogued; every schema they import was already vendored.

### C. Chart and bodies

- Extend `config/mappings/canonical_chart.yaml` with the codes step B identified. Keep the UoR numbering convention; prefix nothing with the form name unless the line genuinely exists only in that form.
- Write `config/mappings/structures/bodies/jednostka_mala.yaml` and `jednostka_mikro.yaml`, each declaring only the statements its schema has, with its own subtotal tree, `of_which` flags, `user_code` slots and `required: true` items.
- `required: true` is set conservatively: total assets, total equity and liabilities, and the net result. A micro balance sheet has 13 lines and little room for a filing to be usefully partial, but quarantining on an optional line would violate invariant 4's intent.

**Done, 2026-09-20.** Chart 554 → **600 codes**; three new bodies (the micro split was needed, as step B
predicted). Every body verified element by element against **all four** of its schema versions — paths,
`section`, user slots, `user_of_which`, and chart-label-against-XSD-label: **0 problems**.

| Body | Versions | `Bilans` | `RZiS` |
|---|---|---|---|
| `jednostka_mala` | 1-0E, 1-2, 1-3, w2 | 46 | 63 |
| `jednostka_mikro_v1_2` | 1-0E, 1-2 | 13 | 16 |
| `jednostka_mikro_v1_3` | 1-3, w2 | 13 | 13 |

- **Codes are matched by meaning, never by position.** This was the decisive finding: **the section letters do
  not line up between the forms.** The small form skips the full form's operating-result line, so its `F` is
  the full form's `G`, its `G` is the full form's `H`, and the shift continues to the end of the statement.
  Path-based reuse would have mapped "Przychody finansowe" onto "Pozostałe koszty operacyjne" — a silent
  mis-map that every identity check would have passed. Matching resolves each line by label, scoped to its
  variant, and disambiguates by **which full-form line its parent matched to**.
- **Two safety rules** were needed after the matcher proposed nonsense twice: never match across
  `Aktywa`/`Pasywa` (it put an assets code on a trade-payables line), and never guess when a generic label
  ("– do 12 miesięcy") fits several full-form parents. Both now fall through to a new code instead.
- **46 new codes**, all genuinely form-specific: 22 for micro (21 of them its own income-statement layout),
  24 for small (its receivables/payables split by *type* where the full form splits by counterparty, plus its
  own computed profit chain and formulas). Everything else reuses the full-form vocabulary, so
  "fixed assets" and "inventories" stay comparable across forms for Phase 5.
- **Three lines the small form narrows in 1-3 and wariant 2** map onto the existing `.R2025` codes
  (`IS.CALC.A`, `IS.CALC.B`, `IS.COMP.B.VIII`). No new narrowing codes were needed; those two specs carry the
  overrides in step D.
- **Label normalisation** now tolerates three presentational differences that never change an amount: a
  leading list marker (`–`, `a)`), a trailing `, w tym X` ("of which" is a subset note), a trailing
  `(dla jednostek …)` applicability clause, and spacing inside a formula (`(A - B)` vs `(A–B)`). None can hide
  a `.R2025`-style narrowing, where the words before "w tym" change.
- **`profit_ties` needs no change for micro**, which declares no balance-sheet net-result line: `_row` already
  returns `skipped` when a side is missing. Pinned by an assertion.
- **`IS.MIKRO.G` deliberately carries no `net_result` role** — a filing with both `F` and `G` would make
  `_role_value` raise. `test_every_role_the_checks_read_is_defined` records the five net-result codes and why.

### D. Specs

- Seven spec files, one per (form, namespace, schema version) — the eight of decision 1 less `full-2025-v1-3`, which step A landed — each binding namespaces, `statement_root`, header paths, `unit`, `statements`, `columns` and `code_overrides` to its body.
- Remove each newly mapped entry from `structure_catalog.yaml`; leave the `…WTysiacach` twins in it.
- `test_mapping_coverage.py` gains the new versions in all three parametrised tests, and `SEED_VERSIONS` shrinks to whatever genuinely remains unmapped.

### Blocked, 2026-09-20: the small form's statement body is a per-document choice

Reading the real seed documents before writing the specs (the namespaces and element names had to come from
filings, not assumptions) turned up something decision 1 did not anticipate.

**A small-form filing does not have to contain the small-form statements.** The `JednostkaMala` envelope
accepts either the small body (`BilansJednostkaMala`, items in `JednostkaMalaStruktury`) or the **full** body
(`BilansJednostkaInna`, items in `JednostkaInnaStruktury`) — and the choice is made per statement, not per
document. Across all 30 small-form seed files:

| Balance sheet | Income statement | Files | Schema versions |
|---|---|---|---|
| `…JednostkaMala` | `…JednostkaMala` | 15 | 1-0E (2), 1-2 (10), 1-3 (3) |
| `…JednostkaInna` | `…JednostkaInna` | 12 | 1-0E (1), 1-2 (7), 1-3 (4) |
| `…JednostkaMala` | **`…JednostkaInna`** | 3 | 1-0E (1), 1-2 (2) |

The 3 mixed files are KRS `0000225354` — one of the four entities with no canonical facts at all. The
full-body filers include `0000181328` and `0000153402`. **Micro is not affected**: all 12 micro files use
`JednostkaMikro` bodies.

This breaks decision 1's "one body per spec". A `small-2018-v1-2` spec bound to `jednostka_mala` parses 10 of
its 19 files and fails the rest; bound to `jednostka_inna` it does the reverse. Detection cannot separate them
either — `kodSystemowy` and `wersjaSchemy` are identical across all three rows above.

The good news: the existing bodies need no change. The `BilansJednostkaInna` inside a small envelope is the
same complexType the full-form schema declares, so `jednostka_inna` applies to it unaltered, and step C's
`jednostka_mala` applies to the small one. What has to change is how a spec binds them.

**Options (owner's call before step D proceeds):**

1. **Per-statement body alternatives in the spec.** `statements:` lists, per statement, the element names that
   may appear with the body and item namespace each implies; the engine picks whichever is present. Handles the
   mixed files directly. Contained change: `mapping_engine.py` resolves the body per statement (it currently
   reads `config.bodies[spec.body]` once) rather than per document, plus the spec schema and the coverage
   tests. Three specs for small, as planned.
2. **A structure version per body combination**, detected from which statement elements are present rather than
   from the header alone. `structure_version` would then record what was actually filed, which is arguably
   better for `dq_mart` and for features. Costs up to 9 small specs instead of 3, and extends `detect` beyond
   the header — a change to the §6C1 rule that ADR 0005 settled.
3. **Map only the pure-small files and leave the rest catalogued.** Cheapest, but it abandons 15 of 30 small
   files and leaves `0000225354` with no facts, which is most of this plan's point.

Recommendation: **option 1**. It is the smallest change that covers every seed file, and `source_element_path`
already records which element each fact came from, so no lineage is lost by not encoding the choice in
`structure_version`.

**Resolved 2026-09-20: option 1, built.** A spec's `statements` entry may now be a list of alternatives, each
naming the element, the body it implies and its item namespace; the engine uses whichever the document
contains and refuses (`statement_body_ambiguous`) if more than one is present. The five Phase 2 specs keep the
plain-string form and are untouched.

**Done, 2026-09-20.** 12 specs (5 + 7 new), catalogue down to 10 (the thousands twins and the two CRWDE
templates). **All 49 previously unmapped files now parse**, except one quarantined for a genuine XSD defect.

| | before step D | after |
|---|---|---|
| valid files | 88 | **129** |
| entities with facts | 13 | **17 of 17** |
| canonical rows | 36,466 | 43,611 |
| `not_yet_mapped` | 42 | **0** |

- **The Phase 2 full-form hash is unchanged** (`7d6d0dee…`, 33,490 rows), and re-materializing leaves all 14
  Parquet files byte-identical.
- **Grades:** 82 `pass`, 19 `warn`, 28 `quarantined`. Of the 41 short-form files, 33 pass, 3 warn, 5
  quarantine — scattered across 5 entities in ones and twos, the signature of filing defects rather than a
  mapping fault. Spot-checked: `0000041651`'s balance sheet is out by 1,000.00; `0000277937`'s micro net
  result (−72,117.63) does not match its own `A-B+C-D-E` (−250,731.91); `0000225354` reports fixed assets of
  2,881,651.81 against components summing to 1,692,594.92.
- **One file quarantines on XSD validation**: `0000507997`, small 1-2, leaves a mandatory `Art` out of
  `PodstawaPrawna` in the *tax* additional information — a section this project does not map. Quarantining the
  whole document is what §6C1 requires, so it stands; the entity's other 8 files parse.

**Three defects found and fixed while doing this, all mine:**

1. **The engine enforced an income-statement variant section on every body.** The micro income statement has
   no `RZiSKalk`/`RZiSPor` choice, so all 12 micro files failed `statement_variant_ambiguous`. The check is now
   read from the body rather than a module constant.
2. **`source_element_path` was written with the spec's default namespace prefix**, not the alternative's, so
   facts from a full body inside a small envelope cited `jma:` paths that resolve to nothing — invariant 3
   broken for exactly the files this step added. The prefix is now threaded through.
3. **The identity checks resolved the body from `spec.body`**, ignoring the alternative, so a small filing
   carrying the full statements was checked against the small body's hierarchy. That produced 12 spurious
   quarantines with a distinctive signature: 59 of 74 failures on just `IS.COMP.A` and `IS.COMP.B`, which is
   what prompted tracing it rather than accepting them as filing defects. The checker now reads the filed
   shape back off `source_element_path`. Quarantines fell from 40 to 28.

   Its fallback matters: when no path matches an alternative the checker uses the spec's first one rather than
   an empty rule set, because checking nothing at all is a worse failure than checking against the wrong body.
   Five existing unit tests caught this by building synthetic frames whose paths match no alternative.

### E. Fixtures

Trimmed seed filings, produced the same way as plan 0004's (signatures removed, `Plik` attachments replaced with a placeholder, BOM stripped, free text read through for personal data), recorded in `tests/fixtures/statements/README.md`:

- `small-2018-v1-2`: one comparative and one calculation filing.
- `small-2018-v1-0` and `small-2025-v1-3`: one each.
- `micro-2018-v1-2`: one, plus one `micro-2025-w2-v1-0` (the single CRWDE file).
- A small-form pair from one entity in adjacent years, so `prior_year_consistency` has a real restatement candidate on the short form.
- A known-bad micro copy with total assets altered, for the quarantine path.

**Done, 2026-09-20.** Ten short-form fixtures, trimmed as plan 0004's were (no signature was present in any of
them; attached files replaced with a placeholder). **11 of the 12 specs now have a golden fixture**; only
`full-2018-v1-2-tys` does not, by design — no seed filing uses it, so `test_mapping_engine` builds a synthetic
thousands document instead. The skip message names it rather than passing silently.

The set deliberately covers **every body-choice case step D uncovered**, since that is where its three defects
hid and nothing else would catch a regression:

| Fixture | Covers |
|---|---|
| `small_2018_v1_2_mala_kalk_2021` / `…_mala_por_2021` / `…_mala_por_2022` | small body, both variants; the last two are an adjacent-year pair, so `prior_year_consistency` has a short-form case |
| `small_2018_v1_2_inna_por_2022` | the **full body inside a small envelope** |
| `small_2018_v1_0_mixed_por_2018` | **mixed**: small balance sheet, full income statement — the case per-statement alternatives exist for |
| `small_2025_v1_3_mala_kalk_2025` | the `.R2025` overrides on the small form |
| `micro_2018_v1_0_2018`, `micro_2018_v1_2_2019` | micro **with** the `G` block (`jednostka_mikro_v1_2`) |
| `micro_2025_v1_3_2024`, `micro_2025_w2_2025` | micro **without** it (`jednostka_mikro_v1_3`), including the seed's only CRWDE micro filing |

- **Fixture discovery is now a glob** over `tests/fixtures/statements/*.xml` in both test modules, so a fixture
  added later is picked up without editing a list. `test_golden_statements_pass` already requires every fixture
  to pass the identity checks, and all ten do.
- **`test_income_statement_variants_are_never_mixed` was too narrow.** It asserted income-statement codes are
  all `COMP` or all `CALC`; the micro layout is neither. It now expresses the real invariant — exactly one of
  the three layouts per document — with `MIKRO` mapping to `variant: "n/a"`.
- **Known-bad case:** `test_altered_micro_total_assets_quarantines` shifts the committed micro fixture's total
  assets by 1,000.00 and asserts `balance_sheet_balances` fails and the file grades `quarantined`. Built from
  the real fixture rather than a synthetic frame, so it exercises the micro body and chart codes.
- **Invariant 6 is now a gate, not a one-off.** `test_no_fixture_contains_personal_data` scans every committed
  fixture's raw bytes for `PESEL`, `X509Certificate`, `SignatureValue` and `ds:Signature`, and its text for
  11-digit runs. Plan 0004 did this scan by hand; a fixture added later can no longer quietly reintroduce
  signer data.

### F. Identity checks over the short forms

- No new rule. Confirm on fixtures that `cashflow_ties` and the equity walk are skipped rather than failed when the statement is absent, and that `subtotals_consistent` walks the shorter tree correctly, including micro's of-which lines being excluded from their parent's sum.
- Add an explicit test that a micro filing's `Aktywa_B_1` ("– zapasy") does **not** count towards `Aktywa_B`. Finding 3 is the trap this plan most needs a regression test for.

**Done, 2026-09-20.** No rule changed, as planned: the four §4.3 checks read the short forms correctly as
they stand. Eight tests in `test_accounting_identities.py` pin that, and the golden set there is now a glob
over `tests/fixtures/statements/*.xml` — it was still `full_*.xml`, so **step E's ten short-form fixtures
were not actually being run through the identity checks**; they are now, and all pass.

- **The of-which regression test earns its place.** `test_micro_of_which_lines_are_not_summed_into_current_assets`
  asserts `BS.ASSETS.B` produces no `subtotals_consistent` row for a micro filing, and its twin
  `test_the_full_form_sums_what_the_micro_form_only_notes` feeds **the same codes and the same amounts**
  through `jednostka_inna` and asserts it fails. Verified by mutation: deleting `of_which: true` from the
  micro body's `Aktywa_B_1` fails that test *and* quarantines two golden micro fixtures, off by the
  106,169.77 the of-which lines do not account for. Finding 3's silent mis-map is no longer silent.
- **Absent statements**, over all ten short-form fixtures: no `cash_flow` or `equity_changes` facts are
  written at all, `cashflow_ties` returns an empty frame rather than a failure, and no check row references
  a `CF.`/`EQ.` code. Decision 3 is now enforced on real filings, not only reasoned from the schemas.
- **`profit_ties` is `skipped` on micro**, which declares no balance-sheet net-result line — step C's
  assertion, now made against a fixture rather than a synthetic frame.
- **The shorter tree is walked as its own.** The small form's profit chain (`IS.COMP.MALA.H`,
  `IS.COMP.MALA.J`) is checked and the full form's operating result `IS.COMP.F` is absent, which is the
  letter shift step C found expressed as a test.
- **The mixed body case is covered too**: `small_2018_v1_0_mixed_por_2018` is asserted to carry small-form
  balance-sheet codes while its income statement is checked against the full chain — the shape whose
  mis-resolution caused step D's 12 spurious quarantines.
- **The short-form restatement pair** (`0000225354`, FY2021/FY2022) agrees exactly, so it emits no
  `restatement_events` row. A one-line alteration of the later filing's prior-year column produces exactly
  one event, which is what proves the comparison ran rather than passing vacuously.
- `make check`: **390 passed**, 2 skipped (up from 354; the widened golden set accounts for most of it).

### G. Dagster

No new asset. `financial_statements_canonical` already walks every stored statement download; the new specs simply make more of them `valid`. Update the asset docstring's input description and the run-metadata counters if they name versions explicitly.

**Done, 2026-09-21.** No new asset, as planned — the counters never named a version (they read
`spec.structure_version`), so the step was nearly empty as written. A scan of steps A–F before starting it
turned up four things worth folding in instead, all now landed:

1. **`spec_hash` ignored alternative bodies — a real lineage defect.** `load_mapping_config` digested the
   spec, *its default body* and the chart, so editing `jednostka_inna.yaml` did not move the three small
   specs' hashes, although 15 seed files parse through that body under them (step D). Those files would have
   kept their `parsed_documents` row and `first_ingestion_run_id` across a mapping change, contradicting
   `parsing/manifest.py`'s own contract. It now digests every body in `spec.bodies_used`. Verified: full-form
   digests are unchanged (recomputed by hand), the three small specs now rotate, and the four micro specs
   correctly do not.
2. **The run-metadata label lost the version for files with no spec.** It fell back to `member_kind`, so any
   catalogued-but-unmapped filing would report as `xml`; it now falls back to `structure_key`. No effect on
   today's seed — every current file resolves a spec — but the catalogue still holds 10 versions.
3. **The fallback in `_bodies_filed` was silent.** When no element path matches an alternative the checks use
   the spec's first one (step D deliberately chose that over checking nothing), but nothing said when it
   happened. `unresolved_bodies` now reports it — one row per (file, statement) with facts but no matching
   path — the asset logs a warning and publishes the count as run metadata. Zero on the seed.
4. **The label-comparison rule lived in two places**, a strict copy in `test_mapping_coverage.py` and a loose
   one in the step B notebook, with the notebook regenerating the ADR addendum the test enforces. Both now
   come from `parsing/xsd_inventory.py`: `normalise_label` (as written) and `same_line` (same statutory
   line), with unit tests pinning that `same_line` does not tolerate a `.R2025` narrowing.

Two gaps in this plan's own test list were closed at the same time: `test_every_chart_code_is_used` (no
chart code without a body or override referencing it — mutation-checked) and the docstring of the asset,
which now states that a spec may bind more than one body per statement.

The asset docstring's input description is updated; `restatement_events` needed no change.

### H. Verification and docs

- Materialize, then compare a column-wise SHA-256 over the canonical values **for the full-form files only**, before and after, to prove decision 6.
- Re-run to confirm byte-identical Parquet (invariant 5).
- Update: `README.md` status line; `docs/data_inventory.md` §2.2 (structure status) and gap 3 (small/micro fixtures now exist); `DIRECTORY_STRUCTURE.md` §1 config tree; `CLAUDE.md` "Known moving targets"; ADR 0005 second addendum from step B.

**Done, 2026-09-21.** Materialized from the stored downloads: 123 stored statement downloads → **129 valid
files, 43,611 canonical facts, all 17 seed entities**, fiscal years 2018–2025, grades **82 `pass` / 19 `warn`
/ 28 `quarantined`**, 130 `restatement_events` over 10 entities. All five asset checks passed.

- **Decision 6 holds.** The four full-form versions mapped before this plan are **33,490 rows** — exactly the
  count steps A and D recorded — and their column-wise value hash is stable across the step G change and two
  consecutive materializations.
- **The hash recipe is now committed**, as `notebooks/exploration/canonical_value_hash.py` (read-only over
  `WAREHOUSE_DIR`): rows sorted by the engine's `SORT_KEY`, then SHA-256 over every column except
  `ingestion_run_id`, which a mapping-config change is *meant* to rotate. The earlier `7d6d0dee…` figure came
  from a session-local recipe that was never written down, so it cannot be recomputed; today's values are
  `228eb7f5…` for the phase-2 full-form subset and `fa74f3a1…` for all 43,611 rows. Compare against these,
  not against the old number.
- **The step G `spec_hash` fix rotated exactly what it should.** `parsed_documents` 291 → 321 rows (+30: the
  30 small-form files gaining a row under the new hash), distinct `spec_hash` 21 → 24. The full-form
  `ingestion_run_id` hash was unchanged; the all-rows one moved. No value moved anywhere.
- **The docs pass then rotated everything once more, as designed.** `canonical_chart.yaml` and
  `jednostka_inna.yaml` gained header comments, and the hash is over file bytes, so all 12 specs rotated:
  `parsed_documents` 321 → 451 (+130, one per statement file with a spec), 35 distinct hashes, every
  canonical partition rewritten with new run ids. **Both value hashes were identical before and after**, and
  the run after that was byte-identical again. This is the mechanism working: a comment is indistinguishable
  from a semantic change without parsing the file, so it is treated as one.
- **Idempotence (invariant 5):** re-materializing left all 14 Parquet files byte-identical, with every
  snapshot field identical.
- `make check`: **413 passed**, 2 skipped (390 before step G). `make test-integration`: 28 passed.
- Docs updated, beginning with this step's list and extended to everything this plan made stale:
  - `README.md` status line, plus a section on running the parsing assets and checking a mapping change
    against the values it must not move — the file documented acquisition and stopped there.
  - `docs/data_inventory.md` §2.2 (small and micro now mapped, with the body-choice caveat and the
    two-micro-bodies reason), the XSD rows (51 → 53), the fixture row, and gaps 3 and 6.
  - `DIRECTORY_STRUCTURE.md` §1 config tree: 4 bodies, 12 specs.
  - `CLAUDE.md` "Known moving targets": the three short-form traps, stated as rules.
  - `AGENT_SPEC.md` §6C2 (body alternatives, `statement_body_ambiguous`, two new CI tests), §4.3 (a
    statement a form does not declare is absent, not empty — checks are `skipped`, no zero rows) and §4.4
    (size class is never inferred from the filed form).
  - ADR 0005: second addendum §3 marked landed, template 13821 corrected to mapped, and a new §6 recording
    the two build findings invisible in the schemas; the vendored-XSD count corrected in the first addendum.
    ADR 0009 records that fixture personal-data scanning is now a test, not a manual pass.
  - `config/mappings/canonical_chart.yaml` and `bodies/jednostka_inna.yaml` headers: the `.MALA`/`.MIKRO`
    families and match-by-label rule, and the fact that a small spec can bind the full-form body. The chart's
    header still described a full-form-only vocabulary.
  - `parsing/statements.py` docstring: `not_yet_mapped` no longer points at Phase 3 as future work.
  - Plan 0004's close-out carries a superseded-figures note; plans 0006 and 0007 carry dated notes on the
    premises this plan invalidated (the header does not fix the body; the quarantine counts moved;
    `skipped` vs `not_applicable`; `dq_mart` cannot split by filed body without a manifest column).

## Tests

- **`test_mapping_coverage.py`:** every new version has a spec, a vendored XSD, body-matches-XSD element for element, and every `required: true` item resolving in a golden fixture. The catalogue still accounts for every version seen in the seed.
- **`test_mapping_engine.py`:** each new fixture reproduces its golden JSON exactly; values are `Decimal`; the micro of-which line is not summed; a small filing yields no `cash_flow` or `equity_changes` rows.
- **`test_accounting_identities.py`:** short-form fixtures pass; the known-bad micro fixture quarantines; a small-form restatement pair emits the expected `restatement_events` rows.
- **`test_canonical_schema.py`:** the extended chart still loads with no cycles, unknown parents or duplicate codes; every new code is referenced by at least one body.
- **Idempotence:** two consecutive runs produce byte-identical Parquet.

## Definition of done

- [x] Full-form 1-3 mapped; 7 files and 7 entities gained (step A, landed separately).
- [x] ADR 0005 second addendum records the line-by-line classification behind every new chart code.
- [x] Small and micro bodies and all seven remaining specs written, catalogue reduced accordingly, CRWDE small catalogued.
- [x] All 49 `not_yet_mapped` files parse to `valid`, or are quarantined with an investigated, recorded reason — 48 valid, 1 quarantined on a genuine XSD defect in a section this project does not map (`0000507997`, small 1-2).
- [x] **All 17 seed entities have canonical facts**; the four currently at zero are covered.
- [x] Full-form canonical values provably unchanged (column-wise hash; recipe now committed as a notebook).
- [x] `make check` and `make test-integration` green; re-materialization byte-identical.
- [x] Docs from step H updated.

## Risks

- **Silent mis-mapping is the main one** (finding 3). The body-versus-XSD coverage test catches a missing or extra element, but not a correct element bound to the wrong canonical code. Step B's written classification and step F's targeted of-which test are the defence; a reviewer should read the ADR addendum table, not the YAML diff.
- **Short forms may not be stable across 1-0E / 1-2 / 1-3** the way the full form is. Step B checks this before any body is written. If they differ, the cost is one body per schema version, not per form — still bounded.
- **Micro filings are thin.** 13 balance-sheet lines give the identity checks very little to verify; a micro filing can be internally consistent and still uninformative. This is a data limit, not a bug, and it belongs in the Phase 6 modelling notes: features derived from short-form filings have systematically fewer inputs.

## Next plan

Plan 0007 (SQLMesh `quarantine` + `dq_mart`). Plan 0006 (C3 PDF tier) was deferred on 2026-09-21 — see its
status section — so Phase 3 completes without it, and the one `needs_pdf_tier` file stays recorded and
counted until a trigger there fires.
