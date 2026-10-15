# 0017 — C: the parsing gaps list v1 exposed

**Stage:** C1 and C2 (AGENT_SPEC §6C), with E2 grading (§4.3) over what they add; PROJECT_OVERVIEW stage 4.
- **Invariants:** 3 (lineage: every new fact carries its file's hash, element path and run), 4 (no silent loss:
  what still cannot be read stays quarantined with its reason), 5 (idempotence: a re-parse leaves every
  already-parsed file's canonical values exactly as they are), 7 (structure versions and tolerances are config,
  not code).
- **ADRs:** 0005 and its addenda (structure versions; trap (a): an envelope may carry another form's
  statements, chosen per statement), 0009 (fixtures without real names or signatures).
- **Output:** canonical facts for up to 143 statement files of 79 entities that are refused or unmapped today,
  through the same specs, bodies and checks as every other file. Then everything downstream is rebuilt.

**Order:** before plan 0016's remaining items (owner, 2026-10-14). Plan 0016's count of the "inputs incomplete"
rows (Progress, 2026-10-14) found these gaps. They lose data for every model and score, while plan 0016's
remaining work is presentation and loses nothing by waiting. This plan does not wait for list v2. Each new
delivery brings more files of the same kinds, and this plan makes them parse.

## Status: active (2026-10-15): owner decisions 1–4 accepted as recommended (2026-10-14); steps A and B built (Progress), step C next

### Progress

**Step B (2026-10-15): the small wariant 2 mapped.** `small-2025-w2-v1-0` (CRWDE 13818): the `Dokument`
envelope, statement root `TrescDokumentu/JednostkaMala`, and alternatives per statement, `jednostka_mala` (jma)
and `jednostka_inna` (jin), in wariant 2's `2025/07/31` namespaces. It takes `small-2025-v1-3`'s eight `.R2025`
overrides and `effective_from: 2025-01-01`, joins the introduction's `wariant_2` generation, and leaves the
catalogue. The coverage tests (body against XSD, labels against the chart) pass on it. The golden fixture
waits for step F, so `test_required_items_resolve_in_a_golden_statement` skips it by name until then.
Read-only dry run over the store's 48 files (47 entities), in memory, nothing written:

- **C1/C2:** all 48 validate and map (they were all `unknown_structure_version`).
- **Bodies filed:** 45 small, 2 full, 1 mixed (one statement in each). No file fell back to a default body.
- **E2:** 36 `pass`, 8 `warn`, 4 `quarantined` (8%, within the short forms' 3–21%). The failing lines in the
  4 are spread over unrelated subtotals: `BS.ASSETS.B`, `BS.EQUITY_LIABILITIES.A`, `IS.COMP.MALA.H`, `IS.COMP.B`,
  and one `profit_ties`. None is a line the overrides touch: filer errors, as finding 5 describes, not the spec.
- **`.R2025`:** 89 facts in 36 files carry an `.R2025` code.
- **Introduction:** going-concern flags, average employment and the audit flag read in all 48.

**Step A (2026-10-15): catalogue and XSD research.** Read from the vendored XSDs only. The store was not
read: Postgres and MinIO were down. The two missing templates' URLs came from the root namespaces of the
XML files in the owner's two delivery folders, and nothing else in those files was read.

- **A1, the catalogue corrected.** `SFJMAZ (2)` (13818) had root `JednostkaMala`, but its XSD's only top-level
  element is `Dokument`. **`SFJMIT (2)` (13819) had the same defect** (`JednostkaMikroWTys`), which the survey
  could not show because no file uses it. Both are now `Dokument`. New test
  `test_every_detect_key_is_what_its_xsd_declares`: every spec's and catalogue entry's root is a top-level
  element of its XSD, its namespace is the XSD's target namespace, and its system code and schema version are
  fixed by one declaration in the XSD or its imports. It fails on the old 13818 entry.
- **A2, the templates.** 13810 (`SFJINT (2)`, dated 2025-08-04) vendored and catalogued. The probe found a
  second template the survey had not listed: **13846 (`SFJMAT (2)`, 2025-09-10), the small thousands twin**,
  found in one file in the deliveries. It is vendored and catalogued too. The 2025/07/31 domain schemas declare
  six headers, `SFJ{IN,MA,MI}{Z,T} (2)`, and with these two each has a template (13810, 13817, 13818, 13819,
  13821, 13846). So the search is complete without sending blind requests through the template range.
  Everything both templates import was already vendored. **13846 stays catalogued, not mapped:** its one file
  belongs to 0001074196, which step 0 quarantined at A2 (`pkd_section_mismatch`, predominant PKD 6812A), so it
  was never imported and no in-scope filer uses the template. Decision 1 leaves such twins catalogued. Read
  from the store with Postgres up (2026-10-15): 13810's 2 files (2 entities) and 13818's 48 (47) are all
  quarantined `unknown_structure_version`, as the survey found.
- **A3, what each envelope admits** for `Bilans` and `RZiS`. These are element names under the statement root;
  the prefixes are each XSD's own (`jmi`/`jma`/`jin`: the micro, small and full `…Struktury` namespace of that
  version: MF `2018/07/09`, MF `2025/01/01` or CRWDE `2025/07/31`):

  | Envelope | `Bilans` | `RZiS` |
  |---|---|---|
  | micro 2018 v1-0, v1-2, 2025 v1-3, wariant 2 (13821), and their thousands twins (v1-0E, v1-2, 13819) | `BilansJednostkaMikro` (jmi), `BilansJednostkaInna` (jin), `BilansJednostkaMala` (jma) | `RZiSJednostkaMikro`, `RZiSJednostkaInna`, `RZiSJednostkaMala`, same namespaces |
  | small wariant 2 (13818), small thousands 1-2 and wariant 2 (13846) | `BilansJednostkaMala` (jma), `BilansJednostkaInna` (jin) | `RZiSJednostkaMala`, `RZiSJednostkaInna` |
  | full thousands 1-0E, 1-3, wariant 2 (13810) | `Bilans` (jin) only | `RZiS` (jin) only |

  The full-form thousands envelopes admit no other form's statements, so their specs bind one body, like
  `full-2018-v1-2-tys`. The thousands types (`…WTys`) have the same element trees as the złoty ones.
- **A4, small wariant 2 against small 1-3.** For every form in both units, the CRWDE 2025/07/31 statement types
  equal schema 1-3's in element tree, amount and user-slot flags, and labels. So `small-2025-w2-v1-0` binds
  `jednostka_mala` and `jednostka_inna`, and there is no new body and no classification table. Against the 2018
  v1-2 types, the trees are identical everywhere except micro `RZiS` (1-3 and wariant 2 drop `G`, as
  `jednostka_mikro_v1_3` already has it). Labels narrow on the full body's six lines and the small body's
  three (`RZiSKalk/A`, `RZiSKalk/B`, `RZiSPor/B/B_VI/B_VI_1`, mapped to `IS.CALC.A`, `IS.CALC.B` and
  `IS.COMP.B.VIII`). These are exactly the codes `small-2025-v1-3`'s eight overrides cover. So every 2025
  spec that binds a full or small body takes those eight overrides: micro 1-3 and wariant 2, small
  wariant 2, and the twins `full-2025-v1-3-tys` and `full-2025-w2-v1-0-tys`. The 2018 specs take none.

## Why

Plan 0005 mapped every structure version the 17-entity seed used. List v1 (plan 0014, imported 2026-10-09 and
2026-10-13) brought about 1,900 statement files from about 600 entities, and with them filing patterns the seed
never showed. Of 1,871 XML statement files in `parsed_documents` (2026-10-14), 1,714 are `valid`. Setting aside
the 49 PDFs (47 `needs_pdf_tier`, plan 0006, deferred) and 14 files refused for reasons of their own (below), **143 files of 79 entities
are refused or unmapped for a reason this project can fix.** Seven of those entities have no canonical facts at
all. For the others, the missing file is often the latest year, so they are scored and modelled on figures a
year old.

## What is refused, and why (survey, 2026-10-14)

From `parsed_documents` (current status) and each file's latest C1/C2 event in `quarantine_events`:

| Gap | Files | Entities | Of entities with no facts today | Cause |
|---|---|---|---|---|
| **Micro envelope carrying other forms' statements** | 59 | 26 | 18 files | A `JednostkaMikro` file may hold `BilansJednostkaInna` / `RZiSJednostkaInna` or `…JednostkaMala` instead of the micro statements, chosen per statement, as its XSD allows. The micro specs bind only the micro body, so C2 refuses it: "no Bilans" (46), "no RZiS" (13). In every micro version: 2018 v1-0 (4), 2018 v1-2 (39), 2025 v1-3 (9), wariant 2 (7). |
| **Small wariant 2 (`SFJMAZ (2)`, CRWDE 13818) not recognised** | 48 | 47 | 0 | Catalogued by plan 0005 with root `JednostkaMala`, but the file's root is the CRWDE envelope `Dokument`, as for the full and micro wariant 2. So it never matches and is quarantined `unknown_structure_version`. The XSD is already vendored (`config/xsd/crd.gov.pl/wzor/2025/08/07/13818/`). Every one is a 2025 statement: the entity's latest year. |
| **Empty `KodPKD`** | 14 | 3 | 14 files | `P_1/P_1C/KodPKD` holds only an XML comment (`<!-- 1 -->`), a placeholder the filer's software left. The XSD's PKD enumeration refuses it. The financial statements themselves are untouched. Full form, in złoty and in thousands. |
| **Thousands twins not mapped** | 11 | 6 | 4 files | `…WTysiacach` versions plan 0005 decision 1 catalogued and left unmapped, since no seed filing used them: small 1-2 (5 files), full 1-0E (2), full 1-3 (2), micro 1-0E (1), micro 1-2 (1). Recorded `not_yet_mapped`, as designed. |
| **Other XSD defects in elements this project does not read** | 9 | 9 | 1 file | One each: the notes' attachment list, the income-tax note, an address field's length, the `P_1D` pattern, an empty `Aktywa_C` amount (the one inside a statement). |
| **Full thousands wariant 2 (`SFJINT (2)`, CRWDE 13810)** | 2 | 2 | 2 files | Not catalogued, XSD not vendored. |
| *Out of scope: other reasons* | 14 | 14 | 0 | Malformed XML (3), unknown containers (4), members matching no filing (2), a missing `KodSprawozdania` (1), `period_mismatch` (9, below) and `required_item_missing` (2). |

`period_mismatch` (9 files) compares the statement's own period with the RDF listing's. Most are listing-side
(a listed period starting on a registration date, or one year off), so they belong to plan 0014's importer
questions, not here.

### Findings that drive the design

1. **Trap (a) holds for micro too.** ADR 0005's second addendum found that a small envelope may carry the full
   form's statements, chosen per statement, and the small specs bind body *alternatives* for that. Micro
   envelopes do the same, and with three bodies to choose from: micro, small or full. Of the 59 files, 25
   carry full balance sheet and income statement, 9 small and small, 5 small and full, and 13 keep the micro
   balance sheet with a full or small income statement (plus 7 wariant 2, all full). The mechanism exists
   (`StatementAlternative`, `_bodies_filed`): what is missing is the micro specs binding the alternatives.
2. **Wariant 2 alternatives sit in the CRWDE namespaces.** A `JednostkaInna` body inside a wariant 2 micro or
   small envelope is in that template's own structure namespaces, not the MF 2018 ones. Each alternative's
   `item_namespace` has to be read from that version's XSD, not copied from `full-2025-w2-v1-0`.
3. **Schema 1-3 and wariant 2 narrow some income-statement lines** (ADR 0005 second addendum §(c)), and the
   specs map them to `.R2025` codes through `code_overrides`, which apply per spec, to every body it binds.
   `small-2025-v1-3` already relies on this for its full-form alternative. The micro 1-3 and wariant 2 specs
   gain the same overrides with their new alternatives; `test_every_code_label_matches_its_xsd_label` is what
   catches a miss. The feature line-item map already lists the `.R2025` codes (`line_items_v3`), so no new
   feature set is needed.
4. **The form a company files stays its `structure_version`.** A micro envelope carrying the full balance sheet
   is still a micro filing (`structure_version` `micro-…`), and the body actually read is in each fact's
   `source_element_path`, as for small today. A by-product: a micro filer that files the full or small balance
   sheet now has short-term liabilities, so `working_capital_to_assets` exists for it (plan 0016's count: 75
   micro rows lacked it).
5. **The grading spread suggests filer error, not a mapping bug, with one exception to check.** 185 parsed
   files are graded `quarantined` (12–18% of full-form files, 3–21% elsewhere), mostly on
   `subtotals_consistent`. No single line dominates: the most frequent, `BS.ASSETS`, fails in 24 files across
   every form. The exception: `BS.EQUITY_LIABILITIES.B` fails only on small 2018 v1-2 (7 files), which is the
   pattern a body formula error would leave. Step F reads it.

## Owner decisions (accepted by the owner, 2026-10-14, as recommended)

1. **Map the thousands twins that filers use.** Plan 0005 decision 1 catalogued them unmapped because no seed
   filing used one. List v1 has 11 files in five of them, and 2 in the uncatalogued `SFJINT (2)`.
   Recommended: map those six versions now. Each binds the existing body of its form at unit 1000, with
   `full-2018-v1-2-tys` as the precedent. Vendor 13810's XSD. Leave the twins no filing uses catalogued, as
   today. *Alternative:* keep them unmapped. That loses 13 files of 8 entities; 6 of those files belong to
   entities with no facts today.

2. **XSD defects confined to elements the project does not read.** Today any XSD violation quarantines the
   file (C1). 22 files violate their XSD only in elements no spec, body or introduction item reads: 14
   placeholder `KodPKD`s and 8 others (an address field, the notes' attachment list, the income-tax note,
   `P_1D`). Their statements validate.
   Recommended: a versioned tolerance list in `config/mappings/xsd_tolerances_v1.yaml`. Each entry is an
   element path pattern and an error kind, never a whole file or entity. A file whose every violation matches
   an entry parses, its grade is capped at `warn`, and each tolerated violation is recorded in
   `quarantine_events` with reason `xsd_tolerated`, so the file stays countable. A violation inside a mapped
   statement or a read introduction item is never tolerable (the test refuses such an entry). The one inside
   `Aktywa_C` stays quarantined.
   *Alternative:* keep them quarantined, as plan 0005 did for `0000507997`. That costs 3 entities' every file.

3. **The rebuild after the re-parse is a new backtest version.** Re-parsing changes `financial_statements_canonical`
   and so `feature_store`'s data, but not its config hash, so the served model (v3) would still pass
   `ScoreContext`'s check. Recommended: rebuild everything downstream (step H), then record `backtest_v7` (v6
   with the label set and population re-pinned if they moved, else only its version) before running it, so
   v6's report stays a statement about the data it ran on. *Alternative:* re-run v6 in place, as plan 0015 did
   for the restatements correction. Cheaper, but v6's report then describes data it no longer matches.

4. **The text job over the newly parsed statements.** New statements bring their notes, and with them text
   signals to extract with Claude (plan 0013 decision 2, the provider's terms confirmed). Estimated from
   plan 0015 step A's run ($8.83 for 1,998 requests): about 150 statements at a few requests each, a few
   dollars. Recommended: run it with the rebuild, through the Batches API as before. *Alternative:* leave
   their text features null until the next scheduled text run. `text_coverage` says so; nothing is imputed.

## Out of scope

- The C3 PDF tier (plan 0006). Step I only measures its triggers.
- The 14 files refused for other reasons, and `period_mismatch` (plan 0014's importer).
- Statements graded `quarantined` on identity failures beyond step F's one check. Grading rules stay as they
  are (plan 0005 decision 5).
- Size classification (`entity_size_class_history`, §4.4), still blocked on average employment.
- The micro `IS.MIKRO.G` question (plan 0016, Progress 2026-10-14): a feature line-item map change, plan
  0012's to version.

## Steps

### A. Catalogue and XSD research (no pipeline code)

1. Correct `SFJMAZ (2)`'s catalogue entry: root `Dokument`, as its vendored XSD declares. Read
   `kod_systemowy`, `wersja_schemy` and the statement root from the XSD, not from a file.
2. Vendor CRWDE 13810 (`SFJINT (2)`) and catalogue it. Probe the neighbouring template numbers, as plan 0005
   step B did, for any further wariant 2 twin, and record what is found.
3. For each micro version (2018 v1-0, v1-2, 2025 v1-3, wariant 2) and the small wariant 2: list, from the
   XSD, which statement elements the envelope admits for `Bilans` and `RZiS`, and in which namespaces
   (`xsd_inventory`). That list is what the specs bind, no more and no less.
4. For the small wariant 2, compare its `JednostkaMala` statement bodies with small 1-3's, element for element,
   as plan 0005 finding 1 did for the full form. Identical: bind `jednostka_mala`. Different: a new body, and
   a classification table in a new ADR 0005 addendum before it is written.

### B. Small wariant 2

`small-2025-w2-v1-0`: the CRWDE envelope (`statement_root` under `TrescDokumentu`), `jednostka_mala` and
`jednostka_inna` alternatives per statement in their wariant 2 namespaces, `effective_from: 2025-01-01`, and
the `.R2025` overrides `small-2025-v1-3` carries for the lines wariant 2 narrows, checked against the XSD's
documentation. Out of the catalogue's unmapped list.

### C. Micro alternatives

The four micro specs gain alternatives for `Bilans` and `RZiS`: the micro body first, then `jednostka_mala` and
`jednostka_inna`, each with the item namespace step A3 found. Micro 1-3 and wariant 2 gain the `.R2025`
overrides for the full and small bodies' narrowed lines (finding 3). Unchanged: `form: micro`, and the
introduction and notes, which stay micro.

### D. Thousands twins (decision 1)

Six specs: `full-2018-v1-0-tys`, `full-2025-v1-3-tys`, `full-2025-w2-v1-0-tys`, `small-2018-v1-2-tys`,
`micro-2018-v1-0-tys`, `micro-2018-v1-2-tys`. Each binds its form's złoty bodies, its alternatives included (a
thousands envelope can carry another form's statements as well), at `unit: 1000`. Out of the catalogue
accordingly.

### E. XSD tolerances (decision 2)

`config/mappings/xsd_tolerances_v1.yaml`, loaded and checked at start-up: each entry's path must lie outside
every spec's statements and every read introduction item. Then C1's validation step applies it: a file whose
every violation is tolerated parses, `xsd_tolerated` events are written, and E2 caps the grade at `warn`. The
tolerance list's version goes into the mapping-config hash, like the specs.

### F. Fixtures and the one grading check

- Synthetic fixtures, no real names or signatures (ADR 0009): a micro envelope with full-form statements, one
  with a micro balance sheet and a full income statement, one wariant 2 micro with full statements, a small
  wariant 2 with each alternative, one thousands twin, and a placeholder `KodPKD`. Each with its golden JSON.
- Read the small 2018 v1-2 body's formula for `BS.EQUITY_LIABILITIES.B` against the XSD's documentation and the
  7 failing files (finding 5). A formula error is fixed here, with its own test. A filer pattern is recorded,
  and the files stay graded as they are.

### G. Re-parse

Re-run C over the stored documents (no acquisition). Then check:
- every one of the 143 files is `valid` (or `warn`), or quarantined with a recorded reason that is not one of
  this plan's gaps;
- **every file that parsed before keeps its canonical values exactly**: the column-wise hash recipe of plan
  0005 step H (`notebooks/`), before and after;
- `make check` and `make test-integration` pass; a second run is byte-identical.

### H. Rebuild downstream (decisions 3 and 4)

Identity checks, `restatement_events`, `statement_disclosures`, the text job (decision 4), labels only if
anything they read moved (they read legal sources, so most likely not), `feature_store` with `leakage` passing,
`backtest_v7` recorded before its run (decision 3), and a read-only scoring dry run whose counts replace plan
0016's 2026-10-14 numbers. Each round's counts go in Progress.

### I. Plan 0006's triggers, measured (no PDF code)

`parsed_documents` holds 47 `needs_pdf_tier` files of 22 entities. For each: is a later XML filing's prior-year
column the same period (recoverable, not a trigger)? Is it the entity's latest statement (trigger 1)? Do the
unrecoverable ones concentrate in entities with a distress label (trigger 2)? The result goes in plan 0006's
status section, with a recommendation. Nothing is built here.

### J. Docs

ADR 0005 third addendum (micro alternatives, small wariant 2, the twins, the tolerances); CLAUDE.md's
moving-targets note on the short forms (trap (a) holds for micro too); `DIRECTORY_STRUCTURE.md` (new spec
files, the tolerance file); README's status line; the doc sweep. `make docs-check` covers the mechanical part.

## Tests

- **`test_mapping_coverage.py`:** every new version has a spec and a vendored XSD; each alternative's body
  matches its XSD element for element; the catalogue accounts for every version in `parsed_documents`.
- **`test_mapping_engine.py`:** each new fixture reproduces its golden JSON; values are `Decimal`, scaled by 1000
  for a twin; a micro envelope's full-form balance sheet yields full-form codes and `structure_version`
  `micro-…`.
- **`test_statement_introduction.py` and the label test:** the `.R2025` overrides on the new alternatives match
  their XSD labels.
- **`test_xsd_validation.py`:** a tolerated violation parses with an `xsd_tolerated` event and a `warn` cap; an
  untolerated one still quarantines; a tolerance entry inside a mapped statement is refused at load.
- **`test_accounting_identities.py`:** the short-form alternatives grade like their own forms.

## Definition of done

- [x] The catalogue corrected; 13810 (and 13846) vendored and catalogued (A).
- [ ] Small wariant 2, the micro alternatives and the six twins mapped (B–D).
- [ ] Tolerances built, if decision 2 is accepted (E).
- [ ] Of the 143 files, each `valid` or `warn`, or quarantined with a recorded reason outside this plan's gaps;
      the count of entities with no canonical facts recorded before and after.
- [ ] Every previously parsed file's canonical values unchanged (column-wise hash).
- [ ] Downstream rebuilt and `backtest_v7` run (H); plan 0016's counts refreshed.
- [ ] Plan 0006's triggers measured and recorded (I).
- [ ] `make check` and `make test-integration` green; docs swept (J).

## Risks

- **Silent mis-mapping across forms** (plan 0005 finding 3). A full-form body bound in a micro envelope reads
  the same element names as in a full envelope, but only if the namespace is right. A wrong namespace binds
  nothing and C2 refuses the file, loudly. A right namespace on the wrong body is the silent case. Step A3's
  XSD list is the defence, and the identity checks run on every new file.
- **Tolerances drift into a loophole.** Each entry is a path and an error kind, versioned, refused inside
  anything read, and every use is recorded. A new kind of defect needs a new version, never a broader pattern.
- **Results move.** More micro filers with liabilities, more 2025 statements: the backtest's numbers change,
  and with 18 distressed entities they can move a lot. That is why decision 3 records v7 before running it.

## Next plan

Plan 0016's remaining items, from item 2 (storing the excluded entities' reasons), on the rebuilt data.
