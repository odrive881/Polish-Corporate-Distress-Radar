# Polish Corporate Distress Radar

A batch data platform that estimates the probability a Polish company enters bankruptcy, restructuring, or liquidation within 12 and 24 months, built from statutory financial filings, registry history, and insolvency registers.

**Status:** Phase 3 complete (`docs/plans/0007-phase-3c-sqlmesh-quarantine-dq-mart.md`, close-out 2026-09-22; plan 0005 before it). Every XML structure version in the seed is mapped — full, small and micro forms, schemas 1-0, 1-2 and 1-3, the 2025 CRWDE wariant 2, and the thousands-of-złoty twin — and parsed from the stored downloads into `financial_statements_canonical`: Parquet under `WAREHOUSE_DIR` (ADR 0008), with full lineage and a `quality_grade` from the accounting identity checks, which run as Dagster asset checks. `restatement_events` holds prior-year differences (130 for the seed). For the 17-entity seed that is **129 statements across all 17 entities**, fiscal years 2018–2025, 43,611 canonical facts: 82 pass, 19 warn, 28 quarantined for genuine filing defects. One stored statement is a PDF inside an ePUAP envelope; it stays recorded as `needs_pdf_tier` because the C3 tier is deferred — that year's figures are already in the warehouse as the next filing's comparative column, and both filings post-date the entity's bankruptcy (plan 0006, status section). Every identity result behind those grades is persisted too (`identity_check_results`). A SQLMesh project (`transform/`, ADR 0010) derives the current `quarantine` set from them (29 files: 28 E2, 1 C1), leaving out the 22 stale rows in the append-only `quarantine_events` log. It also builds `dq_mart`: pass rates by structure version, filed body set, fiscal year and check, plus coverage per fiscal year. `balance_sheet_balances` passes 99.2% of files and `subtotals_consistent` 65.9%. Small-cell suppression is built and switched off until Phase 9 publishes anything. Phase 1 (acquisition) is complete; RDF documents are captured by hand as HAR files (§ "Manual RDF capture") because RDF serves automated browsers a CAPTCHA (ADR 0007).

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

RDF document retrieval (A3) drives a real Chromium through Playwright, a local prerequisite beyond `make install` (pulls system libraries, so it asks for sudo under WSL):

```bash
uv run playwright install chromium --with-deps
```

Local services (MinIO + Postgres): `cp .env.example .env` and fill it in, then `make dev-up` (`make dev-down` to stop; data persists in named volumes).

Dagster (orchestration wiring in `dagster_defs/`, imports from `src/`): `uv run dagster dev -m dagster_defs.definitions` from the repo root. Materialize `universe_candidates` then `entity_master` (needs `make dev-up`; BIR1 defaults to GUS's public test environment, set `GUS_BIR1_ENDPOINT=prod` and `GUS_BIR1_API_KEY` for production). Then `filing_index` and `raw_filing_documents` (A3, RDF through Playwright at `RDF_REQUESTS_PER_MINUTE`, default 3 per request). `raw_filing_documents` runs for hours: its `max_documents` / `download_scope_only` config splits it, and setting the `rdf_browser` resource's `headless: false` shows the browser (worth doing on the first live run). Re-run `notebooks/exploration/rdf_access_probe.py` before any production-scale backfill: RDF's WAF posture and the informal rate confirmation (ADR 0007) can both change without notice.

Parsing (C1/C2) and grading (E2) run from the same place: `uv run dagster asset materialize -m dagster_defs.definitions --select financial_statements_canonical`, then `--select restatement_events`. They need Postgres and MinIO but no network, rebuild the whole dataset on each run, and write Parquet under `WAREHOUSE_DIR` (ADR 0008). The run metadata reports files by structure version and status, quarantine reasons, grades, and identity results per check. The four accounting identities run as Dagster asset checks on the same run.

Data quality (E3/F) is the `dq` asset group, downstream of both. `uv run dagster asset materialize -m dagster_defs.definitions --select "financial_statements_canonical*"` runs everything from parsing to `dq_mart` in one go. The group rebuilds the SQLMesh models in `transform/` (`quarantine`, `dq_mart`, `dq_mart_coverage`, into `WAREHOUSE_DIR/transform.duckdb`) and reports each SQLMesh audit as an asset check. Once, before the first run: `make transform-setup` installs DuckDB's `postgres` extension (runs never download it). From the command line, `make transform-plan` applies model changes and `make transform-test` runs the SQLMesh unit tests, which are part of `make check` and need no services. Don't run a `make transform-*` target while a Dagster DQ run is in progress: both need `transform.duckdb`, and DuckDB allows one writer. `DQ_MART_MIN_CELL_ENTITIES` sets small-cell suppression; it is empty (off) by default, and must be set to at least 5 before `dq_mart` is published anywhere.

Re-running with unchanged inputs and unchanged mapping config reproduces the Parquet byte for byte (invariant 5). Editing anything under `config/mappings/` changes the spec hash by design, which gives every affected file a new `parsed_documents` row and a new `ingestion_run_id` — the figures themselves must not move. `uv run python notebooks/exploration/canonical_value_hash.py` prints the column-wise value hash per structure version (run ids excluded) so a mapping change can be checked against the values it should not have touched.

## Manual RDF capture

RDF shows an automated browser an hCaptcha challenge (ADR 0007, live result 2026-09-16), so for now financial statements are fetched by hand in an ordinary browser. The browser records the session, and the `rdf_manual_import` asset reads everything it needs from that recording: the filing list, each document's details (including the submission date), and the downloaded files. Nothing needs to be typed in.

Use Chrome or Edge on Windows, at a normal pace (about 3 downloads a minute, the rate KRS support confirmed). One or two companies per recording keeps the file small.

1. Open `https://rdf-przegladarka.ms.gov.pl/wyszukaj-podmiot`. If a CAPTCHA appears, solve it as you normally would.
2. Press F12, open the **Network** tab, tick **Preserve log**, and leave the filter on **All**. Keep DevTools open until step 6: requests made while it is closed are not recorded.
3. Enter the KRS number and click **Wyszukaj**.
   - If the list has more than 10 documents, first set the rows-per-page selector under the list to its largest option (50 or 100).
   - If the list is still longer than one page, click through every page with **›**.
   - The type filter is fine to use afterwards. The import ignores filtered lists, but it needs the full list to have loaded once.
4. For each **Roczne sprawozdanie finansowe** row from 2018 onwards (corrections included):
   - Skip pre-2018 statements. They were filed as separate PDF rows (**Bilans**, **Rachunek zysków i strat**, **Informacja dodatkowa…**) and are out of v1 scope. They are still indexed from the list.
   - click the arrow at the end of the row to expand it, and wait for the details;
   - click **Pobierz dokumenty** and wait for the download to finish.

   Expanding other rows is optional; their details (submission dates) are imported too.
5. **Never click "Pokaż zgłoszenie".** It lists the signatories by name, and the project does not collect data on natural persons.
6. In the Network tab, click the download-arrow icon (**Export HAR (sanitized)…**) and save the file into `.cache/rdf_inbox/` in the repo (on Windows: `C:\Users\PC\Desktop\LARGE_pipeline\.cache\rdf_inbox\`), e.g. `2026-09-16_0000209396.har`. The files the browser saved to Downloads are not needed: the recording holds the same bytes.
7. Import: `uv run dagster asset materialize -m dagster_defs.definitions --select rdf_manual_import`. The run's `missing_documents` metadata lists statements that still need a capture (not expanded, or not downloaded). Re-capture just those and import again. Importing a file twice adds nothing.

Recordings are gitignored (`*.har`, `.cache/`). They contain session data and the documents *as filed*, including signatories' names and PESEL numbers, so delete them once imported. The import stores only redacted copies (ADR 0009). Only the company must already be in `entity_master` (A2); other KRS numbers are skipped with a warning. `RDF_MANUAL_INBOX` moves the inbox elsewhere.

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
