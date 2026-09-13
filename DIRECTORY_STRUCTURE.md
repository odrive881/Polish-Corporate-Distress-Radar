# Polish Corporate Distress Radar — Directory Structure

Companion to `AGENT_SPEC.md`. That document specifies what to build; this one specifies where every file goes. When in doubt about placement, this document wins. Do not invent new top-level directories without recording the decision in `docs/adr/`.

---

## 1. Full tree

Root holds the documents an agent reads on every task: `CLAUDE.md`, `AGENT_SPEC.md`, `DIRECTORY_STRUCTURE.md`. `docs/` holds reference material read for context rather than on every task: the business overview, the tool-selection rationale, ADRs, per-stage specs, and the glossary.

This is the authoritative tree. `AGENT_SPEC.md` §7 and `docs/TECHNICAL_ARCHITECTURE.md` §6 carry abridged copies for context; where they disagree with this file, this file wins.

```
.
├── AGENTS.md                          # one-line pointer to CLAUDE.md, for tool compatibility
├── CLAUDE.md                          # agent entry point — read first
├── AGENT_SPEC.md                      # what to build — read before implementing any stage
├── DIRECTORY_STRUCTURE.md             # where files go — read before creating any file
├── README.md
├── pyproject.toml
├── uv.lock
├── docker-compose.yml
├── .env.example
├── .github/
│   └── workflows/
│       ├── ci.yml                     # lint, type-check, tests, leakage test
│       └── extraction-eval.yml        # gates prompt/model changes against evals/
│
├── docs/
│   ├── PROJECT_OVERVIEW.md            # functional view, numbered stages 1–12
│   ├── TECHNICAL_ARCHITECTURE.md      # tool selection and rationale
│   ├── adr/                           # architecture decision records, one file per decision
│   │   └── 0001-record-title.md
│   ├── specs/                         # per-stage functional specs, one file per stage group
│   │   ├── acquisition.md
│   │   ├── parsing.md
│   │   ├── extraction.md
│   │   ├── features.md
│   │   └── models.md
│   └── glossary.md                    # Polish accounting/legal terms, PL/EN
│
├── prompts/
│   ├── extraction/
│   │   ├── going_concern_uncertainty_v3.md
│   │   └── covenant_breach_v2.md
│   └── CHANGELOG.md                   # what changed between prompt versions and why
│
├── evals/
│   └── text_signals/
│       ├── going_concern_uncertainty.jsonl   # hand-labelled golden set
│       ├── covenant_breach.jsonl
│       └── results/                   # score history per eval run, timestamped
│
├── config/
│   ├── segments/
│   │   └── construction_sme_v1.yaml   # declarative universe spec
│   ├── mappings/
│   │   ├── canonical_chart.yaml       # the canonical chart of accounts
│   │   ├── pkd_crosswalk.yaml         # PKD 2007 <-> PKD 2025
│   │   └── structures/                # one file per XML structure version
│   │       ├── full-2018-v1.yaml
│   │       ├── small-2018-v1.yaml
│   │       ├── micro-2018-v1.yaml
│   │       └── full-2026-v1.yaml
│   └── statutory/
│       ├── size_thresholds.yaml       # accounting-law size class thresholds, dated
│       ├── ksh_tripwires.yaml         # Art. 233 / Art. 397 ratios, dated
│       └── procedure_taxonomy.yaml    # bankruptcy/restructuring/liquidation event mapping
│
├── src/
│   └── distress_radar/                # the installable package — see §2
│       ├── __init__.py
│       ├── acquisition/
│       ├── parsing/
│       ├── extraction/
│       ├── features/
│       ├── models/
│       └── api/
│
├── transform/                         # SQLMesh project
│   ├── config.py
│   └── models/
│       ├── staging/
│       ├── marts/
│       └── quarantine/
│
├── dagster_defs/
│   ├── __init__.py
│   ├── assets/                        # one module per stage group, mirrors src/
│   │   ├── acquisition.py
│   │   ├── parsing.py
│   │   ├── extraction.py
│   │   ├── features.py
│   │   └── models.py
│   ├── checks/                        # asset checks — accounting identities, leakage guard
│   ├── partitions.py
│   ├── schedules.py
│   ├── sensors.py
│   └── definitions.py                 # Dagster Definitions entry point
│
├── notebooks/                         # marimo, .py format only
│   └── exploration/
│
├── app/                                # Streamlit internal explorer
│   ├── Home.py
│   └── pages/
│
├── site/                               # Evidence public dashboard
│   ├── pages/
│   └── sources/
│
├── report/                             # Quarto methodology report
│   └── methodology.qmd
│
└── tests/
    ├── acquisition/
    ├── parsing/
    ├── extraction/
    ├── features/
    │   └── test_leakage.py             # §9.1 of AGENT_SPEC.md — blocking
    ├── models/
    ├── fixtures/                       # golden XML/PDF documents for mapping tests
    └── conftest.py
```

---

## 2. `src/distress_radar/` in detail

```
src/distress_radar/
├── acquisition/
│   ├── base.py                 # shared rate limiter, hishel cache config, tenacity policy
│   ├── models.py                # Pydantic models for raw source responses
│   ├── universe_discovery.py    # A1
│   ├── regon_client.py          # A2 — zeep SOAP wrapper, session/token handling
│   ├── document_retrieval.py    # A3
│   ├── legal_events.py          # A4
│   └── reference_data.py        # A5
│
├── parsing/
│   ├── version_detection.py     # C1
│   ├── xsd_validation.py        # C1
│   ├── canonical_schema.py      # typed canonical chart, mirrors config/mappings/canonical_chart.yaml
│   ├── mapping_engine.py        # C2 — reads config/mappings/structures/*.yaml
│   └── pdf/
│       ├── router.py            # C3 tier selection
│       ├── pymupdf_extractor.py
│       ├── docling_extractor.py
│       └── vision_llm_extractor.py
│
├── extraction/
│   ├── preprocessing.py         # G1 — spaCy + keyword prefilter
│   ├── schemas.py                # G2 — extraction response models
│   ├── extractor.py              # G2 — constrained LLM call
│   └── eval_harness.py           # G3 — scores against evals/text_signals/
│
├── features/
│   ├── asof_assembly.py          # H1
│   └── feature_definitions.py    # one function per feature family, §6H
│
├── models/
│   ├── baselines.py               # Altman, Polish discriminant models
│   ├── classical.py               # logistic regression
│   ├── gbm.py                     # LightGBM
│   ├── survival.py                # scikit-survival
│   ├── calibration.py
│   └── registry.py                # MLflow logging wrapper
│
└── api/
    ├── main.py
    ├── routes/
    │   ├── companies.py
    │   ├── scores.py
    │   └── watchlists.py
    ├── schemas.py
    └── db.py
```

Subfolder names match the stage groups in `AGENT_SPEC.md` §6 exactly (acquisition = A, parsing = C, extraction = G, features = H, models = I, api = J). This is intentional and must be preserved: an agent implementing a stage should never need to guess which folder it belongs in.

---

## 3. Placement rules

Use this table before creating any new file. If a file doesn't clearly fit one row, stop and ask rather than guessing.

| If the file... | Goes in | Not in |
|---|---|---|
| Calls an external HTTP/SOAP source | `src/distress_radar/acquisition/` | `dagster_defs/` |
| Parses or validates a document format | `src/distress_radar/parsing/` | `transform/` |
| Calls an LLM or NLP model | `src/distress_radar/extraction/` | `dagster_defs/` |
| Computes a feature from canonical data | `src/distress_radar/features/` | `transform/` |
| Trains, calibrates, or registers a model | `src/distress_radar/models/` | `dagster_defs/` |
| Serves an HTTP endpoint | `src/distress_radar/api/` | `app/` |
| Declares a Dagster `@asset`, `@asset_check`, schedule, or sensor | `dagster_defs/` | `src/` |
| Is a SQL transformation on already-canonical data | `transform/` | `src/distress_radar/parsing/` |
| Is a mapping table, threshold, or taxonomy that changes by legislation, not by engineering | `config/` | hardcoded in `src/` |
| Is a Streamlit page | `app/` | `site/` |
| Is a public static chart/table definition | `site/` | `app/` |
| Is exploratory and not part of the production graph | `notebooks/` | `src/` |
| Is a hand-labelled evaluation example | `evals/` | `tests/fixtures/` |
| Is a golden document used to test parsing logic | `tests/fixtures/` | `evals/` |
| Records why a tool or pattern was chosen | `docs/adr/` | code comments |
| Is a versioned LLM prompt | `prompts/` | inline in `src/distress_radar/extraction/` |

### `AGENTS.md` and `CLAUDE.md`

`CLAUDE.md` at the repo root is the single agent entry point: invariants, locked stack, directory map, domain vocabulary. `AGENTS.md` exists only as a one-line pointer to it, so tools that look for that filename still resolve. Never duplicate content between them — if both describe the same rule and they drift, agents get contradictory instructions.

### The core boundary

`src/` holds framework-agnostic logic — importable and testable without Dagster, SQLMesh, Streamlit, or Evidence running. `dagster_defs/` holds only orchestration wiring: it imports functions from `src/` and wraps them as assets, checks, schedules, and sensors. If a function only makes sense when Dagster is running, it belongs in `dagster_defs/`, not `src/`. If it can be called from a plain Python script or a test with no orchestrator present, it belongs in `src/`.

`config/` holds data, not code. The test for whether something belongs in `config/` rather than `src/`: does it change because a law or regulation changed, or because engineering logic changed? Statutory thresholds, KSH ratios, XML structure mappings, and the PKD crosswalk all change on legislative or Ministry timelines — they are YAML, reviewable in a diff, never Python constants.

---

## 4. `src/` layout requirement

`src/distress_radar/` uses the **src layout**, not the flat layout. The package lives under `src/`, not at the repo root next to `pyproject.toml`.

This is a functional requirement, not a style preference: it prevents the package from being importable via the working-directory path trick that Python applies by default. Without `src/`, running `pytest` from the repo root can silently succeed by importing the raw source tree even if the package was never actually installed — hiding real packaging bugs (a file missing from the manifest, a broken entry point, a bad dependency declaration). With `src/`, the package is only importable after `uv pip install -e .`, so tests and agent-run scripts are forced through the same install path a real deployment uses.

`pyproject.toml` must declare:

```toml
[tool.uv]
package = true

[tool.hatch.build.targets.wheel]
packages = ["src/distress_radar"]
```

Import as `from distress_radar.acquisition import regon_client`, never via relative path manipulation or `sys.path` edits.

---

## 5. Naming conventions

- **Modules:** `snake_case.py`, named after what they do, not the tool they use — `document_retrieval.py`, not `httpx_client.py`.
- **Stage prefixes in comments, not filenames:** reference `# A3` or `# implements stage C2` inside docstrings/comments so the mapping to `AGENT_SPEC.md` is traceable, but don't put stage codes in filenames themselves.
- **Config files:** match the thing they configure — `size_thresholds.yaml`, not `config1.yaml`.
- **XML structure mapping files:** `<form>-<year>-v<n>.yaml`, e.g. `small-2018-v1.yaml`, `full-2026-v1.yaml`. The filename must be independently sufficient to identify which Ministry structure it maps.
- **ADRs:** `docs/adr/NNNN-short-title.md`, sequential, never renumbered or deleted after merge — superseded ADRs are marked superseded in their own text, not removed.
- **Eval sets:** `evals/text_signals/<signal_type>.jsonl`, matching the `signal_type` enum in `AGENT_SPEC.md` §5 exactly — `going_concern_uncertainty.jsonl`, not `going_concern.jsonl`.
- **Prompts:** `prompts/extraction/<signal_type>_v<n>.md`, using the same `signal_type` enum so prompt and eval set pair unambiguously.
- **Tests:** mirror the `src/` path — `tests/parsing/test_mapping_engine.py` tests `src/distress_radar/parsing/mapping_engine.py`.

---

## 6. Rules for adding new files

1. **Check §3 first.** If the file's purpose maps cleanly to an existing row, place it there without asking.
2. **New stage submodules** (e.g. a new PDF extraction tier) go inside the existing stage folder, not in a new top-level directory.
3. **New top-level directories require an ADR.** Write `docs/adr/NNNN-add-<name>-directory.md` explaining what it holds and why none of the existing directories fit, before creating it.
4. **Never create a second location for the same kind of thing.** If a mapping table already lives in `config/mappings/`, a new one of the same kind goes there too — do not start a parallel `mappings/` folder inside `src/`.
5. **Notebooks never get imported.** If exploratory code in `notebooks/` proves useful, promote it by rewriting it as a proper module in `src/`, not by importing the notebook.
6. **Generated artifacts are never committed** except: golden fixtures (`tests/fixtures/`), golden eval sets (`evals/`), and rendered ADRs/specs (`docs/`). Model weights, MLflow run data, Parquet outputs, and rendered site builds are gitignored.
