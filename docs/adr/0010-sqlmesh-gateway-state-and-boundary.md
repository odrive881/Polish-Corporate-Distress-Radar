# 0010 — SQLMesh gateway, state backend, and the Python/SQL boundary

- **Status:** accepted
- **Date:** 2026-09-22

## Context

Plan 0007 builds the first SQLMesh project (stage F) and the E3 models `quarantine` and `dq_mart` over the parsing
side. The locked stack names SQLMesh and DuckDB but settles neither where SQLMesh keeps its own state, nor where
SQLMesh's responsibility starts, nor how its audits relate to the Dagster asset checks E2 already uses. Plan 0004
decision 4 had assumed SQLMesh would take over `financial_statements_canonical` in Phase 3. Several further
choices only showed up while building, and are recorded here so they are not rediscovered.

## Decision

1. **SQLMesh starts where the data becomes tabular.** Producing `financial_statements_canonical` means unwrapping
   ZIPs, walking XSDs and evaluating XPath, which is Python, not SQL. The canonical table, `restatement_events` and
   `identity_check_results` stay the Dagster/Polars assets' Parquet under `WAREHOUSE_DIR` (ADR 0008). SQLMesh reads
   them, and never writes them. This supersedes plan 0004 decision 4. SQLMesh owns `quarantine`, `dq_mart`, and the
   staging models that later feed H.

2. **DuckDB executes; SQLMesh state lives in Postgres.** Models materialize into `WAREHOUSE_DIR/transform.duckdb`.
   SQLMesh's snapshots, plans and environments go to the Postgres that already holds the manifest (schema
   `sqlmesh`), not to a second DuckDB file: Dagster and an interactive `sqlmesh` CLI both touch the state, and a
   file-backed state invites a lock the day they overlap. **Fallback:** if Postgres state proves awkward for a
   single-developer loop, switch the `local` gateway's `state_connection` to a DuckDB file. That is a config change,
   not a redesign, and does not need a new ADR.

3. **The config is `transform/config.py` and reads `Settings`.** A Python config takes `WAREHOUSE_DIR` and the
   Postgres credentials from `distress_radar.settings.Settings`, so the path and the connection have one source of
   truth shared with the Python side (a YAML config would have needed a second copy of both). The Postgres state
   connection needs `psycopg2`. It comes from **`psycopg2-binary`**, not the `sqlmesh[postgres]` extra, which builds
   `psycopg2` from source and needs `pg_config`. SQLMesh's state connection is its only user; project code stays on
   psycopg 3 (ADR 0006).

4. **The external boundary is the DuckDB schema `ext`.** `before_all` recreates one view per upstream dataset at the
   start of every `plan` and `run`:
   - `read_parquet` over each Parquet dataset under `WAREHOUSE_DIR`;
   - the Postgres manifest tables through DuckDB's `postgres` extension, attached read-only as the catalog
     `manifest`. `filing_index` is exposed as an explicit column subset; `quarantine_events` and `parsed_documents`
     in full.

   Models select only from `ext.*`, whose columns are declared in `transform/external_models.yaml`. Tests hold the
   Parquet declarations to the Polars schemas, and the Postgres ones to the live DDL. DuckDB's extension
   auto-install is off, so a run never downloads anything: `make transform-setup` installs `postgres` once.

5. **Tests need no services.** The `test` gateway uses in-memory DuckDB for both execution and state. `before_all`
   does not run for `sqlmesh test`, so the `ext` tables there are the unit tests' own fixtures, and `make check`
   (which includes `make transform-test`) still needs nothing running.

6. **The bitemporal snapshot layer is deferred to Phase 5.** AGENT_SPEC §6F describes F's output as partitioned by
   `as_of_month` with `valid_from` / `valid_to`. That shape serves the point-in-time snapshots H consumes, and H does
   not exist yet. Building it now would fix its shape before the feature store can say what it needs. Staging models
   that feed H will be incremental by `known_from`, the axis on which data arrives (§4.7), not `fiscal_year`.

7. **The DQ models are full rebuilds.** `quarantine` is a current set, and `dq_mart` aggregates filings with many
   `known_from` dates per cell. A grading-rule change rewrites both all the way back, so there is nothing to append
   to. SQLMesh's `run` only executes a model when its cron interval is due, so a rebuild is two plans:
   - an ordinary plan applies any change to the model code;
   - a plan with `restate_models` then rebuilds the tables.

   The order matters: a restatement plan ignores local changes and would rebuild the versions already in `prod`.
   `plan` also runs the SQLMesh unit tests first, so a model change that breaks them stops a rebuild before anything
   is written. That is kept deliberately.

8. **Audits are non-blocking in SQLMesh; Dagster enforces them.** Every audit, built-in or custom, is declared
   `blocking false`. `distress_radar.transform_project.build_dq_models` runs the audits after the rebuild, from a
   fresh `Context`: the planning one still holds the pre-plan snapshots, which SQLMesh refuses to audit.
   `dagster_defs/assets/dq.py` reports each audit as an asset check on its model's asset. A blocking audit would make
   `plan` raise, and the run would fail without saying which audit did. Non-blocking matches the E2 identity checks:
   the table is written, and a red check flags it.

## Consequences

- `transform.duckdb` is held open for the length of a SQLMesh call. DuckDB allows one writer per file, so a Dagster
  DQ run and a `make transform-*` command must not run at the same time; one of them fails on the lock. This is
  acceptable for a single-developer batch project. If it stops being acceptable, the answer is to serialize those
  runs, not to change engines.
- The four DQ assets form one multi-asset and cannot be selected apart: they are one SQLMesh build.
- The Dagster check list is static, so loading definitions never starts SQLMesh. `tests/transform/test_dq_assets.py`
  holds it equal to the audits the project declares, so an added or renamed audit fails there, not in the middle
  of a run.
- `build_dq_models` uses `Context.snapshot_evaluator.audit`, the same call `Context.audit` makes internally, to get a
  failing-row count per audit instead of one boolean. It is not a documented public API, so a SQLMesh upgrade must
  re-run `make test-integration` and a DQ materialization before landing.
- SQLMesh brings in pandas and numpy; project code still uses Polars, enforced by `tests/test_no_pandas.py` (plan
  0007 decision 8).
- Decision 6 is closed by ADR 0012: no separate snapshot layer is built, and features are assembled in Python.
  Revisit decision 2's fallback if the Postgres state ever causes a problem in practice.
