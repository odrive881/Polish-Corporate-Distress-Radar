# Polish Corporate Distress Radar

A batch data platform that estimates the probability a Polish company enters bankruptcy, restructuring, or liquidation within 12 and 24 months, built from statutory financial filings, registry history, and insolvency registers.

**Status:** Phase 1, first half (`docs/plans/0002-phase-1-foundation-identity-rdf-probe.md`): shared acquisition base, content-addressed raw store (MinIO) and manifest (Postgres), a 19-entity hand-picked seed universe, and GUS BIR1 identity validation wired as Dagster assets. The RDF access probe found every RDF host behind an Imperva Incapsula WAF (`docs/adr/0007-rdf-access-probe-results.md`), so financial-statement retrieval (A3, plan 0003) is on hold pending a terms-of-use decision. Parsing is still the prototype XML parser with a golden-fixture harness.

## Where to start

| Document | Purpose |
|---|---|
| `CLAUDE.md` | Agent entry point: invariants, locked stack, conventions |
| `AGENT_SPEC.md` | What to build, stage by stage |
| `DIRECTORY_STRUCTURE.md` | Where every file goes |
| `docs/PROJECT_OVERVIEW.md` | Functional view, stages 1–12 |
| `docs/TECHNICAL_ARCHITECTURE.md` | Tool selection and rationale |
| `docs/adr/` | Architecture decision records |

## Setup (WSL / Ubuntu)

Requires [uv](https://docs.astral.sh/uv/). `.venv` is created and kept in sync with `uv.lock` by `make install` — don't create it by hand or `pip install` into it directly.

```bash
make install   # uv sync --locked --extra dev
make check     # ruff + pyright + pytest (no network, no services)
make test-integration   # tests needing live Postgres/MinIO — after make dev-up
```

Local services (MinIO + Postgres): `cp .env.example .env` and fill it in, then `make dev-up` (`make dev-down` to stop; data persists in named volumes).

Dagster (orchestration wiring in `dagster_defs/`, imports from `src/`): `uv run dagster dev -m dagster_defs.definitions` from the repo root. Materialize `universe_candidates` then `entity_master` (needs `make dev-up`; BIR1 defaults to GUS's public test environment, set `GUS_BIR1_ENDPOINT=prod` and `GUS_BIR1_API_KEY` for production).

## What not to build

| Tempting | Why it is wrong here |
|---|---|
| Kafka / streaming ingestion | Filings are annual, registry events daily. Streaming would misrepresent the domain |
| Spark / Dask | 10⁷ rows fit in RAM. Distributed compute here signals inability to size a problem |
| Kubernetes | One VPS with Docker Compose runs every service |
| Snowflake / BigQuery | Cost and vendor dependency for capability the project does not need |
| A vector database | pgvector on the existing Postgres covers any semantic search need |
| A model-serving framework | Effectively zero QPS; load from the MLflow registry in-process |
| Feast | Built for online serving; the batch as-of join is the actual skill on display |
| A generic DQ framework | Accounting identities are domain rules that deserve first-class, readable code |
