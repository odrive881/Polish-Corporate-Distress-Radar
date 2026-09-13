# CLAUDE.md

Context for Claude Code working in this repository. This file is intentionally short — it orients, then points to the document that has the detail. Read the linked file before working in an area you haven't touched yet.

| Question | Answer |
|---|---|
| What is this? | `docs/PROJECT_OVERVIEW.md` |
| How do I build stage X? | `AGENT_SPEC.md` |
| Where does this file go? | `DIRECTORY_STRUCTURE.md` |
| What does this Polish term mean? | `docs/glossary.md` |
| Why was tool X chosen over Y? | `docs/adr/`, then `docs/TECHNICAL_ARCHITECTURE.md` |
| How do the numbered stages map to A–L? | `docs/PROJECT_OVERVIEW.md` § Stage crosswalk |

`AGENT_SPEC.md` and `DIRECTORY_STRUCTURE.md` sit at repo root, next to this file — they're read on every task, not just for context. `PROJECT_OVERVIEW.md`, `TECHNICAL_ARCHITECTURE.md`, and `glossary.md` live in `docs/`.

---

## What this project is

A batch data platform that estimates the probability a Polish company enters bankruptcy, restructuring, or liquidation within 12 and 24 months. It ingests statutory financial filings (XML/PDF) from KRS-registered companies, normalises them into one canonical financial model under Polish accounting law, enriches them with registry history and Polish-language text signals, labels outcomes from insolvency registers, and trains distress models evaluated strictly out-of-time.

**v1 universe:** construction sector (PKD F), `sp. z o.o.`, small/medium size class, ≥3 filed years. A few thousand entities, ~10⁷ line-item rows. This is a small-data problem with hard domain logic, not a big-data problem — see "Never introduce" below before reaching for distributed tooling.

---

## Environment
- This project runs inside WSL (Ubuntu). Never run commands from a Windows shell against this folder.
- Python environment is managed by **uv** from `uv.lock`. Run everything through `make` targets (`make check` is the gate); don't `pip install` or activate `.venv` by hand. After changing dependencies in `pyproject.toml`, run `make lock`.

---

## Non-negotiable invariants

These are build failures, not style preferences. Full detail in `AGENT_SPEC.md` §2.

1. **Point-in-time correctness.** A feature for `as_of_date` may only use facts with `known_from <= as_of_date`. Enforced by a blocking test — never weaken or skip it.
2. **Raw immutability.** Downloaded bytes are written to the object store unmodified and content-addressed. Never overwritten, never parsed straight from the network.
3. **Full lineage.** Every canonical fact carries its source document hash, source element path, and ingestion run id.
4. **No silent data loss.** Failing records go to quarantine with a reason code. Never drop or impute to make a check pass — this applies especially to missing financial line items, which are often legitimately absent (small entities file simplified forms) rather than missing data to fill in.
5. **Idempotence.** Re-running any stage on the same input reproduces the same output exactly.
6. **Legal entities only.** Never ingest or store natural persons, including consumer bankruptcies in insolvency registers. Filter at acquisition.
7. **Statutory logic is versioned config, not code.** Size thresholds, KSH tripwire ratios, and procedure taxonomies live in `config/statutory/` as dated YAML, never as Python constants.

---

## Stack (locked — do not re-litigate)

Orchestration **Dagster** · packaging **uv** · lint **ruff** · types **pyright strict** · tests **pytest** · HTTP **httpx + hishel + pyrate-limiter + tenacity** · SOAP **zeep** (GUS BIR1 only) · object store **MinIO** · OLTP **Postgres** · XML **lxml + xmlschema** · dataframes **Polars** (not pandas) · PDF **PyMuPDF → Docling → vision LLM**, tiered · notebooks **marimo** (`.py`, not `.ipynb`) · contracts **Pandera** · transform **SQLMesh** · analytics **DuckDB** · storage **Parquet** with explicit validity columns · Polish NLP **spaCy `pl_core_news_lg`** · extraction **Pydantic + constrained LLM output** (not LangChain) · models **statsmodels, scikit-learn, LightGBM, scikit-survival, Optuna, SHAP** · tracking **MLflow** · API **FastAPI** · internal app **Streamlit** · public site **Evidence** · report **Quarto** · drift **Evidently** · errors **Sentry**.

**Never introduce:** Kafka/streaming, Spark/Dask, Kubernetes, a cloud warehouse, a standalone vector DB (use `pgvector`), a model-serving framework (load in-process from MLflow), Feast, Great Expectations, pandas, LangChain. The data fits in RAM — if a change seems to require one of these, the design is wrong, not the constraint.

Full rationale for every choice: `docs/TECHNICAL_ARCHITECTURE.md`.

---

## Directory map

```
config/          statutory + mapping YAML — data, not code
src/distress_radar/
  acquisition/   A — external sources (httpx/zeep adapters)
  parsing/       C — XML/PDF → canonical model
  extraction/    G — Polish text signal extraction
  features/      H — point-in-time ASOF feature assembly
  models/        I — training, calibration, registry
  api/           J — FastAPI
transform/       SQLMesh (F)
dagster_defs/    orchestration wiring ONLY — imports from src/, never the reverse dependency
app/             Streamlit (internal, named/company-level)
site/            Evidence (public, aggregated/pseudonymised only)
tests/           mirrors src/; tests/features/test_leakage.py is blocking
```

Rule of thumb: if it's callable and testable with no orchestrator running, it goes in `src/`. If it only makes sense while Dagster is running, it goes in `dagster_defs/`. Full placement table with more cases: `DIRECTORY_STRUCTURE.md` §3.

`src/` uses the **src layout** (`src/distress_radar/`, not a flat package at repo root) so `pytest` can't silently import an uninstalled package. Import as `from distress_radar.acquisition import regon_client`.

---

## Domain vocabulary

Don't translate or rename these — they're the terms the domain and the data use.

| Term | Meaning |
|---|---|
| **KRS** | National Court Register — company registration |
| **NIP / REGON** | Tax ID / statistical ID, alongside KRS number as entity identifiers |
| **RDF** | Repozytorium Dokumentów Finansowych — the financial statement filing portal. Lookup-only, one entity at a time, not a bulk API |
| **KRZ** | Krajowy Rejestr Zadłużonych — insolvency register, live since late 2021 |
| **MSiG** | Monitor Sądowy i Gospodarczy — the pre-KRZ insolvency notice archive (PDF) |
| **UoR** | Ustawa o rachunkowości — the Polish accounting law; defines size classes and statement formats |
| **KSH** | Kodeks spółek handlowych — Commercial Companies Code; source of the Art. 233/397 loss tripwires |
| **PKD** | Polish activity classification code (sector). Has a 2007 and a 2025 version — cross-walk, don't assume stability |
| **sp. z o.o.** | Spółka z ograniczoną odpowiedzialnością — the v1 legal form scope |

Full glossary: `docs/glossary.md`.

---

## Working conventions

- Money is `decimal.Decimal`, never `float`. Dates are timezone-aware UTC.
- No bare `except`; distinguish transient failures (retry) from permanent ones (don't).
- Every Dagster asset docstring states its inputs, outputs, and partition scheme.
- Every MLflow run logs four IDs: code commit, data snapshot hash, label version, feature-set version. A run missing any of these is invalid, not just incomplete.
- Models are evaluated **out-of-time only** — never a random train/test split. Report calibration (Brier score) alongside AUC; never AUC alone.
- Prompt or extraction-model changes must hold or improve precision/recall against `evals/text_signals/*.jsonl` in CI, or the build fails.
- New top-level directories require an ADR in `docs/adr/` before creation.

---

## Before you start a task

1. Identify which stage (A–L) the task belongs to and read that section of `AGENT_SPEC.md`. If you only have a numbered stage (1–12) from `docs/PROJECT_OVERVIEW.md`, convert it using the stage crosswalk in that document.
2. Check `DIRECTORY_STRUCTURE.md` §3 for exactly where new files belong.
3. If the task touches accounting logic (identities, size classification, tripwires) or the outcome taxonomy, re-read `AGENT_SPEC.md` §4 — these rules are not inferable from the code around them.
4. If a stack or architecture choice in this file seems wrong for the task at hand, flag it in a comment and ask — don't silently substitute a different tool.

## Canonical names

Dataset names are defined once, in `AGENT_SPEC.md` §5. Use those exactly: `financial_statements_canonical`, `entity_size_class_history`, `restatement_events`, `legal_events`, `text_signals`, `outcome_labels`, `feature_store`, `dq_mart`, `quarantine`, `scores_history`, `alerts`. There is no `financial_statements_validated` or `dq_results` — validation status is the `quality_grade` column on the canonical table.

`model_registry` refers to MLflow, the tool, never to a table in this project.

## Known moving targets — verify, don't assume

- RDF was rebuilt in February 2026; confirm current access patterns before building acquisition (A3) against it.
- A new generation of Ministry of Finance XML structures applies from fiscal year 2026 onward; confirm published XSDs before writing mappings (C2) for that year.

Record findings from both checks as ADRs.
