# 0015 — Phase 8: gradient boosting, survival, calibration and explanations

**Stage:** Phase 8 (AGENT_SPEC §10); spec stage I (§6I), generation 3: LightGBM and discrete-time survival with
censoring, Optuna tuning, SHAP explanations, and the champion rule.
- **Domain rules:** out-of-time evaluation only, on Phase 6's purged and settled folds; calibration is the
  primary metric, Brier score beside AUC, never AUC alone; never impute (invariant 4: LightGBM's native
  missing-value handling is the reason it is in the stack); every run logs the four identifiers.
- **Inputs:** `feature_store` on the latest feature set (`feature_set_v6`, plan 0013), a label set rebuilt over
  the seed and list v1 (decision 0), and Phase 6's harness: `models/dataset.py`, `splits.py`, `evaluation.py`,
  `report.py`, `registry.py`.
- **ADRs:** 0012 (feature assembly), 0013 and 0014 (where the universe comes from, and why its rates are not
  the population's).

**Order:** after plan 0012 (complete) and plan 0013 (every step built; its close-out waits on labelling, which
this plan does not need). It does not wait for list v2 (ADR 0014 addendum, proposed): weighting is plan
0014's item 4, and this plan leaves a place for it (decision 9).

## Status: active (2026-10-09): owner decisions 0–9 accepted as recommended; nothing built yet, step A next

## Why

Phase 6 built the harness and the first two generations, and proved the machinery: purged, settled,
out-of-time folds; no imputation; calibration beside discrimination; four identifiers; byte-reproducible
reports. Its regression could only use complete cases on four ratios, which on simplified and micro forms
leaves most rows out. Phase 7 added text, disclosure and auditor features that are null wherever the source
is absent, by design. Generation 3 is the model that can read all of it as it is: nulls kept as nulls, the
censored rows Phase 6 had to leave out used by a survival model, and each score explained.

## What there is to build on (2026-10-09)

- **The harness** (plan 0012, `backtest_v4`): expanding-window folds 2020–2025 at 12 and 24 months, training
  rows purged and settled (the 2026-09-29 correction), `min_events` 3, entity bootstrap, reliability tables,
  `main` and `no_regime` runs, one MLflow run per model, horizon and run.
- **The features** (`feature_set_v6`): the financial, construction, tripwire, filing-behaviour, registry and
  legal-history families, plus the statement's disclosures, the notes' text signals and the auditor reports'.
  Many are null by construction: a micro form has no cash flow, an unaudited company no opinion.
- **The universe beyond the seed** (plan 0014, 2026-10-09): list v1, 592 companies resolved, 190 imported, 186
  with a parsed statement; KRS extracts, MSiG notices and `legal_events` built over all of them. ADR 0014's
  census: **20 of the 592 have a distress event; 8 of those are imported, and 6 have a statement public before
  their first event.** 369 companies are not yet searched on RDF.
- **Not yet rebuilt over list v1:** the label set (the frozen `066d18bbd4cd…` is the seed's), `feature_store`,
  and the text job. Step A does it.
- **Not installed:** `lightgbm`, `scikit-survival`, `optuna`, `shap`.

## The constraint that decides what this phase can show

**About a dozen usable events is still noise.** The seed's events that clear the purge, plus list v1's six,
are too few for any AUC, Brier score or SHAP ranking to mean more than the machinery working: moving one event
between years moves a result more than any modelling choice. And both lists are purposive (the seed by
distress hints, list v1 by hand along axes that include distress status), so no probability from them is
calibrated for the population (ADR 0014). Re-searching list v1's 369 adds events; list v2, drawn by a written
rule and weighted, is the first universe whose rates mean something.

So Phase 8, like Phase 6, proves the **machinery**, and every number it prints says so, with its event count.
Two things follow:

- **Tuning is built, and gated.** Optuna on a dozen events fits the noise. The search runs only on folds whose
  inner validation holds enough events (decision 2); below that, the configured defaults are used and the
  report says so.
- **No champion is crowned on this data.** The rule is written and tested now (decision 7), so promotion is a
  check, not a judgement, when the data can pass it.

## Owner decisions (accepted as recommended, 2026-10-09)

0. **The data: rebuild the labels and features over the seed and list v1 now.** A new label set (same label
   version, new hash, frozen like the seed's) and `feature_store` rebuilt over both lists; the text job run
   over list v1's statements and reports first, so the text families are not null merely because the job has
   not run. Pinned in a new `backtest_v5`. Every table reports its events per list (seed, `rejestr_io_v1`),
   and the caveat names both as purposive.
   *Recommended:* yes. *Alternative:* build on the seed alone and wait for list v2; this tests nothing the
   seed has not, and leaves the null-heavy features (the reason for LightGBM) on 17 entities.
   *Cost:* the text job over list v1 calls the model API for the notes and reports of ~190 companies (the
   provider's terms are confirmed, plan 0013 decision 2); its cost is estimated and shown before the run.

1. **LightGBM: one binary model per horizon, conservative fixed defaults, no class weighting.** The same target
   and folds as generation 2 (distress against `alive`, censored rows out), on every feature of the pinned
   feature set, nulls kept (`use_missing` on, `zero_as_missing` off). Defaults committed in `backtest_v5` before
   the first run: few leaves (`num_leaves` 7), `min_data_in_leaf` high relative to the events (20),
   `learning_rate` 0.05, a fixed number of rounds (200), bagging off, a fixed seed, deterministic mode, single
   thread (byte-reproducible). No class weighting, as in generation 2: it would distort the probabilities the
   Brier score judges.
   - *Feature selection:* none by hand. Identifiers, dates, `*__known_from` columns and `regime_flag` are never
     inputs (a test, as in Phase 6); every other column of the feature set is.

2. **Optuna: built, run only where an inner out-of-time split holds `min_tuning_events`.** Within a fold's
   training rows, the last settled training year is the inner validation year; the search fits on the years
   before it and scores the inner year's Brier score (never the test year's). `min_tuning_events` (recommended
   30 distinct events in the inner validation year) gates it: below, the defaults of decision 1 are used,
   counted and reported. Search space, trial count (50) and sampler seed in `backtest_v5`, fixed before the
   first run. On today's data no fold clears the gate; the search is tested on synthetic data.

3. **Calibration: Platt scaling on the inner validation year, reported beside the raw score.** Boosted trees
   are not calibrated by construction. Each fold fits a one-variable logistic map from the model's score to
   probability on the inner validation year (the same rows decision 2 uses), never on the test year, as
   generation 1's scores are mapped today. Isotonic regression is rejected for now: it needs many events per
   bin. Both the raw and the calibrated model are scored; the calibrated one is the model's result. A fold
   whose inner year holds fewer than `min_events` events keeps the raw score, and the report says so.

4. **Survival: a discrete-time hazard model with censoring, in LightGBM; `scikit-survival` for its metrics.**
   AGENT_SPEC names "discrete-time survival with censoring". Each labelled `as_of_date` becomes person-period
   rows by month up to 24 months: one row per month at risk, the target whether the event fell in that month,
   the rows stopping at the event or the censoring time. The probability of distress within 12 and 24 months
   is one minus the product of the monthly survival, so it is scored on the same folds and by the same Brier
   score as the binary models. It uses the censored rows the binary models leave out, which is its point.
   - **Event and censoring times come from the frozen label set alone** (`as_of_date`, `event_date` or its
     `event_known_from` dating, `censored`, `cutoff_date`), under the label version's own rules, so the
     survival target cannot disagree with the binary one. A row's censoring time is the label version's
     settled horizon: `cutoff_date` less the `alive` lag allowance where the label rules apply it (plan 0009).
   - **Why not `scikit-survival`'s own models:** its Cox and gradient-boosted survival models need complete
     inputs, which here would mean imputation. Whether its survival trees accept missing values in the
     installed version is checked in step A; if they do, a random survival forest joins as a comparison.
   - `scikit-survival` supplies the time-dependent metrics: concordance (IPCW) and the integrated Brier score,
     reported beside the 12- and 24-month Brier scores.

5. **SHAP: global importance in the report, per-row values in the local warehouse only.** `TreeExplainer` on
   each fold's fitted model. The report gives each fold's mean absolute SHAP per feature (top 15, and the rest
   summed), which names no company. Per-row SHAP values, which explain a named company's score, are written to
   `WAREHOUSE_DIR/model_explanations/` (local, gitignored, like every warehouse output) for the internal app
   later, never into the report, MLflow artifacts or anything published (AGENT_SPEC §6K: company-level
   output is local only). With a dozen events the rankings are anecdotes; the report says so above them.

6. **An entity-disjoint sensitivity run, now that there are ~200 entities.** Plan 0012 decision 2 deferred it
   to scale. Beside `main` and `no_regime`, a run whose test rows are only entities never seen in that fold's
   training rows. It shows how much of a result comes from recognising a company rather than reading its
   state. Below `min_events` it prints its reasons like any cell.

7. **The champion rule, written now, applied when it can pass.** A challenger is promoted over the incumbent
   (initially generation 2's regression, the only model fitted on every fold) only if, at each horizon:
   - its pooled Brier score is lower, with the 95% entity-bootstrap interval of the **paired** difference
     entirely below zero;
   - its AUC is not lower by more than 0.02;
   - the comparison holds in the `no_regime` run too;
   - the pooled cells rest on at least `min_promotion_events` distinct events (recommended 50).
   The rule is code (`models/champion.py`), tested on synthetic results; promotion sets the MLflow registry
   alias `champion` on the registered model version and logs the comparison as an artifact. On today's data
   it reports "not evaluable" and promotes nothing.

8. **Feature set and backtest versions.** No new features in this plan: generation 3 reads `feature_set_v6` as
   it is (a new feature is a new feature set, outside this plan). `backtest_v5` pins the new label set,
   `feature_set_v6`, decisions 1–4 and 7's values, and is committed before the first run, as plan 0012
   decision 5 requires.

9. **Weights: an interface now, used with list v2.** The model functions take an optional per-row sample weight
   (ADR 0014 addendum's 1/π), and the evaluation an optional weighted Brier score; both unused until list v2 is
   drawn (plan 0014, item 4). No weight is invented for the seed or list v1.

## Out of scope

- Per-class models (bankruptcy, restructuring, liquidation apart): wait for events.
- New features; size class (waits on employment, AGENT_SPEC §4.4).
- Serving the champion, the Quarto report, drift monitoring (Phase 9 and later).
- List v2's draw and the weighted results (plan 0014, items 4 and 5).
- Re-searching list v1's 369 companies on RDF (the owner's script); this plan reruns on whatever is imported.

## Steps

### A. Data and dependencies

- `lightgbm`, `scikit-survival`, `optuna`, `shap` in `pyproject.toml` (`make lock`); check that each installs
  on the project's Python, that LightGBM's deterministic mode gives identical bytes twice, and whether the
  installed `scikit-survival` accepts missing values in its survival trees (decision 4).
- Rebuild over the seed and list v1 (decision 0): the text job (cost estimate first), `label_models` and
  `outcome_labels` (a new frozen label set), `feature_store` on `feature_set_v6` with its `leakage` check.
  Record the fold table of the new label set (rows, events by class and by list, unsettled rows) in Progress
  before any model runs.

### B. The configuration

`config/models/backtest_v5.yaml`: the inputs (decision 0), LightGBM's defaults (decision 1), the tuning gate,
space and trials (decision 2), the calibration rule (decision 3), the survival horizon and period (decision 4),
`min_promotion_events` and the promotion margins (decision 7). The loader rejects a feature the set lacks and a
key it does not know. Committed before the first run.

### C. Generation 3: LightGBM

`src/distress_radar/models/gbm.py`: fit and predict per fold on the full feature matrix with nulls, the inner
validation year, the gated Optuna search and the Platt mapping; returns the same `FoldPredictions` the other
generations do, so `backtest.py` runs it without a special case.

### D. Survival

`src/distress_radar/models/survival.py`: the person-period expansion from the label set (decision 4), the
discrete-time hazard model, the 12- and 24-month probabilities, and the `scikit-survival` metrics. Its
predictions enter the same cells as the binary models'.

### E. Explanations

`src/distress_radar/models/explain.py`: per-fold SHAP; the global table for the report; per-row values to
`WAREHOUSE_DIR/model_explanations/` (decision 5).

### F. The report, the sensitivity run and the champion rule

`report.py` gains generation 3's rows, raw and calibrated, the survival metrics, the importance tables and the
entity-disjoint run (decision 6); `models/champion.py` (decision 7) runs after the backtest and writes its
verdict into the report.

### G. Tracking and wiring

`registry.py` logs the new models' runs with the four identifiers, Optuna's trials as a child run's
parameters, and registers a model version per horizon; the `backtest` asset runs all three generations from
`BACKTEST_VERSION`.

### H. Docs

AGENT_SPEC §6I as built (generation 3, the champion rule); README (how to run, what the report holds);
DIRECTORY_STRUCTURE (the new modules, `model_explanations/`); TECHNICAL_ARCHITECTURE if a choice differs from
it; `docs/data_inventory.md`; this plan's status.

## Tests

- **No imputation:** LightGBM receives the nulls the feature store holds; a test feeds a matrix with nulls and
  checks none is filled before the fit.
- **No leak:** `regime_flag`, identifiers, dates and `*__known_from` columns are never inputs, for every
  generation; the purging and settling tests run on generation 3's folds unchanged (blocking).
- **Tuning and calibration never see the test year:** the inner validation year is strictly before it and
  settled by its eve; a test fails if any tuning or calibration row is a test row.
- **Survival target:** on hand-built labels, the person-period rows stop at the event or the censoring time,
  and the 12- and 24-month probabilities equal one minus the product of the monthly survival; a censored row
  never contributes an event.
- **Determinism:** two runs give identical predictions, report bytes and SHAP tables.
- **Champion rule:** synthetic results that pass, fail on the interval, fail on AUC, fail in `no_regime`, and
  fall below `min_promotion_events`, each giving the expected verdict; nothing is promoted on a failing verdict.
- **Explanations stay local:** the report and MLflow artifacts carry no KRS number or per-row SHAP value.

## Definition of done

- [x] Owner decisions 0–9 made (2026-10-09, as recommended).
- [ ] Labels and features rebuilt over the seed and list v1, the fold table recorded before any model runs.
- [ ] `backtest_v5` committed before the first run.
- [ ] LightGBM, calibrated, and the discrete-time survival model evaluated on every fold, both horizons, the
      `main`, `no_regime` and entity-disjoint runs; Brier score beside AUC; survival metrics.
- [ ] Optuna gated as decided, its outcome per fold reported (run, or defaults with the reason).
- [ ] SHAP importance in the report; per-row values local only.
- [ ] The champion rule implemented and tested; its verdict in the report.
- [ ] Every run in MLflow with its four identifiers; the report byte-reproducible.
- [ ] `make check` and `make test-integration` green; docs from step H updated.

## Risks

- **A richer model on a dozen events looks better than it is.** LightGBM can fit a handful of events exactly.
  The caveat and event counts are generated into every table, the champion rule refuses below its floor, and
  the entity-disjoint run shows how much is recognition.
- **The label lag near the cutoff** (plan 0009) shortens the latest folds' training data; the survival model's
  censoring follows the label version's settled horizon, not the calendar.
- **The purposive lists set the base rate.** Both lists over-represent distress by construction; calibration
  numbers are about the lists, and say so. Only weighted results on list v2 speak for the segment.
- **Text features on list v1 depend on the text job having run.** A feature null because the job did not run
  is indistinguishable, to a model, from one null because the source has no text; step A runs it first, and
  `text_coverage` tells the two apart for the report.
- **Dependency weight:** `shap` and `scikit-survival` pull in numba and a pinned scikit-learn range; step A
  checks they resolve with the locked stack before anything is built on them.

## After Phase 8

Phase 9 serves the champion in-process from the MLflow registry (AGENT_SPEC §6J) and writes the Quarto report.
What any of it says about construction companies in Poland waits on list v2: a written draw, weighted
results, and a base rate that is the segment's.
