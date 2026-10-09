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
2. **Raw immutability.** Downloaded bytes are written to the object store unmodified, content-addressed, and never overwritten or deleted. All parsing reads from the object store, never from the network. The one exception is invariant 6's redaction, applied before hashing: filed documents have signer data removed, KRS extracts have natural persons removed, and MSiG notices are stored only as person-free records (structured fields, dates, signatures, vocabulary terms), never their text (ADR 0009 and its addendum). Filers' file names (RDF details, `filing_index`, ZIP members, sidecars), attachment names and PDF document metadata are replaced before hashing: a file name becomes a token of its filing's `document_ref` (ADR 0009 second addendum). Each stored object records its redaction version and the hash of what was received (`raw_redactions`). The ADR 0009 migration is the only code that deletes raw objects.
3. **Full lineage.** Every canonical fact carries the source document hash, the source element path, and the ingestion run id. A fact that cannot name its source is a bug.
4. **No silent data loss.** Records failing validation go to a quarantine table with a reason code. Never drop, never impute to make a check pass.
5. **Idempotence.** Every stage produces identical output from identical input. Re-running a stage is always safe.
6. **Legal entities only.** Natural persons are never ingested or stored, including from insolvency registers which contain consumer bankruptcies. Filter at the acquisition boundary. This includes the signatures on filed documents (names, certificates, PESEL numbers), which `acquisition/redaction.py` strips before storing (ADR 0009), and the people named in KRS extracts (board members, shareholders, liquidators, trustees, notaries), which it redacts the same way (ADR 0009 addendum). File names are never kept as text, because filers put people in them (ADR 0009 second addendum). The A3 assets carry a blocking `personal_data` asset check over the whole raw store.
7. **Versioned statutory config.** Size thresholds, KSH tripwire ratios, and procedure taxonomies live in `config/statutory/` as dated YAML. Never hardcode them in Python or SQL.

---

## 3. Locked stack

| Concern | Tool | Notes |
|---|---|---|
| Orchestration | **Dagster** | Software-defined assets; asset checks for DQ. No Dagster partitions yet: every asset rebuilds in full at seed scale, and `feature_store` is laid out by `as_of_date` year in Parquet (ADR 0012) |
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
| `source_member` | str | Path from the stored download to the statement file, e.g. `zip:kQL-7bDLHvl-dIGIeLuLlQ.xml` or `zip:kQL-7bDLHvl-dIGIeLuLlQ.xades>ds:Object[2]>base64`. Member names are tokens of the filing's `document_ref`, never the filer's file name (ADR 0009 second addendum). A download can hold a statement and its correction (plan 0004) |
| `document_ref` | str | RDF document id (`filing_index.document_ref`); tells a statement from its correction |
| `source_element_path` | str | XPath (namespace prefixes from the structure spec). For a filer's own extra lines, summed into one `….USER` fact, an XPath union of every contributing element |
| `known_from` | date | Filing submission date |
| `ingestion_run_id` | str | |
| `quality_grade` | enum | `pass`, `warn`, `quarantined` |

### `legal_events`

`krs`, `event_type`, `outcome_class`, `stage`, `ends`, `precludes_silent_exit`, `event_date` (nullable: no decision date), `known_from` (the source's publication: the KRS entry date or the MSiG publication date; the column this section once called `published_date`), `removed_on`, `source` (`KRZ` \| `MSiG` \| `KRS`), `case_signature`, `proceeding_id` (the linked case files' signature), `statute`, `dedup_group_id`, `source_document_hash`, `source_element_path`, `ingestion_run_id`, `event_year`. Built by `parsing/legal_events.py` (plan 0008 step F), contract `LEGAL_EVENTS`.

### `text_signals`

`krs`, `fiscal_year`, `signal_type`, `value`, `evidence_span`, `source_document_hash`, `page`, `extraction_method`, `confidence`, `known_from`, and for lineage and versions (invariant 3) `period_end`, `document_ref`, `source_member`, `document_kind` (`statement_notes` \| `auditor_report`), `source_element_path` (the attachment's element; empty for an auditor report), `attachment`, `extractor_version`, `masking_version`, `response_key` (the stored model response; null for a rule), `ingestion_run_id`.

One row per kept extraction (plan 0013 step H): `value` is `present` or `absent`, or for `opinion_type` the opinion (`unqualified`, `qualified`, `adverse`, `disclaimer`) or `absent`, and for `post_balance_sheet_event` under a valued prompt (from `extractor_v4`, plan 0013 decision 9) the kind of event (`adverse`, `favourable`, `neutral`) or `absent`; `evidence_span` is masked (ADR 0009, third addendum) and null exactly when absent; `extraction_method` `llm` or `rule`; `confidence` `high`, `medium` or `low`. Dated by the filing: `known_from` is its submission date (an auditor report's own: its detail's, or the owner's list's, plan 0013 decision 6 as amended), `fiscal_year` its period end's year. Two kinds of document are read (`document_kind`): the notes embedded in a statement, and a separately filed auditor report (plan 0013 decision 0c), a PDF read as one attachment; every signal is read from both. Only pages the prefilter selects are extracted, so a signal's absence from this table means nothing on its own: `text_coverage` says what was read. Built by `extraction/text_signals.py`, contract `TEXT_SIGNALS`.

`signal_type` enum: `going_concern_uncertainty`, `opinion_type`, `emphasis_of_matter`, `covenant_breach`, `key_customer_loss`, `litigation`, `post_balance_sheet_event`, `loss_coverage_resolution`, `continued_existence_vote`.

### `text_coverage`

`krs`, `fiscal_year`, `signal_type`, `status`, `known_from`, `period_end`, `document_ref`, `source_document_hash`, `source_member`, `document_kind`, `attachments`, `attachments_unsupported`, `attachment_errors` (list), `pages`, `pages_text`, `pages_needs_ocr`, `pages_sparse`, `pages_selected`, `kept_present`, `kept_absent`, `discarded`, `discard_reasons` (list), `unanswered`, `extractor_version`, `masking_version`, `ingestion_run_id`.

One row per (statement file or auditor report, `signal_type`): what the text stage read (plan 0013 step H), so a missing signal is never read as "no warning" (invariant 4). `status`: `no_text` (no page of the notes, or of the report, has a text layer), `not_run` (the prefilter selected pages for a model signal before the owner confirmed the provider's terms), `partial` (read, but a page was scanned, an attachment unreadable, or an extraction discarded or unanswered), `read` (every page read, every selected page answered; a page not selected counts as absent). Written with `text_signals`, contract `TEXT_COVERAGE`.

### `auditor_reports`

`krs`, `fiscal_year`, `period_end`, `known_from`, `document_ref`, `source_document_hash`, `source_member`, `pages_text`, `pages_needs_ocr`, `opinion` (`unqualified` \| `qualified` \| `adverse` \| `disclaimer`, null when none is stated), `opinion_page`, `opinion_confidence`, `modified_opinion` (null exactly when `opinion` is), `firm_stated`, `auditor_changed`, `compared_with` (the earlier report's `document_ref`), `extractor_version`, `ingestion_run_id`.

One row per auditor report the text stage read (plan 0013 decision 0c), dated by the report's own filing. The opinion is the first page on which the opinion rule states one (`text_signals`, `opinion_type`); no stated opinion is null, never unqualified. `auditor_changed` says whether the report's audit firm differs from the firm on the entity's latest earlier report, of an earlier period, known when this one was filed; it is null when either report states no single firm, or there is none earlier. The firm is told by its number on the list of audit firms, read from the masked text and never stored: a firm can be a sole practitioner, a natural person (owner, 2026-10-05). Written with `text_signals`, contract `AUDITOR_REPORTS`.

### `statement_disclosures`

`krs`, `fiscal_year`, `period_start`, `period_end`, `item`, `value_bool`, `value_number`, `raw_value`, `structure_version`, `config_version`, `source_document_hash`, `source_member`, `source_element_path`, `document_ref`, `known_from`, `ingestion_run_id`.

`item` enum: `going_concern_basis` (prepared on the going-concern basis), `going_concern_threat` (circumstances threaten it; the negation of the filed P_5B), `going_concern_threat_described` (a description is filed; its text is never kept, ADR 0009), and from wariant 2 (fiscal years from 2025) `average_employment` (full-time equivalents, UoR art. 64 ust. 1 pkt 4) and `audit_required`. One row per item a statement's introduction reports, one typed value each; an item not filed has no row. Written with `financial_statements_canonical` from the same files (plan 0013 step B), read with `config/mappings/statement_introduction.yaml`, contract `STATEMENT_DISCLOSURES`.

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
- A1–A4 and C4: the latest event per key in the log;
- G1 (an attachment of the notes, or an auditor report, that cannot be read, per file) and G2 (a discarded text extraction, per file and `signal_type`): `text_coverage` (plan 0013 step H).

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

`krs`, `as_of_date`, `as_of_year` (the partition), `feature_set_version`, `feature_set_hash`, then each feature and its companion `<name>__known_from`, in the feature set's order. Booleans are `Boolean`, counts `Int32`, the rest `Float64`. One row per `(krs, as_of_date)` on the label grid without horizons: month-ends from the label config's start, or the entity's registration, to its cutoff; rows the labels exclude still get features. Built by `features/asof_assembly.py` (plan 0010, ADR 0012), written under `WAREHOUSE_DIR/feature_store/` and replaced whole on every run (ADR 0008).

Rules:
- `<name>__known_from` is the latest `known_from` of every fact the value was built from. A feature is non-null exactly when its companion is, and the companion is never after `as_of_date` (the `FEATURE_STORE` contract, and §9.1).
- Nothing is imputed (invariant 4). A feature is null when an input is missing (the micro form has no liability split or equity breakdown), when a denominator is zero, or when a flow-based feature reads a period outside 335–396 days or of unknown length (plan 0010 owner decision 7: never annualised). A count of zero is a value.
- `config/features/<feature_set_version>.yaml` defines the features; the file name is the version and its hash is on every row. `FEATURE_SET_VERSION` picks one (default `feature_set_v6`: v5 plus the latest auditor report's age; v5 is v4 plus the auditor reports' opinion and auditor change from `auditor_reports` and their emphasis of matter from `text_coverage`; v4 is v3 plus the statement's going-concern flags and employment from `statement_disclosures`, and the notes' going-concern and loss-coverage signals from `text_coverage`; plan 0013. v3 is v1 plus the ratios of the classical models in `config/models/`, and `IS.CALC.A.R2025` counted as revenue; plan 0012). Quarantined statements are excluded from financial features unless the feature set's `include_quarantined_statements` is on.
- A disclosure or text feature speaks for the latest statement known at `as_of_date` (latest period, then latest filing), dated by its filing, and stops when the filing is deleted. An auditor-report feature (the `audit` family, from `auditor_reports`) speaks for the latest report known, by period then filing, dated by the report's own filing; a fact that report does not establish is null. Since a report speaks until a later one is filed, `auditor_report_age_years` (v6) says how old it is. A text feature reads one `document_kind` (the notes unless its config names auditor reports) and is null unless `text_coverage` says the notes were read for its signal: notes never read, scanned or not wholly answered are never "no warning". Its count of periods takes each period's latest filing whose notes were read (plan 0013 step I).
- Ratios over near-zero denominators reach extreme values; they are data, and models transform them (e.g. rank or winsorise) rather than read them raw.

---

## 6. Stage specifications

Each stage is a Dagster asset group. Implement in the order given in §10.

### A — Acquisition

Five adapters, one module each under `src/distress_radar/acquisition/`. All share a common base providing rate limiting, caching, retry, and raw-write-before-parse.

| Stage | Source | Protocol | Output |
|---|---|---|---|
| A1 | Registry aggregators | HTTPS | `universe_candidates` (krs, discovery_source, discovered_at) |
| A2 | GUS REGON BIR1 | SOAP (`zeep`) | `entity_master` — validated identifiers, PKD codes, legal form, status |
| A3 | Financial document repository (RDF) | Per-entity lookup through the public UI; four tiers, below | Raw documents → object store; `filing_index`, `rdf_listed_entities` |
| A4 | Full KRS extract (open KRS API) + MSiG notice search API; KRZ not built (WAF, ADR 0011) | HTTPS JSON (`krs_extract.py`, `msig_client.py`) | Redacted extracts and person-free notice records → object store; `legal_source_fetches`, `msig_notices` |
| A5 | NBP, GUS BDL | HTTPS JSON | Reference and macro series |

Requirements:

- Rate limiting is persistent across process restarts (token bucket in Postgres or on disk).
- HTTP cache is enabled in all environments. Re-running acquisition must not re-hit the source for unchanged documents. Where a source stamps every response (the KRS extract's `dataCzasOdpisu`) and no cache applies, unchanged content is recognised by a fingerprint and stored once (plan 0008 step C).
- A2 session token acquisition and refresh is handled inside the adapter; callers never see it.
- PKD codes are mapped across classification versions via `config/mappings/pkd_crosswalk.yaml`. The segment spec (`config/segments/<segment_name>.yaml`, e.g. `construction_sme_v1.yaml`) declares whether matching is on the predominant code only or any registered code.
- Source conflicts (e.g. differing PKD between registries) resolve by documented precedence and are logged to `entity_reconciliation_log`.

**A3 tiers.** RDF blocks automated clients (ADR 0007), so documents arrive through four tiers. Each is named in its stored objects' `fetch_tier`. All of them redact before hashing (ADR 0009), write the same `filing_index`, and key a document by RDF's id, with `rdf_document_id` holding the numeric `idDokumentu` (plan 0014, decision 1):

| Tier | Module | Input | Use |
|---|---|---|---|
| `playwright` | `document_retrieval.py` | a human-paced browser inside the pipeline | built (plan 0003), blocked by hCaptcha since 2026-09-16; not run |
| `manual_har` | `har_import.py` | browser recordings captured by hand (`rdf_manual_import`) | the seed; single entities and documents the script misses |
| `manual_files` | `report_import.py` | hand-downloaded auditor reports and a dates list (`rdf_auditor_report_import`) | the seed's auditor reports; superseded by `pad_script` for new captures |
| `pad_script` | `script_import.py` | the owner's Power Automate Desktop script: a listing per tab, a listing per search, ZIPs (`rdf_script_import`) | the route at scale (ADR 0013, plan 0014) |

The listed "Data dodania" of a tab is a document's `known_from` wherever no detail response was captured. It points back to the stored listing (`listing_sha256`); a detail captured later replaces it.

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
- Output for the point-in-time layer H consumes: no separate snapshot layer is built (ADR 0012, closing ADR 0010 decision 6). The canonical tables already carry both axes of §4.7 (`period_start` / `period_end` and `known_from`), and H ASOF-joins on `known_from` over them directly. The data snapshot hash MLflow runs log is SHA-256 over the `feature_store` Parquet files in sorted path order, with each file's hash in an artifact (plan 0012, `models/registry.py`).

### G — Semantic extraction

**G1** spaCy `pl_core_news_lg` for sentence segmentation and lemmatisation. Build a lemma-based keyword prefilter that selects candidate pages. Polish is heavily inflected — surface-form matching is insufficient and will silently miss most hits.

**G2** Pydantic response schemas, constrained output. Every extraction returns value, evidence span, source document hash, page, method, confidence. An extraction without evidence is discarded.

**G3** Golden set at `evals/text_signals/*.jsonl`, hand-labelled, committed. Report precision, recall, F1 per `signal_type`. **CI gate:** prompt or model changes must hold or improve scores, or the build fails.

As built (plan 0013), in `src/distress_radar/extraction/`, run by the `text` job:

- **Sources.** The notes embedded in every stored XML statement, and every stored auditor report dated by its own detail or by the owner's listed date (plan 0013 decision 6, as amended 2026-10-02), each row tagged with its `document_kind`. Text comes from the PDF text layer, page by page (`page_text.py`); a page with no text layer is not read and is counted, never treated as "no warning" (OCR is its own decision).
- **Masking before anything else (ADR 0009, third addendum).** `masking.py` replaces person names (spaCy NER), PESEL numbers and contacts on every page; the masked page is the only text the prefilter, the model, the evidence span and the eval set see. Masked text is not stored: it is re-derived from the raw bytes on each run. A blocking check (`evidence_masked`) re-runs the masker over every stored span, and the pre-commit hook over `evals/`.
- **G1:** the lemma prefilter (`preprocessing.py`, `config/extraction/prefilter_v*.yaml`) selects the pages each `signal_type` reads; a page it passes over is read as `absent` for that signal.
- **G2:** `config/extraction/extractor_v*.yaml` (`EXTRACTOR_VERSION`, default `extractor_v4`) names each signal's method. Rules (`rules.py`, `rules_v*.yaml`) read `opinion_type` from the report's KSB headings, and the audit firm's list number, which is compared in memory and never stored. Claude reads the eight free-text signals (`post_balance_sheet_event` from `extractor_v4` with the statement's balance-sheet date in the request and the kind of event as its value, decision 9), with the schema sent as structured output and validated by the same Pydantic model (`schemas.py`, `extractor.py`). A kept answer's evidence is a verbatim substring of the masked page; anything else is discarded with a reason code and quarantined (stage `G2`). Every response is stored by the SHA-256 of its request (`response_store.py`) and replayed, never re-requested (invariant 5); backfills go through the Message Batches API. No call is made unless `EXTRACTION_API_CONFIRMED=true` and `ANTHROPIC_API_KEY` are set; until then those signals are `not_run` in `text_coverage`.
- **G3:** the owner labels local queues of masked pages (`notebooks/labelling/golden_set.py`; `config/extraction/golden_sample_v*.yaml`: the notes, and whole auditor reports), and `make label-export` writes them to `evals/text_signals/` (the pages once in `pages.jsonl`, one file per `signal_type`). `make eval` runs the extractors on them and writes `evals/text_signals/results/`: the prefilter's recall, the extractor alone, and end to end, each with counts, plus the masker's recall. The owner accepts a result (`make eval-accept`). The gate is a test in `make check`, offline, with no API call in CI: for each signal with a result, the method in use needs a result on the current golden files, and its end-to-end precision and recall may not fall below the last accepted result beyond `config/extraction/eval_gate_v1.yaml`'s tolerance (0). A signal is gated from its first result.
- **Outputs:** `text_signals`, `text_coverage` and `auditor_reports` (§5); unreadable files are quarantined as stage `G1`.

### H — Point-in-time features

**H1** Assembly via DuckDB `ASOF JOIN`, run in-process from Python in `src/distress_radar/features/`, not in SQLMesh (ADR 0012): for each `(krs, as_of_date)`, join the most recent fact with `known_from <= as_of_date`.

- **The financial panel** (`panel.py`) says which statement speaks for each period, and from when. The statement filed for a period wins; a correction replaces it from its own filing date; a later filing's prior-year column only fills a period with no usable statement (a restatement never replaces filed figures, it is a feature); a deleted statement stops speaking from its deletion. It is keyed by period, not fiscal year: a liquidation or bankruptcy can split a year.
- **Financial families** are computed once per entity at each date its known figures change, and the ASOF join gives each row the latest such snapshot. Growth and volatility look back by time ("the period that ended 12 months before", within 31 days), not by count.
- **Filing and event families** are evaluated per row: a deadline passing or a window sliding changes them without new data. Events are counted by `known_from`, never by `event_date`, and keyed by (event type, event date), not `dedup_group_id`, which a later notice can change.

Feature families as built (feature set v1, 44 features):

- Financial ratios: current and quick ratio; debt- and equity-to-assets; ROA, ROE, net and operating margin; asset turnover; working capital to assets; operating result over financial costs (the statements do not separate interest, so this stands in for interest coverage); revenue and equity growth over 1, 2 and 3 years; 3-year net-margin volatility. Each input is mapped per form and income-statement variant in `config/features/line_items_v1.yaml`, never derived across variants (§4.1).
- Construction-specific: receivable days and short-term liability days (revenue-based), short-term prepayments and accruals to assets (where UoR art. 34a contract accounting sits). No backlog proxy: nothing in the data carries one.
- Legal tripwires (§4.5): `art233_triggered` (revaluation reserve excluded, a choice recorded in the config) and `negative_equity`.
- Filing behaviour: days from year-end to filing for the latest year; missing years and late filings over 3 years, counted only once the statutory deadline (`config/statutory/filing_deadlines.yaml`, COVID extensions included) has passed; corrections; latest statement filed as PDF; statements quarantined; restating filings and the largest restated line over total assets.
- Registry dynamics: board changes, office moves and capital changes in 12 and 36 months, from the KRS extract's entries (counts only, no names, §12); arrears enforcements in 12 months; curators ever appointed.
- Legal history: petitions and closed proceedings by class, ever.

Feature sets v2 and v3 (plan 0012) keep v1's 44 features unchanged and add five ratios the classical models read: retained earnings (prior years' result plus the year's) to assets, operating result to assets (standing in for EBIT), equity to liabilities, long-term capital (equity plus long-term liabilities) to assets, and the result on sales to revenue. v3 also counts the 2025 calculation-variant revenue line (`IS.CALC.A.R2025`, products and goods, no longer materials) as revenue; without it, revenue was null for those statements. The leakage test runs on every feature set.

Deferred, each with its reason in plan 0010: auditor change and loss-coverage history (text, Phase 7); text signals (§G, Phase 7); macro and sector context (A5 has no adapter, and sector aggregates over the seed would leak its own outcomes); size class (§4.4 needs average employment, which no structured source carried before FY2025, `docs/data_inventory.md` gap 8). Since then, `feature_set_v4` (plan 0013) adds the disclosure family (the going-concern flags, and employment from FY2025's wariant 2) and the notes' text family (going-concern and loss-coverage signals); `feature_set_v5` adds the auditor reports' opinion, auditor change and emphasis of matter, and v6 the report's age; the size class still waits on employment before FY2025.

**H2** Leakage tests — see §9.1. `features/leakage.py` holds both checks: §9.1 as written, and the per-family variant, which recomputes each family from the sources cut to what was public on each `as_of_date` and requires identical output. `tests/features/test_leakage.py` runs them on a synthetic warehouse with traps and shows leaky families fail; the Dagster `feature_store` asset runs them on the live store as a blocking asset check.

### I — Modelling

Three generations trained on identical data and compared:

1. `statsmodels` — Altman Z-score variants and Polish discriminant models from the literature, as baselines.
2. `scikit-learn` — regularised logistic regression.
3. `LightGBM` + `scikit-survival` — gradient boosting and discrete-time survival with censoring.

As built in Phase 6 (plan 0012, complete 2026-09-27; generation 3 is Phase 8, plan 0015):

- **Dataset** (`models/dataset.py`): `feature_store` joined to one frozen label set, both pinned in `config/models/<BACKTEST_VERSION>.yaml` (default `backtest_v4`: v2 on `feature_set_v6`, the regression's inputs unchanged (v3 was the same on `feature_set_v5`); v2 is v1 on `feature_set_v4` with `going_concern_threat` added to the regression, plan 0013); the label set's hash is recomputed before use. The target is distress (bankruptcy, restructuring, liquidation, silent exit) against `alive`; censored rows are counted and left out. A labelled row with no feature row is an error, never dropped. `regime_flag` is carried for the sensitivity run and is never a model input: it describes the label window, which is the future. A declaration with no decision date is dated by `event_known_from`, as the labels do.
- **Folds** (`models/splits.py`): expanding window by calendar year, test years 2020–2025. A test year trains only on rows whose whole label window closed before its 1 January (purged), and whose label was settled by then under the label version's own rules: a distress event already public (`event_known_from`), an `alive` window past the lag allowance for windows ending after KRZ's launch (plan 0009). Unsettled rows are counted, not relabelled; test rows keep their final labels (plan 0012 correction, 2026-09-29). Each fold reports rows, distinct events by class and entities; a fold with fewer than `min_events` (3) distinct training or test events is reported as not evaluable, and the rule is applied again to the rows each model actually uses.
- **Generation 1** (`models/baselines.py`): Altman's Z'' and the Poznań model with their published coefficients, which need no estimation; the coefficients, zones and sources are in `config/models/`. A score is null when any of its ratios is. Each score is mapped to a probability per fold by a one-variable logistic fit (scikit-learn) on fold-fitted ranks, so it shares the regression's Brier score. `statsmodels` is installed but not yet used.
- **Generation 2** (`models/classical.py`): L2 logistic regression, C = 1.0, no class weighting, never tuned, on equity/assets, working capital/assets, ROA and asset turnover (owner-approved), complete cases only, each ratio mid-ranked on the training fold alone.
- **Evaluation and report** (`models/evaluation.py`, `backtest.py`, `report.py`): Brier score, log loss, AUC and top-decile precision, written out and pinned by hand-computed tests; 95% intervals from 1,000 resamples of entities with a fixed seed; a reliability table for each pooled cell that is scored. Two runs side by side: `main` and `no_regime` (the 2020–21 regime rows left out of training and test). A cell is scored only when its model was fitted and trained and was scored on at least `min_events` distinct events; otherwise the report gives the reason and no numbers. The report opens every table with the seed caveat and its event count, and is written to `WAREHOUSE_DIR/reports/backtest/<backtest>.md`, byte for byte reproducible.
- **Tracking** (`models/registry.py`): one MLflow run per model, horizon and backtest run, in the experiment `phase6_backtest` of the local store (`MLFLOW_TRACKING_URI`, a SQLite file under `.data/mlflow/` until Phase 9). Each run carries the four identifiers as parameters: `code_commit` (refused from a working tree with uncommitted or untracked changes), `data_snapshot_hash`, `label_version` with `label_set_hash`, `feature_set_version` with `feature_set_hash`; an identifier set missing any of them cannot be constructed, so an incomplete run is never started. Counts are logged for every cell and metrics only for scored cells, as `<metric>.<test year or pooled>`; the report and the snapshot's per-file hashes are artifacts.
- **Orchestration** (`dagster_defs/assets/models.py`): the `backtest` asset (group `models`, downstream of `feature_store` and `outcome_labels`) in its own `backtest` job, run by hand and never scheduled; it refuses a dirty tree before any work and rebuilds none of its inputs. `BACKTEST_VERSION` picks the config.

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
| Out-of-time purging (blocking) | No training row's label window reaches its test year, and no training label was unknown on the test year's eve, at every horizon (`tests/models/test_splits.py`, plan 0012) |

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

1. ~~The financial statement repository was rebuilt in February 2026. Confirm current document formats, access patterns, and terms of use rather than assuming continuity with the previous platform.~~ **Verified in `docs/adr/0004-rdf-2026-platform-verification.md`:** public per-entity lookup by KRS number, no auth, XML/PDF, no bulk API — the per-entity design below stands. Bot protection on the search UI means A3 may need the `httpx`-first / `Playwright`-fallback tiering A1 already uses; confirm empirically once Phase 1 drives real traffic. **Confirmed and settled:** plain HTTP and Playwright are both blocked (ADR 0007); the seed was captured by hand, and scale goes through the owner's Power Automate Desktop script (ADR 0013), imported by plan 0014.
2. ~~A new generation of Ministry of Finance XML structures applies to statements prepared from 1 January 2026. Confirm published XSDs and which entity types they cover.~~ **Verified in `docs/adr/0005-mf-xml-2026-structures-verification.md`, with a correction:** the trigger is financial statements for **fiscal years beginning 1 January 2025 or later**, not "prepared from 1 January 2026" as stated above — one year earlier than this document previously assumed. Published as CRWDE structure "wariant 2 / wersja 1-0E." `tests/fixtures/neobis_001.xml` uses this generation; it is the full form, mapped as `full-2025-w2-v1-0` (plan 0004), and the micro form of the same generation as `micro-2025-w2-v1-0` (plan 0005).
3. Confirm the current terms of use and rate expectations for every source in §6A before implementing its adapter. Access must stay within those terms; if bulk access is not permitted, the per-entity design in §6A stands. **KRS API, MSiG and KRZ: recorded in `docs/adr/0011-legal-event-sources.md` (accepted 2026-09-23):** the KRS API is open data under the 2021 open-data act, with no published rate limit (15 requests a minute here); MSiG has no terms page and is used as its own UI uses it, per entity; KRZ is behind a WAF and not accessed. **A1:** the owner's hand-filtered Rejestr.io list (ADR 0014); Rejestr.io's terms for this use confirmed by the owner (2026-10-07; ADR 0014).

---

## 12. Compliance constraints

- Natural persons are never ingested (§2, invariant 6). Filter at acquisition, not downstream.
- Board member names are not required for modelling. Where registry dynamics features derive from them, store only derived counts, pseudonymised.
- Request pacing and caching on every external source; acquisition load must stay modest.
- Public artifacts are aggregated or pseudonymised (§6K).
- Any published figure must be regenerable from pinned code commit, data snapshot hash, label version, and model version.
