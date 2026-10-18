# 0021 — Whole-chain reproducibility, and three Phase 10 extras: driver movement, score shift, score intervals

**Stage:** F/H/L (AGENT_SPEC §2 invariant 5, §6L) and J/K (§6J, §6K: what a score shows). Two items of "what can
be built in the meantime" (owner, 2026-10-18):
- **The definition of done's last line** (PROJECT_OVERVIEW): "Every stage can be re-run independently and
  reproduces identical outputs from the same inputs". Each stage is tested on its own, but the chain never has
  been, as a whole and on the real stores.
- **What Phase 10 left out** (PROJECT_OVERVIEW stages 11 and 12, as built): how a score's drivers moved since the
  previous score, a check that the score distribution has shifted, and confidence ranges on scores.

- **Domain rules:** re-running any stage on the same input reproduces the same output exactly (invariant 5).
  A score's interval and its drivers' movement use only what the score itself used (invariant 1), and stay
  local, like every score (§6K). A score's interval is the model's, from the lists it was trained on (ADR
  0014): never a statement about the segment.
- **Inputs:** the offline assets and their stores (`WAREHOUSE_DIR`, Postgres, MinIO); `scores_history`; the
  served version (MLflow) and its fold's training rows, rebuilt as the drift report rebuilds them (plan 0018 step
  F).
- **ADRs:** 0006 (plain idempotent DDL), 0008 (reports under `WAREHOUSE_DIR`), 0014.

**Order:** after plan 0020. It waits on nothing.

## Status: complete (2026-10-18): asked for by the owner; the decisions below are this plan's own, taken while writing it (flag any to change); steps A–E built; `make reproduce` run once: every output reproduced; `score_intervals` run on the stored scores: 484 intervals (§ "Progress")

## Decisions this plan makes (flag any you disagree with)

1. **Reproducibility is a run, not a unit test.** `make reproduce` (`dagster_defs/reproduce.py`) copies
   `WAREHOUSE_DIR` twice into a scratch directory, re-materializes the offline chain into each copy from the same
   stored inputs, and compares each dataset's content digest between the two runs. It also compares each run's
   output with the stored warehouse, which tells which stored datasets are older than their inputs. That is a
   finding, not a failure. The chain: `financial_statements_canonical` (with `statement_disclosures` and
   `identity_check_results`), `restatement_events`, `entity_size_class_history`, `legal_events`, the label
   models and `outcome_labels`, and `feature_store`. Not included: `text` (it replays stored model responses,
   but takes an hour), and the jobs that write insert-only or external state (`backtest`, `scoring`,
   `publish`). It needs `make dev-up` and refuses to start while a Dagster run is in progress. Its Postgres
   writes are those of any re-run, all idempotent (`ON CONFLICT DO NOTHING`, upserts).
2. **A digest independent of file layout:** the dataset read whole, its columns in name order, its rows hashed
   (`hash_rows`, fixed seeds) and sorted, then the SHA-256 of those hashes and the schema. Two runs that write the
   same rows into different files or row groups have the same digest.
3. **Driver movement is computed when a score is read, not stored.** For a score and the previous score of
   the same entity, horizon and model version, each driver's contribution then and now, and its change. A new
   model version has no previous score of its own: the movement is then "model changed", never a comparison
   across versions. The API (`GET /scores/{krs}`) and the explorer's company page show it. Nothing about
   `scores_history` changes. Until a second month-end is scored (the first was 2026-09-30), every score has none.
4. **The score-shift report:** in the monitoring job, per horizon and served version, the latest scored
   month-end's probabilities against the previous one's: rows, mean, the quartiles, the share above each alert
   threshold, the population stability index (10 bins on the previous month's deciles), and the KS statistic.
   Local only (`WAREHOUSE_DIR/reports/score_shift/`), like the drift report, and never a gate: a shift is read,
   and a retraining recommendation stays a decision (plan 0018, out of scope). With one month-end so far it says
   "not evaluable".
5. **Score intervals by entity bootstrap of the served regression.** The served version's fold's training rows
   are rebuilt (as the drift report does), entities are resampled with replacement (200 replicates, a fixed
   seed), the same rank-logistic model is refitted on each, and the scored rows are scored by each refit. The 5th
   and 95th percentiles are the interval: **90%**, for estimation uncertainty only. They go in a new Postgres
   table `score_intervals`, keyed like `scores_history`, so existing scores keep their rows untouched and can be
   given intervals afterwards. For a served LightGBM or survival champion (none yet), no interval is computed,
   and the reason is stored. The API and the explorer show the range beside the probability, with the caveat.

## Progress (2026-10-18)

- **`make reproduce`, first run (13:46 UTC, 2 × about 18 minutes, one step at a time):** every output of the
  chain reproduced. `financial_statements_canonical`, `statement_disclosures`, `identity_check_results`,
  `restatement_events`, `entity_size_class_history`, `legal_events`, `feature_store` and the three label marts
  each had the same digest in both runs. Against the warehouse as stored, everything was current except
  `marts.outcome_label_exclusions`, built on 2026-10-16 before list v2's distress group entered `entity_master`.
  Its frozen label set, `marts.outcome_labels`, was current. Report:
  `WAREHOUSE_DIR/reports/reproducibility/2026-10-18.md`. A first attempt ran WSL out of memory in its second run,
  with Dagster's parallel steps and other work at the same time. The runner now runs one step at a time.
- **Score intervals, dry run on the served versions (2026-10-18):** the refit reproduces both versions'
  coefficients. 200 draws take 3 to 4 seconds per horizon, and all 242 scored companies' probabilities lie
  inside their intervals at each horizon. The intervals are wide: the median upper bound is 15 (12 months) to 18
  (24 months) times the lower one, which is what about 15 training events give. Median widths are 1.3 and 3.1
  percentage points around medians of 0.5% and 1.4%.
- **`score_intervals`, first run (after commit `aca7315`, 2026-10-18):** 484 intervals, the 242 scored companies
  at each horizon, all with bounds; mean width 2.5 percentage points (12 months) and 5.4 (24 months).
- **Score shift:** materialized once: "no earlier month-end of this version" at both horizons, since 2026-09-30
  is the only scored month-end.
- **Driver movement:** in the API (`driver_movement`, `previous_as_of_date` on every score) and the explorer's
  company page. None yet, for the same reason.
- **Tests:** `tests/test_reproducibility.py` (5), `tests/models/test_intervals_drivers.py` (8),
  `tests/monitoring/test_score_shift.py` (6), one more API test, and two explorer tests.

## Steps

### A. `make reproduce` (decisions 1, 2)

- `src/distress_radar/reproducibility.py`: `dataset_digest(frame)` and `warehouse_digests(warehouse_dir)` (each
  chain dataset, the latest label set, and the label marts in `transform.duckdb`). Unit tests: the same rows in
  another order or file layout give the same digest; one changed value changes it.
- `dagster_defs/reproduce.py`: the runner. Copies, the two runs (`dg.materialize` of the chain's assets, with
  `WAREHOUSE_DIR` pointed at each copy), the comparison, and a report at
  `WAREHOUSE_DIR/reports/reproducibility/<date>.md`. It exits non-zero when the two runs differ.
- `make reproduce` in the Makefile. Run once and record the result here.

### B. Driver movement (decision 3)

- `models/drivers.py`: `driver_movement(current, previous)` over two `top_drivers` lists. The API's score
  response gains `driver_movement`, and the explorer's company page a "since the last score" column.

### C. Score-shift report (decision 4)

- `monitoring/score_shift.py`, and an asset `score_shift_report` in the monitoring job, after
  `drift_report`.

### D. Score intervals (decision 5)

- `models/intervals.py`: the bootstrap, pure. `scores_store`: `score_intervals` DDL and insert. The scoring
  asset computes intervals for the rows it scores. The asset `score_intervals_backfill` (by hand) computes them
  for scores that have none.
- The API's score response and the explorer show `probability_low` and `probability_high`.

### E. Docs sweep

- PROJECT_OVERVIEW (the definition of done; stages 11 and 12 as built), AGENT_SPEC §5 (`score_intervals`),
  DIRECTORY_STRUCTURE, README, plan 0018's "not built" lines.

## Definition of done

- [x] Step A built, and `make reproduce` run once with its result recorded here (2026-10-18: every output reproduced).
- [x] Steps B–D built, with their tests in `make check` (2026-10-18).
- [x] Docs swept (step E, 2026-10-18).

## Out of scope

- A retraining recommendation, and any automatic action on a shift or drift (plan 0018).
- Intervals for gradient boosting or survival models.
- Publishing intervals or shifts: scores are local only.
