# 0008 — Derived data storage location

- **Status:** accepted
- **Date:** 2026-09-17

## Context

Plan 0004 (Phase 2) produces the first derived datasets: `financial_statements_canonical` and `restatement_events`. AGENT_SPEC §5 says they are Parquet, and §6F says SQLMesh on DuckDB eventually builds them, partitioned, with content-hashed snapshot manifests. Nothing says *where* the Parquet files live. MinIO is set up as the raw store (B1, invariant 2), and Postgres holds the manifest (ADR 0006). Phase 2 needs a location before SQLMesh arrives in Phase 3.

Options considered:

- **A second MinIO bucket.** Keeps everything behind the S3 API, which suits a later hosted deployment. But DuckDB and Polars then need S3 credentials and `httpfs` for every local read, tests need a MinIO or a fake, and nothing at this scale needs remote storage.
- **The raw bucket under a `derived/` prefix.** Rejected: it mixes immutable raw bytes with rebuildable outputs and weakens invariant 2's "raw store holds only downloaded bytes".
- **Postgres tables.** Rejected: §5 names Parquet, and the analytical path (DuckDB `ASOF JOIN`, H) reads Parquet.
- **A local directory.** The data fits on a laptop (TECHNICAL_ARCHITECTURE "Sizing"). DuckDB and Polars read it with no setup, and tests use `tmp_path`.

## Decision

1. Derived datasets are Parquet files under `WAREHOUSE_DIR` (setting `warehouse_dir`, default `.data/warehouse`, gitignored), one directory per canonical dataset name, Hive-partitioned (e.g. `financial_statements_canonical/fiscal_year=2021/part-0.parquet`).
2. Every derived dataset can be rebuilt from MinIO + Postgres, so it is not backed up. Deleting `.data/` loses nothing that a re-materialization cannot reproduce byte-for-byte (invariant 5).
3. Writers produce each partition deterministically: fixed row order, fixed compression, no timestamps in the data. They write to a temporary name and rename, so readers never see a half-written file.
4. MinIO stays raw-only.

## Consequences

- Phase 3's SQLMesh project (§6F) reads and writes the same directory, and its snapshot manifests live alongside the data.
- A hosted deployment later can point `WAREHOUSE_DIR` at a mounted volume, or revisit this ADR in favour of an S3 bucket. The dataset layout stays the same either way.
- `.data/` is local state, like `.cache/`. It is not shared between machines.
