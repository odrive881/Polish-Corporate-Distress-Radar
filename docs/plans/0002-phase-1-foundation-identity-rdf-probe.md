# 0002 — Phase 1 foundation: raw store + manifest, A1 seed, A2 GUS BIR1, RDF access probe

**Stage:** Phase 1 (AGENT_SPEC.md §10), first half — stages A1 (as a hand-picked seed), A2, B1, B2, plus an A3 access spike. No A3 adapter yet.

## Why

Phase 0 is complete (plan 0001; ADRs 0004/0005; `docker-compose.yml`; Dagster hello-world). Phase 1's deliverable — "acquisition for 20 hand-picked entities; raw documents in MinIO, manifest in Postgres" — is too large for one reviewable step, and nothing under `src/distress_radar/acquisition/` exists: no typed settings, no shared HTTP base, no raw store, no manifest.

This plan builds the shared foundation every adapter needs, lands the seed universe, and validates identities via GUS BIR1. It also runs a small, time-boxed probe against RDF: ADR 0004 left RDF's bot protection unconfirmed, and AGENT_SPEC §10 orders phases so that an inaccessible source is discovered before anything is built on it. The probe's findings (ADR 0007) decide the shape of plan 0003's A3 adapter.

## Out of scope (do not do these here)

- The A3 `document_retrieval.py` adapter, `filing_index`, bulk document download — plan 0003.
- A4 (KRZ/MSiG), A5 (NBP/BDL), and scraping-based A1 discovery from registry aggregators.
- A real `config/mappings/pkd_crosswalk.yaml` — section F is checked at section level only; the full PKD 2007↔2025 crosswalk arrives when discovery goes beyond a seed list.
- Replacing the prototype `parsing/mapping_engine.py` (C2, Phase 2).
- New `docker-compose.yml` services.

## Steps

### A. Dependencies and settings

- Add runtime `[project].dependencies`: `pydantic`, `pydantic-settings`, `httpx`, `hishel`, `pyrate-limiter`, `tenacity`, `zeep`, `boto3`, `lxml`, `pyyaml`, `psycopg[binary]`. Add `marimo` to dev. Run `make lock`.
  - **Flag:** `psycopg` and `pyyaml` are not named in the locked stack, but Postgres and YAML config require a driver/loader — not a stack substitution. Record in ADR 0006.
  - Check current `hishel` (1.x API change) and `pyrate-limiter` v3 APIs before writing code against them.
- `src/distress_radar/settings.py`: `pydantic-settings` `Settings` reading the **existing** `.env.example` variable names (`POSTGRES_*`, `MINIO_*`, `GUS_BIR1_API_KEY`). Add only `GUS_BIR1_ENDPOINT` (test vs prod) and `HTTP_CACHE_DIR`, documented in `.env.example`.

### B. Shared acquisition base — `src/distress_radar/acquisition/base.py`

- Transient vs permanent exception classes (`TransientSourceError`, `PermanentSourceError`); `tenacity` retries only transient ones (network errors, 429, 5xx).
- `httpx.AsyncClient` factory wrapped with a `hishel` disk cache (`HTTP_CACHE_DIR`, gitignored) and a persistent `pyrate-limiter` bucket (`PostgresBucket`, so pacing survives restarts per §6A). Limits come from per-source settings, not constants.
- A "did we get real content" hook (`ContentCheck` callable); failure raises a distinct `ContentCheckFailed` — the seam where plan 0003 plugs in Playwright routing (ADR 0004).

### C. B1 raw store and B2 manifest

- `acquisition/raw_store.py`: `put_raw(bytes, meta) -> sha256`.
  - Key `raw/sha256/<h[:2]>/<h>`; sidecar `<key>.meta.json` with source URL, content type, fetch timestamp, HTTP headers, `ingestion_run_id`.
  - `head_object` hit → no-op; objects are never overwritten.
  - Creates `MINIO_BUCKET` on first use (as the compose file comment promises).
  - Written against a small `ObjectStore` Protocol — in-memory implementation for unit tests, `boto3` for real.
- `acquisition/manifest.py`: plain idempotent DDL (`CREATE TABLE IF NOT EXISTS`) via `ensure_schema()`, and typed insert functions (`ON CONFLICT DO NOTHING`). No Alembic. Tables:
  - `raw_documents(sha256 PK, object_key, byte_size, content_type, first_fetched_at, first_ingestion_run_id)`
  - `raw_document_fetches(sha256, source_url, fetched_at, ingestion_run_id, source)` — one row per fetch, so repeat fetches keep lineage.
  - `universe_candidates(krs, discovery_source, discovered_at, ingestion_run_id)`
  - `entity_master(krs PK, nip, regon, name, legal_form_code, status, pkd_codes jsonb, pkd_predominant, source_document_hash, known_from, ingestion_run_id)`
  - `entity_reconciliation_log(...)` — per §6A, written when BIR1 and seed data disagree.
  - `quarantine(stage, entity_key, reason_code, detail, source_document_hash, ingestion_run_id, created_at)`. **Flag:** reuses the canonical `quarantine` name as the Postgres landing table; E3's SQLMesh `quarantine` model later reads from it. Recorded in ADR 0006.
- Every row carries `ingestion_run_id` — the Dagster run id under Dagster, otherwise passed explicitly.

### D. A1 — hand-picked seed universe

- `config/segments/construction_sme_v1.yaml`: PKD section `F`, legal form sp. z o.o., sizes `[small, medium]`, `min_history_years: 3`, `pkd_match: predominant`. Size and history are only *declared* here; they are enforced from filings in later stages.
- `config/segments/construction_sme_v1_seed.yaml`: ~20 KRS numbers, `discovery_source: manual_seed`.
  - Candidates are researched and proposed by the implementing agent: mostly live construction sp. z o.o., plus several publicly known to have entered bankruptcy or restructuring, so label work later has positives.
  - **The list is reviewed by a human before it is committed.**
  - Placement note: `DIRECTORY_STRUCTURE.md` §3 has no exact row for a seed list; `config/segments/` ("declarative universe specs") is the closest fit.
- `acquisition/universe_discovery.py`: `load_seed(path) -> list[UniverseCandidate]`; validates KRS format (10 digits, zero-padded); malformed entries go to quarantine.

### E. A2 — GUS BIR1 (`acquisition/regon_client.py`, `acquisition/models.py`)

- `zeep` adapter: `Zaloguj` → `sid` HTTP header → `DaneSzukajPodmioty(Krs=…)` → `DanePobierzPelnyRaport` (legal-person reports, including the PKD report) → `Wyloguj`.
  - Session acquisition/refresh is internal; an expired or empty session triggers one re-login.
  - Defaults to the BIR1 **test environment** and its public test key, so development needs no production key.
  - BIR1 uses WS-Addressing and returns XML as embedded strings — verify the zeep binding details against the live WSDL first.
- Raw-first: each report XML string goes to `put_raw` **before** being parsed with lxml into a Pydantic `Bir1LegalEntity`.
- **Legal entities only (invariant 6):** check the search result's `Typ` **before any raw write**. A natural-person type (`F`/`LF`) is never persisted — no bytes, no name — only a `quarantine` row (`reason_code=natural_person`, KRS only). Invariant 6 takes precedence over raw-first here; say so in the module docstring.
- Segment checks go to quarantine with reason codes, never dropped: `not_found`, `legal_form_mismatch`, `pkd_section_mismatch`. `deregistered` is recorded but not rejected — deregistered entities matter for labels.
- `known_from` = fetch date (UTC). PKD codes carry the classification version BIR1 reports.

### F. Dagster wiring — `dagster_defs/`

- Replace `hello_world` in `assets/acquisition.py` with `universe_candidates` (wraps `load_seed`) and `entity_master` (depends on it, wraps `regon_client`). Docstrings state inputs, outputs, partition scheme (unpartitioned for now).
- `definitions.py`: `ConfigurableResource`s for Postgres, object store, and BIR1, built from `Settings`. Kept in `definitions.py` — no new files.

### G. RDF access probe (spike, time-boxed)

- `notebooks/exploration/rdf_access_probe.py` (marimo `.py`): uses the `base.py` client against `ekrs.ms.gov.pl/rdf/rd/` for 2–3 seed KRS numbers at low rate. Records:
  - status codes and any challenge/JS gating;
  - whether the document list is a JSON/XML endpoint or HTML;
  - download URL shape, and whether the submission date (`known_from`) is exposed.
- Any bytes fetched go through `put_raw`.
- Findings → **ADR 0007 `rdf-access-probe-results`** (follow-up to 0004): plain httpx works / Playwright needed / CAPTCHA blocks. This decides plan 0003's shape.

### H. ADRs and docs

- **ADR 0006** `postgres-manifest-and-schema-management`: manifest in Postgres not DuckDB (TECHNICAL_ARCHITECTURE §B2 asks for this ADR), plain idempotent DDL instead of Alembic, the psycopg/pyyaml additions, the `quarantine` landing-table choice.
- Update the `README.md` status line. Add `make test-integration` (`pytest -m integration`, needs `make dev-up`); `make check` stays network- and service-free.

## Tests (`tests/acquisition/`, no network — AGENT_SPEC §8)

- `test_raw_store.py`: content addressing; idempotent re-put leaves object and sidecar unchanged; key layout.
- `test_manifest.py` (`@pytest.mark.integration`, live Postgres): `ensure_schema` idempotent; re-inserts are no-ops.
- `test_universe_discovery.py`: seed loading; malformed KRS → quarantine.
- `test_regon_client.py`: recorded, sanitised BIR1 responses in `tests/fixtures/bir1/` (legal entity, not found, natural person, wrong PKD). Asserts parsing into `Bir1LegalEntity`; natural-person response writes no raw object and only a quarantine row; raw write happens before parse.
  - `tests/conftest.py` globs only top-level `fixtures/*.xml`, so the `bir1/` subdirectory stays out of the `parsed_filing` fixture — keep it that way.
- `test_base.py`: transient errors retry, permanent don't (`httpx.MockTransport`).
- Register the `integration` marker in `pyproject.toml`.

## Definition of done

- [ ] `settings`, `base`, `raw_store`, `manifest`, `universe_discovery`, `regon_client` implemented and tested.
- [ ] Seed list human-reviewed and committed.
- [ ] Dagster assets replace hello-world and materialize end to end against compose services: ~20 `universe_candidates` rows, `entity_master` rows for valid entities, reason-coded `quarantine` rows for the rest, `raw/sha256/...` objects with sidecars in MinIO.
- [ ] Re-materializing adds no new raw objects or manifest rows (idempotence).
- [ ] ADR 0006 and ADR 0007 written; `README.md` updated.
- [ ] `make check` green; `make test-integration` green with `make dev-up`.

## Next plan

`docs/plans/0003-*.md`: A3 document retrieval — RDF per-entity filing index plus XML/PDF download into B1/B2, with `known_from` = RDF submission date — shaped by ADR 0007. A Playwright tier only if the probe requires it.
