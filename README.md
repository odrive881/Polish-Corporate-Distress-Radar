# Polish Corporate Distress Radar

A batch data platform that estimates the probability a Polish company enters bankruptcy, restructuring, or liquidation within 12 and 24 months, built from statutory financial filings, registry history, and insolvency registers.

**Status (2026-10-16):** every stage from acquisition to presentation is built and has run on real data; the limit now is events, not code.

- **Universe:** the 17-entity seed and list v1 (ADR 0014, about 600 construction companies, purposive): 609 entities in `entity_master`, 402 with canonical facts from 2,220 statement files (list v1 imported three times, the last on 2026-10-16; plan 0014). Nothing from list v2 is imported yet.
- **Parsing:** 19 structure versions mapped; the gaps list v1 exposed are closed (plan 0017, complete 2026-10-16). Of 47 statements filed as PDF, 18 are recoverable from no later filing; the PDF tier stays deferred unless the owner decides otherwise (plan 0006).
- **Labels, features, backtest:** label set `fd867aa8…` (`outcome_labels_v2`), `feature_set_v6`, and `backtest_v8`: 370 modelled entities, 20 ever in distress, 22 events per horizon. Nothing is promoted (the champion rule needs 50 paired events and has 10), so the regression serves (version 5 per horizon, `incumbent`). It cannot score micro filers, whose balance sheet has no short-term liabilities line.
- **Text signals:** nine signals accepted on a 189-page golden set; two still have no labelled positive (plan 0013).
- **Phase 9** (plan 0016, every buildable item done): scoring (242 scored, 113 inputs incomplete, 254 not scored with a reason, per horizon), the local API (`make api`) and explorer (`make app`), the `publish` job and the public site built locally (`make site`, nothing deployed), and the methodology report (`make report`), written but not rendered.

**Pick up here.** Waiting on the owner: install the Quarto CLI in WSL and run `make report`; decide on deploying the site and the report (plan 0016); re-search the 72 unsearched list v1 companies, make the download script write every tab of an expanded row, and run it on list v2, recording list v2's frame so it can be weighted (plan 0014, ADR 0014 addendum); the importer's two open questions and label version 3 for unmapped legal records (plan 0014); a golden sample from list v2 (plan 0013); whether the PDF tier stays deferred (plan 0006); whether to move the default `BACKTEST_VERSION` (still `backtest_v5`; every run since sets it); and the history rewrite for personal data in pushed commits. Next to build: a plan for Phase 10 (schedules, alerts, drift monitoring, Sentry). After each new delivery, the round in plan 0014 § "Where this stands", then a new `backtest_v<n>`, `scoring`, `publish`, `make site` and `make report`.

**Status:** Phase 8 (gradient boosting, survival, explanations) complete (`docs/plans/0015-phase-8-gradient-boosting-survival-explanations.md`, 2026-10-12): steps A–C (2026-10-10): labels, text signals and features rebuilt over the seed and list v1, `backtest_v5` committed before any run, with its modelling population (186 entities whose RDF acquisition is complete), and LightGBM as generation 3; steps D–G (2026-10-10 to 10-12): discrete-time survival, SHAP, the champion rule, and MLflow tracking with a registered model version per horizon; `BACKTEST_VERSION` is `backtest_v5`; every challenger is short of the champion rule's 50-event floor, so nothing is promoted. `feature_store` now refuses `restatement_events` built from older statements (found 2026-10-12). The importer of the scripted RDF downloads (`docs/plans/0014-a3-scripted-rdf-download-import.md`) imported list v1 three times (2026-10-09, 2026-10-13 and 2026-10-16; current counts above); Phase 7 (text signals, `docs/plans/0013-phase-7-text-signals.md`) stays open on golden-set labels. What waits on the owner is listed at the top of each plan. Phase 7 so far: every statement's going-concern disclosure, and from FY2025 average employment and the audit flag, are read into `statement_disclosures`; the notes' text layer and the seed's auditor reports (downloaded by hand, stored redacted and dated from the owner's list) are read page by page, masked of person names (`make models` first) and prefiltered by lemma; rules read the auditor's opinion (by the sentence stating it, in the KSB wording) and the audit firm's change (the firm itself is never stored), and Claude reads the free-text signals, with every response stored and replayed, under `extractor_v6` (the default; the owner confirmed the provider's terms on 2026-10-06); the `text` job has run the model over the seed and list v1 (2026-10-09) into `text_signals`, `text_coverage` and `auditor_reports`; the golden set then held 148 labelled pages (189 since 2026-10-15; § Labelling the golden set), on which all nine signals' scores are accepted by the owner and held by the gate in `make check`; `feature_set_v6` (63 features, the default, with `backtest_v4`) carries the statement's going-concern flags and employment, the notes' going-concern and loss-coverage signals, and the auditor reports' opinion, auditor change, emphasis of matter and age.

Phase 6 complete (`docs/plans/0012-phase-6-baselines-out-of-time-backtest.md`, 2026-09-27): a purged out-of-time backtest of Altman's Z'', the Poznań model and a regularised logistic regression, a generated Markdown report with the seed caveat on every table, MLflow runs carrying the four identifiers, and the hand-run `backtest` job. Every result on the 17-entity seed is machinery, not evidence: 11 events, and once each fold trains only on labels already public by its test year (corrected 2026-09-29), no cell of 84 reaches the 3-event floor. Scale is the critical path. ADR 0013 (accepted 2026-10-06) settles how RDF documents are fetched at scale: a Power Automate Desktop script through the public UI, at most 3 documents a minute; the script runs on a list of about 600 construction companies built with Rejestr.io's filters (ADR 0014; purposive, its composition still to be measured), and its importer is built (plan 0014, `rdf_script_import`, 2026-10-08), waiting on the script's new listing for its acceptance test on the seed.

Phase 5 (`docs/plans/0010-phase-5-point-in-time-feature-store.md`, 2026-09-26): `feature_store` holds 49 features (`feature_set_v3`; v1 had 44) for every month-end of the label grid (2,929 rows for the 17-entity seed), each with the date it became knowable: financial ratios and trends, construction metrics, KSH tripwires, filing behaviour, registry dynamics and legal history. It is assembled in Python with DuckDB's `ASOF JOIN` (ADR 0012), and the blocking leakage test (`tests/features/test_leakage.py`) recomputes every family from only what was public on each date. Text signals, macro context and size class were deferred (plan 0010); Phase 7's `feature_set_v4` (56 features) adds the first text and disclosure features.

Phase 4 (`docs/plans/0008-phase-4-legal-events-outcome-labels.md`, close-out 2026-09-23): The seed's legal timeline is built from the **full KRS extract** (open KRS API; both eras, entry-dated) and **MSiG notices** (JSON search, back to 2001). Both are stored only in redacted or person-free form (ADR 0009 addendum). They gave 89 `legal_events` rows in 70 deduplicated events (registrations included; 191 rows since Phase 5 added board, office and capital changes), and **all 9 seed entities with a distress hint are found independently**, with dates and source documents; the other 8 have none. Outcome labels are built in SQLMesh: 12 and 24 months, censored at each entity's cutoff, regime and source-era flags. They are frozen by content hash. The seed's set is `066d18bbd4cd…` (label version 2, plan 0009: a 12-month lag allowance for `alive`, 24-month petition expiry), 4,694 rows, reproduced exactly on rebuild; the current one, over the seed and list v1, is `19bf5d2f1d66…` (label version 2, 111,088 rows, 608 entities, plan 0015 step A). Earlier sets stay frozen: `a1ac9fb07f79…` (v1) and `a5da757f8341…` (v1, before the Phase 4 review fixes). KRZ is not built: it sits behind a WAF (ADR 0011). Phase 3 (the canonical financial model, 129 statements across all 17 entities, `quarantine` and `dq_mart` in SQLMesh) and Phase 1 (acquisition; RDF captured by hand as HAR files, ADR 0007) are complete before it.

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

Requires [uv](https://docs.astral.sh/uv/), and a C compiler with Python's headers (`sudo apt install build-essential python3.14-dev` under WSL): `scikit-survival` depends on `ecos`, which has no prebuilt wheel for Python 3.14 and is compiled on install. `.venv` is created and kept in sync with `uv.lock` by `make install` — don't create it by hand or `pip install` into it directly.

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

The public site (`make site`) also needs Node.js 20 or later installed inside WSL (for example from NodeSource or `nvm`): a Windows `npm` visible on the `PATH` is refused. The first build writes `package-lock.json` in `site/`, which is then committed. The methodology report (`make report`) needs the Quarto CLI inside WSL, likewise; a Windows `quarto` is refused.

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

The feature store (Phase 5) runs as one job, offline: `uv run dagster job execute -m dagster_defs.definitions -j features` (needs `make dev-up`, no network). It rebuilds `financial_statements_canonical`, `restatement_events` and `legal_events` from stored bytes, then `feature_store` under `WAREHOUSE_DIR/feature_store/`, one file per `as_of_year`. Two asset checks run on it: `leakage` (AGENT_SPEC §9.1 plus the per-family recomputation; blocking) and `feature_coverage` (non-null shares by family and statement form; report only). `FEATURE_SET_VERSION` picks `config/features/<version>.yaml` (default `feature_set_v6`: v5 plus the auditor report's age; v5 is v4 plus the auditor reports' opinion, auditor change and emphasis of matter; v4 is v3 plus the statement's going-concern flags, employment and the notes' signals, plan 0013; v3 is v1 plus the classical models' ratios and the 2025 calculation-variant revenue line, plan 0012). The notes' features read `text_coverage` as the `text` job last stored it: run that job first when the notes changed; a model trains on a `feature_set_version` and `feature_set_hash`, both on every row. On `/mnt/c`, close any Windows program (e.g. File Explorer) showing a file inside a dataset directory before a run: it blocks the directory swap, and the run fails with `PermissionError` while leaving the old dataset in place.

The backtest (Phases 6 and 8) runs by hand, never on a schedule: `uv run dagster job execute -m dagster_defs.definitions -j backtest`, from a clean working tree (it refuses uncommitted or untracked changes, since the code commit is one of the four identifiers every MLflow run carries). It reads the stored `feature_store` and the frozen label set pinned in `config/models/<BACKTEST_VERSION>.yaml` (default `backtest_v5`); it rebuilds neither, so run the `features` job first when features changed. It writes the report to `WAREHOUSE_DIR/reports/backtest/<version>.md` (the same bytes for the same inputs and commit); one MLflow run per model, horizon and run to `MLFLOW_TRACKING_URI` (default a SQLite store under `.data/mlflow/`; browse it with `uv run mlflow ui --backend-store-uri sqlite:///.data/mlflow/mlflow.db`), with a child run per fold Optuna tuned; and a registered model version per horizon (`distress_radar_12m`, `distress_radar_24m`): the incumbent with the alias `incumbent`, a promoted challenger with `champion`. Per-row SHAP values, which explain a named company's score, go only to `WAREHOUSE_DIR/model_explanations/`, and every scored test row (each month-end's out-of-time prediction, the explorer's future score history) only to `WAREHOUSE_DIR/model_predictions/`. Every number in it is seed output, not evidence.

Scoring (Phase 9, plan 0016) also runs by hand, from a clean working tree: `uv run dagster job execute -m dagster_defs.definitions -j scoring`. Per horizon it serves the registered version under `champion`, else `incumbent`, as registered (never refitted), and scores the latest month-end in `feature_store`: only the entities the backtest's population rule includes and the label grid still has at that month-end (not already in a proceeding or deregistered); a row missing an input the model needs is kept as "inputs incomplete", never imputed. Rows go to Postgres `scores_history` with the served version, its training eve and the identifiers; a rerun adds nothing. Every row names a company, so none of it leaves the machine.

The scores API (plan 0016 step D) reads them: `make api` serves `127.0.0.1:8000` only, read-only over `scores_history`, `scoring_exclusions` and `watchlists` (routes in the plan; FastAPI's docs at `/docs`). It loads the served versions from the registry at startup and refuses to start when the latest scores came from another version, so run the `scoring` job after any promotion. Every response carries the served versions' identifiers and the disclaimer that a score is a statistical estimate, not a statement of fact. Set `BACKTEST_VERSION` as for the scoring run.

The internal explorer (plan 0016 step E): `make app` runs Streamlit on `127.0.0.1` only: the served models and the latest month-end, a company's scores, out-of-time history and source-linked statements, peers by PKD division as distributions, and watchlists. It names companies, so it stays on the machine; every page says a score is a statistical estimate, not a statement of fact.

The public site (plan 0016 step F) reads one database, built by hand from a clean tree: `DQ_MART_MIN_CELL_ENTITIES=10 BACKTEST_VERSION=backtest_v8 uv run dagster job execute -m dagster_defs.definitions -j publish` (last run 2026-10-16, on `backtest_v8`). It writes `publish.duckdb` in `site/sources/publish/` (never committed) from fixed aggregate tables only: `dq_mart` and its coverage, pipeline coverage per list, and the backtest's pooled metrics and reliability tables as its MLflow runs logged them, every cell covering fewer companies than the threshold suppressed, plus the identifiers each figure is regenerable from. It refuses to run with the threshold unset or below 5, and refuses a database holding an identifying column or any KRS, NIP or REGON. `make site` then builds the static site into `build/` under `site/`; nothing is deployed.

The methodology report (plan 0016 step G), `report/methodology.qmd`, states the rules and includes its figures from the same publish database the site reads, so the two cannot disagree and the report shows nothing the site does not: `make report` regenerates the figures (`python -m distress_radar.methodology`, which refuses served versions registered by another backtest than the one published) and renders the report to HTML. Nothing is deployed.

Re-running with unchanged inputs and unchanged mapping config reproduces the Parquet byte for byte (invariant 5). Editing anything under `config/mappings/` changes the spec hash by design, which gives every affected file a new `parsed_documents` row and a new `ingestion_run_id` — the figures themselves must not move. `uv run python notebooks/exploration/canonical_value_hash.py` prints the column-wise value hash per structure version (run ids excluded) so a mapping change can be checked against the values it should not have touched.

## Manual RDF capture

RDF shows an automated browser an hCaptcha challenge (ADR 0007, live result 2026-09-16), so the pipeline does not fetch from RDF itself. At scale, documents come from the owner's Power Automate Desktop script (ADR 0013, accepted 2026-10-06: at most 3 documents a minute, a challenge stops it; imported by `rdf_script_import`, § "Scripted downloads" below). For a single entity, or a document the script misses, financial statements are fetched by hand in an ordinary browser. The browser records the session, and the `rdf_manual_import` asset reads everything it needs from that recording: the filing list, each document's details (including the submission date), and the downloaded files. Nothing needs to be typed in.

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

**Scripted downloads** (ADR 0013, plan 0014). The owner's Power Automate Desktop script writes into `.cache/rdf_script_inbox/` (`RDF_SCRIPT_INBOX`), as specified in plan 0014 (decision 2 and § "Progress"):

- `documents.csv`: one row per tab of an expanded row (the document and each correction), for every statement and auditor-report row of the entity, downloaded or not. Rows are appended, never rewritten. Its 16 columns: `krs`, `document_id`, `row_document_id`, `type_name`, `period_start`, `period_end`, `prepared_date`, `is_ifrs`, `is_correction`, `submission_date`, `status`, `deleted_on`, `file`, `captured_at`, `tab` and `language`.
  - `submission_date` is the tab's **Data dodania**, never **Data sporządzenia dokumentu**: it becomes the document's `known_from`.
  - `file` names the row's ZIP, and is empty when the row was not downloaded.
- `entities.csv`: one row per search, with the columns `krs`, `searched_at`, `found`, `list_rows` and `complete`.
  - `complete` is `Nie` on any early stop or doubt.
  - A number RDF does not find is written `found` = `Nie`, `complete` = `Tak`.
- `outages.csv` (optional, `from;to`): spans when the script ran without a connection. A search inside one that found nothing is not recorded, since it says nothing about RDF. A run of 10 or more not-found searches in a row that no span covers is held the same way and listed as a suspected outage (`suspected_outages` in the run's log), until its span is added.
- `<krs>/<row_document_id>.zip`: each expanded row's download, exactly as **Pobierz dokumenty** delivered it.

All three files use the same format:

- `;`-separated UTF-8 (a BOM is allowed);
- flags as `Tak` / `Nie`, as RDF shows them;
- dates as `YYYY-MM-DD` or `DD.MM.YYYY`, and a time with no offset is read as Warsaw time.

A2 must have run first, since only entities in `entity_master` are imported. A list of KRS numbers kept outside the repository (ADR 0014) is loaded as candidates with `universe_candidates`' `krs_list` and `krs_list_source` config (e.g. `rejestr_io_v1`); only its KRS column is read.

Import: `uv run dagster asset materialize -m dagster_defs.definitions --select rdf_script_import`. The import:

- stores both listings as received;
- matches each row to `filing_index` by RDF's document id, then by the single row it can only be, and otherwise adds a row keyed `id-<document id>`;
- points each listed date back to the stored listing;
- gives each row its type code from the type name and the period (`rdf_document_types.yaml`);
- judges a search complete when the listing holds as many expanded rows as the page listed, whatever its `complete` flag says;
- reads a missing correction flag (RDF shows none on older filings) from the ids;
- skips entities with fewer filed years than the segment's `min_history_years` (a later listing with enough years imports them);
- holds back the ZIP of an expanded row whose tabs the listing does not all have, until a listing does.

Each ZIP is redacted like a captured download. When it holds a correction group, its files are paired with their tabs by the prepared date in each statement's header. A group that cannot be paired is stored unpaired, and a HAR capture completes it.

**A new delivery, end to end** (as run on 2026-10-13 and 2026-10-16; where the data stands and what is open: plan 0014, § "Where this stands"). All from WSL, with `make dev-up` running:

1. Check the folder has `outages.csv` (`from;to`, Warsaw time). The importer holds back any run of 10 or more not-found searches no span covers and lists it as a suspected outage; confirm each with the owner and add its span. A shorter run between found ones may still be an outage, and without a span it is recorded as "not on RDF".
2. Import, reading the owner's folder in place: `uv run dagster asset materialize -m dagster_defs.definitions --select rdf_script_import --config-json '{"ops":{"rdf_script_import":{"config":{"inbox":"/mnt/e/<folder>/financial_statements"}}}}'`. Refused rows or ZIPs fail the run after everything else is imported, which skips the `personal_data` check: then run `uv run python -m distress_radar.acquisition.redaction_migration` (report only; it must find 0 objects).
3. Parse: `--select financial_statements_canonical,restatement_events`.
4. Legal sources (network; about two hours for ~600 entities): `--select krs_extracts,msig_notices`.
5. Legal events and a new frozen label set: `--select legal_events,legal_events_canonical,outcome_label_grid,outcome_label_exclusions,outcome_labels_mart,legal_event_coverage,outcome_labels`.
6. Features (offline): `uv run dagster job execute -m dagster_defs.definitions -j features`; then DQ: `--select parsed_documents_current,quarantine,dq_mart,dq_mart_coverage`.
7. Backtest, only as a new version: a new label set or population changes the pins, so write `config/models/backtest_v<n>.yaml` from the last one with `label_set_hash` and `population.entities_hash` changed, commit it, then `BACKTEST_VERSION=backtest_v<n> uv run dagster job execute -m dagster_defs.definitions -j backtest`.
8. Record the round's counts in plan 0014, § "Progress".

The run's metadata counts what could not be used: `not_in_entity_master`, `unknown_types`, `rows_without_zip`, `zips_without_row`, `incomplete_entities`, `too_few_years`, `incomplete_groups`, `outage_searches` and `disagreements` (one document id read two ways). Each item is listed in the run's log. A row or file that cannot be read fails the asset, after everything else is imported.

Importing the same inbox again adds nothing, so append the script's newer rows and import again: a later deletion is recorded on its row, and a new correction becomes a row of its own. A HAR captured later completes the script's row rather than adding a second one. For an entity with scripted rows, `rdf_manual_import` adds only the documents the capture expanded, and lists the rest as `listed_not_added`. Like the recordings, the ZIPs name people, so keep them out of the repository.

The text signals (Phase 7) run as one job: `uv run dagster job execute -m dagster_defs.definitions -j text` (needs `make dev-up` and `make models`). It reads the notes embedded in every stored statement, and every stored auditor report dated by its detail or the owner's list (§ above), masked, into `WAREHOUSE_DIR/text_signals/` (one row per kept extraction, with masked evidence) and `WAREHOUSE_DIR/text_coverage/` (per statement and signal: what was read, and a status `read`, `partial`, `not_run` or `no_text`), and quarantines unreadable attachments (G1) and discarded extractions (G2). A blocking check, `evidence_masked`, re-runs the masker over every stored evidence span. It calls the model only with `EXTRACTION_API_CONFIRMED=true` and `ANTHROPIC_API_KEY` set (plus `ANTHROPIC_WORKSPACE_ID` for an identity-linked `sk-ant-usr…` key), confirmed by the owner on 2026-10-06; it then sends the requests it has no stored response for through the Batches API, and replays the rest, never re-requesting. Without them it runs offline, and the model signals are `not_run`.

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
