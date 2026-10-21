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

## Status: active (2026-10-16): owner decisions 0–13 accepted; every item of § "Buildable now" built and the docs swept (items 1–9); 2026-10-21: the report rendered (Quarto 1.5.57 in WSL, `make report` on `backtest_v9`'s publish database); open: the owner's items below

### Where this stands (2026-10-16): pick up here

*Since 2026-10-19:* `backtest_v9` (plan 0022, label v3, 458 entities) registered version 6 of both models, the
regression, `incumbent`, and the default `BACKTEST_VERSION` is v9, so scheduled scoring serves version 6 from the
night of 2026-10-20. *2026-10-21:* `publish` re-run on `backtest_v9` (run `768b29b0`, threshold 10: 42 metric rows, 206 reliability rows, 463 DQ checks, 35 coverage rows), `make site` built, and the report's figures regenerated (served: version 6 of both, `incumbent`, nothing promoted); the report is still not rendered (no Quarto CLI in WSL). Its provenance names the backtest's code commit as MLflow logged it, before the 2026-10-21 history rewrite (README).

**Built and run, on `backtest_v8`** (370-entity population; served: the regression, v5 of `distress_radar_12m`
and `_24m`, alias `incumbent`, training eve 2024-12-31; nothing promoted):

- **Scoring** (`scoring` job): at 2026-09-30, 242 scored, 113 inputs incomplete (mostly micro filers, whose
  balance sheet has no short-term liabilities line), 254 not scored with their reason, per horizon, in local
  Postgres (`scores_history`, `scoring_exclusions`).
- **API** (`make api`) and **explorer** (`make app`): local only, on 127.0.0.1.
- **Publish and site** (`publish` job, `make site`): `publish.duckdb` on `backtest_v8`, the site built into
  `site/build/`; nothing deployed.
- **Report** (`make report`): figures generated from the publish database; **not rendered**, because the
  Quarto CLI is not in WSL.

Every job here runs by hand from a clean tree, with `BACKTEST_VERSION` set (the default is still
`backtest_v5`); `publish` also needs `DQ_MART_MIN_CELL_ENTITIES` (10). After any new backtest version: re-run
`scoring`, then `publish`, then `make site` and `make report`. `publish` refuses a population that no longer
hashes to the backtest's pin, which is what a new delivery imported since the backtest looks like.

**Open for the owner:**

1. ~~Install the Quarto CLI inside WSL, then `make report`.~~ Done 2026-10-21: Quarto 1.5.57 (the release
   tarball, checksum verified, under `~/opt`, linked from `~/.local/bin`), `make report` rendered
   `report/_output/methodology.html` (self-contained, 1.1 MB) with no warnings. Quarto writes
   `report/.gitignore` (`/.quarto/`, its cache) on render; it is committed so the tree stays clean.
2. Read the built site and the report; deploying either is a separate decision (decision 10), and the
   repository is public.
3. Whether to move the default `BACKTEST_VERSION` to the latest backtest (every run since v5 sets it by hand).

**What comes after this plan:** Phase 10 (schedules for scoring and acquisition, alerts into `alerts`, Evidently
drift and calibration monitoring, Sentry) needs a plan of its own. The site's population pages (decision 0)
wait for list v2's weighted draw (plan 0014, ADR 0014 addendum).


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

**Paused after item 1 (owner, 2026-10-14):** plan 0017 (the parsing gaps item 1's count exposed) goes first;
items 2–9 resume on its rebuilt data. *2026-10-15:* the rebuild is in (plan 0017 step H; Progress below). Before
item 3's real scoring run: the job reads its population from `BACKTEST_VERSION`, whose default is still
`backtest_v5` (186 entities), so either set it to `backtest_v7` or change the default.

### Progress (2026-10-16): step H, the docs sweep (buildable item 9)

Read against this plan's results: README's status (the current state and where to pick up), AGENT_SPEC §6J and
§6K and TECHNICAL_ARCHITECTURE (as-built notes for the API, the explorer, the site and the report: local only,
no sector or regional page while the lists are purposive, figures as includes), `DIRECTORY_STRUCTURE.md` (every
new file), `docs/data_inventory.md` (no MLflow server was needed; the deploy token waits on the owner's
decision), and plan 0014's wording (nothing holds `champion`). `make docs-check` green.

### Progress (2026-10-16): step G, the methodology report (buildable item 8)

`report/methodology.qmd` and `src/distress_radar/methodology.py` (`make report`). The report's prose states
rules, never results; every number is a figure included from `report/_figures/` (generated, ignored by git),
which `python -m distress_radar.methodology` rewrites whole from **the publish database** the site reads. So the
report and the site cannot disagree, and the report crosses the publish boundary no further than the site:
small cells are suppressed the same way, and nothing is read that names a company. The only other inputs are
the backtest config and label version the database's provenance names (the settings figure, so no prose quotes
a number a new version could change) and the registry's served version per horizon (the champion rule's
verdict), refused when it was registered by another backtest than the one published.

Figures: the settings; coverage per list and stage; statements by fiscal year; pooled metrics with intervals per
horizon (main run); reliability per horizon; the served versions and the verdict; provenance. Generated on the
real stores (`backtest_v8`): the 12-month pooled metrics equal `backtest_v8.md`'s, cell for cell, and no figure
holds a 10-digit number. Departure from step G as written: the figures are generated as Markdown includes, not
computed in executable Quarto cells, so the report needs no Jupyter kernel (no new dependency), and `make
report` regenerates them before every render, so it still rebuilds from live data.

Rendered 2026-10-21 (Quarto 1.5.57 in WSL; `make report` refuses a Windows one, decision 10).
Tested (`tests/test_methodology.py`, 10 tests): a metric printed exactly as the backtest report prints it
(interval, no interval, undefined, not evaluable), a suppressed cell, every include backed by a figure and every
figure included, the figures read from a synthetic publish database, the verdict's backtest checked, the
figures replaced whole, no 10-digit number in them.

### Progress (2026-10-16): step F, the publish boundary and the site (buildable item 7)

`src/distress_radar/publish.py` and the `publish` asset in its own `publish` job (by hand, clean tree). It
writes `site/sources/publish/publish.duckdb` (decision 9 named `site/sources/publish.duckdb`; Evidence keeps a
DuckDB source in a directory of its own, with its `connection.yaml` and one query per table), replaced whole,
from six fixed tables:

- `dq_checks`, `dq_coverage`: `marts.dq_mart` and `marts.dq_mart_coverage`, re-suppressed at the publish
  threshold whatever threshold SQLMesh built them with (the owner's `.env` has none today).
- `pipeline_coverage`: per list, the entities that reached each stage: listed, identified (GUS), searched on
  RDF, statements stored, statements parsed, labelled (the pinned label set), modelled (the population, refused
  unless it still hashes to the backtest's pin).
- `backtest_metrics`: the pinned backtest's pooled cells per run, horizon and model, read from its finished
  MLflow runs (refused if two runs repeat a cell or the runs disagree on the commit, snapshot, label set or
  feature set), with the distinct companies each covers.
- `backtest_reliability`: for each pooled cell that speaks, the reliability table rebuilt from
  `model_predictions` with the backtest's own rule (the folds that trained on at least `min_events` events) and
  its own function, and the companies in each bin. Refused when the pooled rows do not number what MLflow
  logged: on `backtest_v7` all 42 cells agree, and the pooled metrics equal the report's.
- `provenance`: the publish's code commit, the backtest and its commit, both data snapshots, the label version
  and set, the feature set, the population hash and the threshold.

Every table carries `entities` and `suppressed`; a cell under the threshold keeps its row and loses its
figures. The threshold is refused unset or below 5 before any work. `check_publishable` then refuses an
identifying column (`krs`, `nip`, `regon`, `name`, `company_name`, `document_ref`, `source_document_hash`) or
any KRS, NIP or REGON of `entity_master` and `universe_candidates` as a text value.

The site (`site/`): Evidence 40.1.8 pinned in `package.json`, the `publish` source, and four pages within
decision 0: an overview with the purposive caveat and the provenance table, data quality, pipeline coverage,
and the backtest (pooled metrics with intervals and event counts per horizon, and the main run's reliability
tables). No company, sector, region or predicted rate. `make site` builds under `site/build/`; it refuses a
Windows `npm`. `site/package-lock.json` comes from its first build (2026-10-16, below).

*Re-run on `backtest_v8` (2026-10-16, at `2249d27`):* `publish.duckdb` written at threshold 10, nothing
identifying in it; provenance names `backtest_v8` (commit `070861d`), label set `fd867aa8…` and population
`dbdaa172…`; pipeline coverage for list v1: 609 listed, 592 identified, 518 searched on RDF, 385 with statements
stored and parsed, 591 labelled, 353 modelled (the seed: 17 at every stage).

*The site's first build (2026-10-16):* with Node.js 22 installed in WSL by the owner (`nvm`), `make site` built
the four pages into `site/build/` (not committed, nothing deployed) from that database. Two fixes to
`site/package.json` were needed: `"type": "module"`, without which Vite loads Evidence's config as CommonJS and
refuses its ESM-only packages; and `typescript` pinned at 5.4.2, Evidence 40.1.8's own exact peer, since npm
otherwise picks TypeScript 7 for a looser peer and refuses the tree. The first `npm install` wrote
`site/package-lock.json`; `make site` from it (`npm ci`) rebuilds the same pages. No KRS, NIP or REGON of
`entity_master` or `universe_candidates` occurs in any built file. A shell that skips `~/.bashrc` (a
non-interactive one) needs `. ~/.nvm/nvm.sh` first, or `make site` finds the Windows `npm` and refuses.

Tested (`tests/test_publish.py`, 18 tests): suppression, including over an upstream threshold; coverage per
stage; the pooled rows and reliability equal to the backtest's function, bins and cells suppressed, predictions
disagreeing with MLflow refused; cells read from a real (SQLite) MLflow store; the database replaced whole;
identifying columns and values refused; the job's selection; and every SQL block of every page run against a
synthetic publish database, each `<Column id>` a column it returns.

### Progress (2026-10-16): step E, the explorer (buildable item 6)

`app/` (`make app`, Streamlit on `127.0.0.1` only), laid out over `src/distress_radar/explorer/data.py`, which
holds everything the pages show as frames, testable without Streamlit. Every page carries the disclaimer.

- **Home:** the served versions and the latest month-end's counts per horizon (scored, inputs incomplete, each
  exclusion reason), with the purposive caveat.
- **Company:** identity (KRS, NIP, REGON, GUS status, PKD) and the form of the latest statement filed (the size
  class as filed); per horizon, the probability with its drivers and its percentile in its PKD division, or why
  it is not scored, with the inputs missing. A micro filer missing `working_capital_to_assets` is told the
  served model does not cover micro filers (the 2026-10-14 finding), not that data is missing. The score history
  (decision 3) is out-of-time throughout: each backtest month-end from the fold whose test year holds it (the
  served version's backtest, `main` run, model and horizon), then the served version's own scores; nothing is
  re-scored with the served model. Annotated with filings and distress events, and the training eve. Then legal
  events, filings, and the canonical facts by period, each with its document hash and element path; the stored
  document can be fetched and saved.
- **Peers:** per PKD division at the latest month-end, the count and quartiles of the scored companies'
  probabilities; no company is listed.
- **Watchlists:** pick or name a list, choose its companies, save (through `api.db.replace_watchlist`), and see
  each member's current scores or reasons.

A correction to decision 3 as first built: cutting the backtest's points at the served version's training eve
dropped its own fold's predictions (the served regression is the fold-2025 model, so its 2025 month-ends are
out-of-time). Every fold's point is out-of-time by construction, so all are kept, a served score winning on a
shared month-end.

Tested: `tests/explorer/test_data.py` (history, status, summary, peers, facts, documents) and
`tests/explorer/test_pages.py` (every page under `AppTest` on synthetic sources, the disclaimer on each, the
micro note, an excluded company, a watchlist). Run on the real stores under `AppTest`: every page without an
exception; loading the stores takes about 40 seconds on `/mnt/c`, once per session (cached).

### Progress (2026-10-16): step D, the API (buildable item 5)

`src/distress_radar/api/` (`make api`, on `127.0.0.1:8000` only, no auth): decision 6's routes, read-only over
`scores_history`, `scoring_exclusions` and `watchlists`. At startup the served version per horizon of
`BACKTEST_VERSION`'s horizons is loaded from the registry (`load_served`), and the app refuses to start when the
latest stored scores of a horizon came from another version ("run the scoring job first"). Every response is an
envelope: `data`, `meta.served` (decision 7's fields per served version: name, version, alias, kind, MLflow run,
training eve, the version's code commit, data snapshot, label version and set, feature set and hash) and the
disclaimer, which also says the training list is purposive. Every read is limited to the served versions.

- `GET /companies/{krs}`: per horizon, the newer of the latest score row (a probability with its drivers, or
  "inputs incomplete" with the inputs named) and the latest exclusion with its reason; a company scored once and
  then found in a proceeding shows the exclusion. 404 for a company no served run saw.
- `GET /companies/{krs}/scores`: the served versions' score rows, oldest first. The out-of-time history before
  the training eve (decision 3) is the explorer's, from `model_predictions/`; the API serves what is in Postgres.
- `GET /scores?as_of_date=&horizon_months=`: every score row at a month-end, the latest by default.
- `GET|PUT|DELETE /watchlists/{name}`: PUT sets the whole list (members kept keep their `added_at`), each
  member returned with its latest scores or exclusions.
- `GET /meta`: the served versions and the disclaimer.

Tested (`tests/api/test_api.py`, 13 tests): the bind address, the envelope on every route, a stale database
refused, an empty one starting, each route's shape and 404/422 cases, against a throwaway Postgres schema.
Smoke-tested on the real stores (`BACKTEST_VERSION=backtest_v7`): 572 rows at 2026-09-30, served v4.

### Progress (2026-10-16): step A completed (buildable item 4)

`fastapi` 0.141.1, `uvicorn` 0.52.4, `streamlit` 1.66.0 and `altair` 6.3.0 locked. The threshold (decision 9):
`.env.example` sets `DQ_MART_MIN_CELL_ENTITIES=10`, and `Settings.publish_min_cell_entities()` refuses an unset
value or one below 5 (`PUBLISH_MIN_CELL_FLOOR`); step F's `publish` asset calls it before writing anything.
An existing `.env` keeps its own value: empty (suppression off) until the owner sets it, which is harmless while
nothing is published, and the `publish` asset refuses to run until then.

### Progress (2026-10-16): scoring re-run on list v1's third import

After plan 0014's third import and `backtest_v8` (plan 0014, § "Progress"), the `scoring` job, run from a clean
tree at `2b616e5` with `BACKTEST_VERSION=backtest_v8`, wrote to local Postgres at 2026-09-30. Per horizon, the
served version is v5 of `distress_radar_12m` and `_24m` (registered by `backtest_v8`), the regression,
`incumbent`, label set `fd867aa8…`; the population hash is `backtest_v8`'s pin (`dbdaa172…`, 370 entities).
Version 4's rows stay beside them; every read is limited to the served versions.

| | 12 months | 24 months |
|---|---|---|
| `scores_history`: scored | 242 | 242 |
| `scores_history`: inputs incomplete | 113 | 113 |
| `scoring_exclusions`: `rdf_not_searched` / `no_statement_imported` / `statements_held_back` / `rdf_search_incomplete` | 72 / 133 / 31 / 2 | the same |
| `scoring_exclusions`: `not_at_risk` | 16 | 16 |
| Mean / median probability of the scored | 0.010 / 0.005 | 0.023 / 0.014 |

710 score rows and 508 exclusions, all 609 entities with features accounted for per horizon. Missing inputs
(`working_capital_to_assets` 110, `roa` 11, `asset_turnover` 11, `equity_to_assets` 8): the gap the first run
showed, grown with the new entities.

### Progress (2026-10-16): the first real scoring run (buildable item 3)

The `scoring` job, run from a clean tree at `e3bf84e` with `BACKTEST_VERSION=backtest_v7` set for the run (the
default stays `backtest_v5`; the variable is the owner's to change), wrote to local Postgres at 2026-09-30.
Per horizon, the served version is v4 of `distress_radar_12m` and `_24m`, the regression, `incumbent`, training
eve 2024-12-31, label set `821d4ab3…`; the population hash is `backtest_v7`'s pin (`8f1d5cd3…`, 300 entities).

| | 12 months | 24 months |
|---|---|---|
| `scores_history`: scored | 205 | 205 |
| `scores_history`: inputs incomplete | 81 | 81 |
| `scoring_exclusions`: `rdf_not_searched` / `no_statement_imported` / `statements_held_back` / `rdf_search_incomplete` | 180 / 100 / 27 / 2 | the same |
| `scoring_exclusions`: `not_at_risk` | 14 | 14 |
| Mean / median probability of the scored | 0.012 / 0.006 | 0.026 / 0.016 |

572 score rows and 646 exclusions, all 609 entities with features accounted for per horizon. Missing inputs as
the dry run counted them (`working_capital_to_assets` 79, `roa` 8, `asset_turnover` 7, `equity_to_assets` 5).
A second run inserted nothing (invariant 5). The probabilities are the regression's on a purposive list and
are no statement about the segment (ADR 0014); every row names a company and stays local.

### Progress (2026-10-16): the excluded entities' reasons stored (buildable item 2)

Postgres `scoring_exclusions` (`models/scores_store.py`), keyed like `scores_history` (`krs`, `as_of_date`,
`horizon_months`, `model_version`): one row per entity with a feature row at the scored month-end that was not
scored at all, with its reason (one of the population rule's five, or `not_at_risk`, held by a check), the
population's hash, the label set the risk set was read from (`risk_label_set_hash`), the code commit, the data
snapshot and `scored_at`. `scoring.exclusion_rows` builds them, refusing an in-sample month-end as
`history_rows` does; the `scores` asset inserts them beside the scores with `ON CONFLICT DO NOTHING`, so a rerun
adds nothing. An entity with "inputs incomplete" stays a `scores_history` row (decision 12), not an exclusion.
Tested: the unit test (rows and reasons) and the integration suite (the key, the reasons' check).

### Progress (2026-10-15): the count refreshed on plan 0017's rebuilt data

Plan 0017 (the parsing gaps) re-parsed 142 files of 78 entities and rebuilt everything downstream, up to
`backtest_v7`, whose regression is now the served version (v4 of each horizon, `incumbent`, training eve
2024-12-31). The same read-only count, at 2026-09-30, over v7's population (300 entities, the same pin as v6):
14 not at risk, **81 inputs incomplete (was 91), 205 scored (was 195)**. Missing:
`working_capital_to_assets` 79, `roa` 8, `asset_turnover` 7, `equity_to_assets` 5, `going_concern_threat`
0. Of the 79 without working capital, 72 are micro filers on the micro balance sheet, so the finding below
stands: the served regression does not cover micro filers. Details in plan 0017, § Progress, step H.
Items 2–9 resume on this data.

### Progress (2026-10-14): decision 12's count and named inputs (buildable item 1)

**The count**, read-only, at 2026-09-30, for the served version (`distress_radar_12m` and `_24m` v3, the
regression from `backtest_v6`, training eve 2024-12-31; both horizons take the same five inputs), over
`backtest_v6`'s population as `read_population` gives it today (300 entities, its pinned hash) and the latest
frozen label set (`821d4ab37dcf`). Of 609 entities with features: 309 outside the population (180
`rdf_not_searched`, 100 `no_statement_imported`, 27 `statements_held_back`, 2 `rdf_search_incomplete`), 14 not
at risk, **91 inputs incomplete, 195 scored**. The 43 of the 2026-10-13 dry run were on `backtest_v5`'s 186;
the population has since grown with list v1's second import.

Inputs missing, over the 91 rows: `working_capital_to_assets` 89, `roa` 14, `asset_turnover` 13,
`equity_to_assets` 11, `going_concern_threat` 6. The cause, by the latest usable statement (not graded
`quarantined`, plan 0010 decision 3) known on that day. `entity_size_class_history` is not built, so the split is
by the form filed, which is the class the filer declared:

| Cause | Rows | Form filed | Missing | Kind |
|---|---|---|---|---|
| The micro balance sheet has no short-term liabilities line: UoR annex 4 has only "B. Zobowiązania i rezerwy na zobowiązania" | 75 | micro 2018 v1-2: 12, 2025 v1-3: 29, wariant 2: 34 | `working_capital_to_assets` | legitimately absent |
| Every statement refused at parsing (C1/C2), so no canonical facts at all | 6 | 4 micro, 2 full in thousands | all five | parser gaps, below |
| Every statement graded `quarantined` (material identity failures, mostly `subtotals_consistent`) | 4 | full, 2024–2025 | the four ratios | excluded by design |
| A small filer reported only liabilities' total (B), not B.III (0000361585, 2023) | 1 | small 2018 v1-2 | `working_capital_to_assets` | filer's omission |
| A small filer with no liabilities filed B = 0 and left B.III out (0000934600, 2024) | 1 | small 2025 v1-3 | `working_capital_to_assets` | filer's omission |
| A dormant micro filer, total assets 0 (0000430957) | 1 | micro 2018 v1-2 | the four ratios | the denominator is zero |
| The latest usable period is not about a year long (0000690752: 2017-08-14 to 2018-12-31; 0001025363: 2023-03-15 to 2023-12-31) | 2 | full 2018 v1-2 | `roa`, `asset_turnover` | plan 0010 decision 7 |
| A micro sp. z o.o. filed its net result in `IS.MIKRO.G` (associations' line), not `IS.MIKRO.F` (0000734057, 2022) | 1 | micro 2018 v1-2 | `roa` (and `working_capital_to_assets`, micro) | filer's choice of line |

**No line-item map gap was found.** The only map-adjacent case is the last row: 7 micro statements of 4
entities carry `IS.MIKRO.G` and no `IS.MIKRO.F`. `line_items_v3` leaves G out on purpose (the chart's note: it
is for foundations and associations), and counting it would be a new line-item map and so a new feature set,
plan 0012's to version. Reported here, not fixed.

**Parser gaps (stage C), found through the six.** Not this plan's to fix; reported for a stage C plan.
Across everything imported (`quarantine_events`, 2026-10-14):
- **Micro envelopes carrying full-form statements:** a `JednostkaMikro` file may hold `BilansJednostkaInna` and
  `RZiSJednostkaInna`, which its XSD accepts, but the micro specs bind no full-form body, so C2 refuses it
  ("no Bilans", "no RZiS"): 46 and 13 files in micro 2018 v1-0, 2018 v1-2, 2025 v1-3 and wariant 2. This is
  trap (a) of ADR 0005's second addendum, which the small specs handle, met on the micro form. Four of the six
  above.
- **The small wariant 2 (`SFJMAZ (2)`, 2025-08-07) is not mapped:** 48 files of 47 entities refused as
  `unknown_structure_version`, their 2025 statements. Those entities are scored on their 2024 statement, so
  they are not among the 91, but their latest figures are missing.
- **The full form in thousands of PLN:** 10 files of 2 entities fail the XSD (`JednostkaInnaWTys`,
  `P_1/P_1C/Kod`), and their wariant 2 (`SFJINT (2)`, `1-0E`) is not mapped. The other two of the six.

**The finding about the regression** (decision 12 asked for one): 75 of the 91 rows, a quarter of the 286
at-risk entities, are micro filers, and the regression can score none of them. This is not missing data: its
`working_capital_to_assets` input does not exist on the micro form. Its complete-case rule kept micro rows out of
its training too, so the incumbent is a model of full and small filers. A tree model (LightGBM, the survival
kinds) takes the null as an input, and decision 12 already says this narrows if one is promoted. Until then,
the explorer and the API should say "the served model does not cover micro filers" rather than imply a data
gap.

**Built:** the scorer names the inputs a row lacks, in the model's input order: `missing_inputs` in
`SCORE_SCHEMA` (`list[str]`) and in `scores_history` (`text[]`, added by `ALTER TABLE ... ADD COLUMN IF NOT
EXISTS`, so a table from step A gains it). A check holds it to exactly the rows whose reason is "inputs
incomplete", and never empty. Tested: the unit tests (scorer, history rows) and the integration suite (the
table's checks, 68 passed).

### Buildable now, without further input from the owner (2026-10-13)

With decisions 0–13 accepted, the following need nothing more from the owner. They are listed in build order;
each is committed on its own and recorded in Progress when done.

1. ~~**Decision 12's count and named inputs.**~~ Done 2026-10-14 (Progress, above). Count, over the 43 incomplete rows at 2026-09-30, which inputs are
   missing and how often, split by the size class and form filed. The question is whether a line is legitimately
   absent from a short form (AGENT_SPEC §4) or missing because of a gap in the line-item map (as with the
   `.R2025` codes in plan 0012). The result goes here. Then the scorer names the missing inputs on the row: a
   `missing_inputs` column in `SCORE_SCHEMA` and in `scores_history` (`text[]`, null unless the reason is
   "inputs incomplete"). A mapping gap, if one is found, is reported and not fixed in this plan: a fix changes
   the feature set, which is plan 0012's to version.
2. ~~**Storing the excluded entities' reasons (decision 6).**~~ Done 2026-10-16 (Progress, above). Today they go only to Dagster metadata. Each
   feature-store entity at the scored month-end gets one row with its reason (the population rule's, or
   `not_at_risk`) and the population hash, in a Postgres table beside `scores_history` (`scoring_exclusions`,
   keyed like it, idempotent), so the API can say why a company is not scored.
3. ~~**The first real scoring run (decision 5).**~~ Done 2026-10-16 (Progress, above). Run the `scoring` job on a clean tree at the latest month-end,
   writing to local Postgres, and record the counts here. This is local and company-level, nothing leaves the
   machine. A rerun adds nothing.
4. ~~**Step A, the rest:**~~ Built 2026-10-16 (Progress, above). `fastapi`, `uvicorn`, `streamlit`, `altair` in `pyproject.toml` (`make lock`). The
   threshold check (decision 9): `DQ_MART_MIN_CELL_ENTITIES=10` in `.env.example`, and a settings check that
   refuses a value below 5 before anything is published.
5. ~~**Step D, the API** (decisions 6 and 7), as specified.~~ Built 2026-10-16 (Progress, above). It is bound to `127.0.0.1` and tested against Postgres
   in the integration suite.
6. ~~**Step E, the explorer** (decision 8):~~ Built 2026-10-16 (Progress, above). Streamlit, local only, the disclaimer on every page. Score history
   comes from `model_predictions/` before the training eve and from `scores_history` after it (decision 3).
7. ~~**Step F, the `publish` asset** (decision 9):~~ Built 2026-10-16, and the site built the same day (Progress, above). the fixed aggregate queries into `site/sources/publish.duckdb`,
   and the test that no identifier reaches it. Then the Evidence pages of decision 0, written as source.
8. ~~**Step G, the report's figures:**~~ Built 2026-10-16, up to the render (Progress, above). the backtest's pooled metrics, event counts and reliability tables,
   computed under `src/` from the warehouse and stamped with their identifiers. Then `report/methodology.qmd`.
9. ~~**Step H, the docs sweep.**~~ Done 2026-10-16 (Progress, below).

**What waits on the machine, not on a decision:** rendering the report (`make report`): the Quarto CLI is not
installed. (Node.js was installed in WSL on 2026-10-16, and the site is built.) Steps F and G are written and tested up to that point. Installing both inside WSL is the
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
- [x] The served versions resolve from the registry and reproduce their backtest scores (`tests/models/test_serving.py`).
- [x] `scores_history` filled for the modelled population at the latest month-end, every row traceable to its
      MLflow run; the out-of-time history stored locally (2026-10-16, on `backtest_v8`).
- [x] The API, the explorer and the report run locally; every score shown with its metadata and the disclaimer.
      (API and explorer: done 2026-10-16. The report: rendered 2026-10-21.)
- [x] `publish.duckdb` built behind the threshold; the Evidence site builds from it alone; nothing deployed
      (2026-10-16, on `backtest_v8`).
- [x] `make check` and `make test-integration` green; docs from step H updated (2026-10-16).

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
