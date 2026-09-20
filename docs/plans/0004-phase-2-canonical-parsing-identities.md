# 0004 — Phase 2: two structure versions parsed into the canonical model, accounting identities passing

**Stage:** Phase 2 (AGENT_SPEC.md §10): C1 and C2 (§6C) for two structure versions, E1 and E2 (§6E) for the canonical table, and the `prior_year_consistency` output `restatement_events` (§4.3, §5). Deliverable, from §10: "Two structure versions parsed end-to-end into canonical model, accounting identities passing."

## Status, 2026-09-17: done

The Definition of done is met; see the close-out at the end. Building it changed several decisions below. **Where "As built" and the earlier sections disagree, "As built" wins.**

## Why

Phase 1 is complete (plan 0003, close-out 2026-09-17). Its output is the input here: 131 annual statements for the 17-entity seed, stored as 126 ZIPs in MinIO, each with a `filing_index` row carrying `submission_date` (= `known_from`). Parsing is still the prototype `parsing/mapping_engine.py`. It uses ElementTree and `float`, handles only one hardcoded path in one structure, and returns four numbers instead of canonical facts.

This plan replaces the prototype with the declarative engine from §6C. It maps the two structure versions that matter most, and runs the §4.3 identity checks on every parsed statement.

## What the seed documents actually contain (survey, 2026-09-17)

A read-only pass over every downloaded ZIP (scratch script, not committed) found the following. These findings drive the design below. Step A re-checks them against the official XSDs.

| Structure (root namespace) | Root | Docs | Entities | Fiscal years |
|---|---|---|---|---|
| `mf.gov.pl/…/2018/07/09/JednostkaInnaWZlotych` | `JednostkaInna` | ~70 | 13 | 2018–2023 (one 2024) |
| `…/2018/07/09/JednostkaMalaWZlotych` | `JednostkaMala` | 21 (+1 signed) | 5 | 2018–2022 |
| `…/2018/07/09/JednostkaMikroWZlotych` | `JednostkaMikro` | 8 | 2 | 2018–2023 |
| `…/2025/01/01/Jednostka{Inna,Mala,Mikro}WZlotych` | same roots | 7 / 5 / 3 | 7 / 3 / 2 | 2024 (a few 2025) |
| `crd.gov.pl/wzor/2025/08/07/13817/` (CRWDE wariant 2, `1-0E`) | `Dokument` → `JednostkaInna` | 8 | 8 | 2025 |
| `crd.gov.pl/wzor/2025/08/12/13821/` | `Dokument` → `JednostkaMikro` | 1 | 1 | 2025 |

- **There are three generations, not two.** ADR 0005 names the 2018 and CRWDE 2025 generations. A third, intermediate one (namespace dated `2025/01/01`, same roots as 2018) carries most FY2024 statements. It is a distinct structure version for C1.
- **`JednostkaInna` is the dominant form, not an atypical one.** ADR 0005 left open whether it is an odd case. It is used by 13 of 17 entities. It is most likely the full UoR Annex 1 structure; step A confirms this from the XSD.
- **Every statement declares złoty.** Every `KodSprawozdania` is `SprFinJednostka…WZlotych`. The unit lives in the structure code and namespace (`…WTysiacach` is the thousands twin), not in a separate field. No seed document is in thousands, so the §9.2 unit test needs a synthetic fixture.
- **Some ZIPs hold two statements.** Eight ZIPs hold both a statement and its correction, so two `filing_index` rows share one `sha256`. The ZIP member's base name matches `filing_index.file_name`, which is how a member is tied to its `document_ref`. **`source_document_hash` alone therefore does not identify the statement a fact came from.**
- **Signed files come in several shapes.**
  - An enveloped XAdES signature inside the statement (root is still `Jednostka…`, file named `*.xades.xml`). This needs no unwrapping.
  - An enveloping `ds:Signature` with the statement inline in `ds:Object`.
  - A `Signatures` root with the statement base64-encoded in `ds:Object`.
  - An ePUAP `podpisanyPlik` wrapper.
  - A detached `.XAdES` file sitting next to the statement in the ZIP.
  - Some files start with a UTF-8 BOM.
- **Very large text nodes.** Statements embed the notes (`Plik`/`Zawartosc`, base64 attachments) and can exceed libxml2's default limits (2 files failed without `huge_tree`). Parsing must use `huge_tree=True`, with entity resolution and network access off.
- **Variants seen:**
  - Comparative income statement (`RZiSPor`) and calculation variant (`RZiSKalk`) both occur, in every generation.
  - Only the indirect cash-flow method (`PrzeplywyPosr`) occurs, and not every statement has a cash-flow statement. No statement uses the direct method (`PrzeplywyBezp`).
  - Amount columns are `KwotaA` (current), `KwotaB` (prior), and sometimes `KwotaB1` and `KwotaC`. Step A pins down the meaning of `B1` and `C` from the XSD documentation before anything maps them.
- **No structured average-employment element** exists in any generation. Employment is in the attached notes. This matters for size classification (§4.4) later, not for this plan.
- **The RDF `czyMSR` flag (`filing_index.is_ifrs`) is unreliable.** 29 statements are flagged IFRS, yet all are UoR structures. Routing must go by content, never by this flag.
- **Two statements can fall in one fiscal year.** `0000225506` has periods ending 2022-06-12 and 2022-12-31. The prior period must be found by date adjacency, not by `fiscal_year - 1`.

## Decisions this plan makes (flag any you disagree with before step B)

1. **The two structure versions are `full-2018-v1` (2018 `JednostkaInna`) and `full-2025-v1` (CRWDE wariant 2 `JednostkaInna`).** The first covers ~70 of the 131 statements. The second is the generation every new filing uses, already has a golden fixture (`neobis_001.xml`), and proves early that the canonical chart holds across generations.
   - The alternative, `full-2018-v1` + `small-2018-v1`, parses ~20 more seed statements but postpones the cross-generation risk. `small`, `micro`, and the `2025/01/01` intermediate versions are Phase 3.
   - The name is `full-2025-v1`, not `full-2026-v1` as `DIRECTORY_STRUCTURE.md` §1 says. ADR 0005 moved the trigger to fiscal years beginning in 2025. Step H fixes the file name there.
2. **Two columns are added to `financial_statements_canonical`** (a §5 amendment, made in step H):
   - `document_ref`: the RDF document id, which tells a statement from its correction.
   - `source_member`: the path from the stored object to the statement, e.g. `zip:BD-XML-2019.xml` or `zip:x.xades>ds:Object[2]>base64`.

   Without them, lineage (invariant 3) is ambiguous for the 8 two-statement ZIPs.
3. **Corrections are kept, not merged.** A correction's facts are separate rows with their own `document_ref` and `known_from`. Choosing "the latest known version as of a date" is H's job (Phase 5). Nothing here overwrites or deletes a fact.
4. **Canonical output is Parquet on local disk** under a new `WAREHOUSE_DIR` setting (default `.data/warehouse`, gitignored), written by Polars and partitioned by `fiscal_year`. No document says where derived data lives, so **ADR 0008** records this choice. MinIO stays raw-only, and SQLMesh takes over the table in Phase 3 (F).
5. **`ingestion_run_id` is stable across re-runs.** A new Postgres table, `parsed_documents`, records the first run that parsed each `(sha256, source_member)` under a given mapping-spec hash. Re-runs reuse that id, so the Parquet output is byte-identical (invariant 5). A changed mapping spec means a new hash, a new run id, and new rows.
6. **Quarantine reuses the existing Postgres `quarantine` table** with `stage = 'C1'`, `'C2'` or `'E2'`, as A2/A3 already do. The SQLMesh `quarantine` model (E3) is Phase 3.
7. **Mapped statements are the only ones parsed.** Statements whose version is recognised but not mapped yet (Mala, Mikro, 2025-01) are recorded in `parsed_documents` with status `not_yet_mapped` and counted in the run metadata. They are not quarantined, because nothing is wrong with them. **An unrecognised namespace is quarantined** (`unknown_structure_version`).
8. **Official XSDs are vendored under `config/xsd/<structure_version>/`**, including every schema they import (`etd`, `dtsf`, `str`, …). Validation resolves imports locally through `xmlschema` locations and never over the network.
9. **The identity tolerance is a setting**, `IDENTITY_TOLERANCE_PLN` (default `1.00`, a `Decimal`). §4.3 calls it configurable, and it is an engineering choice, not statutory, so it doesn't belong in `config/statutory/`.

## Out of scope (do not do these here)

- `small-2018-v1`, `micro-2018-v1`, the `2025/01/01` versions, CRWDE Mikro, and the `…WTysiacach` twins as real specs. They are Phase 3. The thousands case only gets a synthetic unit-normalisation test here.
- C3 (PDF tier), the embedded `Plik` notes and attachments (G, Phase 7), and the 3 pre-2018 PDFs (out of v1 scope).
- Size classification (`entity_size_class_history`, §4.4). It is not in the Phase 2 deliverable, and no structured employment source exists (see survey).
- SQLMesh models, `dq_mart`, and the public DQ dashboard (E3/F, Phase 3).
- New acquisition, and any change to A3 or to raw objects.
- A "current version" view of facts, and any feature logic (H).

## Steps

### A. Structure research (prerequisite, no pipeline code)

- Download from CRWDE / the MF e-Sprawozdania page the official XSDs for the 2018 `JednostkaInnaWZlotych` and CRWDE `13817` structures, plus every schema they import. Commit them under `config/xsd/full-2018-v1/` and `config/xsd/full-2025-v1/`, each with a `SOURCE.md` giving the URL, download date, and SHA-256. Also fetch (but don't map) the `WTysiacach` twin of `full-2018-v1`, to confirm its element tree is identical apart from the namespace.
- From the XSD annotations, confirm:
  - that `JednostkaInna` is UoR Annex 1;
  - the meaning of `KwotaA`/`KwotaB`/`KwotaB1`/`KwotaC`;
  - the element names for each statement section, both income-statement variants, both cash-flow methods, and the equity-changes statement.
- Record the findings in ADR 0005 as an addendum. This closes its open `JednostkaInna` question.
- Add a marimo notebook, `notebooks/exploration/statement_structure_survey.py`, that reproduces the survey table above from MinIO and Postgres via DuckDB. It guards against the table going stale before Phase 3 and before any backfill.

### B. Dependencies and settings

- Add to `[project].dependencies`: `polars`, `xmlschema`, `pandera[polars]`, `duckdb` (DuckDB is used by tests and the notebook to read the Parquet output). Run `make lock`.
- In `settings.py`, add `warehouse_dir: Path = Path(".data/warehouse")` and `identity_tolerance_pln: Decimal = Decimal("1.00")`. Document both in `.env.example`. Add `.data/` to `.gitignore`.

### C. Canonical chart — `config/mappings/canonical_chart.yaml` + `parsing/canonical_schema.py`

- Define codes such as `BS.ASSETS.TOTAL`, `BS.ASSETS.A`, `BS.ASSETS.A.I`, …, `IS.NET_RESULT`, `CF.NET_CHANGE`, `CF.CASH.OPENING`, `EQ.…`. Each code has:
  - `statement_type`;
  - `parent` (the subtotal tree that `subtotals_consistent` walks);
  - `sign` (how the child adds into the parent: `+1` or `-1`);
  - `variant` (`comparative`, `calculation`, `direct`, `indirect` or `n/a`);
  - a Polish label.
- Comparative and calculation income-statement items get separate codes under `IS.COMP.*` and `IS.CALC.*`. Only true common-denominator items (net revenue, operating result, gross result, tax, net result, …) get shared `IS.*` codes, and both variants map to them. Never derive one variant from the other (§4.1).
- `canonical_schema.py` loads the chart into frozen Pydantic models. The loader rejects cycles, unknown parents, and duplicate codes.

### D. C1 — `parsing/containers.py`, `parsing/version_detection.py`, `parsing/xsd_validation.py`

- **`containers.py`: `iter_statements(raw: bytes) -> Iterator[StatementCandidate]`.** It opens the ZIP and yields each member's statement bytes with its `source_member` path. It unwraps the signature shapes from the survey (inline or base64 `ds:Object`, the `Signatures` root, the ePUAP `podpisanyPlik` wrapper) and leaves the enveloped-signature case untouched. It strips a BOM. Detached signature files are yielded as `kind = detached_signature` and not parsed.
  - One shared, hardened lxml parser is used throughout: `huge_tree=True, resolve_entities=False, no_network=True, load_dtd=False`.
  - An unrecognised wrapper raises `ContainerError`. The caller quarantines it as `unknown_container`.
- **Mapping members to `filing_index`:** a member's base name is matched to `filing_index.file_name` among the rows that share the ZIP's `sha256`.
  - A single-row ZIP with one statement member maps to that row even if the name differs.
  - Otherwise, no match or an ambiguous match is quarantined as `member_not_in_filing_index`.
- **`version_detection.py`:** `(root local name, namespace)` → `structure_version`, read from the `namespaces:` list in each `config/mappings/structures/*.yaml`, plus `config/mappings/structure_catalog.yaml`. The catalog lists the known but not-yet-mapped namespaces from the survey, so they get `not_yet_mapped` rather than `unknown`. Never infer the version from the file name (§6C1).
- **`xsd_validation.py`:** validate against the vendored XSD, with schemas compiled once and cached. Invalid documents are quarantined (`xsd_invalid`, with the first N errors in `detail`) and not parsed. Step A decides whether the enveloped `ds:Signature` needs to be stripped before validation, and on a copy only (raw bytes are never modified).

### E. C2 — `parsing/mapping_engine.py` (rewrite) + `config/mappings/structures/full-2018-v1.yaml`, `full-2025-v1.yaml`

- **Spec format** is §6C2's, with these additions:
  - `namespaces:` (a list: the złoty and thousands twins share one spec if step A confirms identical trees);
  - `unit:` (the `KodSprawozdania` XPath plus a value → multiplier map: `…WZlotych: 1`, `…WTysiacach: 1000`);
  - `columns:` (`KwotaA → current_year`, `KwotaB → prior_year`, and whatever step A decides for `B1`/`C`).
- **The engine** reads the spec, extracts with namespace-aware lxml XPath, and returns a Polars frame in the §5 schema (plus `document_ref` and `source_member`). The frame is sorted deterministically. Values are parsed straight from text to `Decimal` and multiplied by the unit multiplier.
  - A missing or unrecognised unit quarantines the document (`unit_unrecognised`, §4.2).
  - A missing `required: true` element quarantines it (`required_item_missing`).
  - A missing optional element produces no row. It is never imputed as 0 (invariant 4).
- **The income-statement and cash-flow variants** are detected per document from which section is present. The `variant` column carries the result. If both sections, or neither, are present, the document is quarantined.
- **Filled from the manifest:** `krs`, `nip`, `regon` (`entity_master`), `period_start`/`period_end` (the statement header, cross-checked against `filing_index`; a mismatch quarantines as `period_mismatch`), `known_from` (`filing_index.submission_date`), and `fiscal_year` (the year of `period_end`).
- **The prototype `parse_filing`/`float` API is deleted.** `tests/fixtures/neobis_001_expected.json` is regenerated in the canonical long format.

### F. E1/E2 — `parsing/accounting_identities.py`, `parsing/contracts.py`

- **`contracts.py`:** a Pandera Polars schema for `financial_statements_canonical` (types, enums, the 10-character `krs`, `value` as decimal(20,2), non-null lineage columns), applied where C2 output leaves `src/`. A schema failure fails the asset. It is not quarantined, because it means a bug, not bad data.
- **`accounting_identities.py`:** pure functions, one per §4.3 rule. Each takes the canonical frame plus the chart and returns a frame of failures (`krs`, `document_ref`, `period_end`, `column`, `check`, `expected`, `actual`, `difference`).
  - `balance_sheet_balances`, `subtotals_consistent` (recursive over the chart, for items present on both sides), `profit_ties`, and `cashflow_ties` (only for documents with a cash-flow statement; absent is not a failure). All four run on both `current_year` and `prior_year` columns.
  - `prior_year_consistency` compares each document's `prior_year` column with the `current_year` column of the adjacent previous period (previous `period_end` = this `period_start` − 1 day). The previous document chosen is the latest one whose `known_from` is on or before this document's `known_from`. Each differing line becomes a `restatement_events` row: `original_document_hash`/`restating_document_hash`, plus `document_ref`s, as for decision 2. This is a finding, not a failure.
- **`quality_grade`:**
  - `pass`: every applicable check passes.
  - `warn`: only `subtotals_consistent` fails, within 1% of the parent. This threshold is a named constant in `accounting_identities.py`; move it to settings if a second consumer appears.
  - `quarantined`: anything else. The document's rows stay in the table with that grade, and a `quarantine` row (`stage = 'E2'`, `reason_code = <check name>`) records why. Nothing is dropped.

### G. Dagster wiring — `dagster_defs/assets/parsing.py`, `dagster_defs/checks/accounting_identities.py`

- **`statement_documents`** (C1), deps `rdf_manual_import` and `raw_filing_documents`. It walks every `filing_index` row with `sha256` and `rdf_type_code = '18'`, unwraps each ZIP, detects the version, validates the mapped versions, and upserts `parsed_documents` (`sha256`, `source_member`, `document_ref`, `structure_version`, `status` ∈ `valid`/`not_yet_mapped`/`quarantined`, `spec_hash`, `first_ingestion_run_id`). Metadata: counts by version and status.
- **`financial_statements_canonical`** (C2 + grading) parses every `valid` document, applies the contract, runs the identity functions, sets `quality_grade`, writes quarantine rows, and writes `warehouse_dir/financial_statements_canonical/fiscal_year=YYYY/part-0.parquet`. The files are rewritten whole, deterministically, and atomically (write to a temp name, then rename).
- **`restatement_events`** writes to `warehouse_dir/restatement_events/`, same rules.
- **Asset checks, one per rule** (`balance_sheet_balances`, `subtotals_consistent`, `profit_ties`, `cashflow_ties`, `prior_year_consistency`): thin wrappers over the step-F functions, reporting the failing `(krs, period_end)` set as metadata. `prior_year_consistency` always passes and reports restatement counts.
- Every asset docstring states inputs, outputs, and partition scheme (unpartitioned: the whole seed is re-parsed each run, which takes seconds). `parsed_documents` DDL goes in a new `parsing/manifest.py`, following `acquisition/manifest.py` and ADR 0006. Register the assets in `definitions.py`.

### H. Docs

- **`AGENT_SPEC.md`:**
  - §5: add `document_ref` and `source_member`.
  - §6C1: note the container-unwrapping step.
  - §4.2: say the unit is carried by the structure code and namespace.
- **`DIRECTORY_STRUCTURE.md`:**
  - §1: `full-2026-v1.yaml` → `full-2025-v1.yaml`; add `config/xsd/` and `config/mappings/structure_catalog.yaml`.
  - §2: add `containers.py`, `accounting_identities.py`, `contracts.py` and `manifest.py` under `parsing/`.
- **ADR 0008**, derived-data storage location (decision 4). **ADR 0005 addendum** (step A).
- **`docs/data_inventory.md`:**
  - §2.2: update the status of each structure, add the third generation, and mark the XSDs as collected.
  - §8: add the unreliable `czyMSR` flag and the missing structured employment source.
  - Also fix the "from 2026" wording in `PROJECT_OVERVIEW.md` stage 4 and `TECHNICAL_ARCHITECTURE.md` (gap 7).
- **`README.md` status line and `CLAUDE.md`** once the DoD is met.

## Tests (`tests/parsing/`, no network — AGENT_SPEC §8)

**Fixtures (`tests/fixtures/statements/`).** Trimmed copies of real seed statements. For each copy:
- remove the `Plik` attachments and every signature block;
- check that no natural-person name remains (invariant 6; the preparer and signatory fields are dropped or replaced with placeholders);
- record the source KRS, `document_ref`, and what was trimmed in `tests/fixtures/statements/README.md`.

Needed fixtures:
- **`full-2018-v1`, comparative variant:** one document with an indirect cash-flow statement and an equity-changes statement. Pick a year whose prior-year document is also in the fixture set, so `prior_year_consistency` has a real pair.
- **`full-2018-v1`, calculation variant:** one document.
- **`full-2025-v1`:** `neobis_001.xml` (comparative) and one calculation-variant seed document.
- **Synthetic:**
  - a `WTysiacach` copy of the 2018 comparative fixture (unit test);
  - a direct-method cash-flow document built from the XSD;
  - a known-bad copy with total assets altered by 100 PLN;
  - one example of each signature wrapper, built around a trimmed statement;
  - a two-member ZIP (statement plus correction).

Tests:
- **`test_canonical_schema.py`:** the chart loads; cycles, unknown parents and duplicates are rejected.
- **`test_mapping_coverage.py`** (§9.2):
  - every namespace in the catalog is either mapped or explicitly `not_yet_mapped`;
  - every spec's `canonical` targets exist in the chart;
  - every `required: true` mapping resolves against at least one golden fixture;
  - every mapped version has a vendored XSD.
- **`test_containers.py`:** each wrapper shape yields the right bytes and `source_member`; the detached signature is skipped; an unknown wrapper raises; a BOM is stripped. Also the member-to-`document_ref` matching, including the single-row fallback and the quarantine cases.
- **`test_version_detection.py`, `test_xsd_validation.py`:** fixtures detect and validate. A tampered fixture fails validation and quarantines. The vendored schemas load with network access disabled.
- **`test_mapping_engine.py`** (rewritten): each golden fixture produces its `*_expected.parquet`/`.json` exactly. Values are `Decimal`. The thousands fixture yields złoty (unit test). Comparative and calculation both map with no cross-derived rows (variant test). The direct-method fixture maps. A missing required item or unit quarantines. An absent optional item yields no row.
- **`test_accounting_identities.py`** (rewritten): every golden fixture passes; the known-bad fixture fails `balance_sheet_balances` and is graded `quarantined`; the tolerance boundary is exact; a restatement pair emits the expected `restatement_events` rows; two periods within one fiscal year pair by date adjacency.
- **Idempotence** (§9.2): running C1 → C2 → checks twice on the fixture set produces byte-identical Parquet files.
- **`@pytest.mark.integration`:** `parsed_documents` DDL is idempotent, and the upsert keeps `first_ingestion_run_id`.

## As built (2026-09-17)

The step A research and the first seed runs changed the design. In summary:

1. **Four structure versions are mapped, not two.**
   - The plan's "full-2018-v1" turned out to be one namespace holding two schema versions, 1-0 and 1-2 (ADR 0005 addendum). Both are mapped: `full-2018-v1-0` and `full-2018-v1-2`.
   - `full-2025-w2-v1-0` is mapped as planned.
   - `full-2018-v1-2-tys` is mapped so that the thousands-of-złoty normalisation runs against a real schema. No seed filing uses it.
   - Adding schema 1-3 (7 seed statements) is one small spec in Phase 3: its line items are identical.
2. **Detection uses the header's system code and schema version** (`kodSystemowy` + `wersjaSchemy`) as well as the root element (AGENT_SPEC §6C1 updated).
3. **The chart is vocabulary only.** Codes follow the UoR Annex 1 numbering (`BS.ASSETS.A.I.1`, `IS.COMP.L`, `CF.IND.D`). Parents, "of which" lines and computed-line formulas live in a shared body file (`config/mappings/structures/bodies/jednostka_inna.yaml`) that all full-form specs use. A coverage test checks the body against each XSD element by element.
4. **Wariant 2's narrowed lines get their own codes.** Six income-statement lines no longer include sales of materials (`….R2025`, applied through each spec's `code_overrides`).
5. **XSDs are mirrored by URL**, under `config/xsd/<host>/<path>` with one `catalog.yaml`, not per version: versions share most imports.
6. **One asset, `financial_statements_canonical`, runs C1 + C2 + E2 grading.** It reads each download once instead of unwrapping and validating the large files twice. `restatement_events` is its own asset. The per-file status still lands in `parsed_documents`.
7. **Canonical columns:** `prior_year_restated` (`KwotaB1`) is a third `column` value. `restatement_events` also carries period, column, member and document-ref lineage (AGENT_SPEC §5 updated).
8. **The filer's own extra lines are captured.** Most elements allow them (`PozycjaUszczegolawiajaca_N`); seed filers use them for real components (e.g. business travel as a cost line), for "of which" breakdowns, and for stray old-format subtotals. Each element's extra lines become one `….USER` fact, and they count towards the element's subtotal except under "w tym" lines. Dropping them had failed correct statements and lost data (invariant 4). Breakdowns of a single line are still skipped: the line's total is already a fact.
9. **Grading was refined against the real filings** (AGENT_SPEC §4.3 updated):
   - Materiality is measured against the file's current total assets, not the parent line. A 158 PLN split gap no longer quarantines a 1.76M PLN balance sheet.
   - Failures confined to the prior-year columns grade `warn`: their authority is the previous filing, and differences there are restatement findings.
   - A cash gap exactly explained by the reported exchange-rate effect passes `cashflow_ties`.
10. **Schema 1-0 section headings are kept but not checked.** The cash-flow headings A/B/C carry amounts only in schema 1-0, and not in the UoR template. They are kept as facts (`CF.*.A/B/C`, `header: true`) but never checked; most filers wrote 0.00 there.
11. **Quarantine `entity_key` is `krs:document_ref`** for stages C1/C2/E2, because a statement and its correction share one stored file.
12. **Where things live:**
    - `warehouse.py` (the Parquet writer) sits at the package root next to `settings.py`.
    - Golden outputs are JSON, since `*.parquet` is gitignored.
    - The fixtures are trimmed seed filings (`tests/fixtures/statements/README.md`).

## Definition of done

- [x] Step A: XSDs vendored with sources; ADR 0005 addendum records the `JednostkaInna` finding and the column meanings.
- [x] `polars`, `xmlschema`, `pandera[polars]` and `duckdb` added; `make lock` run.
- [x] Canonical chart, both structure specs, containers, version detection, XSD validation, mapping engine, contracts and identity checks implemented and tested; prototype `parse_filing` removed.
- [x] `make check` green; `make test-integration` green with `make dev-up`.
- [x] Dagster run materializes the parsing assets for the seed, with counts recorded below. `statement_documents` was folded into `financial_statements_canonical` (As built 6).
- [x] Every seed document in the mapped versions is graded `pass`/`warn`, or quarantined with a reason that was investigated and classified as a filing defect (listed below).
- [x] Re-materializing produces byte-identical Parquet and no new manifest or quarantine rows (invariant 5).
- [x] Every fact row has non-null lineage (invariant 3), checked by the query below.
- [x] Docs from step H updated; ADR 0008 accepted.

## Close-out (2026-09-17)

**Checks.**
- `make check`: ruff clean, pyright 0 errors, 221 passed.
- `make test-integration`: 27 passed.

**Seed run** (Dagster run `0db2a464`, config as committed). Of the 123 stored statement downloads (131 statement files):

| Status | Files |
|---|---|
| `full-2018-v1-2` valid | 62 |
| `full-2018-v1-0` valid | 11 |
| `full-2025-w2-v1-0` valid | 8 |
| `not_yet_mapped` | 49 (full 1-3: 7; small: 30; micro: 12) |
| `needs_pdf_tier` | 1 (KRS 0000181328 FY2023, a PDF inside an ePUAP envelope) |
| C1/C2 quarantine | 0 |

**Canonical facts.**
- 33,490 rows from 81 files for 13 entities, fiscal years 2018–2025.
- 204 of the rows are `….USER` totals, from 34 files.
- Lineage check, `SELECT count(*) … WHERE source_document_hash IS NULL OR source_member IS NULL OR source_element_path IS NULL OR document_ref IS NULL OR known_from IS NULL OR ingestion_run_id IS NULL`: 0.

**Grades.**

| Grade | Files | Rows |
|---|---|---|
| `pass` | 46 | 16,950 |
| `warn` | 14 | 7,317 |
| `quarantined` | 21 | 9,223 |

All five asset checks passed: no failing figure is graded `pass`.

**Quarantined files.** Every current-year failure below was traced to the filed XML: none is a mapping error.
- **0000123720, FY2018–2022 (5 files):** trade receivables reported with a 0.00 + 0.00 maturity split, material against total assets. FY2021 also reports a net loss of 2.24M on the income statement but not on the balance sheet.
- **0000188883, FY2019 and FY2022 (bankruptcy estate):**
  - FY2019: net result differs by 15.3M between the statements; the receivables split and the other-accruals split are both missing.
  - FY2022: the other-accruals split is missing.
- **0000198429:**
  - FY2019 original: fixed-asset and long-term investment subtotals don't sum. The filer's correction, filed in the same download, passes.
  - FY2022: tangible assets overstated by 50,000 against their components; revenue components don't sum (change in products with the wrong sign, 5.88M).
- **0000209396:**
  - FY2021: operating cash-flow adjustments don't sum (1.62M).
  - The FY2022 correction: adjustments don't sum (3.48M). The FY2022 original only has prior-column gaps and grades `warn`.
- **0000225506, FY2023–FY2025:** the components of short-term liabilities to other entities exceed their total by 41,783.98 in every year, and the balance-sheet net result doesn't match the income statement (FY2023's also carries the prior year's loss).
- **0000397658, all 5 files:** the filer's software put the old-template profit chain into its own extra lines and reported the statutory operating/gross/net lines as 0. In the FY2021 Q4 period, the equity components also don't sum.
- **0000440028, FY2021–FY2022:** net cash flow (D) is not A.III + B.III + C.III. It was filed as the financing total in FY2021, and as 0.00 in FY2022.

**Warn (14 files).** Immaterial current-year subtotal gaps (the largest, 41,784, against 17.7M total assets), or failures confined to prior-year columns. Examples of the latter:
- 0000440028 FY2019 has a 90M typo in the prior-year fixed assets.
- 0000123720 FY2023 shows its comparative balance sheet after profit distribution: last year's profit sits in retained earnings, not in net result.

**Restatement events.** 107 rows from 14 restating files, 9 entities, all in the `prior_year` column. Restated comparatives (`KwotaB1`) do occur: 0000123720 FY2023 restates its 2022 net result to −2.34M. They produce no event there, because the 2022 filing they compare against is quarantined and excluded.

**Idempotence.** Run `7c89b1ad` repeated `0db2a464` on unchanged input and config. All 12 Parquet files stayed byte-identical (SHA-256 compared), and the `parsed_documents`, `quarantine`, `raw_documents` and `filing_index` counts were unchanged.

**Runs.** `aa921320` and `f0c88e9d` were development runs under earlier rules (before user lines, then before the grading refinements). `0db2a464` is the final config, and `7c89b1ad` is the repeat.

**Left over from development runs.**
- **`parsed_documents`:** 162 rows keyed to three superseded mapping-config hashes. They are harmless by design, since each config hash is its own row set.
- **`quarantine`:** 9 E2 rows from the first development run (`aa921320`), for 7 files that the final rules grade `pass` or `warn`. Quarantine rows are an append-only log of first detection. The current state is `quality_grade` in the canonical table, and Phase 3's SQLMesh `quarantine` model should derive the current set from it. To clear exactly those 9 rows by hand:

  ```sql
  DELETE FROM quarantine
  WHERE stage = 'E2'
    AND entity_key IN (
      '0000123720:uGP29iFo25vZNWs6vbAZhw==', '0000181328:Z25rZwjGBjrBuhes1oDvxw==',
      '0000209396:Ba9NaSo7e5Z_wjrAkIanRw==', '0000209396:FKXxl8ctx1iDveYINEDQuQ==',
      '0000209396:zjm56cf5mgG-TQRMzCF4EQ==', '0000440028:7ovDwj35lgQCgy0PRAm8vg==',
      '0000440028:t74OoG8AGv6dkLZGZ2KeTw==');
  ```

**Findings for later phases** (also in `docs/data_inventory.md` §8):
- No structured average-employment field, which size classification needs.
- `czyMSR` is unreliable.
- Raw downloads hold signatories' PESEL numbers inside XAdES signatures, which conflicts with invariant 6 and needs a decision.
- Notes and single-line breakdowns are not captured yet.

## Addendum, 2026-09-17: owner decisions after the close-out

1. **Personal data is redacted** (ADR 0009).
   - **New downloads:** signer data is stripped before hashing (`acquisition/redaction.py`).
   - **Migration:** run `redaction-b247d350` replaced the 113 stored downloads that held signatures (69 with PESEL numbers, 14 with signed notes PDFs) and deleted the originals.
   - **Verification:** all 473 MinIO objects scan clean. Re-parsing (Dagster run `9b158140`) reproduced the canonical facts, grades and restatement events exactly: a SHA-256 over every value column matched before and after. A further run was byte-identical.
   - **Changed values:** `source_document_hash` changed for the affected files. Their `source_member` no longer shows a signature wrapper (`zip:x.xades` instead of `zip:x.xades>ds:Object[2]>base64`).
   - **Manifest:** `parsed_documents` rows for the replaced hashes were dropped, leaving 147 rows (131 current plus 16 for superseded config hashes). `quarantine` rows were repointed to the new hashes, none deleted.
   - ~~**Test fixture:** `neobis_001.xml` had its two signatures removed.~~
   - ~~**Still open:** the fixture's original, with two PESEL numbers, remains in the public git history (commit `77a012b`, on `origin/master`). Removing it needs a history rewrite and force push, which is the owner's call.~~
   - **Correction, 2026-09-20: both claims above were wrong; there is nothing to remediate.** Verified against the object database:
     - `tests/fixtures/neobis_001.xml` has exactly **one** blob in the whole history, `047ccd164bb6`, byte-identical in all 14 commits from `a699750` (2026-09-13) to `44c9db7` and to the working tree. It contains no `ds:Signature` element and no PESEL. The committed fixture was therefore never signed, and nothing was removed from it on 2026-09-17.
     - Commit `77a012b` is not a valid object and never reached any ref. It cannot have been rewritten away either: `44c9db7`, the commit carrying this addendum, still exists, so a rewrite would have changed its SHA too. It was most likely a work-in-progress commit amended into `44c9db7` while this plan was being written.
     - No history rewrite or force push has happened: `origin/master`'s reflog holds a single entry, `9f7b702 → 44c9db7` `update by push` (a fast-forward, not `forced-update`), and there are no rewrite artefacts and no unreachable objects.
     - A scan of every blob git holds, reachable or not, for `X509Certificate`, `SignatureValue`, `<PESEL>` and 11-digit runs hits only `acquisition/redaction.py` (which names the elements it strips), its tests, and ADR 0009. The single PESEL value present anywhere is the placeholder `00000000000`.

     ADR 0009 still governs new downloads, and the MinIO migration it records (above) stands. Only these two lines about git history were false.
2. **Average employment:** the management report (RDF types 20/5) is recorded as a possible source, probably out of v1 scope. Distressed seed entities file it late or not at all, and micro/small entities may be exempt (`docs/data_inventory.md` §8).
3. **The 9 stale quarantine rows stay**; they will be deleted only if they cause trouble.

## Next plan

Phase 3 (AGENT_SPEC §10):
- **The remaining structure versions** for the other 49 seed statements:
  - full-form schema 1-3 (a one-file spec on the existing body);
  - the small- and micro-form bodies and specs (schemas 1-0/1-2/1-3, CRWDE template 13821).
- **The C3 PDF tier.**
- **SQLMesh over the canonical Parquet**, with the `quarantine` (current set, from `quality_grade`) and `dq_mart` models.
- **Two decisions to take first:**
  - the average-employment source for size classification. The management report (RDF types 20/5) is a candidate but probably out of v1 scope; see `docs/data_inventory.md` §8.
  - ~~how raw downloads that carry signatories' PESEL numbers square with invariant 6~~: decided 2026-09-17, redact at acquisition (ADR 0009).
