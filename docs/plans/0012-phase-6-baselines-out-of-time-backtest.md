# 0012 — Phase 6: baseline and classical models, out-of-time backtest

**Stage:** Phase 6 (AGENT_SPEC §10: "Baseline and classical models, out-of-time backtest report").
- **Spec stages:** I (§6I), generations 1 and 2: Altman-type and Polish discriminant baselines
  (`statsmodels`), regularised logistic regression (`scikit-learn`); MLflow tracking (I4).
- **Domain rules:** out-of-time evaluation only; Brier score beside AUC, never AUC alone; never impute
  (invariant 4, §6I); every run logs four identifiers (CLAUDE.md, §6I).
- **Inputs:** `feature_store` (plan 0010) and the frozen label set `066d18bbd4cd…` (plan 0009).

**Order:** after plans 0010 and 0011, which are complete. Phase 7 (text signals) and Phase 8 (LightGBM,
survival, SHAP) build on the harness this plan makes.

## Status: active (2026-09-27); steps A–D built, step E next

### Progress

- **Step A (2026-09-27):** `statsmodels`, `scikit-learn`, `mlflow` locked; `MLFLOW_TRACKING_URI` and
  `MLFLOW_ARTIFACT_DIR` default to a SQLite store and artifacts under `.data/mlflow/`.
- **Step B (2026-09-27), what the sources allowed:**
  - **Configured:** Altman's Z'' (Altman 2000, p. 27) and the Poznań model (Hamrol, Czajka,
    Piechocki 2004, *Przegląd Organizacji* 6/2004, p. 38). Both documents were read, and their
    SHA-256 is recorded with the coefficients.
  - **Not configured:** Mączyńska (*Życie Gospodarcze* 38/1994) and Hołda (*Rachunkowość* 5/2001)
    exist only in print; their coefficients circulate in secondary sources, which decision 4 rules
    out. They wait until someone obtains the publications. Decision 4 asked for two Polish models;
    there is one.
  - **Z'' has no published zones in its source.** The 2000 paper says the cut-offs changed without
    printing them. The zones usually quoted come from later publications, not read here, so Z'' is
    reported as a score only.
  - **Neither source prints a worked example**, so the coefficient test pins the published values and
    the zone boundaries instead (see Tests).
  - **The leakage test was hard-wired to `feature_set_v1`,** contrary to decision 3's "without change".
    It now runs on every feature set, and fails if a ratio is never computed on its fixtures.
  - **FY2025+ calculation-variant revenue was null.** Schemas 1-3 and wariant 2 map the calculation
    variant's revenue to `IS.CALC.A.R2025` (sales of products and goods, no longer materials), which
    `line_items_v1` and `v2` do not list, so revenue and every ratio over it were null for those
    statements. The owner decided (2026-09-27) to count it as revenue: `line_items_v3`, read by
    `feature_set_v3`, which is v2's features unchanged and the new default. No other ratio input is
    remapped by a 2025 spec.
  - **`feature_store` rebuilt on `feature_set_v3`:** the `leakage` check passed, and two builds gave
    the same bytes (SHA-256 over the sorted files `028c4290…`).
- **Step C (2026-09-27):** `models/dataset.py` joins `feature_store` to the frozen label set named in
  `config/models/backtest_v1.yaml`, recomputing the set's hash before use; `models/splits.py` builds
  the purged folds and their report. The purging test is blocking and fails on an unpurged split.
  - **Undated declarations.** A declaration with no decision date has a null `event_date` and is
    dated by `event_known_from` (plan 0008). The dataset carries both, and an event is identified by
    entity, class, trigger and that date.
  - **Seed folds** (events are distinct events, not rows; `min_events` 3):

    | test year | 12 m: train / test events | evaluable | 24 m: train / test events | evaluable |
    |---|---|---|---|---|
    | 2020 | 3 / 2 | no | 3 / 3 | yes |
    | 2021 | 4 / 3 | yes | 4 / 4 | yes |
    | 2022 | 5 / 3 | yes | 5 / 3 | yes |
    | 2023 | 7 / 1 | no | 6 / 3 | yes |
    | 2024 | 9 / 2 | no | 8 / 2 | no |
    | 2025 | 9 / 2 | no | 9 / 2 | no |

    11 events at each horizon, 2,127 labelled rows at 12 months (220 censored) and 2,031 at 24
    (316 censored). The 2025 test year has 8 rows: the lag allowance censors the rest (plan 0009).
  - **For step D: these counts are the fold's, not a model's.** The training events before 2019
    have no financial features, so a complete-case model sees fewer. `min_events` is applied again
    to the rows each model actually fits and scores.
- **Owner decision 5 filled in (2026-09-27, commit `ecc61bc`, before any model code):** equity/assets,
  working capital/assets, ROA, asset turnover; L2, C = 1.0, no class weighting. On the seed no fold
  clears `min_events` with complete cases, under any ratio set tried; the regression is fitted and
  logged, its metrics print n/a.
- **Step D (2026-09-27):** `models/classical.py` (the regression: complete cases counted, fold-fitted
  mid-rank transform, `lbfgs`) and `models/baselines.py` (the published score, its zone, and its
  mapping to a probability). The mapping reuses the regression's rank transform and penalty on the
  one variable, so both are judged by the same Brier score. A fold whose complete training rows
  hold one class is reported with the reason and not fitted.
  - **Seed smoke run:** all three models run on every fold. The regression and the Poznań model fit
    from 2021 (12 m) and 2022 (24 m). Z'' needs the operating result and the equity breakdown, which
    small income statements and micro balance sheets lack, so its training rows hold no event
    until 2023 (12 m) and 2024 (24 m).

## Why

Phase 5 made every feature knowable on its date; Phase 6 asks what they predict, honestly: trained on the
past, tested on the future, scored for calibration as well as ranking. It is also where the project's
reproducibility claim becomes checkable: a model run that cannot name its code, data, labels and features is
invalid (CLAUDE.md).

## What there is to build on (seed, 2026-09-26)

- **Labels** (`066d18bbd4cd…`, v2): month-end grid 2012–2026 × 12/24 months.
  - 12 months: 2,011 `alive`, 116 positive (64 bankruptcy, 28 liquidation, 24 restructuring), 220 censored.
  - 24 months: 1,807 `alive`, 224 positive, 316 censored.
  - **The positives are 11 events in 9 entities.** Each event is seen from up to 12 (or 24) consecutive
    month-ends, so 116 positive rows are 11 observations, not 116.
- **Features** (`feature_set_v1`, 44): financial ratios exist only once a statement is public, the first on
  2019-03-31. At 12 months, 689 labelled rows have financial features, and **6 events** reach them; the
  2014, 2017 and 2018 events precede any electronic filing.
- **Nothing for stage I is installed or deployed:** no `statsmodels`, `scikit-learn` or `mlflow` in
  `pyproject.toml`; `MLFLOW_TRACKING_URI` is empty; `src/distress_radar/models/` is empty.

## The constraint that decides what this phase can show

**Seventeen entities cannot say whether a model works.** Eleven events make any AUC or Brier score noise:
moving one event between years moves the result more than any modelling choice. And the seed was
hand-picked with distress hints: 9 of 17 entities (53%) fail, where the population rate is a small
fraction of that, so any probability calibrated on the seed is wrong for the population by construction.

What Phase 6 can prove on the seed is the **machinery**: leak-free out-of-time splits, purged label windows,
no imputation, calibration reported beside discrimination, the four identifiers, byte-reproducible runs.
Every number it reports is labelled as seed output, with its event count, and none is a claim.

**The critical path to a meaningful model is acquisition at scale, and it is blocked.** RDF sits behind a
WAF that served an hCaptcha to an honest browser (ADR 0007: options a, an allow-listed client, and b, a
licensed aggregator feed, remain open), and no A1 aggregator is chosen. Manual capture does not scale: a few
thousand entities with several filings each is hundreds of hours of clicking at the confirmed pace. Owner
decision 0 is about this, not about models.

## Owner decisions (made 2026-09-27)

The owner accepted the recommendations below on 2026-09-27. Decisions 1, 2, 5, 7 and 8 carry additions
from the review that preceded them; decision 9 was changed, because the recommendation it replaces would
have leaked the future (see there).

0. **Build Phase 6 on the seed now, and open the scale question in parallel.** The harness is built now,
   every result marked "seed, N events, not evidence". An ADR on scaled RDF access (ADR 0007 options a, a
   formal request to KRS / the Ministry of Justice for an allow-listed client, and b, quotes for a licensed
   aggregator feed) and the A1 aggregator choice is opened alongside; it is the critical path to a model
   that means anything, and this plan does not wait for it. A larger hand-captured seed was rejected: still
   hand-picked and distress-heavy, so calibration stays meaningless, at days of manual capture.
1. **Target: one binary model per horizon.** Distress (`bankruptcy`, `restructuring`, `liquidation`,
   `silent_exit`) against `alive`, at 12 and 24 months. Censored rows are left out of the binary models
   (they are for Phase 8's survival models); rows the labels exclude are already absent. Per-class models
   wait for scale. *Addition:* each fold's report gives the class mix of its positives, since a
   `silent_exit` behaves differently from a court proceeding.
2. **Out-of-time scheme: expanding window by calendar year, with purged label windows.** For a test year Y,
   train on rows whose whole label window has closed before 1 January Y (`as_of_date + horizon <
   Y-01-01`). Test years 2020–2025. The same entities appear on both sides of a split, by design of a panel;
   an entity-disjoint check is added at scale.
   - *Addition, a minimum-events rule:* a fold with fewer training events, or fewer test events, than
     `min_events` (in `config/models/backtest_v1.yaml`, initially 3) is reported as **not evaluable**: its rows
     and events are listed, no metric is printed. Expected on the seed: at 12 months the 2020 fold trains
     only on `as_of_date < 2019-01-01`, before the first public statement (2019-03-31), so the 2020 and 2021
     folds have no financial training data; at 24 months the gap reaches a year further.
3. **The baselines' ratios enter as features, in `feature_set_v2`.** Computing them inside model code would
   bypass the leakage test and the contract.
4. **Classical models: Altman's Z'' and two Polish discriminant models.** Z'' is the four-ratio variant for
   private, non-manufacturing firms. The two Polish models are chosen by whether the primary publication and
   a worked example can be obtained, not by reputation; candidates in order: Mączyńska, then Hołda or
   Hadasik (then Gajdka–Stos, Prusak, the Poznań model).
   - **Coefficients are taken from the primary publications, never from memory,** each with its citation,
     sample and published cut-off, into versioned config (`config/models/`). A model whose source cannot be
     verified is not configured.
   - A fixed-coefficient score is not a probability. Each is reported as a score (discrimination, and its
     published zones), and mapped to a probability only by a one-variable logistic fit on the training
     folds, so its Brier score is comparable.
5. **Logistic regression without imputation.** Complete cases on a small fixed set of ratios, with the rows
   and events left out reported per fold; extreme ratios rank-transformed within each training fold (fitted
   on the training rows only). Missingness indicators with a filled value would be imputation; native
   missing-value handling is LightGBM's, in Phase 8.
   - *Addition:* the ratio list (four to six ratios) and the regularisation strength are fixed in
     `config/models/` and committed **before the first backtest run**; changing either after seeing results
     is a new config version, recorded as such. The regularisation strength is not tuned: tuning on six
     events fits noise.
6. **MLflow: a local tracking store now, a server later.** SQLite backend and file artifacts under
   `.data/mlflow/` (gitignored), no service to run; the Docker Compose server (Postgres backend, MinIO
   artifacts) comes with serving in Phase 9.
7. **The four identifiers, and what makes a run invalid.**
   - **code commit:** `git rev-parse HEAD`; a run from a working tree with uncommitted changes is refused;
   - **data snapshot hash:** SHA-256 over the `feature_store` Parquet bytes, which are byte-reproducible
     (plan 0010). This closes the question ADR 0012 left to Phase 6. *Addition:* the files are hashed in
     sorted path order, and each file's own hash is logged beside the combined one, so a changed snapshot
     can be traced to the file that changed;
   - **label version:** the `label_set_hash` (with `label_version`);
   - **feature-set version:** `feature_set_version` and `feature_set_hash`.
   A run missing any of them is not logged at all, rather than logged incomplete.
8. **The report: generated Markdown tables, no plotting yet.** Per test year and pooled: Brier score, log
   loss, AUC, precision in the top decile, a reliability table by probability bin, and bootstrap intervals
   resampled by entity. Written under `WAREHOUSE_DIR/reports/backtest/` and logged to MLflow as an artifact.
   Charts wait for the Quarto report (Phase 9).
   - *Addition:* below `min_events` (decision 2) a metric prints as `n/a (<k events)`, not a number. This
     matters most for top-decile precision, whose decile holds a fraction of one event on the seed; the
     metric stays in the code for scale.
9. **The 2020–21 regime: a sensitivity run, not a feature.** *Changed from the draft,* which proposed
   `regime_flag` as a feature. In `outcome_labels`, `regime_flag` marks a row whose **label window**
   overlaps 2020–21 (`labels_regime_flag_is_window_overlap`): a fact about the months after `as_of_date`,
   which no model scoring today can know. As a feature it would leak the future. Instead:
   - the main run trains on all rows, without the flag;
   - a sensitivity run excludes the flagged rows from training and test, and the report gives both (§4.6:
     models must be able to exclude or control for the regime);
   - if the model should control for the regime itself, that is a feature defined on `as_of_date` alone,
     added to a feature set and so covered by the leakage test; not in this plan.

## Out of scope

- LightGBM, survival models, Optuna, SHAP, the model registry's champion rule (Phase 8).
- Text signals (Phase 7); serving and the Quarto report (Phase 9).
- Growing the universe and scaled RDF access: their own ADR and plan (owner decision 0).

## Steps

### A. Dependencies and tracking

`statsmodels`, `scikit-learn` and `mlflow` in `pyproject.toml` (`make lock`). Project code stays Polars and
hands NumPy arrays to the libraries; pandas arrives transitively, as with SQLMesh (`tests/test_no_pandas.py`
still holds for `src/`). `MLFLOW_TRACKING_URI` defaults to the local store (owner decision 6).

### B. Coefficients and feature set v2

- `config/models/`: one YAML per classical model — coefficients, the ratios they multiply, published
  cut-offs, citation, estimation sample — with a loader that rejects a ratio the feature set cannot supply.
- `config/features/feature_set_v2.yaml`: v1 plus the ratios step B's models need, each mapped per form and
  variant as in v1. The leakage test and the contract cover them without change.
- `config/models/backtest_v1.yaml`: test years, `min_events` (owner decision 2), and the logistic regression's
  ratio list and regularisation strength (owner decision 5), committed before the first backtest run.
- The primary sources are read and cited; a coefficient without a verifiable source is not configured.

### C. The modelling dataset and the splits

`src/distress_radar/models/dataset.py` joins `feature_store` (one `feature_set_version`) to one frozen label
set by hash, with a Pandera contract; `splits.py` builds the purged expanding-window folds of owner
decision 2 and reports, per fold, training and test rows, events (with their class mix) and entities, and
whether the fold clears `min_events`. `regime_flag` is carried for the sensitivity run's row filter, never
as a model input (owner decision 9).

### D. Models

`baselines.py` (fixed-coefficient scores, their zones, and the per-fold logistic mapping) and `classical.py`
(logistic regression, complete cases, fold-fitted rank transform), seeded and deterministic.

### E. Evaluation and the report

`evaluation.py`: the metrics of owner decision 8, hand-checked on small cases, and the Markdown report
generated from them, with the seed caveat and event counts at the top of every table; `n/a (<k events)`
below `min_events`; the main run and the regime sensitivity run side by side.

### F. Tracking

`registry.py`: one MLflow run per model × horizon × fold set, with the four identifiers (owner decision 7),
parameters, metrics and the report as an artifact. It refuses to log a run missing an identifier.

### G. Dagster wiring

Group `models`: an asset `backtest` downstream of `feature_store` and `outcome_labels`, in its own job, run
by hand (never on a schedule: a backtest is a decision, not a refresh).

### H. Docs

AGENT_SPEC §6I as built; DIRECTORY_STRUCTURE (`config/models/`, the new modules); the README's status and how
to run the backtest; `docs/data_inventory.md` (Altman and Polish coefficients: collected); this plan's status.

## Tests

- **Purging:** no training row's label window reaches its test period, on every fold (blocking, like the
  leakage test).
- **No imputation:** the logistic regression never sees a null; rows it drops are counted, not lost.
- **Identifiers:** a run from a dirty tree, or missing any of the four, is refused; the snapshot hash does
  not depend on file listing order.
- **No regime leak:** `regime_flag` is not among any model's inputs.
- **Minimum events:** a fold below `min_events` prints no metric.
- **Determinism:** two runs on the same inputs give identical metrics and report bytes.
- **Metrics:** Brier, log loss, AUC and the reliability table against hand-computed values.
- **Coefficients:** each configured model holds its published coefficients and zone boundaries (neither
  source prints a worked example); a model naming a ratio its feature set lacks is refused.

## Definition of done

- [x] Owner decisions 0–9 made (2026-09-27).
- [ ] The scaled-access ADR opened (owner decision 0; its outcome is not part of this plan).
- [x] Coefficients sourced and cited; the feature set built, leak-free, byte-reproducible (`feature_set_v3`,
      2026-09-27; Mączyńska and Hołda not obtainable, see Progress).
- [x] Purged out-of-time folds for 2020–2025 at 12 and 24 months, each fold's rows, events and class mix
      reported, folds below `min_events` marked not evaluable.
- [ ] Altman Z'', the chosen Polish models and logistic regression evaluated on every fold, Brier score beside
      AUC, bootstrap intervals, the regime sensitivity run.
- [ ] Every run in MLflow with its four identifiers; incomplete runs refused.
- [ ] The backtest report generated, reproducible byte for byte, with the seed caveat.
- [ ] `make check` and `make test-integration` green; docs from step H updated.

## Risks

- **The seed makes every result anecdotal**, and its distress-heavy mix makes calibration meaningless for the
  population. The report says both on its first line; nothing from this phase is quoted as performance.
- **Early events have no financial features.** Pre-2019 events can be predicted only from registry and legal
  features; per-fold event counts make the gap visible instead of averaging it away.
- **Literature models were estimated on other samples, sectors and years.** Their published cut-offs are
  reported as published, not re-tuned, and their mapping to probabilities is fitted, openly, on the seed.
- **Label lag near the cutoff** (plan 0009): the latest test year's `alive` windows are limited by the lag
  allowance, so 2025 has few labelled rows.
- **A harness that looks finished invites reading its numbers.** The caveat is generated into every table,
  not written once in prose.

## After Phase 6

Phase 7 (text signals) adds features the harness picks up through a new feature set; Phase 8 adds LightGBM
and survival models to the same folds. Neither changes what the seed can prove: the scale decision (owner
decision 0) does.
