# 0016 — Phase 9: scoring, the API, the explorer, the public site and the methodology report

**Stage:** Phase 9 (AGENT_SPEC §10); spec stages J (§6J, serving) and K (§6K, presentation), with the
`scores_history` table (§5) and the publish gate on `dq_mart` (§5, §10).
- **Domain rules:** named company-level scores are local only, and every score display says it is a statistical
  estimate, not a statement of fact (§6K); public output is aggregated or pseudonymised (§12); every published
  figure is regenerable from its code commit, data snapshot hash, label version and model version (§12);
  nothing is published while `dq_mart`'s small-cell threshold is unset (§10).
- **Inputs:** MLflow's registered models `distress_radar_12m` and `distress_radar_24m` (plan 0015 step G:
  version 2 of each, the regression at alias `incumbent`, no `champion`); `feature_store` on `feature_set_v6`;
  the frozen label set and population pinned in `backtest_v5`; `dq_mart` and `dq_mart_coverage`; the backtest
  report; the canonical tables for the explorer's source-linked statements.
- **ADRs:** 0006 (plain idempotent DDL for the new Postgres tables), 0008 (derived data under `WAREHOUSE_DIR`),
  0009 (no natural persons, anywhere a page can show), 0014 (why no rate from these lists is the segment's).

**Order:** after plan 0015 (complete, 2026-10-12). It does not wait for list v2 or a champion: it serves what the
registry holds and says so, and the public site's population-level pages wait for list v2 (decision 0).

## Status: active (2026-10-13): owner decisions 0–10 accepted as recommended, 11–13 added from building steps B and C (accepted the same day); steps A–C built in part (Progress)

### Progress (2026-10-13), built ahead of the decisions, on the parts every alternative shares

- **Step B:** `models/serving.py` resolves `champion`, else `incumbent` (decision 1), and rebuilds a scorer per
  kind from the version's `model/` files; a loaded version reproduces its fold's backtest scores (regression,
  LightGBM, both survival kinds). `top_drivers` as decision 4 recommends. Decision 4 defines no contribution
  for the survival kinds (plan 0015 explains only the binary booster): decision 13 leaves them without drivers
  until a survival model is promoted.
- **Step C:** the backtest writes every scored test row to `WAREHOUSE_DIR/model_predictions/` (decision 3's
  source; local only). The `scores` asset and `scoring` job (`models/scoring.py`), on decisions 2, 5 and 7 as
  recommended. Decision 2 did not say what happens to an entity already in a proceeding or deregistered
  (decision 11, accepted as built): it has a feature row at the latest month-end, but the label rules leave it off the grid, so no
  model was trained on rows like it. Built: not scored, reason `not_at_risk`, the grid read from the latest
  frozen label set of the served version's label version. Read-only dry run on the real stores (2026-10-13, not
  written): at 2026-09-30, of 186 population entities, 12 not at risk, 43 with inputs incomplete, 131 scored
  per horizon (`incumbent`, the regression, training eve 2024-12-31). The 43 are a quarter of the at-risk
  population: the regression's complete-case rule (decision 12: stay not scored, the missing inputs named). Not built:
  storing the excluded entities' reasons for the API (decision 6), which today go only to the run's metadata.
- **Step A, tables only:** `scores_history` and `watchlists` (`models/scores_store.py`). Beyond §5, a row the
  served model cannot score is kept with `not_scored_reason` and no probability (decision 2), and a check refuses
  a score dated on or before the version's training eve. Not built: dependencies, the threshold's check.

### Buildable now, without further input from the owner (2026-10-13)

With decisions 0–13 accepted, the following need nothing more from the owner. They are listed in build order;
each is committed on its own and recorded in Progress when done.

1. **Decision 12's count and named inputs.** Count, over the 43 incomplete rows at 2026-09-30, which inputs are
   missing and how often, split by the size class and form filed. The question is whether a line is legitimately
   absent from a short form (AGENT_SPEC §4) or missing because of a gap in the line-item map (as with the
   `.R2025` codes in plan 0012). The result goes here. Then the scorer names the missing inputs on the row: a
   `missing_inputs` column in `SCORE_SCHEMA` and in `scores_history` (`text[]`, null unless the reason is
   "inputs incomplete"). A mapping gap, if one is found, is reported and not fixed in this plan: a fix changes
   the feature set, which is plan 0012's to version.
2. **Storing the excluded entities' reasons (decision 6).** Today they go only to Dagster metadata. Each
   feature-store entity at the scored month-end gets one row with its reason (the population rule's, or
   `not_at_risk`) and the population hash, in a Postgres table beside `scores_history` (`scoring_exclusions`,
   keyed like it, idempotent), so the API can say why a company is not scored.
3. **The first real scoring run (decision 5).** Run the `scoring` job on a clean tree at the latest month-end,
   writing to local Postgres, and record the counts here. This is local and company-level, nothing leaves the
   machine. A rerun adds nothing.
4. **Step A, the rest:** `fastapi`, `uvicorn`, `streamlit`, `altair` in `pyproject.toml` (`make lock`). The
   threshold check (decision 9): `DQ_MART_MIN_CELL_ENTITIES=10` in `.env.example`, and a settings check that
   refuses a value below 5 before anything is published.
5. **Step D, the API** (decisions 6 and 7), as specified. It is bound to `127.0.0.1` and tested against Postgres
   in the integration suite.
6. **Step E, the explorer** (decision 8): Streamlit, local only, the disclaimer on every page. Score history
   comes from `model_predictions/` before the training eve and from `scores_history` after it (decision 3).
7. **Step F, the `publish` asset** (decision 9): the fixed aggregate queries into `site/sources/publish.duckdb`,
   and the test that no identifier reaches it. Then the Evidence pages of decision 0, written as source.
8. **Step G, the report's figures:** the backtest's pooled metrics, event counts and reliability tables,
   computed under `src/` from the warehouse and stamped with their identifiers. Then `report/methodology.qmd`.
9. **Step H, the docs sweep.**

**What waits on the machine, not on a decision:** building the site and rendering the report (`make site`,
`make report`). Node.js is not on WSL's `PATH` (only a Windows `npm` under `/mnt/e` is visible), and the Quarto
CLI is not installed. Steps F and G are written and tested up to that point. Installing both inside WSL is the
owner's to do, as a documented prerequisite (decision 10). Nothing is deployed (decision 10).

**Not in this list:** plan 0014 item 4's remaining work, weights through the backtest's folds, cells and
bootstrap. Building it needs no input, but it has no use until list v2's frame is recorded (ADR 0014,
§ "Lists received, 2026-10-13"). Until then the lists are used unweighted, like v1.

## Why

Phases 1–8 built a pipeline whose output lives in Parquet files, a SQLite MLflow store and a Markdown report. Phase
9 makes it usable and visible: a score per company that a person can look up with its sources and its reasons, an
API that says exactly which data and model produced each number, and a public page and report that show the method
and the data quality without naming anyone.

## The constraint that decides what this phase can show

The same as Phase 8's, now in front of readers. The models rest on 18 events across two purposive lists (ADR
0014); nothing beat the regression, because nothing could be judged. So:

- **Every score is a ranking device on purposive lists, not a calibrated probability for the segment.** The
  explorer and the API say so beside each number, in words a reader outside the project understands.
- **The public site shows the machinery, not findings:** data quality, coverage, the method, and the backtest's
  pooled metrics with their event counts and the caveat. Sector and regional risk dashboards and "realised vs
  predicted rates" wait for list v2's weighted results; on today's lists they would print the lists' distress
  share as if it were the sector's.

## What there is to build on (2026-10-12)

- **The registry** (plan 0015 step G): per horizon, a version whose `model/` holds `model.json` (kind, inputs in
  order, parameters) and, for a tree model, the booster's text or the forest's pickle; tags carry the four
  identifiers and the fold year it was trained for. Today's versions are the regression from the 2025 fold:
  trained on labels settled on 2024-12-31.
- **No scoring path exists.** The backtest scores folds; nothing scores the current month-end. `scores_history`
  and `alerts` are specified (§5) and not built.
- **Per-row backtest predictions are not stored**, only their SHAP values for LightGBM
  (`WAREHOUSE_DIR/model_explanations/`).
- **Empty scaffolding:** `src/distress_radar/api/` (package only), `app/pages/`, `site/pages/`, `site/sources/`,
  `report/` (each a `.gitkeep`). None of FastAPI, Streamlit, Evidence (Node.js) or Quarto is installed.
- **The publish gate:** `DQ_MART_MIN_CELL_ENTITIES` is null; the SQLMesh audits already refuse a suppressed cell
  without a threshold and an unsuppressed cell below it.

## Owner decisions (accepted by the owner, 2026-10-13, as recommended)

0. **What the public site holds in this phase.** Recommended: a methodology page; data quality (`dq_mart`,
   `dq_mart_coverage`); pipeline coverage (entities searched, imported, parsed, labelled, modelled, by list, as
   counts); and the backtest's pooled metrics per model and horizon with event counts, the purposive caveat and
   the reliability tables with any bin under the threshold suppressed. No sector, region or company page, no
   predicted rate. *Alternative:* sector and regional pages now, captioned; rejected, since a caption does not
   stop a reader taking a purposive list's share as the sector's.

1. **Which model serves, and no refit.** Recommended: the scoring job resolves alias `champion`, else
   `incumbent`, per horizon, and serves that registered version as it is: the model the backtest evaluated, not a
   refit on everything settled at the cutoff (a refit has no out-of-time result of its own). Its training eve
   (the version's fold year) is part of every score's metadata. *Alternative:* a refit registered as its own
   version (alias `serving`), with the evaluated version beside it for comparison; worth it once a champion is
   promoted on list v2 and its training data is a year or more behind.

2. **Who is scored: the modelled population only.** Recommended: the entities `backtest_v5`'s population rule
   includes (acquisition complete, 186 today), for the same reason they alone are modelled (plan 0015 decision
   10): to a model, a company whose filings were never fetched looks like one that never filed. Every other
   entity shows "not scored" with its reason. A row missing an input the served model needs (the regression takes
   complete cases) is "not scored: inputs incomplete", never imputed.

3. **Score history: out-of-time where it exists.** Recommended: the explorer's history shows, for each past
   month-end, the backtest's out-of-time prediction from the fold whose test year holds it (the model trained
   only on what was settled before that year), and the served model's scores only from its training eve onward.
   Scoring past months with the served model would show in-sample fits as a track record. This needs the backtest
   to write its per-row predictions locally (`WAREHOUSE_DIR/model_predictions/`, like `model_explanations/`:
   company-level, never published). *Alternative:* live scores only, no history before this phase.

4. **`top_drivers`: exact contributions, from the served model's own kind.** Recommended: for the regression,
   each input's coefficient times its rank's distance from the training mean rank (the exact SHAP value of a
   linear model with independent inputs); for a tree model, TreeSHAP as in plan 0015. The top 5 by absolute
   value, with the input's value and its `known_from`, in `scores_history.top_drivers`.

5. **Scoring cadence and storage.** Recommended: a `scores` asset in its own `scoring` job, run by hand like the
   backtest (scheduling is Phase 10), scoring the latest month-end in `feature_store` for each served horizon and
   inserting into Postgres `scores_history` with `ON CONFLICT DO NOTHING` on (`krs`, `as_of_date`,
   `horizon_months`, `model_version`), so a rerun adds nothing (invariant 5). It refuses a dirty working tree, as
   the backtest does, since the code commit is part of every score's metadata.

6. **The API: read-only over Postgres, local only.** Recommended: FastAPI serves `scores_history`, the
   population's reasons and watchlists; the model is loaded in-process from the registry at startup only to state
   the served versions and to refuse a database whose latest scores came from another version (§6J: no scoring
   per request, which would need the feature build in the request path). Bound to `127.0.0.1`, no auth, since it
   serves named scores and has one user. Every response carries the version metadata (decision 7's fields) and
   the disclaimer. Routes: `GET /companies/{krs}`, `GET /companies/{krs}/scores`, `GET /scores?as_of_date=`,
   `GET|PUT|DELETE /watchlists/{name}`, `GET /meta`.

7. **Version metadata in every response and display.** Recommended: model name, version and alias; its training
   eve; code commit; data snapshot hash; label version and set hash; feature-set version and hash; population
   hash; `scored_at`. The identifiers are those the backtest's runs carry, so any score traces to its MLflow
   run.

8. **The explorer's pages (Streamlit, local only).** Recommended for this phase: company profile (identity,
   size class as filed, legal events, filing behaviour, the disclaimer); score history (decision 3) annotated
   with filings and legal events; source-linked statements (each canonical fact with its document hash and
   element path, opening the stored document); peers (the same PKD division within the modelled population, as
   distributions, not a league table); watchlists. Charts in Altair. *Deferred:* alerts (Phase 10).

9. **The suppression threshold and the publish boundary.** Recommended: `DQ_MART_MIN_CELL_ENTITIES` = 10 (the
   spec's floor is 5; with under 200 modelled companies, 10 keeps a cell from narrowing to a handful a reader
   could name). A `publish` asset writes the only database Evidence reads, `site/sources/publish.duckdb`, from a
   fixed list of aggregate queries, and refuses to run while the threshold is null or below 5. A test asserts no
   KRS, NIP, REGON or company name column reaches it. The file is built, never committed.

10. **Deployment and the report.** Recommended: Evidence builds the static site locally (`make site`) and Quarto
    renders `report/methodology.qmd` to HTML locally (`make report`), its figures from the warehouse; nothing is
    deployed in this plan. Deploying to GitHub Pages is the owner's separate go, after reading the built site,
    because it is outward-facing and the repository is public. Node.js (pinned in `site/package-lock.json`) and
    the Quarto CLI become documented prerequisites, like the C compiler `scikit-survival` needs.

### Added from building steps B and C (accepted by the owner 2026-10-13, as recommended)

11. **An entity already in a proceeding or deregistered is not scored** (reason `not_at_risk`), as built: it
    has a feature row at the latest month-end, but the label rules leave it off the grid, so no model was
    trained on rows like it, and a probability of entering a proceeding it is already in means nothing.

12. **Inputs incomplete stay not scored, and say which.** The 43 of 174 at-risk entities (2026-09-30 dry run)
    the regression cannot score for a missing input are never imputed; their row names the inputs missing, so
    the explorer and the API can show why. Before the explorer shows them, the most often missing inputs are
    counted (their share of a quarter of the at-risk population is a finding about the regression, not a
    detail). A tree model handles missing inputs natively, so this narrows if one is promoted.

13. **No drivers for a survival model until one is promoted.** Today only the regression serves, and nothing is
    promoted before list v2 gives the champion rule enough events. A survival champion's scores carry an empty
    `top_drivers` and say so; how to explain them is decided when one wins.

## Out of scope

- Alerts, scheduling, Evidently drift, Sentry (Phase 10).
- A refit model (decision 1's alternative), per-class models, new features.
- Sector, regional and realised-vs-predicted pages (decision 0), until list v2's weighted results.
- Any deployment (decision 10), and the MLflow server: the local SQLite store stays, since one process on one
  machine reads it.
- Plan 0014 item 7 (unmapped legal records, label v3): an owner decision of its own.

## Steps

### A. Dependencies, tables and the publish gate

`fastapi`, `uvicorn`, `streamlit`, `altair` in `pyproject.toml` (`make lock`); Node.js and Evidence in `site/`,
the Quarto CLI checked by `make site` / `make report`. Postgres `scores_history` and `watchlists` in `SCHEMA_DDL`
(ADR 0006). The threshold setting's check (decision 9).

### B. Loading a registered model

`src/distress_radar/models/serving.py`: resolve the alias (decision 1), read `model/` from the version's run, and
build a scorer per kind (`rank_logistic`, `lightgbm_binary` with its Platt map, the survival kinds at a
horizon) with its `top_drivers` (decision 4). Tested against the backtest: a loaded version reproduces the scores
its fold printed.

### C. Scoring

The `scores` asset and `scoring` job (decisions 2, 5, 7); the backtest's per-row predictions written to
`WAREHOUSE_DIR/model_predictions/` (decision 3; local only, one file per test year, replaced whole).

### D. The API

`src/distress_radar/api/` as DIRECTORY_STRUCTURE lays it out (`main.py`, `routes/companies.py`, `scores.py`,
`watchlists.py`, `schemas.py`, `db.py`); decision 6's routes and decision 7's metadata; `make api`.

### E. The explorer

`app/Home.py` and `app/pages/` (decision 8), reading Postgres, the warehouse and the raw store; `make app`.

### F. The publish boundary and the site

The `publish` asset (decision 9) and the Evidence pages of decision 0 in `site/pages/`, each table and chart
captioned with its event count and the caveat; `make site`.

### G. The methodology report

`report/methodology.qmd`: the universe and its lists, the pipeline, the label rules and their lag, the folds, the
models, the backtest's pooled results and the champion rule's verdict, every figure computed from the warehouse
and stamped with its identifiers; `make report`.

### H. Docs

AGENT_SPEC §6J and §6K as built; README (how to run each surface, and what never leaves the machine);
DIRECTORY_STRUCTURE; `docs/data_inventory.md` (Node.js and Quarto as prerequisites); this plan's status.

## Tests

- **Nothing named crosses the publish boundary:** `publish.duckdb` holds no identifier column and no value of
  any KRS, NIP or REGON in the entity tables; every cell under the threshold is suppressed; the asset refuses a
  null or sub-5 threshold.
- **Served = evaluated:** a loaded registered version reproduces its fold's backtest scores; the alias rule
  picks `champion` over `incumbent` and fails loudly with neither.
- **Scoring:** only the population is scored; incomplete inputs are not scored and say why, never imputed; a
  rerun inserts nothing; every row carries all of decision 7's fields; no score dated before the served model's
  training eve.
- **History is out-of-time:** each past point comes from the fold whose test year holds it.
- **API:** every response has the metadata and the disclaimer; it binds to localhost; it refuses scores from a
  version other than the served one; schema tests on each route.
- **The explorer:** page smoke tests (Streamlit's `AppTest`), the disclaimer on every page that shows a score.
- **Report and site:** both build from a synthetic warehouse in CI-like conditions; the report's numbers equal the
  backtest report's.

## Definition of done

- [x] Owner decisions 0–10 made (2026-10-13, as recommended; 11–13 added the same day).
- [ ] The served versions resolve from the registry and reproduce their backtest scores.
- [ ] `scores_history` filled for the modelled population at the latest month-end, every row traceable to its
      MLflow run; the out-of-time history stored locally.
- [ ] The API, the explorer and the report run locally; every score shown with its metadata and the disclaimer.
- [ ] `publish.duckdb` built behind the threshold; the Evidence site builds from it alone; nothing deployed.
- [ ] `make check` and `make test-integration` green; docs from step H updated.

## Risks

- **A score read as a fact about a company.** The disclaimer, the local-only rule and the purposive caveat are on
  every surface; the public site has no company page at all.
- **Re-identification through small cells.** Under 200 modelled companies: decision 9's threshold, suppression
  kept rather than dropped, and no cross-tabulation finer than the fixed queries.
- **In-sample history shown as a track record.** Decision 3 and its test.
- **A served model a year behind its data.** Its training eve is in every response; decision 1's alternative is
  the remedy once a champion exists.
- **Two new toolchains (Node.js, Quarto) outside uv.** Pinned versions, `make` targets that check them, and
  neither on the path of `make check`'s Python gate.

## After Phase 9

Phase 10: the scoring and acquisition schedules, alerts into `alerts`, Evidently drift and calibration
monitoring, Sentry. List v2's draw and weighted results make the population pages of decision 0 possible.
