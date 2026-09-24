# 0010 — Phase 5: the point-in-time feature store

**Stage:** Phase 5 (AGENT_SPEC §10: "Feature store with ASOF assembly and blocking leakage tests").
- **Spec stages:** H1 (ASOF assembly) and H2 (leakage tests), §6H; PROJECT_OVERVIEW stage 9.
- **Domain rules:** bitemporality (§4.7), legal tripwires (§4.5), invariant 1 (point-in-time correctness).
- **Output:** the canonical dataset `feature_store` (§5).

**Order:** after plans 0008 and 0009, which are complete. Phase 6 (baseline models, out-of-time backtest) trains
on `feature_store` joined to a frozen label set, so nothing downstream starts before this lands.

## Status: owner decisions made (2026-09-24); steps A and B complete, step C next

## Why

Phase 4 answered *what happened and when*. Phase 5 answers *what an observer could have known on each date*.
That is the project's core correctness claim (TECHNICAL_ARCHITECTURE "Bitemporality"), and the leakage test is
"the single most important test in the repository" (AGENT_SPEC §9.1). Nothing for it exists yet:
- `src/distress_radar/features/` is empty;
- `tests/features/` is empty, so the blocking `test_leakage.py` does not exist;
- `config/statutory/ksh_tripwires.yaml` and `size_thresholds.yaml` are "not written" (`docs/data_inventory.md` §5).

## What there is to build on (seed, 2026-09-23)

- **`financial_statements_canonical`:** 129 statement files for 17 entities, covering 116 entity-years.
  - By form: 88 full, 29 small, 12 micro.
  - **28 files (22%) are graded `quarantined`**, for genuine filing defects (plan 0004).
  - Every fact has `known_from`: the filing's RDF submission date (§4.7). Each filing carries the current year
    and a prior-year column.
- **Coverage by form.**
  - Full and small forms carry every line the ratios below need.
  - Micro forms carry total assets, current assets, receivables, equity and liabilities, but not the equity
    breakdown (share, supplementary, reserve capital, retained result) or the short- and long-term liability
    split. Art. 233 and the liquidity ratios cannot be computed for micro filers: those features are null, never
    imputed (invariant 4).
- **The income statement differs by variant.** Comparative (`IS.COMP.*`), calculation (`IS.CALC.*`), the small
  forms' own lines (`IS.COMP.MALA.*`, `IS.CALC.MALA.*`) and micro (`IS.MIKRO.*`) each name revenue, operating
  result and net result differently. §4.1 forbids deriving one variant from another, so each ratio input is mapped
  per variant.
- **`restatement_events`:** prior-year differences, 130 for the seed.
- **`filing_index` (Postgres):**
  - 538 documents;
  - submission dates for the statements (RDF type 18, 131 documents, 9 corrections);
  - no dates for the other document types, which were never expanded.
- **`legal_events`:** 89 rows in 70 events, each with `known_from`. It has proceedings, deregistration,
  registration, arrears enforcement and curator appointments, but **not yet** board, office or capital changes.
  The KRS extracts do carry those: `dzial2.reprezentacja[].sklad`, `dzial1.siedzibaIAdres` and `dzial1.kapital`,
  each record with the entry numbers that date it.
- **Outcome labels:** set `066d18bbd4cd…` (v2), month-end grid × 12/24 months. Phase 6 joins features to it; this
  plan does not.

## Owner decisions (made 2026-09-24)

The owner accepted all six as recommended, with one change to decision 3: the quarantine exclusion is a
config switch, not a fixed rule. Below, "owner decision N" refers to this list and "plan decision N" to the next.

1. **Features are computed in `src/distress_radar/features/`, with DuckDB run in-process, not in SQLMesh.**
   **Accepted.** The documents disagree:
   - `TECHNICAL_ARCHITECTURE.md` §H picks "DuckDB ASOF JOIN inside SQLMesh incremental models";
   - `DIRECTORY_STRUCTURE.md` §3 puts "computes a feature from canonical data" in `src/distress_radar/features/`,
     "not in `transform/`", and lists `asof_assembly.py` and `feature_definitions.py` there.

   Why `src/`:
   - the blocking leakage test must run the real assembly code in `make check` with no services;
   - feature definitions are driven by config, which is awkward in SQL;
   - the ASOF join is still DuckDB's `ASOF JOIN`, run from Python.

   SQLMesh keeps the DQ and label models. Recorded as **ADR 0012**, and the architecture doc corrected.
2. **For a fiscal year, the statement filed for that year wins; a later filing's prior-year column only fills a
   year with no usable statement.** **Accepted**; this is plan 0006's substitute.
   - The alternative, "latest known value wins", would let a restated comparative silently replace the filed
     figures. The restatement stays visible as a feature (restatement count and size) instead.
   - A correction (`is_correction`) of the year's statement replaces the original from its own `known_from`.
   - A filled year keeps the later filing's `known_from`, and its source kind is recorded.
3. **Quarantined statements are excluded from financial features, by default.** **Accepted, as a switch.** Their
   figures failed a material identity check, so a ratio built on them is noise presented as signal. The filing
   still counts for filing behaviour, which only needs its date, and a `statements_quarantined` count is itself a
   feature. The alternative, using them with a quality flag, keeps 22% more statement files at the cost of
   known-bad inputs.
   - **The owner's change:** the exclusion is a key in the feature-set config,
     `include_quarantined_statements` (`false` in `feature_set_v1.yaml`), so that a later experimental run can
     include them.
   - The switch belongs to the feature set, not to a runtime setting. An experimental run therefore uses its own
     feature-set file, such as `feature_set_v1q.yaml`, so its `feature_set_version` and hash differ, and MLflow
     can never confuse it with v1.
   - When the switch is on, a quarantined statement counts as usable for owner decision 2: the year's filed
     statement wins even if quarantined, and a comparative no longer fills that year. The panel's
     `quality_grade` column records which inputs were quarantined.
4. **Defer `entity_size_class_history`.** **Accepted.**
   - §4.4 needs average employment, and no structured source carries it (`docs/data_inventory.md` gap 8).
   - Balance-sheet total and revenue enter as raw features anyway, so nothing is lost.
   - The alternative: classify on the two financial criteria, with `undetermined` where employment would decide.
     That builds a statutory classification from two of its three inputs.
5. **Defer macro and sector context (A5) to its own plan.** **Accepted.**
   - A5 (NBP, GUS BDL) has no adapter yet.
   - Sector aggregates over 17 entities would be noise, and would leak the seed's own outcomes into its features.
   - The alternative is the NBP reference rate alone: one small adapter, one feature, the same for every entity.
6. **Registry dynamics are built here:** the taxonomy gains board, office and capital changes as signal event
   types. **Accepted.**
   - They come from the same stored extracts, redacted of names but with entry numbers intact, dated by their
     entries.
   - Plan 0008 left them for this phase, and they are the only registry features besides arrears and curators.

## Decisions this plan makes (flag any you disagree with before step C)

1. **The grid is the label grid without horizons:** `(krs, as_of_date)` month-ends from `2012-01-31` (or the
   entity's registration) to its cutoff. Excluded label rows still get features; Phase 6 filters.
2. **Every feature `f` has `f__known_from`:** the latest `known_from` of every fact that contributed to it
   (AGENT_SPEC §5).
   - A feature with no inputs is null, with a null `__known_from`.
   - A non-null feature without a `__known_from` is a contract failure.
   - Nothing is imputed (invariant 4).
3. **ASOF, twice.**
   - **Statements:** for each `(krs, as_of_date)`, the latest fiscal year with a statement known by then, then the
     one before it and so on for trends. One DuckDB `ASOF JOIN` on `known_from` per lag.
   - **Events:** counted over trailing windows ending at `as_of_date`, by `known_from`, never by `event_date`.
     `event_date` can precede its publication by 21 months, and using it is the classic leakage bug (§9.1).
4. **Feature set v1** (`config/features/feature_set_v1.yaml`): each feature's name, family, inputs and
   definition. The file name is the `feature_set_version`, and its hash is recorded with every build, like label
   versions.
   - **Financial ratios:**
     - current and quick ratio;
     - debt-to-assets and equity-to-assets;
     - ROA, ROE, net and operating margin;
     - asset turnover;
     - working capital to assets;
     - operating result over financial costs (not "interest coverage": the statements do not separate
       interest);
     - revenue and equity growth, 1, 2 and 3 years;
     - net-margin volatility over 3 years.
   - **Construction:** receivable days and short-term liability days (revenue-based), and short-term accruals and
     prepayments to assets (UoR art. 34a contract accounting sits there). There is no backlog proxy: nothing in
     the data carries one.
   - **Legal tripwires (§4.5):** `art233_triggered` and `negative_equity`. Art. 397 is configured for S.A. but
     does not apply to v1. Loss coverage history needs resolutions (text, Phase 7).
   - **Filing behaviour:**
     - days from fiscal year-end to filing, for the latest year;
     - missing years, counted only once their filing deadline has passed by `as_of_date`. The deadline is
       statutory: year-end + 6 months to approve + 15 days to file (UoR art. 53, 69), with the COVID-era
       extensions. It lives in `config/statutory/filing_deadlines.yaml` (invariant 7);
     - late filings;
     - corrections;
     - the latest statement filed as PDF (`needs_pdf_tier`);
     - statements quarantined;
     - restatements and their total size.

     Auditor change needs the audit report's text (Phase 7).
   - **Registry dynamics:** board changes, office moves and capital changes in 12 and 36 months; arrears
     enforcements in 12 months; curators ever appointed; days since the last registry entry.
   - **Legal history:** petition-stage events and proceedings closed before `as_of_date`, by class. An entity still
     in a proceeding is excluded by the labels, so these only describe closed episodes.
5. **Ratio inputs are mapped per variant in config** (`config/features/line_items_v1.yaml`): each input, for
   example `revenue` or `operating_result`, names its chart code per income-statement variant and form. A load
   check fails on a code the chart does not define. Denominators of zero give null.
6. **Tripwire ratios live in `config/statutory/ksh_tripwires.yaml`** (invariant 7), dated per statute. Art. 233:
   accumulated losses (retained result plus the year's result, when negative) exceed supplementary capital, plus
   reserve capital, plus half the share capital.
7. **`feature_store` is Parquet under `WAREHOUSE_DIR/feature_store/`:**
   - partitioned by `as_of_date` year;
   - a Pandera contract (`FEATURE_STORE`), which also requires `__known_from ≤ as_of_date`;
   - the same atomic, deterministic write as the other datasets (ADR 0008);
   - an `ext.feature_store` view for SQLMesh, for coverage marts only.
8. **Seed scale.** Nothing here enumerates anything. Features are recomputed in full on each run.

## Out of scope

- Text signals (Phase 7, stage G), and with them loss coverage history, auditor change and going-concern flags.
- Macro and sector features (owner decision 5), and size class (owner decision 4).
- Joining features to labels, training, and backtesting (Phase 6).
- Growing the universe beyond the seed, and KRZ (deferred, plan 0009).

## Steps

### A. ADR 0012 and doc reconciliation

ADR 0012 covers where features are computed (owner decision 1), and `TECHNICAL_ARCHITECTURE.md` §H is corrected to
match. `DIRECTORY_STRUCTURE.md` gains `config/features/`.

### B. Config

- `config/features/feature_set_v1.yaml` and `config/features/line_items_v1.yaml` (plan decisions 4, 5), with
  Pydantic loaders that reject:
  - an unknown chart code;
  - a feature whose inputs its family cannot supply;
  - a duplicate name;
  - a feature set without an explicit `include_quarantined_statements` (owner decision 3).
- `config/statutory/ksh_tripwires.yaml` (plan decision 6) and `config/statutory/filing_deadlines.yaml` (plan
  decision 4, filing behaviour), each with effective dates and a loader test across them.
- Owner decision 6: the taxonomy gains `board_changed`, `office_moved` and `capital_changed` (stage
  `signal`) with their KRS locators, and `from_krs_extract` dates them by entry. Fixture tests on the four
  committed extracts cover it.

**As built (2026-09-24).** Loaders in `features/config.py` and `features/statutory.py`; tests in
`tests/features/`. Choices made while building, all in config and easy to revisit before step D:
- **Registry changes.** A taxonomy mapping may carry a `change`: `replaced` (an entry introduces a
  record while removing one that differs on the `compare` fields) or `membership` (a board member
  joins or leaves). The state at the first entry is never a change, and an exit's removals are
  not one either. The seat is compared on locality, so a new street address in the same town is
  not a move; a change of function inside the board is not a board change. One replacement (one
  member leaves, one joins, in one entry) is one `dedup_group_id`, which is what the counts use.
- **`legal_event_coverage` leaves the three out**, like `registered`: only KRS records them, and
  counting them would show a source break that is not one.
- **Revenue is each layout's headline line** (`IS.COMP.A`, `IS.CALC.A`, `IS.MIKRO.A`). The
  comparative and micro lines include inventory change and own work capitalised, and the
  calculation line does not; summing sub-lines to align them is the derivation §4.1 forbids.
- **Art. 233 leaves out the revaluation reserve** (A.III), counting supplementary capital and the
  other reserve capitals (A.IV). Commentaries differ; including it is one line and a new version.
- **Filing deadlines** are verified against Dz.U. 2020 poz. 570 as amended: +3 months to approval
  for balance-sheet dates 2019-09-30…2020-04-30, 2020-09-30…2021-04-30 and 2021-09-30…2022-04-30;
  the 15 days to file were never extended.
- **Missing years and late filings look back 3 fiscal years** (`lookback_years`).
- **Restatement size is the largest single-line change over total assets**, not the total: lines
  nest, so a sum counts one change several times.
- **Not built: days since the last registry entry.** No dataset holds the extract's entry list;
  `legal_events` has only the entries that mapped to an event. It needs the entry dates exposed
  first, and is left for a later feature set.

### C. The point-in-time financial panel

`src/distress_radar/features/panel.py`, pure functions over the canonical Parquet and `filing_index`:
- one row per `(krs, fiscal_year, input)`, with `value`, `known_from`, `source_kind` (`filed` | `correction` |
  `comparative`), `source_document_hash` and `quality_grade`;
- the rule of owner decision 2 applied: a comparative only where no usable statement exists for the year;
- quarantined statements excluded per owner decision 3, and counted, unless the feature set's
  `include_quarantined_statements` is on.

Tests use synthetic statements for:
- a correction replacing its original from its own date;
- a comparative filling a missing year with the later `known_from`;
- a restated comparative *not* replacing a filed year;
- a quarantined statement leaving its year empty;
- with the switch on, the same quarantined statement filling its year, flagged by `quality_grade`.

### D. Feature families

`src/distress_radar/features/feature_definitions.py`, one function per family. Each returns
`(krs, as_of_date, feature, feature__known_from)` for the grid:
- financial and construction ratios, from the panel via the ASOF join;
- tripwires;
- filing behaviour;
- registry dynamics;
- legal history.

Each family has tests on hand-computed values: one entity per form, including a micro filer whose liquidity and
Art. 233 features are null.

### E. ASOF assembly and the dataset

`src/distress_radar/features/asof_assembly.py`:
- builds the grid;
- runs the families;
- assembles the wide `feature_store` with every companion column;
- validates the contract;
- writes the Parquet.

A second build on unchanged input is byte-identical.

### F. The leakage tests (blocking)

`tests/features/test_leakage.py`, run by `make check` with no services, on a synthetic warehouse built in the
test:
- **§9.1 as written:** every non-null feature's `__known_from ≤ as_of_date`, on every row.
- **Per family:** no contributing source violated the bound. This is checked independently of the companion
  columns, by recomputing each family with every fact after `as_of_date` deleted and requiring identical values.
- **Traps:**
  - a statement filed the day *after* an `as_of_date`;
  - a legal event whose `event_date` is before `as_of_date` but whose `known_from` is after;
  - a correction filed later.

  None may reach that row.
- **The test must bite:** a deliberately leaky variant (joining events on `event_date`) must fail it.

The same assertion runs on the live store as a Dagster asset check.

### G. Dagster wiring

- Asset `feature_store` (group `features`), downstream of `financial_statements_canonical`,
  `restatement_events` and `legal_events`.
- Asset checks: leakage (the §9.1 assertion on live data), and feature coverage by family (the share of
  non-null values).
- The job `legal_to_labels` stays as it is; a `features` job runs parsing outputs and legal events into the store.

### H. Docs

- **AGENT_SPEC:** §5 `feature_store` as built; §6H with the families as built and what is deferred.
- **`docs/data_inventory.md`:** §5 statuses for the tripwire config, and gap 8 cross-referenced.
- **The README:** status, and how to run the `features` group.
- **This plan's status.**

## Tests

- **Leakage (blocking):** step F.
- **Panel:** step C's cases.
- **Families:** hand-computed values per form. A zero denominator gives null. Art. 233 is tested at its boundary
  (losses exactly equal to the threshold do not trigger).
- **Config:** unknown chart codes rejected; tripwire effective dates resolved across a boundary.
- **Contract:** a non-null feature without `__known_from` fails, and so does `__known_from > as_of_date`.
- **Idempotence:** re-running gives byte-identical Parquet.

## Definition of done

- [x] Owner decisions 1–6 made (2026-09-24).
- [x] ADR 0012 written.
- [x] Feature set v1, the line-item map, the tripwire config and the filing-deadline config written, validated and
      tested.
- [ ] The point-in-time panel built, with the filed-wins rule and the quarantine exclusion, tested.
- [ ] All families in feature set v1 computed for the seed, with `__known_from` on every feature.
- [ ] `tests/features/test_leakage.py` exists, runs in `make check`, blocks, and is shown to fail on a leaky
      variant.
- [ ] `feature_store` persisted and contracted; the Dagster leakage check passes on the live store.
- [ ] `make check` and `make test-integration` green; re-running is byte-identical.
- [ ] Docs from step H updated.

## Risks

- **Seventeen entities make every ratio anecdotal.** This phase proves correctness, not predictive value. Coverage
  per family is reported so that Phase 6 does not over-read a sparse feature.
- **Micro and small filers carry fewer lines.** Features are null there, not imputed. Models must handle
  missingness that is structural (the form) rather than random, and coverage by form is part of the report.
- **Filing deadlines move.** Fiscal years that are not calendar years, and the COVID-era extensions (2020–2021),
  shift them. The deadline is computed from each statement's own period end, and the extensions are dated
  entries in `config/statutory/filing_deadlines.yaml`.
- **Registry dynamics depend on redacted extracts keeping entry numbers.** They do (ADR 0009 addendum), and the
  fixture tests pin it.
- **The leakage test is only as good as its traps.** The mutation check in step F is what keeps it honest.

## After Phase 5

Phase 6 (AGENT_SPEC §10): baseline and classical models on `feature_store` joined to a frozen label set,
evaluated out-of-time only, with Brier score reported beside AUC, and the four IDs logged to MLflow. The four IDs
are the code commit, the data snapshot hash, the `label_set_hash` and the `feature_set_version`.
