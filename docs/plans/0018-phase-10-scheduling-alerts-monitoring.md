# 0018 — Phase 10: schedules, alerts, drift and calibration monitoring, error reporting

**Stage:** Phase 10 (AGENT_SPEC §10: "Scheduling, alerting, Evidently drift monitoring"); spec stage L (§6L,
monitoring), the `alerts` table (§5), and the scoring half of stage I (§6I as built, plan 0016).
- **Domain rules:** an alert at `as_of_date` uses only facts known by then (invariant 1, read from `feature_store`
  and the stored scores, never a fresh join); alerts name companies, so they are local only, like the scores
  (§6K, §12); statutory rules (filing deadlines, KSH tripwires) come from `config/statutory/`, never constants
  (invariant 7); re-running any job on the same inputs writes nothing new (invariant 5); no natural person's
  data leaves the machine, in an error report least of all (invariant 6, ADR 0009).
- **Inputs:** `scores_history` and `scoring_exclusions` (plan 0016); `feature_store` (`feature_set_v6`:
  `art233_triggered`, `negative_equity`, `going_concern_threat`, `going_concern_basis_abandoned`, the auditor
  reports' opinion); `filing_index`, `rdf_listed_entities` and `config/statutory/filing_deadlines.yaml`; the
  served version's MLflow run and its fold's training rows (the drift reference); `outcome_labels` (realised
  outcomes); the existing jobs in `dagster_defs/definitions.py`.
- **ADRs:** 0006 (plain idempotent DDL for `alerts`), 0008 (reports under `WAREHOUSE_DIR`), 0009 (what may
  appear in an error report), 0011 (pacing of the KRS API and MSiG), 0013 (RDF is the owner's script, never a
  pipeline fetch), 0014 (why a probability from these lists is not the segment's).

**Order:** after plan 0016 (active only on rendering the report, which waits on the Quarto CLI). It does not wait
for list v2 or a champion: what it schedules and alerts on is what the registry already serves.

## Status: active (2026-10-17): owner decisions 0–9 accepted as recommended; step A checked (Evidently and `sentry-sdk` both usable, no ADR needed); steps B–E built (`make orchestrate`; the schedules, sensors and RDF queue, all stopped until the owner turns them on; the `alerts` table and its rules; alerts in the explorer and the API); next: step F

## Why

Every stage runs today, but only by hand: someone remembers to fetch the registers, rebuild the features, re-score
and look at what changed. Phase 10 makes the pipeline notice things on its own: new legal events from the open
registers each night, a company whose score jumps or whose statement turns up a going-concern warning, a filing
that is overdue, inputs drifting away from what the model was trained on. It is also the last phase in §10.

## The constraints that shape this phase

1. **RDF cannot be scheduled.** Statements reach the pipeline only through the owner's Power Automate Desktop
   script (ADR 0013); the importer then waits on the owner (outage spans, refused rows). The "weekly filing check"
   of §6L becomes a **queue the pipeline writes for the script**, never a fetch.
2. **The feature grid moves only when RDF does.** `feature_store`'s cutoff is the earliest last complete fetch
   across sources, and RDF's is the last delivery. A monthly re-score between deliveries reproduces the same
   `as_of_date` and writes nothing (`ON CONFLICT DO NOTHING`). Scoring is triggered by new features, not by the
   calendar.
3. **Nothing has a realised outcome yet.** The first stored scores are at 2026-09-30, so the first 12-month window
   closes 2027-09-30, plus the `alive` lag allowance (plan 0009). Realised-outcome calibration is built now and
   reports "not evaluable" until windows settle. The backtest's reliability tables are the only calibration
   evidence until then.
4. **Few rows.** About 240 scored entities per horizon. Drift tests on samples this size flag little and prove
   less: drift is a report to read, never a gate.
5. **Schedules fire only while Dagster runs.** This is the owner's laptop, not a server. A missed tick is not caught
   up, and nothing here may assume one ran.
6. **Probabilities are list-relative.** The lists are purposive (ADR 0014), so a probability threshold is a rank on
   this list, not a population risk. Alert texts say so.

## What there is to build on (2026-10-17)

- Jobs: `legal_to_labels` (KRS API + MSiG at 15 a minute each, legal events, frozen label set), `features`
  (offline rebuild), `backtest` and `publish` (by hand, decisions), `scoring` (by hand, clean tree,
  `BACKTEST_VERSION` set), `text`.
- No schedule, sensor, `DAGSTER_HOME` or structured logging exists. A CLI run's metadata is not kept (plan 0014's
  lessons).
- `alerts` is specified in AGENT_SPEC §5 but not created. `watchlists` exists (plan 0016).
- `filing_deadlines.yaml` already gives each statement's deadline (plan 0010), with the COVID extensions.
- Neither Evidently nor Sentry is a dependency yet. The venv runs Python 3.14, so whether each has a release
  supporting it is checked first (step A).

## Owner decisions (accepted by the owner, 2026-10-17, as recommended)

0. **Where Dagster runs, and its home.** Recommended: a persistent `DAGSTER_HOME` at `.data/dagster/` (gitignored)
   so run history and schedule state survive, started by a new `make orchestrate` (`dagster dev`, 127.0.0.1 only).
   Every schedule ships `default_status=STOPPED`; the owner turns each on in the UI. This also keeps import runs'
   metadata, which the CLI loses today.
1. **The nightly registry check.** Recommended: `legal_to_labels` daily at 02:00 Warsaw time over `entity_master`,
   at the existing pacing (ADR 0011; measured in step A: KRS 40 minutes and MSiG 81 for 609 entities, the two run
   side by side, so about 3 hours a night once list v2's 800 are in).
   A frozen label set is content-addressed, so a night with no new event adds no set. A night that adds one does
   **not** move any backtest: a backtest pins its label set by hash, and a new one stays a decision.
2. **Scoring after new features.** Recommended: a run-status sensor runs `features`, then `scoring`, then `alerts`
   after each successful `rdf_script_import` or `legal_to_labels` run. `scoring`'s guards stay: it refuses a dirty
   tree and reads `BACKTEST_VERSION`, which this decision asks to set in `.env` (see decision 9). `backtest` and
   `publish` are never triggered: each is a decision.
3. **The RDF re-search queue** (replaces §6L's weekly filing check and the filing-wave scaling). Recommended: a
   weekly job writes `.cache/rdf_queue/queue_<date>.txt`, one KRS number per line, for the owner's script. In
   order: entities never searched or searched only in an outage; then entities whose next statement's deadline has
   passed since their last complete search; then the rest by age of the last search. Only KRS numbers, nothing
   else (ADR 0014). From June to August, during the filing wave, the job runs daily.
4. **Alert rules**, versioned in `config/alerts/alert_rules_v1.yaml` (the file name is the version). Recommended,
   one rule per §5 type:
   - `threshold_crossed`: a 12-month probability at or above 0.25 (`high`) or 0.10 (`medium`), where the
     entity's previous score from the same model version was below it. No alert on a first score: there is no
     "crossing".
   - `score_jump`: from one `as_of_date` to the next under the same model version, up by at least 0.10 absolute
     and at least ×2 (`medium`). A new model version starts a new series, never a jump.
   - `tripwire_triggered`: `art233_triggered` or `negative_equity` turns true in `feature_store` (`high` for
     art. 233). This reads features, not scores, so it covers micro filers and entities with incomplete inputs,
     which the model cannot score.
   - `going_concern_flagged`: `going_concern_threat`, `going_concern_basis_abandoned`, or an auditor's opinion
     other than unqualified with a going-concern emphasis, turns true (`high`).
   - `filing_overdue`: the next statement's deadline (`filing_deadlines.yaml`) passed with none listed, judged
     **only as of the entity's last complete RDF search**. The alert's `as_of_date` is that search's date, never
     today: what RDF shows after the search is unknown (`low`).
   Thresholds are list-relative (constraint 6); the owner sets the numbers.
5. **Who is alerted on, and how.** Recommended: every entity in `entity_master` in the v1 segment, with the
   watchlists shown first; delivery only to the `alerts` table, a page in the explorer (acknowledging sets
   `acknowledged_at`) and an API endpoint. **No email, chat or push**: an alert names a company and a statistical
   estimate, and sending one off the machine is a publication.
6. **Drift monitoring.** Recommended: an Evidently data-drift report per horizon on the served model's inputs
   (the regression's four ratios, and every feature once a GBM serves). The reference is that version's training
   rows; the current data is the latest scored `as_of_date`. Written to `WAREHOUSE_DIR/reports/drift/` and linked
   from the explorer. **Not embedded in the public site** (§6L says it should be): Evidently's HTML draws
   per-row distributions, whose tails can single out a company. It waits for list v2's weighted draw and a check
   against the small-cell rule, like the site's population pages (plan 0016 decision 0).
7. **Realised-outcome calibration.** Recommended: a monitor joining each stored score to its outcome once the
   label version says the window is settled. It reports Brier score and reliability per horizon and model version
   only when at least `min_events` (3) settled events exist, else "not evaluable" and the date the first window
   settles. Same code path as the backtest's evaluation (`models/evaluation.py`).
8. **Sentry.** Recommended: built, but **off unless `SENTRY_DSN` is set**, on the owner's account in Sentry's EU
   region, terms read by the owner first (it is an external service). Locked down in code: `send_default_pii`
   off, no local variables, no breadcrumbs, no request bodies; and a `before_send` that keeps only the exception
   type, the stack's file and function names and line numbers, and the Dagster job and asset name. Exception
   **messages are dropped**: ours carry KRS numbers, document refs and, on the import path, text a filer wrote.
   A test feeds it an exception holding a PESEL, a name and a KRS number and asserts none survives.
9. **The default `BACKTEST_VERSION`.** Scheduled scoring needs one set without a human. Recommended: move the
   code default to `backtest_v8`, the latest run, so `.env` need not carry it. This is plan 0016's open item 3,
   decided here.

## Steps

### A. Verify before building (no code)

Check that Evidently and `sentry-sdk` have releases at least two weeks old that install on Python 3.14 with the
locked Polars and pandas. If Evidently does not, write the drift report on `scipy.stats` (KS test and PSI; scipy is
already in the lock) and record why in an ADR before step F. Confirm `legal_to_labels`' runtime over the current
`entity_master` from a real run's log.

**Findings (2026-10-17).** Checked in a scratch venv; nothing in `pyproject.toml` or `uv.lock` changed yet.

- **Evidently 0.7.23** (released 2026-09-11) resolves against the lock on Python 3.14 and moves no locked version.
  It adds 22 packages, among them a web server stack it brings for its own UI (`litestar`, `uvicorn` extras,
  `uvloop`), `nltk`, `faker`, `dynaconf`, and `plotly` pinned below 6. A `DataDriftPreset` report over two numeric
  columns with nulls ran in about 3 seconds and gave per-column K-S drift.
- **Evidently has telemetry.** It depends on `iterative-telemetry`, switched off by three variables
  (`DO_NOT_TRACK`, `ITERATIVE_DO_NOT_TRACK`, `EVIDENTLY_DISABLE_TELEMETRY`). With sockets and DNS trapped, the
  report path tried no connection with or without them; the senders sit in its UI and collector, which this
  project does not run. Step F still sets all three before Evidently is imported, and a test asserts it.
- **The HTML is self-contained** (3.8 MB, scripts inlined, no external load), but **not byte-reproducible**:
  every render draws fresh widget ids (32-hex and UUIDs). Renumbering them by first appearance makes two renders
  of the same input identical, so step F writes the report through that normalisation and its test compares
  bytes.
- **`sentry-sdk` 2.71.0** (2026-09-28; 2.72.0 is under two weeks old) installs, adds nothing beyond itself.
  `sentry_sdk.init(dsn=None)` still leaves an "active" client that sends nothing, so step H's "off without
  `SENTRY_DSN`" means `init` is never called, and its test asserts that.
- **`legal_to_labels`' runtime:** from `legal_source_fetches` (the CLI keeps no run log): KRS 40 minutes and MSiG
  81 to 88 for 609 entities, the two side by side, on the runs of 2026-10-09, 10-13 and 10-16. `entity_master`
  now holds 806 (list v2's distress group, plan 0014), so about 1 hour 50 minutes tonight, and about 3 hours
  once list v2's random group is in. Decision 1's 02:00 start ends before 06:00.

### B. Dagster home and `make orchestrate` (decision 0)

`DAGSTER_HOME` in `.env.example`, `.data/dagster/dagster.yaml` written by the make target, run storage in SQLite
there. README § Orchestration.

**Built (2026-10-17).** `make orchestrate` resolves `DAGSTER_HOME` (environment, then `.env`, then
`<repo>/.data/dagster`), refuses a relative path (Dagster does too; an empty one silently falls back to a
temporary home), copies `dagster_defs/dagster.yaml` in and starts `dagster dev` on 127.0.0.1:3000. The settings:
Dagster's telemetry off, and the `QueuedRunCoordinator` at one run at a time, so the nightly fetch, the scoring
chain and a run started by hand never overlap (two fetches side by side would double a source's pace, ADR 0011).
Dagster reads `DAGSTER_HOME` from `.env` for CLI runs too (tested), which ends plan 0014's lost CLI metadata once
the owner sets it.

- **Found starting it the first time:** the repository had never loaded in the UI since plan 0012. The asset and
  the job were both named `backtest` (and, from plan 0016, `publish`), which Dagster refuses once it resolves every
  job; `dagster job execute -j <name>` builds only its own job, so no CLI run showed it. The assets are now
  `backtest_run` and `publish_database`; the jobs keep their names, so every documented command is unchanged.
  `tests/test_orchestration.py` resolves the whole repository and loads the instance settings, in `make check`.
- **Checked live:** the code location loads, all six daemons report healthy (scheduler, sensor, queued run
  coordinator among them), the UI answers on 127.0.0.1:3000 only, and the home holds its history after a stop.

### C. Schedules and sensors (decisions 1–3)

In `dagster_defs/schedules.py` and `dagster_defs/sensors.py`, wiring only: the nightly `legal_to_labels`
schedule; the run-status sensor chaining `features` → `scoring` → `alerts`; the RDF queue job and its schedule.
Every schedule and sensor ships `STOPPED`. The queue's ordering is a function in
`src/distress_radar/acquisition/rdf_queue.py`, testable without Dagster.

**Built (2026-10-17).** The jobs moved to `dagster_defs/jobs.py` so the sensors can name them; `rdf_queue` is
new. Schedules (`dagster_defs/schedules.py`): `registry_check_nightly` (`legal_to_labels`, 02:00),
`rdf_queue_weekly` (Mondays 06:00, September to May) and `rdf_queue_filing_wave` (daily 06:00, June to August),
all Warsaw time; a queue run gets its tick's date as config, so a rerun writes the same file. Sensors
(`dagster_defs/sensors.py`): `features_after_registry_check`, `features_after_rdf_import` (an asset sensor: a
failed import never materializes, so it starts nothing) and `scoring_after_features`, each keyed by its upstream
run, so each fires once. Everything ships `STOPPED`. Decision 9 is in with it: `BACKTEST_VERSION` now defaults to
`backtest_v8`. The queue asset is `rdf_research_queue` (not `rdf_queue`, the job's name: step B's clash).

- **Tiers** (`acquisition/rdf_queue.py`): `not_searched` (never stored, which includes every company reached
  only in an outage; not the seed, captured by hand), `search_incomplete`, `filing_due` (the latest listed
  period end plus twelve months, through `filing_deadlines.yaml`, past and after the last complete search),
  `oldest_search`. Within a tier the oldest complete search first, then by KRS.
- **First real run (2026-10-17, 806 entities):** 159 `not_searched` (72 list v1, plan 0014's count exactly; 87
  of list v2's distress group, the 2026-10-16 outage's), 4 `search_incomplete`, 6 `filing_due` (5 of them seed
  companies, whose last statement on file is years old: some stopped filing before they failed, so the script
  re-searches them at the cost of five searches) and 637 `oldest_search`. The file holds 806 lines, every one
  a ten-digit KRS number.
- **Tests:** the tiers and their order, the file's bytes and its rerun; every schedule and sensor stopped; the
  whole chain named, with `backtest`, `publish` and `text` never started; the queue schedules' crons, time zone
  and tick date. `test_nothing_schedules_the_scoring` (plan 0016) became `test_only_new_features_start_the_scoring`.


### D. The `alerts` table and rules (decisions 4–5)

`src/distress_radar/alerts/` (`rules.py`, `store.py`): DDL for §5's columns plus `rule_version`, `detail`
(JSON: the values that fired it) and `known_from`. `alert_id` is a hash of (krs, as_of_date, alert_type,
rule_version, triggering ids), inserted `ON CONFLICT DO NOTHING`, so a re-run adds nothing. The `alerts` asset
reads `scores_history` and `feature_store` only. `triggering_score_id` is null for the rules that read features.
`DIRECTORY_STRUCTURE.md` gains the package and `config/alerts/`.

**Built (2026-10-17).** `config/alerts/alert_rules_v1.yaml` holds decision 4's numbers; `alerts/rules.py` the
five rules as pure functions; `alerts/store.py` the table. Asset `alerts` (group `alerts`), job `alerting` (not
`alerts`: step B's clash), and the sensor `alerts_after_features_or_scoring`, stopped like the rest.

- **Read as decided, with three choices the decision left open:** (a) alerts start at `alerts_from: 2026-09-30`,
  the first stored scores: `feature_store` reaches back to 2001 and every past change would otherwise alert at
  once; (b) `going_concern_flagged` reads `going_concern_threat`, `going_concern_basis_abandoned` and
  `going_concern_in_notes` (high) and `modified_opinion` (medium, since an opinion is modified for other reasons
  too); `emphasis_of_matter` is left out, being any matter, not going concern's; (c) `filing_overdue` fires once
  per missing period, at the first complete search showing it, against the statements submitted by that search's
  date. The alerts also read `rdf_listed_entities` and `filing_index`, which the step's first sketch did not name.
- **The sensor follows `features` as well as `scoring`,** so the feature rules, which cover entities the model
  cannot score, do not wait on a scoring run a dirty tree refuses; the second run adds only the score alerts.
- **First real run (2026-10-17):** 137 alerts on 137 companies, the rerun 0 new. 136 `filing_overdue` (low), at
  the searches of 2026-10-07 to 10-16: 96 for FY2025 (due 2026-07-15) and 40 for FY2021–FY2024, companies whose
  filings stopped years ago, most of them likely long failed. One `tripwire_triggered` (Art. 233, at
  2026-09-30). No score alert: `scores_history` holds one month-end, so no series has a previous score yet.
  **Open for the owner:** whether `filing_overdue` should look back only one filing cycle, so the 40 old
  stoppages stop appearing (a rules v2, if so).
- **Tests:** each rule on hand-built frames (a crossing fires once, never on a first score; two levels give the
  higher; a jump needs both its increase and its ratio; a new model version and a not-scored row break a series;
  a flag fires on turning true only, never on an entity's first month-end; an incomplete search judges nothing;
  a statement submitted after a search is not seen by it); point in time: later month-ends add alerts and never
  change earlier ones; against Postgres, a rerun inserts nothing and an acknowledgement stays, and the table
  refuses an alert known after its date or a score rule without its score.


### E. Alerts in the explorer and the API (decision 5)

An "Alerts" page in `app/`: unacknowledged first, watchlists first, an acknowledge button, the disclaimer on
every row. `GET /alerts` in `api/` in the versioned envelope. Both local only.

**Built (2026-10-17).** `app/pages/4_Alerts.py`: the open alerts (a toggle shows the acknowledged ones), watchlist
companies starred and first, then by severity and month-end, one line per alert saying what fired it
(`explorer.data.describe`, from `detail`), the disclaimer and the list-relative caveat; tick rows and acknowledge
them. `GET /alerts` (`api/routes/alerts.py`): open by default, `acknowledged=true` for the others, filters `krs`
and `watchlist`, in the versioned envelope; both share `api.db.alerts`, so they order alerts alike. The API's
startup creates the table if no `alerting` run has yet.

- **Checked live:** in-process over the real database, `GET /alerts` returns the 137 stored alerts, the Art. 233
  tripwire (high) first.
- **Found, not fixed (it predates this plan):** `make api` refuses to start on today's database. `scoring` ran on
  `backtest_v7` and then `backtest_v8`, both at 2026-09-30, so that month-end holds 12-month scores from v4 and
  v5, and `check_served` (plan 0016 decision 6) demands that the latest month-end hold the served version only.
  The served version is there; the refusal was meant for a database whose latest scores the registry no longer
  serves. **For the owner:** loosen the check to "the served version is among the latest", or delete the
  superseded v4 rows at that month-end.
- **Tests:** the page runs on synthetic sources with the disclaimer, counts the open alerts and keeps its
  acknowledge button disabled until a row is ticked, and says when nothing is acknowledged; each alert type's
  line from its `detail`; against Postgres, `GET /alerts` open by default, watchlist first, then severity,
  `acknowledged=true`, the `watchlist` filter, and a malformed KRS refused.


### F. Drift report (decision 6)

`src/distress_radar/monitoring/drift.py`: reference and current frames from `feature_store` and the served
version's fold, one report per horizon, written byte-reproducibly where the tool allows (its timestamp pinned to
the scored `as_of_date`). An asset `drift_report` in the chain after `scoring`.

### G. Realised calibration (decision 7)

`src/distress_radar/monitoring/realised.py` and an asset after `drift_report`. Until a window settles, it writes
the "not evaluable" line with the first settling date.

### H. Sentry (decision 8)

`src/distress_radar/errors.py`: `init_error_reporting()` called from `dagster_defs/definitions.py` and the API's
startup, a no-op without `SENTRY_DSN`; the scrubbing `before_send`. `data_inventory.md` lists the DSN as the
owner's credential.

### I. Docs sweep and close-out

README (orchestration, alerts, the queue), AGENT_SPEC §6L "as built", TECHNICAL_ARCHITECTURE, `data_inventory.md`,
CLAUDE.md's pointers if anything there changes; then `make check` and the hand-read sweep.

## Tests

- **Point in time:** every alert's facts have `known_from <= as_of_date`, and an alert computed at an earlier
  `as_of_date` is unchanged when later facts arrive (added to `tests/features/test_leakage.py`'s pattern, as a
  test of its own).
- Each rule on hand-built frames: a crossing fires once; no alert on a first score; a model-version change is no
  jump; a tripwire fires on the false→true change only; `filing_overdue` is judged at the last complete search
  date, and not at all when the last search was incomplete or in an outage.
- Idempotence: running `alerts` twice on the same inputs writes nothing the second time.
- Queue ordering on synthetic searches, and that the file holds only KRS numbers.
- Sentry's `before_send` drops a PESEL, a name, a KRS number and a file name from message, frames' locals and
  extra; `init_error_reporting()` without a DSN installs nothing.
- Every schedule and sensor's default status is `STOPPED` (a definitions test).
- The drift and realised-calibration reports are byte-identical on a re-run, and "not evaluable" below
  `min_events`.

## Definition of done

- [x] Owner decisions 0–9 accepted (2026-10-17, as recommended).
- [x] Step A's findings recorded (2026-10-17; Evidently kept, so no ADR).
- [ ] Steps B–H built, with their tests in `make check`.
- [ ] The nightly registry check has run unattended at least once from the schedule, and the chain after it
      wrote alerts, with the counts recorded here.
- [ ] Docs swept (step I).

## Risks

- **Scheduled jobs on a dirty tree fail.** `scoring` refuses one by design. On a development machine the chain
  will often stop there: the sensor reports the refusal and does not retry.
- **Alert fatigue on a distress-weighted list.** List v1 was chosen partly by distress status, so many entities sit
  above any threshold from their first score. Decision 4's "no alert on a first score" keeps the first run quiet.
- **The KRS API or MSiG changes without notice** (ADR 0011). A night that fails retries once (tenacity, transient
  errors only), then waits for the next night.
- **Evidently on Python 3.14** may not exist yet (step A).
- **A sensor chain can run on a half-imported delivery** when the importer fails at its end by design (plan 0014).
  The chain triggers on a *successful* import only.

## Out of scope

- Any RDF fetch by the pipeline (ADR 0013), and importing deliveries without the owner.
- Deploying the site or the report (plan 0016 decision 10), and anything public from the alerts.
- Retraining or promotion on a schedule: a backtest stays a decision.
- `structlog` JSON logs (§6L): Dagster's run logs, kept by decision 0, cover what is run here. Revisit if a job
  ever runs outside Dagster.
- KRZ (behind the WAF, ADR 0011).
