# 0005 — Phase 3a: the remaining structure versions (small, micro, full 1-3)

**Stage:** Phase 3 (AGENT_SPEC.md §10), first of three plans: C1 and C2 (§6C) for every structure version left in the seed, and E2 (§4.3) grading over the forms that file no cash-flow statement. Phase 3's other two parts are the C3 PDF tier (plan 0006) and SQLMesh `quarantine` + `dq_mart` (plan 0007).

**Order:** this plan first, then 0006, then 0007. Plan 0006's single in-scope PDF turned out to be a rendered **small-form** statement (`SprFinJednostkaMalaWZlotych`, schema 1-2), so its extraction target is the `jednostka_mala` body and chart codes this plan introduces. Plan 0007 aggregates the canonical table and wants its final shape.

## Status: step A done (2026-09-20), steps B–H not started

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
  column share **119 line items and differ on none** — the old and new specs agree exactly on the same
  company's same figures, parsed through different namespaces.
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

### C. Chart and bodies

- Extend `config/mappings/canonical_chart.yaml` with the codes step B identified. Keep the UoR numbering convention; prefix nothing with the form name unless the line genuinely exists only in that form.
- Write `config/mappings/structures/bodies/jednostka_mala.yaml` and `jednostka_mikro.yaml`, each declaring only the statements its schema has, with its own subtotal tree, `of_which` flags, `user_code` slots and `required: true` items.
- `required: true` is set conservatively: total assets, total equity and liabilities, and the net result. A micro balance sheet has 13 lines and little room for a filing to be usefully partial, but quarantining on an optional line would violate invariant 4's intent.

### D. Specs

- Six spec files, one per (form, namespace, schema version), each binding namespaces, `statement_root`, header paths, `unit`, `statements`, `columns` and `code_overrides` to its body.
- Remove each newly mapped entry from `structure_catalog.yaml`; leave the `…WTysiacach` twins in it.
- `test_mapping_coverage.py` gains the new versions in all three parametrised tests, and `SEED_VERSIONS` shrinks to whatever genuinely remains unmapped.

### E. Fixtures

Trimmed seed filings, produced the same way as plan 0004's (signatures removed, `Plik` attachments replaced with a placeholder, BOM stripped, free text read through for personal data), recorded in `tests/fixtures/statements/README.md`:

- `small-2018-v1-2`: one comparative and one calculation filing.
- `small-2018-v1-0` and `small-2025-v1-3`: one each.
- `micro-2018-v1-2`: one, plus one `micro-2025-w2-v1-0` (the single CRWDE file).
- A small-form pair from one entity in adjacent years, so `prior_year_consistency` has a real restatement candidate on the short form.
- A known-bad micro copy with total assets altered, for the quarantine path.

### F. Identity checks over the short forms

- No new rule. Confirm on fixtures that `cashflow_ties` and the equity walk are skipped rather than failed when the statement is absent, and that `subtotals_consistent` walks the shorter tree correctly, including micro's of-which lines being excluded from their parent's sum.
- Add an explicit test that a micro filing's `Aktywa_B_1` ("– zapasy") does **not** count towards `Aktywa_B`. Finding 3 is the trap this plan most needs a regression test for.

### G. Dagster

No new asset. `financial_statements_canonical` already walks every stored statement download; the new specs simply make more of them `valid`. Update the asset docstring's input description and the run-metadata counters if they name versions explicitly.

### H. Verification and docs

- Materialize, then compare a column-wise SHA-256 over the canonical values **for the full-form files only**, before and after, to prove decision 6.
- Re-run to confirm byte-identical Parquet (invariant 5).
- Update: `README.md` status line; `docs/data_inventory.md` §2.2 (structure status) and gap 3 (small/micro fixtures now exist); `DIRECTORY_STRUCTURE.md` §1 config tree; `CLAUDE.md` "Known moving targets"; ADR 0005 second addendum from step B.

## Tests

- **`test_mapping_coverage.py`:** every new version has a spec, a vendored XSD, body-matches-XSD element for element, and every `required: true` item resolving in a golden fixture. The catalogue still accounts for every version seen in the seed.
- **`test_mapping_engine.py`:** each new fixture reproduces its golden JSON exactly; values are `Decimal`; the micro of-which line is not summed; a small filing yields no `cash_flow` or `equity_changes` rows.
- **`test_accounting_identities.py`:** short-form fixtures pass; the known-bad micro fixture quarantines; a small-form restatement pair emits the expected `restatement_events` rows.
- **`test_canonical_schema.py`:** the extended chart still loads with no cycles, unknown parents or duplicate codes; every new code is referenced by at least one body.
- **Idempotence:** two consecutive runs produce byte-identical Parquet.

## Definition of done

- [ ] Full-form 1-3 mapped; 7 files and 7 entities gained (step A, landed separately).
- [ ] ADR 0005 second addendum records the line-by-line classification behind every new chart code.
- [ ] Small and micro bodies and all six specs written, catalogue reduced accordingly, CRWDE small catalogued.
- [ ] All 49 `not_yet_mapped` files parse to `valid`, or are quarantined with an investigated, recorded reason.
- [ ] **All 17 seed entities have canonical facts**; the four currently at zero are covered.
- [ ] Full-form canonical values provably unchanged (column-wise hash).
- [ ] `make check` and `make test-integration` green; re-materialization byte-identical.
- [ ] Docs from step H updated.

## Risks

- **Silent mis-mapping is the main one** (finding 3). The body-versus-XSD coverage test catches a missing or extra element, but not a correct element bound to the wrong canonical code. Step B's written classification and step F's targeted of-which test are the defence; a reviewer should read the ADR addendum table, not the YAML diff.
- **Short forms may not be stable across 1-0E / 1-2 / 1-3** the way the full form is. Step B checks this before any body is written. If they differ, the cost is one body per schema version, not per form — still bounded.
- **Micro filings are thin.** 13 balance-sheet lines give the identity checks very little to verify; a micro filing can be internally consistent and still uninformative. This is a data limit, not a bug, and it belongs in the Phase 6 modelling notes: features derived from short-form filings have systematically fewer inputs.

## Next plan

Plan 0006 (C3 PDF tier) and plan 0007 (SQLMesh `quarantine` + `dq_mart`) complete Phase 3.
