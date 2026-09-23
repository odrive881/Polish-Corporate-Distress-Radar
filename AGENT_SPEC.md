# Polish Corporate Distress Radar — Build Specification

Specification for coding agents. All architectural decisions are already made. Do not re-evaluate tool choices or propose alternatives — implement what is specified here. If a decision appears wrong, flag it in a comment and continue; do not silently substitute.

---

## 1. What is being built

A batch data platform that estimates the probability a Polish company enters bankruptcy, restructuring, or liquidation within the next 12 and 24 months.

It ingests statutory financial statements (XML and PDF) filed by KRS-registered companies, normalises them into one canonical financial model, enriches them with registry history and signals extracted from Polish-language auditor reports, labels outcomes from insolvency registers, and trains distress models that are evaluated strictly out-of-time.

**v1 universe:** construction sector (PKD section F), legal form `sp. z o.o.`, small and medium entities under the Polish accounting law size thresholds, with at least 3 consecutive filed financial years. Expect a few thousand entities.

---

## 2. Non-negotiable invariants

Violating any of these is a build failure, not a code-review comment.

1. **Point-in-time correctness.** A feature computed for `as_of_date` may only use data whose `known_from <= as_of_date`. Enforced by a blocking test (§9.1). No exceptions, including for "obviously static" reference data.
2. **Raw immutability.** Downloaded bytes are written to the object store unmodified, content-addressed, and never overwritten or deleted. All parsing reads from the object store, never from the network. The one exception is invariant 6's redaction, applied before hashing: filed documents have signer data removed, KRS extracts have natural persons removed, and MSiG notices are stored only as person-free records (structured fields, dates, signatures, vocabulary terms), never their text (ADR 0009 and its addendum). Each stored object records its redaction version and the hash of what was received (`raw_redactions`). The ADR 0009 migration is the only code that deletes raw objects.
3. **Full lineage.** Every canonical fact carries the source document hash, the source element path, and the ingestion run id. A fact that cannot name its source is a bug.
4. **No silent data loss.** Records failing validation go to a quarantine table with a reason code. Never drop, never impute to make a check pass.
5. **Idempotence.** Every stage produces identical output from identical input. Re-running a stage is always safe.
6. **Legal entities only.** Natural persons are never ingested or stored, including from insolvency registers which contain consumer bankruptcies. Filter at the acquisition boundary. This includes the signatures on filed documents (names, certificates, PESEL numbers), which `acquisition/redaction.py` strips before storing (ADR 0009), and the people named in KRS extracts (board members, shareholders, liquidators, trustees, notaries), which it redacts the same way (ADR 0009 addendum).
7. **Versioned statutory config.** Size thresholds, KSH tripwire ratios, and procedure taxonomies live in `config/statutory/` as dated YAML. Never hardcode them in Python or SQL.

---

## 3. Locked stack

| Concern | Tool | Notes |
|---|---|---|
| Orchestration | **Dagster** | Software-defined assets; asset checks for DQ; partitions by `fiscal_year` and `as_of_month` |
| Packaging | **uv** | `pyproject.toml` + `uv.lock`, committed |
| Lint / format | **ruff** | Also handles import sorting |
| Types | **pyright** | Strict mode on `src/` |
| Tests | **pytest** | |
| Config | **pydantic-settings** | Typed settings; `SOPS` + `age` for encrypted secrets |
| HTTP | **httpx** (async) | With `hishel` cache, `pyrate-limiter`, `tenacity` |
| HTML parsing | **selectolax** | |
| Browser fallback | **Playwright** | Only for JS-gated pages, routed on failure |
| SOAP | **zeep** | GUS BIR1 only |
| Object store | **MinIO** | S3 API, via `boto3` |
| Operational DB | **Postgres** | Manifest, scores, alerts, watchlists |
| XML | **lxml** + **xmlschema** | Parse with lxml, validate against official XSD |
| DataFrames | **Polars** | Project code uses Polars; pandas is allowed as a transitive dependency (SQLMesh pulls it) |
| PDF text | **PyMuPDF** → **Docling** → vision LLM | Tiered, routed by detection |
| Notebooks | **marimo** | `.py` format only, no `.ipynb` |
| Table contracts | **Pandera** | At every Python stage boundary |
| Transformation | **SQLMesh** | Incremental by time range |
| Analytical engine | **DuckDB** | |
| Storage format | **Parquet** | Partitioned, explicit validity columns |
| Polish NLP | **spaCy** `pl_core_news_lg` | Lemmatisation for keyword prefilter |
| LLM extraction | **Pydantic** schemas + constrained output | No LangChain |
| Classical models | **statsmodels**, **scikit-learn** | |
| GBM / survival | **LightGBM**, **scikit-survival** | |
| Tuning | **Optuna** | |
| Explainability | **SHAP** | |
| Experiment tracking | **MLflow** | Self-hosted, includes model registry |
| API | **FastAPI** + Pydantic v2 | |
| Internal app | **Streamlit** | |
| Public site | **Evidence** | Static build against DuckDB |
| Report | **Quarto** | |
| Drift monitoring | **Evidently** | |
| Errors | **Sentry** | |
| Containers | **Docker Compose** | |
| CI | **GitHub Actions** | |

### Do not introduce

Kafka or any streaming layer. Spark or Dask. Kubernetes. Any cloud warehouse. A separate vector database (use `pgvector` if vector search is needed). A model-serving framework (load in-process from MLflow). Feast. Great Expectations. LangChain.

Data volume is ~10⁷ rows of financial line items, a few GB of Parquet. It fits in RAM. Size all solutions accordingly.

---

## 4. Domain rules

These are the rules an agent cannot infer from the code. Implement them exactly.

### 4.1 Reporting variants

Polish statements come in variants that are **not** losslessly interchangeable:

- **Income statement:** comparative variant (costs by nature) or calculation variant (costs by function). Map both into the canonical model. Where no direct mapping exists, populate only the common-denominator items and leave the rest null. Never derive one variant's items from the other.
- **Cash flow:** direct or indirect method.
- **Form:** micro, small (simplified), or full. A single entity may switch variants between years. Detect per document, never per entity.

### 4.2 Units

Statements declare amounts in złoty or thousands of złoty. Normalise everything to złoty at parse time. A missing unit conversion silently corrupts the warehouse — assert the declared unit is present and recognised, and quarantine the document if not.

In the Ministry of Finance XML structures the unit is not a separate field: it is part of the structure itself (`JednostkaInnaWZlotych` vs `…WTysiacach`, with the `KodSprawozdania` header saying which). Each structure spec maps that header value to a multiplier (plan 0004).

### 4.3 Accounting identity checks

Run on every parsed statement. Tolerance: absolute difference ≤ 1 currency unit (rounding), configurable.

| Check | Assertion |
|---|---|
| `balance_sheet_balances` | total assets == total equity and liabilities |
| `subtotals_consistent` | each subtotal == sum of its components, recursively |
| `profit_ties` | income statement net result == balance sheet net result line |
| `cashflow_ties` | net cash movement == closing cash − opening cash |
| `prior_year_consistency` | prior-year column matches the previously filed current-year column; mismatch emits a row into `restatement_events`, not a failure |

A statement a form does not declare is **absent, not empty**: the small and micro structures have no cash-flow and no equity-changes statement at all (ADR 0005 second addendum §1), so those checks are `not_applicable` for such a filing, never failed and never passed, and no zero rows are written for the missing lines (invariant 4). `cashflow_ties` records one `not_applicable` row per column rather than none, so the exemption is counted, not hidden (plan 0007). The same applies to a tie whose other side is missing. Grading (plan 0004): each failure is judged `material` or `immaterial`, and a statement file is `quarantined` on any material failure: a tie failing in the current-year column, or a current-year subtotal off by more than 1% of its total assets. Every other failure is immaterial and grades it `warn`: immaterial subtotal gaps, and anything confined to the prior-year columns, whose authority is the earlier filing. A cash difference exactly explained by the reported exchange-rate effect on cash passes `cashflow_ties`. Sums include the filer's own extra lines (`PozycjaUszczegolawiajaca_N`), except under "w tym" (of which) lines.

### 4.4 Size classification

Computed per fiscal year from the filed statement, never taken from a registry — and never inferred from which form the entity filed: choosing the micro form is the filer asserting a size class, and filers get it wrong (plan 0005 decision 4). Inputs: balance sheet total, net revenue, average employment. The accounting law applies thresholds over a multi-year window, so classification requires the prior year too. Thresholds come from `config/statutory/size_thresholds.yaml`, keyed by effective date range.

### 4.5 Legal tripwires

From the Commercial Companies Code (KSH), computed as boolean features:

- **Art. 233 (sp. z o.o.):** triggered when accumulated losses exceed supplementary capital + reserve capital + half of share capital. Obliges shareholders to vote on the company's continued existence.
- **Art. 397 (S.A.):** same structure, threshold is one third of share capital. Implement for future scope even though v1 is sp. z o.o. only.
- **Negative equity:** total equity < 0.

Ratios live in `config/statutory/ksh_tripwires.yaml`.

### 4.6 Outcome taxonomy

| Class | Includes |
|---|---|
| `bankruptcy` | petition filed, bankruptcy declared, petition dismissed for insufficient assets |
| `restructuring` | arrangement approval, accelerated arrangement, arrangement proceedings, remedial proceedings, COVID-era simplified restructuring |
| `liquidation` | voluntary liquidation opened |
| `silent_exit` | deregistered with no earlier event that rules it out: a bankruptcy, restructuring or liquidation (the deregistration then takes that class), or a merger (the row is censored). Filings ceasing is a Phase 5 feature, not a second label rule (plan 0008 decision 5) |
| `alive` | none of the above within the horizon |

Rules:

- Labels are generated per `(entity, as_of_date, horizon)` for horizons of 12 and 24 months.
- Entities alive but whose horizon extends past the data cutoff are **censored**, not `alive`: `outcome_class` is null and `censored` true. Survival models must receive the censoring flag. The cutoff is per entity: the earliest of the latest complete fetches across the sources used (`config/labels/`).
- The window is `(as_of_date, as_of_date + horizon]`, ending on a month-end. An entity already in a proceeding at `as_of_date` (an event on `as_of_date` counts) or already deregistered is excluded, not labelled. An event with no decision date happened on or before its `known_from` (plan 0008). From label version 2 (plan 0009): a window ending on or after KRZ's launch is `alive` only if it ends `alive_lag_months` (12) before the cutoff, because the registry enters decisions late; and a petition-stage event keeps an entity excluded for at most `petition_expiry_months` (24). `LABEL_VERSION` chooses the version.
- Sources and the source break (ADR 0011): the **full KRS extract** records proceedings in both eras, per entity, each fact dated by its registry entry; **MSiG** (a JSON search API with notice text, from 2001) adds petition-stage orders, the COVID-era simplified restructuring and earlier publication dates; **KRZ** (from December 2021, behind a WAF, not built in Phase 4) would add post-2021 petitions and dismissals. Labels record the break in `source_era` (`pre_krz` \| `mixed` \| `krz`) and `trigger_event_type`. Deduplicate events describing the same proceeding across sources (`dedup_group_id`), keeping every source row.
- Regime flag: mark 2020–2021, when simplified restructuring caused an artificial filing spike. Models must be able to exclude or control for it.
- Every label set is frozen with a content hash. Models record which hash they trained on.

### 4.7 Bitemporality

Two independent time axes on every fact:

- `valid_from` / `valid_to` — the period the fact describes (fiscal period).
- `known_from` — when the fact became publicly available. **For financial statements this is the filing submission date in the repository, not the balance sheet date.** A 2023 statement may not appear until mid-2024, or never.

Entities that stop filing before collapsing create survivorship bias. Model missing filings explicitly as a feature; do not drop those entities.

---

## 5. Canonical data model

Long format. One row per fact. Parquet unless noted otherwise; `scores_history` and `alerts` live in Postgres because the API reads them.

These names are canonical. `PROJECT_OVERVIEW.md` refers to the same datasets and its stage crosswalk maps its numbered stages onto the letter-coded stages used here.

### `financial_statements_canonical`

| Column | Type | Notes |
|---|---|---|
| `krs` | str | Zero-padded, 10 chars |
| `nip`, `regon` | str | |
| `fiscal_year` | int | |
| `period_start`, `period_end` | date | |
| `line_item` | str | Canonical chart code, see `config/mappings/canonical_chart.yaml` |
| `value` | decimal(20,2) | Always złoty |
| `statement_type` | enum | `balance_sheet`, `income_statement`, `cash_flow`, `equity_changes` |
| `variant` | enum | `comparative`, `calculation`, `direct`, `indirect`, `n/a` |
| `column` | enum | `current_year`, `prior_year`, `prior_year_restated` (restated comparatives, `KwotaB1`) |
| `structure_version` | str | Detected XML structure version |
| `source_document_hash` | str | SHA-256, joins to `raw_documents` |
| `source_member` | str | Path from the stored download to the statement file, e.g. `zip:SF.xml` or `zip:SF.xades>ds:Object[2]>base64`. A download can hold a statement and its correction (plan 0004) |
| `document_ref` | str | RDF document id (`filing_index.document_ref`); tells a statement from its correction |
| `source_element_path` | str | XPath (namespace prefixes from the structure spec). For a filer's own extra lines, summed into one `….USER` fact, an XPath union of every contributing element |
| `known_from` | date | Filing submission date |
| `ingestion_run_id` | str | |
| `quality_grade` | enum | `pass`, `warn`, `quarantined` |

### `legal_events`

`krs`, `event_type`, `outcome_class`, `stage`, `ends`, `precludes_silent_exit`, `event_date` (nullable: no decision date), `known_from` (the source's publication: the KRS entry date or the MSiG publication date; the column this section once called `published_date`), `removed_on`, `source` (`KRZ` \| `MSiG` \| `KRS`), `case_signature`, `proceeding_id` (the linked case files' signature), `statute`, `dedup_group_id`, `source_document_hash`, `source_element_path`, `ingestion_run_id`, `event_year`. Built by `parsing/legal_events.py` (plan 0008 step F), contract `LEGAL_EVENTS`.

### `text_signals`

`krs`, `fiscal_year`, `signal_type`, `value`, `evidence_span`, `source_document_hash`, `page`, `extraction_method`, `confidence`, `known_from`.

`signal_type` enum: `going_concern_uncertainty`, `opinion_type`, `emphasis_of_matter`, `covenant_breach`, `key_customer_loss`, `litigation`, `post_balance_sheet_event`, `loss_coverage_resolution`, `continued_existence_vote`.

### `entity_size_class_history`

`krs`, `fiscal_year`, `size_class` (`micro` | `small` | `medium` | `large`), `balance_sheet_total`, `net_revenue`, `average_employment`, `threshold_config_version`, `known_from`.

Computed per §4.4. Never sourced from a registry.

### `restatement_events`

`krs`, `fiscal_year`, `period_start`, `period_end`, `line_item`, `restated_column` (`prior_year` \| `prior_year_restated`), `originally_reported_value`, `restated_value`, `original_document_hash`, `original_source_member`, `original_document_ref`, `restating_document_hash`, `restating_source_member`, `restating_document_ref`, `known_from`.

Emitted by the `prior_year_consistency` check (§4.3). A restatement is a finding, not a validation failure. The earlier filing is found by period adjacency (its period ends the day before the restating one starts), not by `fiscal_year - 1`, and must have been public no later than the restating one. Only files graded `pass` or `warn` take part.

### `identity_check_results`

`krs`, `fiscal_year`, `period_end`, `document_ref`, `source_document_hash`, `source_member`, `structure_version`, `known_from`, `ingestion_run_id`, `column`, `check`, `line_item`, `status` (`pass` \| `fail` \| `not_applicable`), `severity` (`material` \| `immaterial`, on `fail` rows only), `expected`, `actual`, `difference`.

One row per evaluated identity (§4.3) per statement file, amount column and line item, written with `financial_statements_canonical` from the same run (plan 0007 step A). `quality_grade` is the per-file roll-up of `severity`; this is the per-check detail behind it, and the source of `dq_mart`'s pass rates by check type. `not_applicable` is never counted as a pass. `expected`/`actual` are null where a side of the identity was not reported; `difference` is null exactly on `not_applicable` rows.

### `quarantine`

`stage`, `entity_key`, `krs`, `document_ref`, `source_document_hash`, `source_member`, `reason_codes` (list), `known_from`, `first_detected_at`.

The **current** quarantined set: SQLMesh model `quarantine.quarantine`, recomputed from scratch on every run (plan 0007 decision 4). Each stage's answer comes from the source that knows it now:
- E2: `quality_grade = 'quarantined'` on the canonical table, with the checks that failed materially as reasons;
- C1/C2: `parsed_documents` on the latest parsing run;
- A1–A3: the latest event per key in the log.

The log itself is the Postgres table **`quarantine_events`**: append-only, one row per first detection, never updated or deleted. A log row describing a file the current rules no longer quarantine is simply not selected. Never answer "is this quarantined now?" from the log (ADR 0006 addendum, ADR 0010).

### `dq_mart`

Two SQLMesh models (plan 0007 step E), both rebuilt in full on every run:
- **`marts.dq_mart`**: one row per `structure_version` × `filed_bodies` × `fiscal_year` × `check`. Columns `files`, `passed`, `failed_material`, `failed_immaterial`, `not_applicable`, `pass_rate` = passed / (passed + failed), `entities`, `suppressed`.
- **`marts.dq_mart_coverage`**: one row per `fiscal_year`. Columns `files_stored`, `parsed` and its split `graded_pass` / `graded_warn` / `graded_quarantined`, `not_yet_mapped`, `needs_pdf_tier`, `needs_pdf_tier_without_later_filing`, `quarantined_before_grading`, `entities`, `suppressed`.

Rules:
- A file counts once per check.
- `not_applicable` is never in a pass rate's denominator.
- No entity identifiers appear, only counts.
- A cell covering fewer entities than `DQ_MART_MIN_CELL_ENTITIES` publishes every measure as null, flagged `suppressed`, and is kept rather than dropped. The setting ships null (off), and must be set before anything is published (§10, phase 9).

### `outcome_labels`

`krs`, `as_of_date`, `horizon_months` (12 | 24), `outcome_class` (§4.6 enum; null exactly when censored), `censored` (bool), `event_date`, `event_known_from`, `trigger_event_type`, `proceeding_id`, `proceeding_id_note` (why a labelled event has none), `regime_flag`, `source_era` (`pre_krz` \| `mixed` \| `krz`), `cutoff_date`, `label_version`, `label_set_hash`. Built in SQLMesh (`marts.outcome_labels`), then frozen once per hash under `WAREHOUSE_DIR/outcome_labels/label_set_hash=<hash>/` and recorded in Postgres `label_sets` (plan 0008 step G).

### `scores_history`

`krs`, `as_of_date`, `horizon_months`, `probability`, `model_version`, `feature_set_version`, `label_version`, `data_snapshot_hash`, `top_drivers` (JSON, from SHAP), `scored_at`.

Lives in Postgres, not Parquet — it is read by the API.

### `alerts`

`alert_id`, `krs`, `as_of_date`, `alert_type` (`threshold_crossed` | `score_jump` | `tripwire_triggered` | `going_concern_flagged` | `filing_overdue`), `severity`, `triggering_score_id`, `created_at`, `acknowledged_at`.

Lives in Postgres.

### `feature_store`

`krs`, `as_of_date`, `feature_set_version`, plus feature columns. Every feature column has a companion `<name>__known_from` used by the leakage test.

---

## 6. Stage specifications

Each stage is a Dagster asset group. Implement in the order given in §10.

### A — Acquisition

Five adapters, one module each under `src/distress_radar/acquisition/`. All share a common base providing rate limiting, caching, retry, and raw-write-before-parse.

| Stage | Source | Protocol | Output |
|---|---|---|---|
| A1 | Registry aggregators | HTTPS | `universe_candidates` (krs, discovery_source, discovered_at) |
| A2 | GUS REGON BIR1 | SOAP (`zeep`) | `entity_master` — validated identifiers, PKD codes, legal form, status |
| A3 | Financial document repository | HTTPS, per-entity lookup | Raw documents → object store; `filing_index` |
| A4 | Full KRS extract (open KRS API) + MSiG notice search API; KRZ not built (WAF, ADR 0011) | HTTPS JSON (`krs_extract.py`, `msig_client.py`) | Redacted extracts and person-free notice records → object store; `legal_source_fetches`, `msig_notices` |
| A5 | NBP, GUS BDL | HTTPS JSON | Reference and macro series |

Requirements:

- Rate limiting is persistent across process restarts (token bucket in Postgres or on disk).
- HTTP cache is enabled in all environments. Re-running acquisition must not re-hit the source for unchanged documents. Where a source stamps every response (the KRS extract's `dataCzasOdpisu`) and no cache applies, unchanged content is recognised by a fingerprint and stored once (plan 0008 step C).
- A2 session token acquisition and refresh is handled inside the adapter; callers never see it.
- PKD codes are mapped across classification versions via `config/mappings/pkd_crosswalk.yaml`. The segment spec (`config/segments/<segment_name>.yaml`, e.g. `construction_sme_v1.yaml`) declares whether matching is on the predominant code only or any registered code.
- Source conflicts (e.g. differing PKD between registries) resolve by documented precedence and are logged to `entity_reconciliation_log`.

**Do not** implement bulk enumeration of any source. Acquisition is per-entity, seeded from `universe_candidates`.

### B — Raw persistence

- Object store layout: `raw/sha256/<hash[:2]>/<hash>`. Metadata (original filename, content type, source URL, fetch timestamp, HTTP headers) in a sidecar key.
- `raw_documents` manifest in **Postgres** (not DuckDB — acquisition writes concurrently and DuckDB is single-writer).
- Writing an already-present hash is a no-op.

### C — Structural decoding

**C1 — Version detection and validation.** First unwrap the stored download (a ZIP, possibly holding signature containers: enveloping or base64 XAdES, ePUAP envelopes) into its statement files, and tie each to its `filing_index` row. Detect structure version from XML namespace URI plus root element, plus the `KodSprawozdania` header's `kodSystemowy` and `wersjaSchemy`: MF schemas 1-0 and 1-2 share a namespace. Never from filename. Validate against the official XSD for that version, vendored under `config/xsd/` and resolved offline. Validation failure is recorded as a finding and the document is quarantined, not discarded. PDF statements are routed to C3; versions recognised but not yet mapped (`config/mappings/structure_catalog.yaml`) are recorded and skipped.

**C2 — Canonical mapping.** Declarative specs in `config/mappings/structures/<version>.yaml`. Structures that declare the same statutory elements share one body file under `bodies/` (element paths, the canonical code for each, and the hierarchy `subtotals_consistent` walks); each version spec binds namespaces, header paths, unit, amount columns and code overrides to that body:

```yaml
structure_version: full-2018-v1-2
form: full
body: jednostka_inna         # config/mappings/structures/bodies/jednostka_inna.yaml
detect:                      # C1: namespace alone is ambiguous, see C1 above
  root_namespace: "http://www.mf.gov.pl/schematy/SF/.../2018/07/09/JednostkaInnaWZlotych"
  root_name: JednostkaInna
  kod_systemowy: "SFJINZ (1)"
  wersja_schemy: "1-2"
xsd: "https://www.gov.pl/.../JednostkaInnaWZlotych(1)_v1-2.xsd"   # vendored, config/xsd/
effective_from: null
effective_to: null
namespaces: {tns: "...", jin: "...", dtsf: "..."}
statement_root: "/tns:JednostkaInna"
header: {kod_sprawozdania: "...", period_start: "...", period_end: "..."}
unit:                        # §4.2: the structure fixes the unit
  SprFinJednostkaInnaWZlotych: 1
statements: {Bilans: "tns:Bilans", RZiS: "tns:RZiS", ...}
columns: {KwotaA: current_year, KwotaB: prior_year, KwotaB1: prior_year_restated}
code_overrides: {}           # where this version narrows a line's meaning
```

A statement entry may instead list the shapes the structure accepts, when the envelope does not fix the body — a `JednostkaMala` filing may carry either the small statements or the full ones, chosen per statement, and the header is identical either way (plan 0005 step D, ADR 0005 second addendum §6):

```yaml
statements:
  Bilans:
    - {xpath: "tns:BilansJednostkaMala", body: jednostka_mala, item_namespace: jma}
    - {xpath: "tns:BilansJednostkaInna", body: jednostka_inna, item_namespace: jin}
```

The engine uses whichever alternative the document contains, quarantines the file (`statement_body_ambiguous`) if more than one is present, and writes `source_element_path` with that alternative's prefix — which is what lets E2 check each statement against the body it was actually filed in.

Engine reads the spec, extracts via lxml XPath, normalises units, and emits long-format rows in Polars. Mapping logic lives in data, not code.

CI tests: every known structure version has a spec; every spec's `canonical` targets exist in the canonical chart; every spec marked `required: true` resolves against at least one golden fixture document; every chart code is reachable from some body or override; and every mapped element's chart label matches its XSD's own label, which is the only way a version that narrows a line's meaning without renaming it can be caught.

**C3 — PDF tier.** Router:

1. Text layer present and extraction confidence above threshold → PyMuPDF.
2. Tables or layout needed → Docling.
3. Scanned or degraded → vision LLM.

Record which tier handled each document. IFRS filers arrive here. MSiG notices do not: the notice base is served as JSON with text (ADR 0011), so the tier stays deferred (plan 0006).

### D — Exploration

marimo notebooks in `notebooks/`, `.py` format, reading via DuckDB. Not part of the production graph. Never import from `notebooks/` in `src/`.

### E — Validation

- **E1** Pandera schemas at every Python stage boundary.
- **E2** Accounting identities (§4.3) as **Dagster asset checks**, one named check per rule. Each check is independently testable and reports the failing entity/year set.
- **E3** SQLMesh models producing `quarantine` (with reason codes) and `dq_mart` (pass rates by structure version, filed body set, fiscal year, check type, plus a coverage grain), defined in §5.
  - The current quarantined set is **derived, never stored**. It is recomputed on every run from the sources that know each stage's answer. The Postgres `quarantine_events` log is never cleaned and is not the source of "quarantined now".
  - Each SQLMesh audit is non-blocking and runs as a Dagster asset check on the model it audits (ADR 0010).
  - `dq_mart` is built in Phase 3 with a publish-safe shape. Publishing it to the public site is Phase 9, behind the suppression threshold.

### F — Transformation and storage

- SQLMesh project in `transform/`: DuckDB engine, SQLMesh state in Postgres (ADR 0010).
- **Boundary:** SQLMesh starts where the data becomes tabular. `financial_statements_canonical`, `restatement_events` and `identity_check_results` are Python (Dagster/Polars) outputs. SQLMesh reads them, and the Postgres manifest tables, only through the `ext.*` views declared in `transform/external_models.yaml`, and never writes them.
- Incremental models keyed by time range so a late-arriving filing for an old period triggers a correct partial rebuild. The key is `known_from`, the axis on which data arrives (§4.7), not `fiscal_year`. Current-state and aggregate models (`quarantine`, `dq_mart`) are full rebuilds instead: a rule change rewrites them all the way back.
- Output for the point-in-time layer H consumes: Parquet partitioned by `fiscal_year` and `as_of_month`, with `valid_from`, `valid_to`, `known_from` columns and content-hashed snapshot manifests. **Deferred to Phase 5**, when the feature store can say what shape it needs (ADR 0010 decision 6).

### G — Semantic extraction

**G1** spaCy `pl_core_news_lg` for sentence segmentation and lemmatisation. Build a lemma-based keyword prefilter that selects candidate pages. Polish is heavily inflected — surface-form matching is insufficient and will silently miss most hits.

**G2** Pydantic response schemas, constrained output. Every extraction returns value, evidence span, source document hash, page, method, confidence. An extraction without evidence is discarded.

**G3** Golden set at `evals/text_signals/*.jsonl`, hand-labelled, committed. Report precision, recall, F1 per `signal_type`. **CI gate:** prompt or model changes must hold or improve scores, or the build fails.

### H — Point-in-time features

**H1** Assembly via DuckDB `ASOF JOIN`: for each `(krs, as_of_date)`, join the most recent fact with `known_from <= as_of_date`.

Feature families:

- Financial ratios: liquidity, leverage, profitability, efficiency, working capital, interest coverage; plus 1/2/3-year trends and volatility.
- Construction-specific: receivables and payables turnover, contract-related balances, backlog proxies where available.
- Legal tripwires (§4.5).
- Filing behaviour: days from fiscal year-end to filing, missing years, late filings, corrections, auditor change.
- Text signals (§G).
- Registry dynamics: board turnover count, registered office changes, capital changes.
- Macro: NBP reference rate, sector aggregates, regional indicators.

**H2** Leakage tests — see §9.1.

### I — Modelling

Three generations trained on identical data and compared:

1. `statsmodels` — Altman Z-score variants and Polish discriminant models from the literature, as baselines.
2. `scikit-learn` — regularised logistic regression.
3. `LightGBM` + `scikit-survival` — gradient boosting and discrete-time survival with censoring.

Rules:

- **Never impute missing line items.** Absent items in simplified-form filings are legitimately missing. LightGBM handles them natively; preserve the missingness.
- Evaluation is **out-of-time only**: train on earlier years, test on later, rolling forward. No random splits.
- Test windows must include COVID (2020–21), the 2022 rate hiking cycle, and the energy price shock.
- Report calibration (Brier score, reliability curves) alongside discrimination (AUC, precision at top deciles). Calibration is the primary metric; never report AUC alone.
- Tuning via Optuna, explanation via SHAP.
- Every MLflow run records four identifiers: code commit, data snapshot hash, label version, feature set version. A run missing any of these is invalid.
- Promotion to champion requires beating the incumbent on the agreed metrics in the registered comparison.

### J — Serving

- FastAPI, Pydantic v2. Every response includes data version metadata.
- Postgres holds scores, alerts, watchlists.
- Models load in-process from the MLflow registry at startup.

### K — Presentation

- Streamlit internal explorer: company profile, score history with annotations, source-linked statements, peer comparison, watchlists.
- Evidence static site: sector and regional dashboards, `dq_mart`, realised vs predicted rates. Builds against DuckDB, deploys static.
- Quarto methodology report, regenerated from live data.

**Public output constraint:** the public site shows aggregated or pseudonymised results only. Named company-level risk scores are local deployment only, and every score display carries a disclaimer that it is a statistical estimate, not a statement of fact.

### L — Monitoring

- Dagster UI plus `structlog` JSON logs for pipeline and DQ observability.
- Evidently for feature drift and realised-outcome calibration; reports embed into the Evidence site.
- Sentry for unhandled exceptions.
- Scheduled runs: daily registry check, weekly filing check, scaled acquisition during the early-summer filing wave (most calendar-year filers approve statements within six months of year end and file shortly after).

---

## 7. Repository layout

```
.
├── AGENTS.md                  # one-line pointer to CLAUDE.md, for tool compatibility
├── CLAUDE.md                  # agent entry point
├── AGENT_SPEC.md              # this file
├── DIRECTORY_STRUCTURE.md     # where files go
├── pyproject.toml
├── uv.lock
├── docker-compose.yml
├── docs/
│   ├── PROJECT_OVERVIEW.md
│   ├── TECHNICAL_ARCHITECTURE.md
│   ├── adr/                   # one record per decision
│   ├── plans/                 # numbered, per-stage build plans (see docs/plans/)
│   ├── specs/                 # per-stage specs
│   └── glossary.md            # Polish accounting/legal terms, PL/EN
├── prompts/                   # versioned extraction prompts
├── evals/                     # golden label sets + score history
├── config/
│   ├── segments/              # declarative universe specs
│   ├── mappings/              # canonical_chart.yaml, pkd_crosswalk.yaml, structures/
│   └── statutory/             # size_thresholds.yaml, ksh_tripwires.yaml, procedure_taxonomy.yaml
├── src/
│   └── distress_radar/        # installable package (src layout)
│       ├── acquisition/       # A1–A5, one adapter per source
│       ├── parsing/           # C1–C3
│       ├── extraction/        # G1–G3
│       ├── features/          # H1–H2
│       ├── models/            # I
│       └── api/               # J
├── transform/                 # SQLMesh
├── dagster_defs/              # assets, checks, partitions, schedules, sensors
├── notebooks/                 # marimo .py
├── app/                       # Streamlit
├── site/                      # Evidence
├── report/                    # Quarto
└── tests/                     # mirrors src/; tests/features/test_leakage.py is blocking
```

This tree is abridged; `DIRECTORY_STRUCTURE.md` holds the authoritative full version, and any disagreement is resolved in its favour. The package sits at `src/distress_radar/` rather than at the repo root so `pytest` cannot silently import an uninstalled copy; import as `from distress_radar.parsing import mapping_engine`. `config/mappings/` and `config/statutory/` sit outside `src/` deliberately: they encode external rules that change on legislative timelines and must be reviewable as data.

---

## 8. Coding conventions

- Python 3.12+. Full type annotations; `pyright` strict on `src/`.
- Pydantic models for all external boundaries (HTTP responses, parsed documents, LLM output, API payloads).
- No bare `except`. Distinguish transient from permanent failures explicitly; only transient failures retry.
- Money as `decimal.Decimal`, never float.
- Dates as `datetime.date`. All timestamps UTC, timezone-aware.
- No network calls in tests. Fixtures only.
- Polish identifiers (`krs`, `nip`, `regon`, `pkd`) keep their Polish names. Do not translate to `company_id`.
- Every Dagster asset has a docstring naming its inputs, outputs, and partition scheme.

---

## 9. Testing requirements

### 9.1 Leakage test (blocking)

The single most important test in the repository. Must run in CI and block merge.

```
for every row in feature_store:
    for every feature column f:
        assert row[f"{f}__known_from"] <= row["as_of_date"]
```

Plus per-family variants asserting no contributing source violated the bound. A feature that cannot report its `known_from` fails the test.

### 9.2 Other required tests

| Test | Scope |
|---|---|
| Mapping coverage | Every structure version has a spec; every required target resolves against golden fixtures |
| Accounting identities | Golden fixture statements, including known-bad ones that must quarantine |
| Unit normalisation | Documents declaring thousands produce złoty values |
| Variant handling | Comparative and calculation statements both map; no cross-derivation |
| Label construction | Each outcome class, censoring, source deduplication, regime flagging |
| Extraction eval | Precision/recall per signal type against `evals/` |
| Idempotence | Re-running any stage on identical input produces byte-identical output |
| Statutory config | Threshold lookups resolve correctly across effective-date boundaries |

---

## 10. Build order

Each phase must be demonstrable before the next begins.

| Phase | Deliverable |
|---|---|
| 0 | Repo skeleton, Docker Compose, CI, ADR template, Dagster hello-world asset |
| 1 | Acquisition for 20 hand-picked entities; raw documents in MinIO, manifest in Postgres |
| 2 | Two structure versions parsed end-to-end into canonical model, accounting identities passing |
| 3 | Remaining structure versions, `quarantine` and `dq_mart` built in SQLMesh; `dq_mart` is published in phase 9 (plan 0007) (the C3 PDF tier stays deferred: MSiG turned out to serve notice text, not PDFs, so Phase 4 did not need it — plan 0006, ADR 0011) |
| 4 | Legal events, outcome labels, censoring, regime flags |
| 5 | Feature store with ASOF assembly and blocking leakage tests |
| 6 | Baseline and classical models, out-of-time backtest report |
| 7 | Text extraction with measured eval, folded into features |
| 8 | Modern and survival models, calibration, SHAP, MLflow registry |
| 9 | FastAPI, Streamlit explorer, Evidence site, Quarto report. **Before anything is published: set `dq_mart`'s small-cell suppression threshold (≥5 entities).** It ships `null` — suppression off — for Phases 3–8 (plan 0007 decision 9), and the publish must refuse to run while it is still `null` |
| 10 | Scheduling, alerting, Evidently drift monitoring |

Phase 1 precedes phase 2 deliberately: discovering a source is inaccessible must happen before anything is built on top of it.

---

## 11. Verify before building

Three assumptions rest on a moving target. Confirm each in phase 0 and record findings in `docs/adr/`:

1. ~~The financial statement repository was rebuilt in February 2026. Confirm current document formats, access patterns, and terms of use rather than assuming continuity with the previous platform.~~ **Verified in `docs/adr/0004-rdf-2026-platform-verification.md`:** public per-entity lookup by KRS number, no auth, XML/PDF, no bulk API — the per-entity design below stands. Bot protection on the search UI means A3 may need the `httpx`-first / `Playwright`-fallback tiering A1 already uses; confirm empirically once Phase 1 drives real traffic.
2. ~~A new generation of Ministry of Finance XML structures applies to statements prepared from 1 January 2026. Confirm published XSDs and which entity types they cover.~~ **Verified in `docs/adr/0005-mf-xml-2026-structures-verification.md`, with a correction:** the trigger is financial statements for **fiscal years beginning 1 January 2025 or later**, not "prepared from 1 January 2026" as stated above — one year earlier than this document previously assumed. Published as CRWDE structure "wariant 2 / wersja 1-0E." `tests/fixtures/neobis_001.xml` already uses this generation; which UoR annex(es) it corresponds to for `sp. z o.o.` size classes is still open and deferred to stage C2.
3. Confirm the current terms of use and rate expectations for every source in §6A before implementing its adapter. Access must stay within those terms; if bulk access is not permitted, the per-entity design in §6A stands. **KRS API, MSiG and KRZ: recorded in `docs/adr/0011-legal-event-sources.md` (accepted 2026-09-23):** the KRS API is open data under the 2021 open-data act, with no published rate limit (15 requests a minute here); MSiG has no terms page and is used as its own UI uses it, per entity; KRZ is behind a WAF and not accessed.

---

## 12. Compliance constraints

- Natural persons are never ingested (§2.7). Filter at acquisition, not downstream.
- Board member names are not required for modelling. Where registry dynamics features derive from them, store only derived counts, pseudonymised.
- Request pacing and caching on every external source; acquisition load must stay modest.
- Public artifacts are aggregated or pseudonymised (§6K).
- Any published figure must be regenerable from pinned code commit, data snapshot hash, label version, and model version.
