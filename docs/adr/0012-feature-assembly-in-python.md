# 0012 — Feature assembly runs in Python with in-process DuckDB, not in SQLMesh

- **Status:** accepted (2026-09-24, by the owner, with plan 0010's owner decisions)
- **Date:** 2026-09-24

## Context

Plan 0010 (Phase 5) builds `feature_store` (stage H). The documents disagreed on where feature assembly runs:
- `TECHNICAL_ARCHITECTURE.md` §H1 picked "DuckDB `ASOF JOIN` inside SQLMesh incremental models";
- `DIRECTORY_STRUCTURE.md` §3 puts a file that "computes a feature from canonical data" in
  `src/distress_radar/features/`, "not in `transform/`", and lists `asof_assembly.py` and `feature_definitions.py`
  there.

ADR 0010 decision 6 also left one question for this phase: the bitemporal snapshot layer that AGENT_SPEC §6F
describes as F's output for H (Parquet partitioned by `fiscal_year` and `as_of_month`, with `valid_from`,
`valid_to`, `known_from` and content-hashed snapshot manifests). It was deferred "until the feature store can say
what shape it needs".

Three forces decide it:
- **The leakage test is blocking and runs in `make check` with no services** (AGENT_SPEC §9.1, CLAUDE.md). It
  must exercise the real assembly code, including a deliberately leaky variant that has to fail it (plan 0010
  step F). Features built inside SQLMesh models would force that test either through SQLMesh's own YAML tests,
  which cannot express "recompute with every later fact deleted and compare", or through a SQLMesh context
  started from pytest.
- **Feature definitions are driven by config** (`config/features/`, `config/statutory/`): a line-item map per
  income-statement variant, dated tripwire ratios, filing deadlines with COVID-era extensions, and the
  `include_quarantined_statements` switch. Generating SQL from that config is possible, but the result is harder
  to test and review than Python functions reading the same config.
- **The ASOF join itself does not need SQLMesh.** DuckDB's `ASOF JOIN` runs the same from Python, over the same
  Parquet.

## Decision

1. **Features are computed in `src/distress_radar/features/`.** Python functions read the canonical Parquet
   datasets and the Postgres manifest, run DuckDB in-process for the ASOF joins, and use Polars where a step is
   awkward in SQL. `asof_assembly.py` builds the grid and assembles the dataset; `feature_definitions.py` holds one
   function per feature family; `panel.py` builds the point-in-time financial panel. `dagster_defs/` only wraps
   them as the `feature_store` asset and its checks.
2. **`feature_store` is a Python output, like `financial_statements_canonical`.** It is Parquet under
   `WAREHOUSE_DIR/feature_store/` (ADR 0008), with a Pandera contract. SQLMesh reads it through an `ext` view for
   coverage marts only, and never writes it (the boundary of ADR 0010 decision 1).
3. **SQLMesh keeps what it already owns:** `quarantine`, `dq_mart`, and the label and coverage models. It does not
   gain a feature layer.
4. **No separate bitemporal snapshot layer is built** (closes ADR 0010 decision 6). The canonical tables already
   carry both time axes of §4.7: `period_start` / `period_end` are the fiscal period (`valid_from` / `valid_to`),
   and `known_from` is the filing date. Assembly ASOF-joins on `known_from` directly over them. A second copy
   partitioned by `as_of_month` would duplicate the canonical data once per month, and it would give the leakage
   test a second place where a late fact could slip in. The `feature_store` grid already is the monthly as-of
   view. Content-hashed manifests, the data snapshot hash that every MLflow run must log, are Phase 6's concern and
   are designed there.

## Consequences

- `make check` runs the leakage test against the real assembly code on a synthetic warehouse, with nothing
  running. The same assertion runs on the live store as a Dagster asset check.
- **SQLMesh's incremental restatement is not used for features.** A late filing for an old period changes
  features for every `as_of_date` after its `known_from`. SQLMesh would have rebuilt just those intervals.
  `feature_store` is recomputed in full on each run instead (plan 0010 decision 8). At the seed's scale, and for
  the v1 universe of a few thousand entities, a full rebuild is seconds to minutes. If it ever stops being cheap,
  the fix is to partition the rebuild by `as_of_date` year in Python, not to move features into SQLMesh.
- `TECHNICAL_ARCHITECTURE.md` §H1 and §F3, and AGENT_SPEC §6F and §6H1, are corrected to match. The `ASOF JOIN`
  pick itself stands.
- The features that need SQL-shaped aggregation over the whole universe, sector and macro context (A5), are
  deferred (plan 0010 owner decision 5). When they arrive, they can be SQLMesh models that the Python assembly
  reads through the warehouse, on the same terms as any other input with a `known_from`.
