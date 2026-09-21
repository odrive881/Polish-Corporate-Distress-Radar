# 0007 — Phase 3c: SQLMesh over the canonical Parquet, `quarantine` and `dq_mart`

**Stage:** Phase 3 (AGENT_SPEC.md §10), E3 (§6E3) and the first F models (§6F) — the transformation and data-quality layer over the parsing side. Written as the third of three plans; with plan 0006 deferred (2026-09-21) it is the second and last of Phase 3.

**Order:** after plan 0005, which is done. `dq_mart` reports coverage and pass rates by structure version, and every XML version in the seed is now mapped, so its first published numbers describe the corpus as it is. It no longer waits for the PDF route: plan 0006 is deferred, and the single `needs_pdf_tier` file is something `dq_mart` should **report**, not something it needs resolved first — its coverage grain is where that gap becomes visible and measurable, which is also how plan 0006's triggers get counted.

## Status: not started

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
   - This touches A2, A3, C1, C2 and E2 writers and `acquisition/models.py`. It is a rename, not a semantic change, and the 46 existing rows migrate as they are.

4. **The log is never cleaned; the current set is recomputed from scratch on every run.** `quarantine_events` stays append-only and nothing is ever deleted from it — a detection log that gets tidied stops being evidence of what was detected when. The `quarantine` model rebuilds membership each run, asking **whichever source actually knows** for each stage: for stages with a graded output (C2/E2), the canonical table's `quality_grade = 'quarantined'`, because grading is recomputed from the current rules on every materialization; for stages that produce no row to grade (A2, A3, C1 — nothing was written), the latest event per key in `quarantine_events`.
   - This is why no `DELETE` is needed and why the plan-0004 close-out's hand-written SQL is dropped: rows describing files the current rules no longer quarantine simply stop being selected. They remain in the log, which is the point — the log answers "what did we reject, and when", the model answers "what is rejected now".
   - It also means the model is a full rebuild, not an incremental one. Decision 5's `known_from` keying applies to the staging layer, not to this.

5. **Incremental models are keyed on `known_from`, not `fiscal_year`.** §6F asks that a late-arriving filing for an old period trigger a correct partial rebuild. `known_from` is the axis on which data actually arrives (§4.7); a 2019 statement filed in 2026 is new data for an old period. The model processes by `known_from` range and rebuilds whichever `fiscal_year` partitions that touches.

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
- `not_applicable` is a real outcome and must be recorded, not omitted: a small-form filing has no cash-flow statement (plan 0005), and "this check did not apply" is different from "this check passed". A pass rate that silently counts exemptions as passes is wrong.
- **Rename `skipped` → `not_applicable` at the source** (owner decision 1): `accounting_identities._row`, `RESULT_SCHEMA`'s documented values, the asset-check metadata counters in `dagster_defs/checks/`, and the tests that assert on it. One name, used by the rule, the dataset and the mart. It is a pure rename — no row changes status — so the canonical values and both value hashes must be unchanged afterwards (`notebooks/exploration/canonical_value_hash.py`).
- Amend **AGENT_SPEC §5** with the dataset, and add a Pandera contract in `parsing/contracts.py` beside the other two.
- Deterministic row order and the same atomic write as the other datasets (ADR 0008 point 3).

### B. SQLMesh project skeleton

- Add `sqlmesh` to `[project].dependencies`; `make lock`.
- `transform/config.yaml`: DuckDB gateway for execution, Postgres for state (decision 2), `WAREHOUSE_DIR` resolved from the same setting the Python side uses so there is one source of truth for the path.
- External models for `financial_statements_canonical`, `restatement_events`, `identity_check_results` (Parquet) and `quarantine_events` (Postgres), with their columns declared so SQLMesh can type-check the models above them.
- `make` targets: `transform-plan`, `transform-run`, and `transform-test` wired into `make check` so a broken model fails the gate like anything else.
- SQLMesh installs **pandas and numpy** transitively (0.236 resolves on this Python; probed 2026-09-21). That is accepted, and the prohibition is being removed from the specs (decision 8). Add the replacement guard here: a test asserting no module under `src/` or `dagster_defs/` imports pandas, so the Polars idiom is enforced where it matters.

### C. Rename `quarantine` → `quarantine_events`

- Migration in the Postgres schema modules, following ADR 0006's pattern; update A2, A3, C1, C2 and E2 writers and `QuarantineRecord`.
- Integration test: the migration is idempotent and preserves every row (68 at 2026-09-21 — assert against the count read before the migration, not a literal).
- **Same migration adds the filed body to `parsed_documents`** (owner decision 2): which body each statement was filed in, for the specs that accept alternatives. The asset already resolves it per statement while mapping (`mapping_engine`) and E2 reads it back off `source_element_path`, so this is recording what is already known, not deriving anything new. Nullable, and null for a spec with one body. Plan 0006's `tier` column joins the same set when that plan is built.

### D. `quarantine` model

- `transform/models/quarantine/`: the current quarantined set, union of the graded source (canonical `quality_grade = 'quarantined'`, with the failing check names from `identity_check_results`) and the ungraded stages (latest event per key from `quarantine_events`).
- Columns: stage, reason code, entity key, `krs`, `document_ref`, `known_from`, first-detected and last-seen timestamps.
- Audits: no row without a reason code; no key appearing in both halves; the row count for C2/E2 equals the distinct quarantined file count on the canonical table.

### E. `dq_mart` model

- Grain: structure version × **filed body** × fiscal year × check type (decision 2). Measures: files checked, passed, warned, failed, not-applicable, pass rate, and distinct entities. The body matters because a small envelope carrying full-form statements is a different parsing path with its own failure modes, and `structure_version` alone hides it.
- A companion coverage grain: files stored, parsed, `not_yet_mapped`, `needs_pdf_tier`, by fiscal year — which is the number that makes plans 0005 and 0006 legible, and which today can only be got by hand-querying `parsed_documents`. With plan 0006 deferred, this grain is also how its triggers get counted: a `needs_pdf_tier` file with no later filing for the same entity is the case that would justify building the PDF tier.
- Small-cell suppression per decision 9, as a model-level rule with its threshold in settings, plus an audit that no cell has an entity count below it while it is not flagged `suppressed`. The setting is nullable and **defaults to `null` (suppression off)**; its definition carries a comment stating that it must be set to at least 5 before Phase 9 publishes, and that the publish step must refuse to run while it is `null`. Test both states: `null` suppresses nothing, and a set threshold suppresses and flags exactly the cells below it.
- Incremental by `known_from` (decision 5).

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

- **`identity_check_results`:** contract holds; `not_applicable` is emitted for a small-form filing's cash-flow check; the dataset reproduces byte-identically on re-run.
- **SQLMesh model tests** (fixtures, not the live seed): the `quarantine` model drops a key whose grade improved between runs — the 9-stale-row scenario, as a test; a file failing two checks appears once with both reasons; an ungraded A3 failure survives.
- **`dq_mart`:** pass rates computed against a hand-built fixture; `not_applicable` excluded from the denominator; with a threshold set, a 1-entity cell is suppressed and flagged, not dropped; with the threshold `null` (the shipped default), nothing is suppressed and every `suppressed` flag is false.
- **Audits run in CI** via `make check`.
- **Idempotence:** `transform-run` twice over unchanged input produces identical output.

## Definition of done

- [ ] `identity_check_results` persisted, contracted and in §5.
- [ ] SQLMesh project runs from `make`, state in Postgres, DuckDB reading `WAREHOUSE_DIR`.
- [ ] Postgres `quarantine` renamed `quarantine_events`, all 46 rows preserved.
- [ ] `quarantine` model materializes and **excludes all 9 stale rows with no manual SQL**.
- [ ] `dq_mart` materializes with pass rates by structure version × filed body × fiscal year × check type, plus the coverage grain; the suppression mechanism is built and tested, and ships switched off (threshold `null`) with the Phase 9 instruction recorded.
- [ ] Dagster runs the models; audits surface as asset checks.
- [ ] ADR 0010 accepted; docs from step G updated, including the plan-0004 supersession pointer.
- [ ] `make check` and `make test-integration` green; re-running is byte-identical.

## Risks

- **SQLMesh's state in Postgres is new operational surface.** If it proves awkward for a single-developer local loop, the fallback is a DuckDB state file — a config change, not a redesign. ADR 0010 should record that fallback so it is not re-litigated.
- **The rename in step C touches five writers.** It is mechanical, but it is the one step here that can break acquisition, which is otherwise untouched by Phase 3. Land it on its own, with the integration tests green, before the models are built on top.
- **`dq_mart` will make the seed's quality look poor** — 21 of 81 files quarantined today, all traced to genuine filing defects. That is an accurate picture of Polish small-company filings, and the mart should present it alongside the coverage grain so a reader sees "21 files with defects" rather than inferring "the parser is broken". Worth getting the framing right before anything is published in Phase 9.

## After Phase 3

Phase 4 (AGENT_SPEC §10): legal events, outcome labels, censoring and regime flags — KRZ and MSiG acquisition (A4), which brings the first documents that genuinely need the deferred C3 tiers (plan 0006). The average-employment decision for size classification (`docs/data_inventory.md` §8) is still open and blocks §4.4 wherever it is eventually needed.
