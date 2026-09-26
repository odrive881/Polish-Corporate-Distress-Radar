# 0012 — Phase 6: baseline and classical models, out-of-time backtest

**Stage:** Phase 6 (AGENT_SPEC §10: "Baseline and classical models, out-of-time backtest report").
- **Spec stages:** I (§6I), generations 1 and 2: Altman-type and Polish discriminant baselines
  (`statsmodels`), regularised logistic regression (`scikit-learn`); MLflow tracking (I4).
- **Domain rules:** out-of-time evaluation only; Brier score beside AUC, never AUC alone; never impute
  (invariant 4, §6I); every run logs four identifiers (CLAUDE.md, §6I).
- **Inputs:** `feature_store` (plan 0010) and the frozen label set `066d18bbd4cd…` (plan 0009).

**Order:** after plans 0010 and 0011, which are complete. Phase 7 (text signals) and Phase 8 (LightGBM,
survival, SHAP) build on the harness this plan makes.

## Status: draft (2026-09-26); owner decisions pending before step A

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

## Owner decisions needed before step A (recommendations first)

0. **Build Phase 6 on the seed now, and open the scale question in parallel.** *Recommended:* build the
   harness now, every result marked "seed, 11 events, not evidence"; and start an ADR on scaled RDF access
   (ADR 0007 options a and b: a formal request for an allow-listed client to KRS / the Ministry of Justice,
   and quotes for a licensed aggregator feed) plus the A1 aggregator choice. The harness is needed either
   way, and does not change when the universe grows.
   - *Alternative:* pause modelling until the universe grows. Nothing built would be wasted by waiting,
     but nothing would be learned either, and the harness's bugs would surface later.
   - *Alternative:* hand-capture a larger seed first (50–100 entities). More events, but a distress-heavy
     hand-picked sample keeps the calibration problem, at a cost of days of manual capture.
1. **Target: one binary model per horizon.** *Recommended:* distress (`bankruptcy`, `restructuring`,
   `liquidation`, `silent_exit`) against `alive`, at 12 and 24 months. Censored rows are left out of the
   binary models (they are for Phase 8's survival models); rows the labels exclude are already absent.
   Per-class models have one to four events each and wait for scale.
2. **Out-of-time scheme: expanding window by calendar year, with purged label windows.** *Recommended:* for a
   test year Y, train on rows whose whole label window has closed before 1 January Y (`as_of_date +
   horizon < Y-01-01`); otherwise a training label would already know the test period. Test years 2020–2025,
   which cover COVID (2020–21), the 2022 rate rises and the 2022–23 energy shock (§6I). On the seed, the
   financial models have almost no training positives before 2022, and the report says so per fold.
   - The same entities appear on both sides of a split, by design of a panel; at scale, an entity-disjoint
     check is added to catch a model memorising companies. On 17 entities it would be meaningless.
3. **Altman inputs and the Polish models' ratios enter as features, in `feature_set_v2`.** *Recommended:*
   the baselines need ratios v1 does not carry (e.g. retained earnings and operating result over total
   assets). Adding them to the feature set keeps them under the leakage test and the contract; computing them
   inside the model code would bypass both.
4. **Which classical models, and from which sources.** *Recommended:* Altman's Z'' (the four-ratio variant
   for private, non-manufacturing firms), plus two or three Polish discriminant models chosen from the
   literature (candidates: Mączyńska, Gajdka–Stos, Hadasik, Hołda, Prusak, the Poznań model).
   - **Coefficients are taken from the primary publications, never from memory,** each with its citation,
     sample and published cut-off, into versioned config (`config/models/`): they change by literature, not
     engineering (DIRECTORY_STRUCTURE §3).
   - A fixed-coefficient score is not a probability. Each is reported as a score (discrimination, and its
     published zones), and mapped to a probability only by a one-variable logistic fit on the training folds,
     so its Brier score is comparable.
5. **Logistic regression without imputation.** *Recommended:* a small fixed set of ratios, fitted on the rows
   where all of them are present (complete cases), with the rows and events left out reported per fold.
   Extreme ratios are rank-transformed within each training fold (fitted on the training rows only), per plan
   0010's note on near-zero denominators. Missingness indicators with a filled value would be imputation;
   native missing-value handling is LightGBM's, in Phase 8.
6. **MLflow: a local tracking store now, a server later.** *Recommended:* SQLite backend and file artifacts
   under `.data/mlflow/` (gitignored), no service to run; the Docker Compose server (Postgres backend, MinIO
   artifacts) comes with serving in Phase 9. *Alternative:* the Compose server now.
7. **The four identifiers, and what makes a run invalid.** *Recommended:*
   - **code commit:** `git rev-parse HEAD`; a run from a working tree with uncommitted changes is refused,
     since its code has no commit;
   - **data snapshot hash:** SHA-256 over the `feature_store` Parquet bytes, which are byte-reproducible
     (plan 0010). This closes the question ADR 0012 left to Phase 6;
   - **label version:** the `label_set_hash` (with `label_version`);
   - **feature-set version:** `feature_set_version` and `feature_set_hash`.
   A run missing any of them is not logged at all, rather than logged incomplete.
8. **The report: generated Markdown tables, no plotting yet.** *Recommended:* per test year and pooled —
   Brier score, log loss, AUC, precision in the top decile, a reliability table by probability bin, and
   bootstrap intervals resampled by entity (wide on the seed, and shown). Written under
   `WAREHOUSE_DIR/reports/backtest/` and logged to MLflow as an artifact. Charts wait for the Quarto report
   (Phase 9), so this phase adds no plotting library to the locked stack.
9. **The 2020–21 regime.** *Recommended:* train on all rows with `regime_flag` as a feature, and report a
   sensitivity run without the flagged rows (§4.6: models must be able to exclude or control for it).

## Out of scope

- LightGBM, survival models, Optuna, SHAP, the model registry's champion rule (Phase 8).
- Text signals (Phase 7); serving and the Quarto report (Phase 9).
- Growing the universe and scaled RDF access: their own ADR and plan (owner decision 0).

## Steps (after the owner's decisions)

### A. Dependencies and tracking

`statsmodels`, `scikit-learn` and `mlflow` in `pyproject.toml` (`make lock`). Project code stays Polars and
hands NumPy arrays to the libraries; pandas arrives transitively, as with SQLMesh (`tests/test_no_pandas.py`
still holds for `src/`). `MLFLOW_TRACKING_URI` defaults to the local store (owner decision 6).

### B. Coefficients and feature set v2

- `config/models/`: one YAML per classical model — coefficients, the ratios they multiply, published
  cut-offs, citation, estimation sample — with a loader that rejects a ratio the feature set cannot supply.
- `config/features/feature_set_v2.yaml`: v1 plus the ratios step B's models need, each mapped per form and
  variant as in v1. The leakage test and the contract cover them without change.
- The primary sources are read and cited; a coefficient without a verifiable source is not configured.

### C. The modelling dataset and the splits

`src/distress_radar/models/dataset.py` joins `feature_store` (one `feature_set_version`) to one frozen label
set by hash, with a Pandera contract; `splits.py` builds the purged expanding-window folds of owner
decision 2 and reports, per fold, training and test rows, events and entities.

### D. Models

`baselines.py` (fixed-coefficient scores, their zones, and the per-fold logistic mapping) and `classical.py`
(logistic regression, complete cases, fold-fitted rank transform), seeded and deterministic.

### E. Evaluation and the report

`evaluation.py`: the metrics of owner decision 8, hand-checked on small cases, and the Markdown report
generated from them, with the seed caveat and event counts at the top of every table.

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
- **Identifiers:** a run from a dirty tree, or missing any of the four, is refused.
- **Determinism:** two runs on the same inputs give identical metrics and report bytes.
- **Metrics:** Brier, log loss, AUC and the reliability table against hand-computed values.
- **Coefficients:** each configured model reproduces a worked example from its source.

## Definition of done

- [ ] Owner decisions 0–9 made.
- [ ] Coefficients sourced and cited; `feature_set_v2` built, leak-free, byte-reproducible.
- [ ] Purged out-of-time folds for 2020–2025 at 12 and 24 months, each fold's rows and events reported.
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
