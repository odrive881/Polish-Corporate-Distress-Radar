# 0006 — Postgres manifest and schema management

- **Status:** accepted; decision 4 superseded (addendum, 2026-09-22)
- **Date:** 2026-09-14

## Context

Plan 0002 builds B1 (raw object store) and B2 (manifest) plus the first two acquisition stages (A1 seed, A2 GUS BIR1). Four decisions came up that the locked stack does not settle on its own, and `docs/TECHNICAL_ARCHITECTURE.md` §B2 explicitly asks for the manifest-database choice to be recorded as an ADR.

## Decision

1. **The manifest lives in Postgres, not DuckDB.** Acquisition writes concurrently: several adapters, later several async workers per adapter, plus Dagster retries. DuckDB allows one writer process per database file. Postgres handles the OLTP side (manifest, `universe_candidates`, `entity_master`, `entity_reconciliation_log`, the quarantine landing table, and the pyrate-limiter bucket tables). DuckDB stays the analytical engine that reads from it later (F, H). This split has a concrete reason and is not a preference.

2. **Plain idempotent DDL, no Alembic.** `distress_radar.acquisition.manifest.ensure_schema()` runs `CREATE TABLE IF NOT EXISTS` statements. Every insert is `ON CONFLICT DO NOTHING` against a natural key, which is what makes re-materialization a no-op (invariant 5):
   - `raw_documents`: `sha256`
   - `raw_document_fetches`: `(sha256, source_url, ingestion_run_id)`, so a repeat fetch in a new run keeps lineage without duplicating the document
   - `universe_candidates`: `(krs, discovery_source)`
   - `entity_master`: `krs`
   - `entity_reconciliation_log`: `(krs, field, seed_value, bir1_value)`
   - `quarantine`: `(stage, entity_key, reason_code, source_document_hash)`

   The last two use `UNIQUE NULLS NOT DISTINCT` (Postgres ≥ 15; compose runs 16), so rows with a NULL hash still dedupe. A natural-person quarantine row is one such row. Six tables with no production data do not justify a migration framework yet. Revisit when the first change to an existing column is needed: `IF NOT EXISTS` cannot express that.

3. **`psycopg` and `pyyaml` added as runtime dependencies.** Neither is named in the locked stack, but the stack's Postgres and YAML config need a driver and a loader. This is not a stack substitution:
   - `psycopg[binary,pool]` is the maintained Postgres driver. The `pool` extra is required by pyrate-limiter's `PostgresBucket`.
   - `pyyaml` (`safe_load` only) reads `config/`.

   Two other additions in the same change are also not substitutions:
   - `hishel[async]` pulls `anysqlite` for hishel 1.x's async SQLite cache storage.
   - Type stubs (`boto3-stubs[s3]`, `types-pyyaml`, `lxml-stubs`) are dev-only, for pyright strict.

4. **`quarantine` is also the name of the Postgres landing table.** AGENT_SPEC §5 reserves `quarantine` as a canonical dataset produced by the E3 SQLMesh model. Acquisition-stage rejects (A1 malformed seed rows, A2 `not_found` / `natural_person` / `legal_form_mismatch` / `pkd_section_mismatch` …) need somewhere durable to land before any SQLMesh project exists. A second name such as `acquisition_rejects` would create two concepts for one thing. The Postgres table uses the canonical name and columns (`stage`, `entity_key`, `reason_code`, `detail`, `source_document_hash`, `ingestion_run_id`, `created_at`). E3's SQLMesh `quarantine` model later reads it as a source and unions in parse- and validation-stage rejects.

Two smaller choices, recorded so they are not rediscovered:

- `entity_master` gains `pkd_source_document_hash` alongside `source_document_hash`. BIR1 serves the entity report and the PKD report as separate documents, and invariant 3 requires both to be traceable.
- The A2 asset skips candidates that already have an outcome (a master row or an A2 quarantine row). That is how re-materializing adds no raw objects, fetch rows, or BIR1 calls. Refreshing entity data is a later, explicit operation, not a side effect of re-running.

## Consequences

- `make check` stays service-free; manifest tests are `@pytest.mark.integration` and run with `make test-integration` after `make dev-up`. Each test works in a throwaway schema.
- Schema changes to existing tables need a hand-written, idempotent `ALTER` in `SCHEMA_DDL` until a migration tool is justified. That is the trigger to revisit this ADR.
- E3 must treat the Postgres `quarantine` table as an upstream source, not recreate it.
- `PostgresBucket` creates its own `ratelimit___<source>` tables on first use; they share the manifest database.

## Addendum, 2026-09-22 — `quarantine` renamed `quarantine_events` (plan 0007)

Decision 4 is superseded. Once the SQLMesh `quarantine` model existed, one name covered two objects with opposite
semantics: the Postgres table is an append-only log of *first detection*, and the model is the *current*
quarantined set, recomputed on every run. That is exactly what the canonical-names rule exists to prevent. Plan
0007 decision 3 gave the canonical name to the model, and the table became **`quarantine_events`**. Its role, its
natural key `(stage, entity_key, reason_code, source_document_hash)` and its append-only rule (never updated, never
deleted) are unchanged. It gained nullable `krs` and `document_ref` columns, which every writer now sets, and old
rows were backfilled from `entity_key`.

The rename was the first change to an existing table that `IF NOT EXISTS` cannot express, which is this ADR's
stated trigger for a migration tool. It was still done in plain idempotent DDL: a `DO` block renames the table
only if the old name exists and the new one does not, and a validating backfill fails on a key in no known format
rather than skipping it. Both sit in `SCHEMA_DDL` and are no-ops once applied. A migration tool is still not
justified for one rename on a single-developer database, but the next such change should re-ask the question
rather than add a third `DO` block by default.

`parsed_documents` (C1) also gained `filed_bodies`, `last_seen_run_id` and `last_seen_at` in the same plan, all
nullable and added by `ADD COLUMN IF NOT EXISTS`.

The consequence above that "E3 must treat the Postgres `quarantine` table as an upstream source" now reads
`quarantine_events`, and it is what `transform/models/quarantine/quarantine.sql` does: the log is the only source
for A-stage rejects, and supplies stage and reason codes for C-stage files. It is never the source of *whether* a
parsed or graded file is currently quarantined (ADR 0010, plan 0007 decision 4).
