# Polish Corporate Distress Radar

A batch data platform that estimates the probability a Polish company enters bankruptcy, restructuring, or liquidation within 12 and 24 months, built from statutory financial filings, registry history, and insolvency registers.

**Status:** Phase 2 complete (`docs/plans/0004-phase-2-canonical-parsing-identities.md`, close-out 2026-09-17). The full-form Ministry of Finance statements (schemas 1-0 and 1-2, the 2025 CRWDE wariant 2, and the thousands-of-złoty twin) are parsed from the stored downloads into `financial_statements_canonical`: Parquet under `WAREHOUSE_DIR` (ADR 0008), with full lineage and a `quality_grade` from the accounting identity checks, which run as Dagster asset checks. `restatement_events` holds prior-year differences. For the 17-entity seed that is 81 statements: 46 pass, 14 warn, 21 quarantined for genuine filing defects. Small- and micro-form structures (49 seed statements) and the PDF tier are Phase 3. Phase 1 (acquisition) is complete; RDF documents are captured by hand as HAR files (§ "Manual RDF capture") because RDF serves automated browsers a CAPTCHA (ADR 0007).

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
