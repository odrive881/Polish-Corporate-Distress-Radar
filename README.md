# Polish Corporate Distress Radar

A batch data platform that estimates the probability a Polish company enters bankruptcy, restructuring, or liquidation within 12 and 24 months, built from statutory financial filings, registry history, and insolvency registers.

**Status:** Phase 7 (text signals) in progress (`docs/plans/0013-phase-7-text-signals.md`): every statement's going-concern disclosure, and from FY2025 average employment and the audit flag, are read into `statement_disclosures`; the notes' text layer is read page by page, masked of person names (`make models` first) and prefiltered by lemma; the golden set's tooling is built and 50 of its 240 pages are labelled, the rest waiting for the owner (§ Labelling the golden set); the extractors (lemma rules, and Claude with every response stored and replayed) are built and make no call until the owner confirms the provider's terms; the eval harness and its gate in `make check` are built, and wait for the first model run to score against; `text_signals` and `text_coverage` are built by the `text` job (below), the model signals waiting on the owner's confirmation; `feature_set_v4` (56 features, the new default) adds the statement's going-concern flags and employment and the notes' signals, the notes' values null until they are read, and `backtest_v2` adds the going-concern flag to the regression; the seed's auditor reports, downloaded by hand, are stored redacted and dated from the owner's list (51 of 52, 49 dated), and the `text` job reads them as their own `document_kind` (48 dated reports; only the opinion rule has run on them).

Phase 6 complete (`docs/plans/0012-phase-6-baselines-out-of-time-backtest.md`, 2026-09-27): a purged out-of-time backtest of Altman's Z'', the Poznań model and a regularised logistic regression, a generated Markdown report with the seed caveat on every table, MLflow runs carrying the four identifiers, and the hand-run `backtest` job. Every result on the 17-entity seed is machinery, not evidence: 11 events, and once each fold trains only on labels already public by its test year (corrected 2026-09-29), no cell of 84 reaches the 3-event floor. Scale is the critical path, and it waits on the owner's decision in ADR 0013 (proposed).

Phase 5 (`docs/plans/0010-phase-5-point-in-time-feature-store.md`, 2026-09-26): `feature_store` holds 49 features (`feature_set_v3`; v1 had 44) for every month-end of the label grid (2,929 rows for the 17-entity seed), each with the date it became knowable: financial ratios and trends, construction metrics, KSH tripwires, filing behaviour, registry dynamics and legal history. It is assembled in Python with DuckDB's `ASOF JOIN` (ADR 0012), and the blocking leakage test (`tests/features/test_leakage.py`) recomputes every family from only what was public on each date. Text signals, macro context and size class are deferred (plan 0010).

Phase 4 (`docs/plans/0008-phase-4-legal-events-outcome-labels.md`, close-out 2026-09-23): The seed's legal timeline is built from the **full KRS extract** (open KRS API; both eras, entry-dated) and **MSiG notices** (JSON search, back to 2001). Both are stored only in redacted or person-free form (ADR 0009 addendum). They gave 89 `legal_events` rows in 70 deduplicated events (registrations included; 191 rows since Phase 5 added board, office and capital changes), and **all 9 seed entities with a distress hint are found independently**, with dates and source documents; the other 8 have none. Outcome labels are built in SQLMesh: 12 and 24 months, censored at each entity's cutoff, regime and source-era flags. They are frozen by content hash. The current set is `066d18bbd4cd…` (label version 2, plan 0009: a 12-month lag allowance for `alive`, 24-month petition expiry), 4,694 rows, reproduced exactly on rebuild. Earlier sets stay frozen: `a1ac9fb07f79…` (v1) and `a5da757f8341…` (v1, before the Phase 4 review fixes). KRZ is not built: it sits behind a WAF (ADR 0011). Phase 3 (the canonical financial model, 129 statements across all 17 entities, `quarantine` and `dq_mart` in SQLMesh) and Phase 1 (acquisition; RDF captured by hand as HAR files, ADR 0007) are complete before it.

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
make models    # once: the Polish spaCy model (550 MB, resumable, SHA-256 checked) that text masking needs
make check     # ruff + pyright + pytest (no network, no services)
make test-integration   # tests needing live Postgres/MinIO — after make dev-up
make hooks     # once per clone: a pre-commit scan refusing staged personal data (ADR 0009)
```

`make docs-check` (also part of `make check`) catches mechanical doc drift: a living doc naming a path that does not exist, a file missing from `DIRECTORY_STRUCTURE.md`'s tree, a plan or ADR without a status, a plan marked complete with open items.

RDF document retrieval (A3) drives a real Chromium through Playwright, a local prerequisite beyond `make install` (pulls system libraries, so it asks for sudo under WSL):

```bash
uv run playwright install chromium --with-deps
```

Local services (MinIO + Postgres): `cp .env.example .env` and fill it in, then `make dev-up` (`make dev-down` to stop; data persists in named volumes).

Dagster (orchestration wiring in `dagster_defs/`, imports from `src/`): `uv run dagster dev -m dagster_defs.definitions` from the repo root. Materialize `universe_candidates` then `entity_master` (needs `make dev-up`; BIR1 defaults to GUS's public test environment, set `GUS_BIR1_ENDPOINT=prod` and `GUS_BIR1_API_KEY` for production). Then `filing_index` and `raw_filing_documents` (A3, RDF through Playwright at `RDF_REQUESTS_PER_MINUTE`, default 3 a minute). `raw_filing_documents` runs for hours: its `max_documents` / `download_scope_only` config splits it, and setting the `rdf_browser` resource's `headless: false` shows the browser (worth doing on the first live run). Re-run `notebooks/exploration/rdf_access_probe.py` before any production-scale backfill: RDF's WAF posture and the informal rate confirmation (ADR 0007) can both change without notice.

Parsing (C1/C2) and grading (E2) run from the same place: `uv run dagster asset materialize -m dagster_defs.definitions --select financial_statements_canonical`, then `--select restatement_events`. They need Postgres and MinIO but no network, rebuild the whole dataset on each run, and write Parquet under `WAREHOUSE_DIR` (ADR 0008). The run metadata reports files by structure version and status, quarantine reasons, grades, and identity results per check. The four accounting identities run as Dagster asset checks on the same run.

Data quality (E3/F) is the `dq` asset group, downstream of both. `uv run dagster asset materialize -m dagster_defs.definitions --select "financial_statements_canonical*"` runs everything from parsing to `dq_mart` in one go. The group rebuilds the SQLMesh models in `transform/` (`quarantine`, `dq_mart`, `dq_mart_coverage`, into `WAREHOUSE_DIR/transform.duckdb`) and reports each SQLMesh audit as an asset check. Once, before the first run: `make transform-setup` installs DuckDB's `postgres` extension (runs never download it). From the command line, `make transform-plan` applies model changes and `make transform-test` runs the SQLMesh unit tests, which are part of `make check` and need no services. Don't run a `make transform-*` target while a Dagster DQ run is in progress: both need `transform.duckdb`, and DuckDB allows one writer. `DQ_MART_MIN_CELL_ENTITIES` sets small-cell suppression; it is empty (off) by default, and must be set to at least 5 before `dq_mart` is published anywhere.

Legal events and outcome labels (Phase 4) run as one job: `uv run dagster job execute -m dagster_defs.definitions -j legal_to_labels` (needs `make dev-up` and network). The job runs, in order:
- **`legal` group:**
  - `krs_extracts` fetches every entity's full KRS extract, redacts it and stores it once per content change;
  - `msig_notices` searches MSiG by KRS and stores each new notice as a person-free record;
  - `legal_events` rebuilds the Parquet dataset.
- **`labels` group:** it rebuilds the SQLMesh label models (`legal_events_canonical`, `outcome_label_grid`, `outcome_labels`, exclusions, event coverage), reports their audits as asset checks, then `outcome_labels` freezes the set under `WAREHOUSE_DIR/outcome_labels/label_set_hash=<hash>/` and records it in Postgres `label_sets`.

`seed_acceptance` on `legal_events` checks every seed entity against its status hint. A model trains on a `label_set_hash`, never on "the latest labels". Label parameters live in `config/labels/` (`LABEL_VERSION` picks one, default `outcome_labels_v2`); the procedure taxonomy in `config/statutory/procedure_taxonomy.yaml`; MSiG notice typing in `config/mappings/msig_notice_kinds.yaml`. Editing the MSiG vocabulary (`msig_vocabulary.yaml`) re-fetches every notice on the next run, because the text is not kept.

The feature store (Phase 5) runs as one job, offline: `uv run dagster job execute -m dagster_defs.definitions -j features` (needs `make dev-up`, no network). It rebuilds `financial_statements_canonical`, `restatement_events` and `legal_events` from stored bytes, then `feature_store` under `WAREHOUSE_DIR/feature_store/`, one file per `as_of_year`. Two asset checks run on it: `leakage` (AGENT_SPEC §9.1 plus the per-family recomputation; blocking) and `feature_coverage` (non-null shares by family and statement form; report only). `FEATURE_SET_VERSION` picks `config/features/<version>.yaml` (default `feature_set_v4`: v3 plus the statement's going-concern flags, employment and the notes' signals, plan 0013; v3 is v1 plus the classical models' ratios and the 2025 calculation-variant revenue line, plan 0012). The notes' features read `text_coverage` as the `text` job last stored it: run that job first when the notes changed; a model trains on a `feature_set_version` and `feature_set_hash`, both on every row. On `/mnt/c`, close any Windows program (e.g. File Explorer) showing a file inside a dataset directory before a run: it blocks the directory swap, and the run fails with `PermissionError` while leaving the old dataset in place.

The backtest (Phase 6) runs by hand, never on a schedule: `uv run dagster job execute -m dagster_defs.definitions -j backtest`, from a clean working tree (it refuses uncommitted or untracked changes, since the code commit is one of the four identifiers every MLflow run carries). It reads the stored `feature_store` and the frozen label set pinned in `config/models/<BACKTEST_VERSION>.yaml` (default `backtest_v2`); it rebuilds neither, so run the `features` job first when features changed. It writes the report to `WAREHOUSE_DIR/reports/backtest/<version>.md` (the same bytes for the same inputs and commit) and one MLflow run per model, horizon and run to `MLFLOW_TRACKING_URI` (default a SQLite store under `.data/mlflow/`; browse it with `uv run mlflow ui --backend-store-uri sqlite:///.data/mlflow/mlflow.db`). Every number in it is seed output, not evidence.

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

Recordings are gitignored (`*.har`, `.cache/`). They contain session data and the documents *as filed*, including signatories' names and PESEL numbers, so delete them once imported. The import stores only redacted copies: no signatures, no PDF metadata, and every file name replaced by a token of its document's id (ADR 0009 and its second addendum). Only the company must already be in `entity_master` (A2); other KRS numbers are skipped with a warning. `RDF_MANUAL_INBOX` moves the inbox elsewhere.

**Auditor reports downloaded without a recording** (plan 0013, decision 6 as amended 2026-10-02) go in `.cache/rdf_auditor_reports/` (`RDF_REPORT_INBOX`): each ZIP exactly as **Pobierz dokumenty** delivered it, as `<krs>/<any folder>/<krs>_<period end>.zip`, and one `filing_dates.csv` beside the folders (`doc_name;krs;filing_date;period;document_id`, where `filing_date` is the detail's **Data dodania**, never **Data sporządzenia dokumentu**, and `unknown` is allowed). Import: `uv run dagster asset materialize -m dagster_defs.definitions --select rdf_auditor_report_import`. Each ZIP is redacted like a captured download and stored on its auditor-report row; the list is stored as received, and each date points back to it. The run's metadata lists files and dates with no single matching row (`unmatched`), ZIPs that are not one PDF (`not_a_report`) and stored reports still without a date (`stored_undated`). Add rows to the list and import again: only what is still missing is written, and a detail captured later in a HAR replaces a listed date. The ZIPs as downloaded name people, so keep them out of the repository like the recordings.

The text signals (Phase 7) run as one job, offline until the owner confirms the provider's terms: `uv run dagster job execute -m dagster_defs.definitions -j text` (needs `make dev-up` and `make models`). It reads the notes embedded in every stored statement, and every stored auditor report dated by its detail or the owner's list (§ above), masked, into `WAREHOUSE_DIR/text_signals/` (one row per kept extraction, with masked evidence) and `WAREHOUSE_DIR/text_coverage/` (per statement and signal: what was read, and a status `read`, `partial`, `not_run` or `no_text`), and quarantines unreadable attachments (G1) and discarded extractions (G2). A blocking check, `evidence_masked`, re-runs the masker over every stored evidence span. With `EXTRACTION_API_CONFIRMED=true` it sends the model signals through the Batches API; stored responses are replayed, never re-requested.

## Labelling the golden set

Text extractors are scored against pages the owner has labelled (plan 0013 step E, `evals/text_signals/labelling_guide.md`). There is one queue per golden sample: the statements' notes (`golden_sample_v1`) and the auditor reports (`golden_sample_v2`: every page of 20 whole reports), picked at the top of the notebook; the export writes both. The queues are local and never committed: until a page has been read, a name the masker missed can still be in it.

```bash
make label-queue    # after make dev-up and make models: every sample's masked pages to label, under LABELLING_DIR
uv run marimo edit notebooks/labelling/golden_set.py   # mask what the masker missed, label every signal
make label-export   # every queue's labelled pages to evals/text_signals/; refused if the masker would still change one, or a page already there would be dropped
```

The pre-commit hook re-runs the masker over every eval file staged under `evals/`, so it needs `make models` too.

Once pages are labelled, the extractors are scored on them:

```bash
make eval           # results to evals/text_signals/results/; model signals only after EXTRACTION_API_CONFIRMED=true
make eval-accept SIGNAL=litigation BY=owner   # after reading a result's scores: the baseline the gate holds
```

`make check` then fails when the prompt, model, rules or prefilter in use has no current result, or scores below the last accepted one on the same golden file (`config/extraction/eval_gate_v1.yaml`). A signal with no result yet is not gated.

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
