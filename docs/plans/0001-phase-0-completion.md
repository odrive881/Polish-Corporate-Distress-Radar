# 0001 — Finish Phase 0: verification ADRs, Docker Compose, Dagster hello-world

## Status: complete (2026-09-14; recorded 2026-09-26)

**Stage:** Phase 0 (AGENT_SPEC.md §10 / TECHNICAL_ARCHITECTURE.md §5) — infrastructure and orchestration bootstrap, no domain code.

## Why

Phase 0's deliverable per both spec documents is "repo skeleton, Docker Compose, CI, ADR template, Dagster hello-world asset." The skeleton, CI, and ADR pattern already exist (see `docs/adr/0001`–`0003`). `docker-compose.yml` does not exist yet, `dagster` is not a dependency, and `dagster_defs/` has no assets or `Definitions`. AGENT_SPEC.md §11 additionally requires two "moving target" checks to be confirmed in Phase 0, with findings recorded as ADRs — these have not been done. Phase 1 (acquisition) cannot be demonstrated without MinIO/Postgres running and Dagster wired, so this work blocks it directly.

## Out of scope (do not do these here)

- Any real acquisition adapter (A1–A5) — that's Phase 1.
- Rewriting `src/distress_radar/parsing/mapping_engine.py` — that's stage C2 / Phase 2, already tracked in ADR 0001's consequences.
- Populating `config/segments/`, `config/mappings/`, `config/statutory/` with real values — those arrive with the stages that need them.
- Any service beyond MinIO + Postgres in `docker-compose.yml`. Add MLflow, etc. only when the stage that needs them starts (I4, J) — keep the compose file matched to what Phase 1 actually requires, per the project's small-data, add-only-when-needed posture (ADR 0002).

## Steps

### A. Verification checks (do first — findings may change later steps)

1. Confirm the current state of RDF (rebuilt February 2026): document formats served, whether per-entity lookup still works as described, authentication if any, current terms of use and rate expectations. Record findings — including "could not verify, here's what's assumed and why" if live access isn't possible from this environment — in `docs/adr/0004-rdf-2026-platform-verification.md`.
2. Confirm the 2026-generation Ministry of Finance XML structures (effective for statements prepared from 1 January 2026): published XSDs, which entity/form types they cover. Record in `docs/adr/0005-mf-xml-2026-structures-verification.md`.
3. Both ADRs follow the existing template (`docs/adr/0000-template.md`) and existing numbering (next after 0003).

### B. `docker-compose.yml` at repo root

- Services: `minio` (S3 API + console; bucket from `MINIO_BUCKET`) and `postgres` (db/user/password from `.env`, matching `.env.example`'s existing `POSTGRES_*` and `MINIO_*` variables — don't invent new env var names).
- Named volumes for both, so `make dev-down` doesn't lose data by default.
- Add `make dev-up` / `make dev-down` targets (one line each, matching the existing Makefile style from ADR 0003) wrapping `docker compose up -d` / `docker compose down`.

### C. Dagster hello-world asset

- Add `dagster` (and `dagster-webserver` for local dev) to `pyproject.toml`'s dev dependencies; run `make lock` to regenerate `uv.lock`.
- `dagster_defs/definitions.py`: the `Definitions` entry point.
- A minimal, real (not stubbed) asset in `dagster_defs/assets/acquisition.py` — the file `DIRECTORY_STRUCTURE.md` §2 already reserves for stage A — with a docstring noting it is a placeholder proving the orchestration wiring, to be replaced by the real A1 (`universe_discovery`) asset when Phase 1 starts.
- Do **not** create empty stub files for `dagster_defs/partitions.py`, `schedules.py`, `sensors.py`, or anything under `dagster_defs/checks/` yet — ADR 0001 established the convention of no placeholder content until there's something real to put in a file; `.gitkeep` stays until then.
- Verify: `uv run dagster dev -f dagster_defs/definitions.py` loads without error and the hello-world asset materializes; `make check` still passes.

### D. Housekeeping

- Update `README.md`'s status line from "Phase 0 (repo skeleton)" to reflect Phase 0 being complete and Phase 1 starting next.
- If step A's findings materially change an assumption elsewhere in `CLAUDE.md` / `AGENT_SPEC.md` (e.g. RDF access pattern turns out not to be per-entity-only), update that document too and say so in the ADR's Consequences section.

## Definition of done

Met (2026-09-14, recorded 2026-09-26): plan 0002 opens with Phase 0 complete, and its close-out verifies `make check` on fresh compose volumes.

- [x] `docs/adr/0004-rdf-2026-platform-verification.md` and `docs/adr/0005-mf-xml-2026-structures-verification.md` exist with real findings (or documented inability to verify, with the assumption it falls back to).
- [x] `docker-compose.yml` brings up MinIO + Postgres; `make dev-up` / `make dev-down` work.
- [x] `dagster` is a real dependency; `dagster dev -f dagster_defs/definitions.py` runs and materializes the hello-world asset.
- [x] `make check` is green.
- [x] `README.md` status line updated.

## Next plan

Once this lands, `docs/plans/0002-*.md` should cover Phase 1: acquisition for ~20 hand-picked entities (A1 universe discovery + A2 GUS BIR1 identity validation at minimum), raw documents landing in MinIO with the manifest in Postgres.
