# 0007 — Phase 3c: SQLMesh over the canonical Parquet, `quarantine` and `dq_mart`

**Stage:** Phase 3 (AGENT_SPEC.md §10), E3 (§6E3) and the first F models (§6F) — the transformation and data-quality layer over the parsing side. Written as the third of three plans; with plan 0006 deferred (2026-09-21) it is the second and last of Phase 3.

**Order:** after plan 0005, which is done. `dq_mart` reports coverage and pass rates by structure version, and every XML version in the seed is now mapped, so its first published numbers describe the corpus as it is. It no longer waits for the PDF route: plan 0006 is deferred, and the single `needs_pdf_tier` file is something `dq_mart` should **report**, not something it needs resolved first — its coverage grain is where that gap becomes visible and measurable, which is also how plan 0006's triggers get counted.

## Status: steps A–E done (2026-09-22); F–G not started

**Step A close-out, 2026-09-22.** `identity_check_results` is written beside the canonical table, contracted
(`IDENTITY_CHECK_RESULTS`) and in AGENT_SPEC §5. Against the seed warehouse, the refactored `grade()` reproduces
all 129 stored grades (82 pass, 19 warn, 28 quarantined), and the recomputed canonical frame equals the stored one
row for row, so both value hashes are unchanged. The seed gives 12,597 result rows. One behaviour change beyond the
rename: `cashflow_ties` used to emit no row for a file with no cash-flow statement, and now records one
`not_applicable` row per column (173 on the seed). Without it, owner decision 1 had nothing to count.

**Step B close-out, 2026-09-22.** SQLMesh 0.236.2 is in `[project].dependencies` with the `local` and `test`
gateways, the external models, the `transform-*` targets (`transform-test` is in `make check`) and the pandas
guard (`tests/test_no_pandas.py`). Three departures from the step text, all for ADR 0010 to record:
- **`transform/config.py`, not `config.yaml`.** A Python config reads `WAREHOUSE_DIR` and the Postgres credentials
  from `Settings` itself, which is the single source of truth the step asks for. `DIRECTORY_STRUCTURE.md` already
  names `config.py`.
- **`psycopg2-binary`, not `sqlmesh[postgres]`.** The extra pulls `psycopg2`, which builds from source and needs
  `pg_config`. The binary wheel provides the same module. SQLMesh's state connection is its only user; project
  code stays on psycopg 3.
- **The external boundary is the DuckDB schema `ext`.** `before_all` recreates one view per upstream dataset
  at the start of every `plan` and `run`: `read_parquet` over `WAREHOUSE_DIR`, and the Postgres tables through
  the attached catalog `manifest` (read-only). DuckDB's extension auto-install is off, and `make transform-setup`
  installs `postgres` explicitly. `before_all` does not run for `sqlmesh test`, so the test gateway (in-memory
  DuckDB for execution and state) replaces the `ext` views with fixtures; `make transform-test` passes with
  Postgres unreachable.

Checked against the seed: the three Parquet views match their declared columns exactly (43,611 / 130 / 12,597
rows), and re-materializing `financial_statements_canonical` left its Parquet byte-identical. Step C still owes two
things here: the `quarantine_events` view and the two new `parsed_documents` columns, which are already declared
in `external_models.yaml` but do not exist until the migration lands. The integration test comparing the
Postgres declarations with `information_schema` lands with it.

**Step C close-out, 2026-09-22.** Counts taken before the migration, against the live manifest:
- **`quarantine`: 68 rows**, 1 C1 and 67 E2, with no A-stage rows. **22 E2 rows are stale**, over 19 files the
  current rules grade `pass` (12) or `warn` (7). Every file quarantined today (28) has at least one log row. This
  replaces the old "9 stale rows" figure; step D's model must exclude exactly these 22.
- **`parsed_documents`: 451 rows, 131 live** (129 `valid`, 1 `needs_pdf_tier`, 1 C1 `quarantined`). The rest are
  271 rows under old config hashes and **49 `not_yet_mapped` rows under `spec_hash ''` for files mapped since**:
  the double count amendment 10 predicted.

The migration ran on the live database through a normal `financial_statements_canonical` materialization.
`quarantine` is now `quarantine_events`: all 68 rows are kept with their original columns byte-identical, and
every row has `krs` and `document_ref` backfilled (the one C1 row had a filing). The run marked exactly the 131 live
`parsed_documents` rows as seen; 129 carry a body set, 3 of them mixed (`jednostka_inna+jednostka_mala`). All five
`ext` views now match their declarations, and `make transform-plan` applied the external models to `prod`.

One addition to the step text: **`parsed_documents` also gets `last_seen_at`**. Run ids are UUIDs, so they have
no order; the latest run is the `last_seen_run_id` on the row with the newest `last_seen_at`. Every row one run
touches shares that run's timestamp. The backfill validates before it writes, and three shapes fail the schema
step rather than being skipped: an A2/A3 key that is not a KRS, a C-stage key with no `:`, and a C2/E2 key whose
reference `filing_index` does not know. A C1 key whose tail is a `source_member` is backfilled with a null
`document_ref`, which is correct: that file has no filing row.

**Step D close-out, 2026-09-22.** `quarantine.quarantine` (FULL) is in `transform/models/quarantine/`. It reads
a new staging view, `staging.parsed_documents_current` (the latest run's rows, joined to their filings), and a
sixth external view, `ext.filing_index`, limited to the columns the models use. That view gives C-stage rows their
`known_from`, and step E's coverage grain the fiscal year of files that never reach the canonical table. On the
seed the model holds **29 rows, 28 E2 and 1 C1** (`xsd_invalid`). It leaves out **exactly the 22 stale E2 log
rows over 19 files** counted before step C, with no hand-written SQL. All six audits pass, and a restatement
rebuilds it to identical contents. SQLMesh unit tests (`transform/tests/`) cover a stale row dropped, one row per
file with both reasons, an A3 failure surviving, a C2 file shown without canonical rows and dropped once
mapped, a C1 file keyed by its member, and the staging view's latest-run selection. A deliberately wrong
expectation fails `make transform-test`.

One choice beyond the step text: **E2 reason codes are the checks with a `material` failure**, the ones that
quarantine the file. The log recorded every failing check of a quarantined file, so it lists 10 more reasons on
the seed, all immaterial. Every reason the model gives is also in the log. Custom audits live in
`transform/audits/`; `DIRECTORY_STRUCTURE.md` gains it, and `transform/tests/`, in step G.

**Step E close-out, 2026-09-22.** `dq_mart` is two FULL models in `transform/models/marts/`. `marts.dq_mart` is the
check grain (structure version × filed body set × fiscal year × check); `marts.dq_mart_coverage` is one row per
fiscal year. The threshold is `Settings.dq_mart_min_cell_entities` (env `DQ_MART_MIN_CELL_ENTITIES`), passed to
the models as the SQLMesh variable of the same name. It defaults to None, and 0 is rejected: off is spelled empty,
never as a number that suppresses nothing. The Phase 9 instruction is in the setting's definition and in
`.env.example`, as well as AGENT_SPEC §10.

On the seed:
- **Coverage:** 131 files stored = 129 parsed (82 pass, 19 warn, 28 quarantined) + 1 `needs_pdf_tier` + 1 quarantined
  before grading. `not_yet_mapped` is 0, because the 49 stale rows are excluded. Plan 0006's trigger count is **0**:
  the one PDF has a later filing.
- **Check grain:** 148 cells over 5 body sets, none suppressed. Every check covers all 129 files. Pass rates:
  `balance_sheet_balances` 99.2%, `cashflow_ties` 95.6% (84 files not applicable, outside the denominator),
  `profit_ties` 85.5%, `subtotals_consistent` 65.9% (22 material failures, 22 immaterial). Cross-checked against
  the raw results: 28 files have a material failure and 47 have any failure, matching the grades.
- All twelve audits pass (six per model), and a restatement rebuilds both to identical contents.

Unit tests cover pass rates on a hand-built fixture, `not_applicable` outside the denominator (a cell where every
file is exempt has no pass rate, never 100%), a file counting once per check, the suppression mechanism in both
states, and the coverage grain including plan 0006's trigger. Setting the threshold to 1 against the threshold-2
expectation fails the test, so it really depends on the variable.

Departures from the step text:
- The measures are `failed_material` and `failed_immaterial` rather than "warned" (amendment 8).
- The coverage grain also carries the graded outcome and `needs_pdf_tier_without_later_filing`. The graded outcome
  gives the "28 defects out of 129 parsed" framing the risks section asks for.
- Suppression also applies to the coverage grain, which is just as publishable.

**Owner decisions, 2026-09-21.** The three premises flagged at plan 0005's close-out are settled, and the
stale figures are corrected:

1. **`not_applicable` is the name.** `run_identity_checks` currently emits `skipped`; step A renames it
   everywhere — the rule, `RESULT_SCHEMA`, the asset-check metadata, the tests and the new dataset. The
   distinction matters more since plan 0005: a small or micro filing has no cash-flow statement to check, and
   counting that as a pass would inflate every pass rate the mart publishes.
2. **`dq_mart` gets the filed-body dimension.** `parsed_documents` gains a column recording the body each
   statement was filed in (a small envelope may carry the full-form statements — plan 0005 step D). Plan 0006
   would have carried it alongside its `tier` column, but that plan is deferred, so this one owns the
   migration; `tier` joins the same column set whenever 0006 is built.
3. **Never clean the log; recompute the current set from scratch on every run**, taking each stage's answer
   from whichever source actually knows — see decision 4, which this sharpens.
4. **Current counts** (2026-09-21, replacing "46 rows, 9 of them stale"): the Postgres `quarantine` table
   holds **68 rows**; the canonical table grades **28 of 129 files** `quarantined`; `parsed_documents` holds
   451 rows across 35 mapping-config hashes, of which the current config accounts for 130 statement files.

**Amendments proposed 2026-09-22 (pre-build review, pending owner confirmation).** Checking the plan against
the code found premises that do not hold; the steps below are already edited to match. Confirm or overrule
before step A.

5. **C2 is not a graded stage.** A `MappingError` file is recorded `quarantined` in `parsed_documents` and
   skipped (`dagster_defs/assets/parsing.py`): it has no canonical rows, hence no `quality_grade`. Only E2
   sets `quality_grade = 'quarantined'`. The source that knows the current C1/C2 answer is
   `parsed_documents.status`, recomputed on every run. Decision 4 and step D now use it.
6. **The filed body is per statement, not per file.** One file can mix bodies (plan 0005 step D), so a
   single scalar on `parsed_documents` cannot hold "the body". The column records the file's **body set**:
   its distinct filed bodies, sorted and joined with `+`. That is exactly `dq_mart`'s dimension, and the
   per-statement detail stays readable from `source_element_path`.
7. **`quarantine_events` gains `krs` and `document_ref`; the model drops last-seen.** Today both are packed
   into `entity_key` in stage-specific formats, and inserts are `ON CONFLICT DO NOTHING`, so `created_at` is
   first detection only. A last-seen timestamp would mean mutating the log, against decision 4. `known_from`
   is null for A-stage rows, where no filing exists to take it from.
8. **Identity results carry a per-row `severity`.** Checks emit `pass`/`fail`/`not_applicable`; `warn` is a
   per-file grade derived in `grade()` from the materiality of each failure. Step A makes that per-row
   judgement explicit (`material`/`immaterial` on failing rows), and `grade()` rolls it up. `dq_mart` then
   counts material and immaterial failures per check instead of "warned", which no check produces.
9. **`dq_mart` is a full rebuild, not incremental by `known_from`.** A fiscal-year cell aggregates filings
   with many `known_from` dates, and a grading-rule change rewrites past cells. Decision 5 applies to the
   staging layer only.
10. **`parsed_documents` records which run last saw each row.** Rows are upserted only when a run touches
    them, so a file once `not_yet_mapped` under `spec_hash ''` keeps that row after it is mapped. The
    coverage grain and the C1/C2 half of `quarantine` read only rows seen by the latest run.

## Why

Three things are outstanding and they resolve together.

1. **`transform/` is empty scaffolding.** `models/staging/`, `models/marts/` and `models/quarantine/` contain only `.gitkeep`. SQLMesh is not in `pyproject.toml`. The whole F stage is unbuilt.
2. **There are two different things called "quarantine".** The Postgres `quarantine` table is an append-only log of first detection, written by A2, A3, C1, C2 and E2 (68 rows at 2026-09-21). `quality_grade` on the canonical table is the current state (28 of 129 files). They disagree, and always will: the log holds rows written under grading rules that have since changed, describing files the current rules grade `pass` or `warn`. The owner chose to leave them rather than clean them, and confirmed it on 2026-09-21 — the log is evidence of what was detected when, and the current set is derived, never stored. That is this plan (decision 4).
3. **`dq_mart` has no source for its headline number.** §6E3 requires pass rates **by check type**. `run_identity_checks` produces exactly that, per file per check, in `dagster_defs/assets/parsing.py` — and then throws it away. Only `quality_grade` (a per-file roll-up) and quarantine rows (failures only) are persisted. **Pass and warn outcomes per check do not exist anywhere on disk.** Step A closes this before any SQL is written.

## Decisions this plan makes (flag any you disagree with before step B)

1. **SQLMesh does not take over `financial_statements_canonical`.** Plan 0004 decision 4 said it would. That was wrong in a way worth correcting explicitly: producing that table means unwrapping ZIPs, walking XSDs and evaluating XPath, which is Python, not SQL. The canonical table stays the Dagster/Polars asset's output, and SQLMesh reads it as an **external model** — the boundary is exactly where the data becomes tabular.
   - What SQLMesh does own: `quarantine`, `dq_mart`, and the staging models that later feed H.

2. **DuckDB is the execution engine; Postgres is the state backend.** DuckDB reads the Parquet under `WAREHOUSE_DIR` directly (ADR 0008) and is the locked analytics engine. SQLMesh's own state (snapshots, plans, environments) goes to the Postgres that already runs for the manifest, rather than a second DuckDB file: Dagster and an interactive `sqlmesh` CLI will both touch it, and a file-backed state under `.data/` invites a locking problem the day that happens. Recorded as **ADR 0010**.

3. **The Postgres `quarantine` table is renamed `quarantine_events`.** §5 makes `quarantine` a canonical dataset name, and after this plan that name belongs to the SQLMesh model holding the *current* set. Two objects with one name and opposite semantics — a log versus a current state — is precisely what the canonical-names rule exists to prevent. The Postgres table keeps its append-only detection-log role under the clearer name.
   - This touches the A1, A2, A3, C1, C2 and E2 writers, `acquisition/models.py`, the pending-work queries in `acquisition/manifest.py`, `table_counts()` and `redaction_migration.py`. It is a rename, not a semantic change, and the existing rows (68 at 2026-09-21) migrate as they are.

4. **The log is never cleaned; the current set is recomputed from scratch on every run.** `quarantine_events` stays append-only and nothing is ever deleted from it — a detection log that gets tidied stops being evidence of what was detected when. The `quarantine` model rebuilds membership each run, asking **whichever source actually knows** for each stage (amendment 5):
   - **E2:** the canonical table's `quality_grade = 'quarantined'`, because grading is recomputed from the current rules on every materialization.
   - **C1, C2:** `parsed_documents.status = 'quarantined'` among rows seen by the latest run (amendment 10), which is recomputed on every run too. These files have no canonical rows. The reason code comes from the matching `quarantine_events` row.
   - **A1, A2, A3:** nothing downstream records these failures, so the latest event per key in `quarantine_events` is the only source. A2 and A3 never retry a quarantined key, so these rows leave the current set only when that retry rule changes. That is acquisition behaviour, not this plan's to fix.
   - This is why no `DELETE` is needed and why the plan-0004 close-out's hand-written SQL is dropped: rows describing files the current rules no longer quarantine simply stop being selected. They remain in the log, which is the point — the log answers "what did we reject, and when", the model answers "what is rejected now".
   - It also means the model is a full rebuild, not an incremental one. Decision 5's `known_from` keying applies to the staging layer, not to this.

5. **Incremental models are keyed on `known_from`, not `fiscal_year`.** §6F asks that a late-arriving filing for an old period trigger a correct partial rebuild. `known_from` is the axis on which data actually arrives (§4.7); a 2019 statement filed in 2026 is new data for an old period. The model processes by `known_from` range and rebuilds whichever `fiscal_year` partitions that touches.
   - This applies to the staging layer only. `quarantine` and `dq_mart` are full rebuilds (decision 4, amendment 9): both are aggregates or current-state sets that a grading-rule change rewrites all the way back.

6. **`as_of_month` partitioning and `valid_from`/`valid_to` are not in this plan.** §6F lists them as F's output shape, but they describe the point-in-time snapshot models that H consumes, and H is Phase 5. Building a bitemporal snapshot now, with no consumer, would fix its shape before the feature store can say what it needs. This plan builds the `known_from`-keyed staging layer those models will sit on, and says so in the ADR.

7. **`dq_mart` is built and materialized here; publishing to the Evidence site is Phase 9.** §10 says "`dq_mart` published" and §6E3 says "published to the public site", but `site/` is Phase 9 scaffolding (stage K). Phase 3's deliverable is the model plus a **publish-safe contract** the site can later consume unchanged.

8. **pandas is no longer prohibited (owner, 2026-09-21).** SQLMesh 0.236 installs pandas and numpy transitively, and a ban that the locked stack itself violates is a dead letter. The restriction is removed from `CLAUDE.md` and `AGENT_SPEC.md` §3 rather than carried with an exception. Project code still uses **Polars** — that is a stack choice, not a prohibition — and step B adds a test asserting `import pandas` appears nowhere in `src/` or `dagster_defs/`, so the idiom is enforced where it matters instead of at the dependency graph.

9. **`dq_mart` is built to suppress small cells, with suppression off until Phase 9 (owner, 2026-09-21).** It is the one dataset intended to leave the building, and the public site is "aggregated/pseudonymised only". With 17 entities, a cell like (`micro-2018-v1-0`, FY2019) has exactly one filer, so a pass rate of 0% names a company to anyone who can read a KRS search. The mechanism is built now: cells below a threshold publish their counts as null with a `suppressed` flag rather than being dropped — dropping them would hide that the data exists.
   - **The threshold setting ships as `null`, meaning no cell is suppressed.** At any useful value the 17-entity seed would suppress nearly every cell, and the mart would be useless as the working artifact Phases 3–8 need. Nothing leaves the building before Phase 9, so nothing is exposed by this.
   - **Before Phase 9 publishes anything, the threshold must be set — to at least 5 entities — and the publish must refuse to run while it is `null`.** This is recorded in `AGENT_SPEC.md` §10 (phase 9) and in the setting's own definition, so it is not left to anyone remembering this plan. The `suppressed` column exists from the start (always false while the threshold is `null`), so the publish-safe contract of decision 7 does not change shape when suppression is switched on.

## Out of scope

- The Evidence site itself, and any HTML (Phase 9).
- ASOF/bitemporal snapshot models, `valid_from`/`valid_to`, `as_of_month` (Phase 5, with H).
- `outcome_labels` and the F models that build them (Phase 4).
- Any change to parsing, grading or the identity rules. If `dq_mart` shows something alarming, that is a finding for a later plan, not a licence to retune §4.3 here.
- Backfilling `dq_mart` history: it is computed from the current canonical table, which is itself reproducible.

## Steps

### A. Persist the identity-check results (prerequisite, no SQLMesh yet)

- Add a third derived dataset, `identity_check_results`, written by the `financial_statements_canonical` asset from the `run_identity_checks` frame it already computes: one row per (file, check, column) with `status` (`pass`/`fail`/`not_applicable`), the expected/actual/difference it already carries, and the file's lineage columns.
- **Add a `severity` column** (amendment 8): `material` or `immaterial` on `fail` rows, null otherwise. Use the rule `grade()` applies inline today: a current-year tie failure is material, and so is a current-year subtotal failure above `SUBTOTAL_WARN_RELATIVE` of total assets (or with no total assets to compare); every other failure is immaterial. `grade()` then becomes a roll-up (any material → `quarantined`, any fail → `warn`, else `pass`). That is a refactor, not a rule change: every file's grade must come out the same, and so must both value hashes.
- `not_applicable` is a real outcome and must be recorded, not omitted: a small-form filing has no cash-flow statement (plan 0005), and "this check did not apply" is different from "this check passed". A pass rate that silently counts exemptions as passes is wrong.
- **Rename `skipped` → `not_applicable` at the source** (owner decision 1): `accounting_identities._row`, `RESULT_SCHEMA`'s documented values, the asset-check metadata counters in `dagster_defs/checks/`, and the tests that assert on it. One name, used by the rule, the dataset and the mart. It is a pure rename — no row changes status — so the canonical values and both value hashes must be unchanged afterwards (`notebooks/exploration/canonical_value_hash.py`).
- Amend **AGENT_SPEC §5** with the dataset, and add a Pandera contract in `parsing/contracts.py` beside the other two.
- Deterministic row order and the same atomic write as the other datasets (ADR 0008 point 3).

### B. SQLMesh project skeleton

- Add `sqlmesh` to `[project].dependencies`; `make lock`.
- `transform/config.yaml`: DuckDB gateway for execution, Postgres for state (decision 2), `WAREHOUSE_DIR` resolved from the same setting the Python side uses so there is one source of truth for the path.
- External models for `financial_statements_canonical`, `restatement_events`, `identity_check_results` (Parquet), plus `quarantine_events` and `parsed_documents` (Postgres). Declare their columns so SQLMesh can type-check the models above them. `parsed_documents` feeds the coverage grain, the body-set dimension and the C1/C2 half of `quarantine`.
- DuckDB reads the Postgres tables through its `postgres` extension, attached as a catalog in the gateway config. The extension is fetched on first `INSTALL`, so pin how it gets installed (a `make` step, not an implicit download inside a run) and record that in ADR 0010.
- `make` targets: `transform-plan`, `transform-run`, and `transform-test` wired into `make check` so a broken model fails the gate like anything else.
- **`make check` must still run without `make dev-up`.** Today the gate is lint, typecheck and unit tests, with anything that needs Postgres in `make test-integration`. First confirm whether `sqlmesh test` opens the state connection. If it does, `transform-test` runs against a test-only gateway: in-memory DuckDB for both execution and state, fixtures in place of the Postgres external models.
- SQLMesh installs **pandas and numpy** transitively (0.236 resolves on this Python; probed 2026-09-21). That is accepted, and the prohibition is being removed from the specs (decision 8). Add the replacement guard here: a test asserting no module under `src/` or `dagster_defs/` imports pandas, so the Polars idiom is enforced where it matters.

### C. Rename `quarantine` → `quarantine_events`

- Migration in the Postgres schema modules, following ADR 0006's pattern. Update every writer and reader: `QuarantineRecord`, the A1 (`universe_discovery`), A2, A3, C1, C2 and E2 writers, the A2/A3 pending-work queries in `acquisition/manifest.py`, `table_counts()`, and the `UPDATE` in `redaction_migration.py`.
- **`quarantine_events` gains nullable `krs` and `document_ref`** (amendment 7), written by every writer from now on. Existing rows are backfilled from `entity_key`: the KRS alone for A2/A3, `krs:document_ref` for C1/C2/E2, and both null for A1's `file#index`. A key that parses as none of these fails the migration; it is not skipped.
- Integration test: the migration is idempotent and preserves every row (68 at 2026-09-21 — assert against the count read before the migration, not a literal).
- **Same migration adds the filed body set to `parsed_documents`** (owner decision 2, amendment 6): column `filed_bodies`, holding the distinct bodies the file's statements were filed in, sorted and joined with `+`. For a spec with one body it is that body, so SQL never needs the mapping config to fill it in. It is null only for rows with no spec. The asset already resolves the body per statement (`_bodies_filed`, from `source_element_path`), so this records what is already known. Plan 0006's `tier` column joins the same set when that plan is built.
- **Same migration adds `last_seen_run_id`** to `parsed_documents` (amendment 10), set on every upsert, next to the never-overwritten `first_ingestion_run_id`. The latest run's id identifies the current rows. Before the migration, measure how many rows the latest run did not touch, and record that count here; the stale `spec_hash ''` rows are the expected case.

### D. `quarantine` model

- `transform/models/quarantine/`: the current quarantined set, a union of three sources (decision 4):
  - E2: canonical `quality_grade = 'quarantined'`, with the failing check names from `identity_check_results`.
  - C1/C2: `parsed_documents.status = 'quarantined'` on the latest run, with the reason from `quarantine_events`.
  - A1–A3: the latest event per key from `quarantine_events`.
- Columns: stage, reason code, entity key, `krs`, `document_ref`, `known_from` (null for A stages), and first-detected (the earliest matching `quarantine_events.created_at`). There is no last-seen (amendment 7).
- Audits:
  - no row without a reason code;
  - no file appears under more than one of the C1, C2 and E2 sources;
  - the E2 file count equals the distinct quarantined file count on the canonical table;
  - the C1/C2 file count equals the latest-run `quarantined` count in `parsed_documents`.

### E. `dq_mart` model

- Grain: structure version × **filed body set** × fiscal year × check type (owner decision 2, amendment 6). Measures: files checked, passed, failed materially, failed immaterially, not applicable, pass rate, and distinct entities. Failures are split by `severity` (amendment 8), so the material failures are the quarantine drivers and the immaterial ones the warn drivers. A file counts once per check: it fails a check if any of its rows for that check fails, and the failure is material if any failing row is. A check is not applicable to a file only if every one of its rows for that check is `not_applicable`. The body matters because a small envelope carrying full-form statements is a different parsing path with its own failure modes, and `structure_version` alone hides it.
- A companion coverage grain: files stored, parsed, `not_yet_mapped`, `needs_pdf_tier`, by fiscal year, counted over `parsed_documents` rows seen by the latest run (amendment 10) so a file mapped since its `not_yet_mapped` row was written is not counted twice — which is the number that makes plans 0005 and 0006 legible, and which today can only be got by hand-querying `parsed_documents`. With plan 0006 deferred, this grain is also how its triggers get counted: a `needs_pdf_tier` file with no later filing for the same entity is the case that would justify building the PDF tier.
- Small-cell suppression per decision 9, as a model-level rule with its threshold in settings, plus an audit that no cell has an entity count below it while it is not flagged `suppressed`. The setting is nullable and **defaults to `null` (suppression off)**; its definition carries a comment stating that it must be set to at least 5 before Phase 9 publishes, and that the publish step must refuse to run while it is `null`. Test both states: `null` suppresses nothing, and a set threshold suppresses and flags exactly the cells below it.
- Full rebuild on every run, not incremental (amendment 9).

### F. Dagster wiring

- A `dq` asset group running the SQLMesh models after `financial_statements_canonical` and `restatement_events`, so one Dagster run takes the seed from stored bytes to published quality metrics.
- Asset checks on the SQLMesh audits, reporting failures the way the identity checks do.
- Docstrings state inputs, outputs and partition scheme, per §8.

### G. Docs

- **ADR 0010**: SQLMesh gateway and state backend; the external-model boundary (decision 1); why the bitemporal snapshot layer is deferred (decision 6).
- `AGENT_SPEC.md` §5: `identity_check_results`; §6E3: the derived-current-set semantics; §6F: the boundary note.
- `DIRECTORY_STRUCTURE.md`: the `transform/` tree as built.
- `docs/plans/0004-…md`: a pointer noting that its decision 4 ("SQLMesh takes over the table in Phase 3") is superseded by decision 1 here, and that its hand-written stale-row `DELETE` is now unnecessary.
- `README.md` status line; `CLAUDE.md` if the canonical-name list changes.

## Tests

- **`identity_check_results`:** contract holds; `not_applicable` is emitted for a small-form filing's cash-flow check; `severity` is set on exactly the `fail` rows; rolling `severity` up reproduces every file's current `quality_grade` over the seed; the dataset reproduces byte-identically on re-run.
- **Migration (step C):** the `entity_key` backfill fills `krs`/`document_ref` correctly for each stage's key format and fails on a key that matches none; `filed_bodies` is `a+b` for a small envelope carrying one full-form statement.
- **SQLMesh model tests** (fixtures, not the live seed):
  - the `quarantine` model drops a key whose grade improved between runs (the stale-log-row scenario, as a test);
  - a file failing two checks appears once with both reasons;
  - an ungraded A3 failure survives;
  - a C2 `MappingError` file appears although it has no canonical rows, and disappears once a later run maps it;
  - a `parsed_documents` row the latest run did not touch counts in neither `quarantine` nor the coverage grain.
- **`dq_mart`:** pass rates computed against a hand-built fixture; `not_applicable` excluded from the denominator; material and immaterial failures counted apart; with a threshold set, a 1-entity cell is suppressed and flagged, not dropped; with the threshold `null` (the shipped default), nothing is suppressed and every `suppressed` flag is false.
- **Audits run in CI** via `make check`.
- **Idempotence:** `transform-run` twice over unchanged input produces identical output.

## Definition of done

- [x] `identity_check_results` persisted with `severity`, contracted and in §5; grades and value hashes unchanged.
- [x] SQLMesh project runs from `make`, state in Postgres, DuckDB reading `WAREHOUSE_DIR`; `make check` still needs no running Postgres.
- [x] Postgres `quarantine` renamed `quarantine_events`, every row preserved (count taken before the migration), `krs`/`document_ref` backfilled; `parsed_documents` has `filed_bodies` and `last_seen_run_id`.
- [x] `quarantine` model materializes and **excludes every stale log row with no manual SQL**. Count the stale rows against the log before the migration, record the figure here, and check the model against it: **22 rows over 19 files** (step C close-out).
- [x] `dq_mart` materializes with pass rates by structure version × filed body set × fiscal year × check type, plus the coverage grain; the suppression mechanism is built and tested, and ships switched off (threshold `null`) with the Phase 9 instruction recorded.
- [ ] Dagster runs the models; audits surface as asset checks.
- [ ] ADR 0010 accepted; docs from step G updated, including the plan-0004 supersession pointer.
- [ ] `make check` and `make test-integration` green; re-running is byte-identical.

## Risks

- **SQLMesh's state in Postgres is new operational surface.** If it proves awkward for a single-developer local loop, the fallback is a DuckDB state file — a config change, not a redesign. ADR 0010 should record that fallback so it is not re-litigated.
- **The rename in step C touches six writers, two pending-work queries and the redaction migration.** It is mechanical, but it is the one step here that can break acquisition, which is otherwise untouched by Phase 3. Land it on its own, with the integration tests green, before the models are built on top.
- **`dq_mart` will make the seed's quality look poor** — 28 of 129 files quarantined at 2026-09-21, all traced to genuine filing defects. That is an accurate picture of Polish small-company filings, and the mart should present it alongside the coverage grain so a reader sees "21 files with defects" rather than inferring "the parser is broken". Worth getting the framing right before anything is published in Phase 9.

## After Phase 3

Phase 4 (AGENT_SPEC §10): legal events, outcome labels, censoring and regime flags — KRZ and MSiG acquisition (A4), which brings the first documents that genuinely need the deferred C3 tiers (plan 0006). The average-employment decision for size classification (`docs/data_inventory.md` §8) is still open and blocks §4.4 wherever it is eventually needed.
