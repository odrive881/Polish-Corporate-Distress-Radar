# 0020 — `entity_size_class_history`: the statutory size class per fiscal year

**Stage:** C/E (AGENT_SPEC §4.4, §5 `entity_size_class_history`), from the canonical facts. It undoes plan 0010
owner decision 4's deferral (owner, 2026-10-18), and writes `config/statutory/size_thresholds.yaml`.
- **Domain rules:** the size class is computed per fiscal year from the filed statement. It is never taken from a
  registry, and never inferred from the form filed (§4.4; choosing the micro form is the filer's assertion).
  Thresholds are dated config, never constants (invariant 7). A missing input stays missing, and a class it
  could change stays undetermined, never estimated (invariant 4). Every row carries its source document and run
  (invariant 3). A class for a year is known no earlier than every fact it rests on (`known_from`, §4.7).
- **Inputs:** `financial_statements_canonical` (balance sheet total and the revenue lines, current-year and
  prior-year columns); `statement_disclosures` (`average_employment`, wariant 2 only); `legal_events` (the
  registration, for the first-year rule).
- **ADRs:** 0005 and its addenda (the forms, the `.R2025` lines, the bodies), 0006 (plain DDL; this plan has
  none: the output is a Parquet dataset).

**Order:** after plan 0019 (active, waiting on the owner). It does not wait for it, and changes no backtest,
feature set or population: using the class (a feature, the segment's `size_classes` filter) is decision 9's
later step.

## Status: active (2026-10-18): owner decisions 0–9 accepted (1 and 7 as asked, the rest as recommended, 2026-10-18); steps A–F built and run (§ "Progress"); decision 9's later step answered by the owner (2026-10-20): the class becomes a feature (`feature_set_v7`), not the segment's filter, since list v3's random draw may bring more small and medium companies (201 of 268 decided population entities are micro); the feature built (2026-10-20, § "The feature"); open: a backtest pinning `feature_set_v7` (the owner's), and a unit-error check for the filer found in step E

## The feature (2026-10-20): `feature_set_v7`

Built on the owner's answer to decision 9 (2026-10-20). Decisions taken while building it, each to flag if
unwanted:

1. **One feature, a rank.** `size_class`: 0 micro, 1 small, 2 medium, 3 large (`count` dtype). A rank, since the
   classes are ordered and a tree splits them in order; the regression reads none of it unless a backtest names
   it. *Alternative:* one flag per class.
2. **The latest fiscal year known speaks**, by its latest row, dated by the filing that set it (a correction
   replaces from its own date, a deleted filing stops speaking), as the disclosure family reads statements.
   While that year is `undetermined`, the feature is null, never an older year's class (invariant 4).
3. **Recomputed at assembly, never read from the stored dataset.** `feature_inputs` runs
   `entity_size_class_history` on the sources it already loads (2 s on the full store). Found while building:
   the stored dataset (2026-10-18) differed from a recompute in 24 rows' `first_year` and prior-year figures,
   after the night of 2026-10-19 added registrations; the classes were the same, but a stored copy can lag. From
   sources cut at a date, the recompute is the history as an observer could compute it then, so the blocking
   truncation check tests the class itself, not a cut of a table.
4. **The hash.** A set with the class also hashes `config/statutory/size_thresholds.yaml` and its
   `size_class_inputs_v<n>.yaml`; sets without it hash exactly as before (tested).
5. **Not the default.** `FEATURE_SET_VERSION` stays `feature_set_v6`: the store holds one set, and `backtest_v9`
   (which scoring serves) pins v6. Using v7 is a new `backtest_v<n>` pinning it, then a store rebuild.

**Checked:** the leakage tests run v7 (the synthetic statements gain the revenue lines the size rules read, for
v7 only; a size family dated by period end fails the truncation check); unit tests of the rules; `make check`.
On the live stores, read-only (806 grid entities, 85,450 rows): 14,387 rows (16.8%) have a class, 355 entities;
latest values 256 micro, 84 small, 14 medium, 1 large; the size family's truncation check on 26 month-ends
from 2012-01-31 to 2026-08-31 found no difference.

## Progress (2026-10-18): steps A–F built and run

- **Step A:** `config/statutory/size_thresholds.yaml` (`size_thresholds_v1`): three regimes, `uor_2017`
  (fiscal years starting 2017-01-01 to 2018-12-31; a few first years start in 2017, so 2017 is configured, read
  from the consolidated texts Dz.U. 2016 poz. 1047 and 2018 poz. 395, identical in both), `uor_2019` and
  `uor_2024`. `config/mappings/size_class_inputs_v1.yaml`: 11 revenue rules. The loader proves each one against
  the bodies with the specs' `.R2025` overrides applied, so a rule no statement could satisfy is refused.
- **Step B:** `parsing/size_class.py`. A criterion becomes a set of *regions* between the regime's three
  thresholds. A year becomes the set of *levels* it can be at (the first boundary it is within), and a class is
  decided only when every level, prior level and carried class gives the same one. Unknowns in different years
  are treated as independent: this can leave a year `undetermined` that a joint test would decide, never the
  reverse. One reason was corrected while running step E: `no_prior_side` is given only when the legal gap is
  the *only* outcome, not merely a possible one (it had been given to 439 years whose figures were missing).
- **Step C:** the asset `entity_size_class_history` (group `parsing`, after `financial_statements_canonical` and
  `legal_events`; not in any job, materialized by hand, README "A new delivery" step 3), and the Pandera contract
  `ENTITY_SIZE_CLASS_HISTORY` (a reason exactly when undetermined, a basis exactly when decided, exact revenue
  equal to its bounds, one row per entity, year and date).
- **Step D:** `tests/parsing/test_size_class.py`, 47 tests: each regime's start date and values, each statute
  case, each revenue rule, the loaders' refusals, point in time (a correction adds a row and leaves the earlier
  one unchanged; a later filing of 2024 adds a row for 2025 through the carry-over), quarantined statements
  ignored, the form never read, and a property test. In that test, 1,500 random years with figures missing or
  bounded are drawn; each decided one is filled 20 times with values on and around every threshold, and its
  class never changes.

### Step E: measured over the warehouse (2026-10-18, 2,848 files, 491 entities)

2,423 rows over 2,408 entity-years (15 corrections changed a result). The latest result per entity-year:

| Fiscal year | Micro | Small | Medium | Large | Undetermined |
|---|---|---|---|---|---|
| 2018 | 52 | 26 | 12 | 0 | 99 |
| 2019 | 67 | 39 | 5 | 0 | 112 |
| 2020 | 87 | 37 | 3 | 0 | 131 |
| 2021 | 100 | 41 | 3 | 1 | 162 |
| 2022 | 118 | 35 | 8 | 1 | 187 |
| 2023 | 136 | 39 | 9 | 0 | 207 |
| 2024 | 142 | 46 | 5 | 0 | 186 |
| 2025 | 141 | 41 | 5 | 0 | 122 |
| 2026 | 1 | 1 | 0 | 0 | 1 |

- **Undetermined, by reason:** before FY2025, 414 `employment_decides` and 670 `inputs_missing` of 2,096 years
  (52% undetermined). From FY2025, 7 and 116 of 312 (39%). No `no_prior_side`, `revenue_bounds` or `no_regime`
  as a latest result. Decided: 1,158 `statutory`, 43 `by_analogy` (medium or large before FY2024).
- **The class against the form filed:** micro-form statements are never classed above micro (355 micro, 327
  undetermined). Full-form statements: 309 micro, 276 small, 50 medium. Small-form: 180 micro, 29 small, and 2
  large (below). Choosing a fuller form than the class needs is allowed, so these are not errors, but they show
  why §4.4 never reads the form.
- **The backtest population:** `backtest_v8`'s 370 entities, recomputed from the stores (its pinned hash
  matches). 268 have a decided year. In their latest decided year, **201 are micro**, 58 small, 8 medium and 1
  large, and 102 have no decided year. The segment declares `size_classes: [small, medium]`, so most of what
  the models are trained and tested on is outside it by statute. This is decision 9's question.
- **Found: a filer's unit error.** `0000310926` filed its FY2021 and FY2022 statements on the thousands schema
  (`small-2018-v1-2-tys`) with amounts in złoty, so C multiplied them by 1,000: total assets of 2.7 and 2.95
  billion PLN between 2.2 million (FY2020) and 3.0 million (FY2023). Both files are graded `warn` and `pass`, so
  the figures reach `feature_store` too. That is where the two "large" years come from. Stage 5's plausibility
  check for unit mistakes (PROJECT_OVERVIEW, "values off by a factor of 1,000") is not built. Not fixed here: a
  check of its own, the owner's to schedule.

### Outstanding (2026-10-18): the owner's

1. **Decision 9's later step:** whether the class becomes a feature, or the segment's `size_classes` filter
   on the universe or a backtest population (201 of 268 decided population entities are micro).
2. **The unit error of `0000310926`** (step E): whether to build a check for unit mistakes (values off by a
   factor of 1,000 between years, PROJECT_OVERVIEW stage 5), and what to do with its FY2021 and FY2022 files
   meanwhile: grade them down, quarantine them, or leave them. Until then their figures stay in the canonical
   facts and `feature_store` as filed.

## Why

§4.4 and §5 specify the dataset, and plan 0010 deferred it (decision 4, 2026-09-24) because average employment has
no structured source before FY2025. Three things have changed:

- **The class matters more now.** The segment spec declares `size_classes: [small, medium]` and nothing enforces it:
  micro filers are in the universe, and 113 per horizon cannot be scored (plan 0016). List v2's distress group
  adds companies that shrink in their last years (ADR 0014 addendum, "Things to expect").
- **Employment is filed from FY2025** (wariant 2, `P_8` and `P_7`, in `statement_disclosures`): 296 statements so
  far.
- **The owner accepted an exact rule where employment is missing** (decision 1). The law needs two of three
  criteria. Where the two financial figures agree, employment cannot change the result, so the class is decided
  without it. Only where they disagree does the year stay `undetermined`. This is not classifying on two of three
  inputs: it is classifying only where the third input could not matter.

## The statute (checked 2026-10-18)

Read from the Sejm's ELI texts (`api.sejm.gov.pl/eli`). For a `sp. z o.o.`, a class is tested on the fiscal year
**and the year before it**, and on the first year alone for an entity in its first year of activity. A threshold
is "not exceeded" at or below its value. The three criteria are total assets at the year's end, net revenue from
sales of goods and products for the year, and average employment in full-time equivalents.

| Fiscal years starting | Micro (assets / revenue / FTE) | Small | Medium and large | Source |
|---|---|---|---|---|
| 2018-01-01 to 2018-12-31 | 1,500,000 / 3,000,000 / 10 | 17,000,000 / 34,000,000 / 50 | not defined | art. 3 ust. 1a, 1c, text Dz.U. 2018 poz. 395 |
| 2019-01-01 to 2023-12-31 | 1,500,000 / 3,000,000 / 10 | 25,500,000 / 51,000,000 / 50 | not defined | Dz.U. 2018 poz. 2244 art. 9 and art. 43 ust. 1; text Dz.U. 2023 poz. 120 |
| from 2024-01-01 | 2,000,000 / 4,000,000 / 10 | 33,000,000 / 66,000,000 / 50 | medium within 110,000,000 / 220,000,000 / 250; large above | art. 3 ust. 1 pkt 1a–1d, Dz.U. 2024 poz. 1863 art. 1 and art. 14 ust. 3; unchanged in text Dz.U. 2026 poz. 522 |

Three things in the text matter for the code:

1. **The pre-2024 law has no medium or large class.** Its micro and small classes (art. 3 ust. 1a and 1c) are
   also the approving body's option ("w stosunku do których organ zatwierdzający podjął decyzję"). What the
   pipeline classifies is eligibility, not the option taken.
2. **The pre-2024 continuation rules depend on the form filed** (ust. 1b and 1d: a unit that filed as micro last
   year stays micro unless …). §4.4 forbids reading the form, so they are not applied (decision 5).
3. **From FY2024 a status is kept until two years beyond it** ("jednostka traci status …, jeżeli w roku obrotowym
   … oraz w roku poprzedzającym … przekroczyła co najmniej dwie z trzech tych wielkości"). Gaining a class needs
   both years within it; losing one needs both years beyond it. So a class can depend on the year before's class
   (decision 5).

The revenue quantity has its own definition from FY2024 (art. 3 ust. 1 pkt 30a): net of discounts and rebates,
without VAT. Sales of materials are no longer part of it (pkt 32 lit. b), so forms before wariant 2, which report
goods and materials on one line, do not carry the exact quantity (decision 2). Not checked: thresholds before
2018. No statement in the warehouse is for an earlier year, and the loader refuses a year with no regime.

## What there is to build on (2026-10-18)

- **Canonical facts:** 2,848 statement files, fiscal years 2018–2026, each with `current_year` and `prior_year`
  columns (58,517 `prior_year_restated` facts too). `BS.ASSETS` is on every body.
- **Revenue lines carried** (current year, by file):

  | Lines | Files | What they give |
  |---|---|---|
  | `IS.COMP.A`, `.A.I`, `.A.IV` (or `.A.IV.R2025`) | 1,166 | products + goods (pre-2025: goods and materials) |
  | `IS.MIKRO.A` | 649 | a headline with change in inventories and own work in it |
  | `IS.COMP.A`, `IS.COMP.MALA.A.I` | 527 | small form: sales of products, goods and materials, one line |
  | `IS.COMP.A`, `.A.I` (no `.A.IV`) | 155 | products only |
  | `IS.CALC.A` | 148 | products, goods and materials |
  | `IS.COMP.A` only | 72 | the headline alone |
  | none | 67 | no income-statement revenue line |
  | `IS.CALC.A.R2025` | 44 | products and goods: the exact quantity |
  | `IS.COMP.A`, `.A.IV` (no `.A.I`) | 20 | goods only |

- **Employment:** `average_employment` in `statement_disclosures`, 296 rows, from fiscal year 2023 (wariant 2
  files, including some filed for earlier years in the new structure). Current year only: no form carries the
  prior year's.
- **The pattern to follow:** `parsing/restatements.py` and its asset (`dagster_defs/assets/parsing.py`), a Parquet
  dataset under `WAREHOUSE_DIR` built from the canonical facts graded `pass` or `warn`.

## Owner decisions

1. **Missing employment: exact where decidable.** **Accepted (owner, 2026-10-18).** Reverses plan 0010 decision
   4. A year gets a class only where no value of a missing input could change it. Otherwise it is
   `undetermined`, with the reason.
7. **Medium and large: the UoR bounds.** **Accepted (owner, 2026-10-18).** From FY2024, the statute's medium and
   large (110m / 220m / 250). Before FY2024 the statute has none (§ "The statute", 1), so see decision 8.

Accepted as recommended (owner, 2026-10-18):

0. **Where the code and the output live.** *Recommended:* `src/distress_radar/parsing/size_class.py` (pure,
   beside `restatements.py`), the asset `entity_size_class_history` in `dagster_defs/assets/parsing.py`
   (downstream of `financial_statements_canonical`, `statement_disclosures` and `legal_events`), and the dataset
   in `WAREHOUSE_DIR/entity_size_class_history/fiscal_year=YYYY/`. The thresholds go in
   `config/statutory/size_thresholds.yaml` (already in DIRECTORY_STRUCTURE). The input mapping goes in
   `config/mappings/size_class_inputs_v1.yaml`: which lines make each criterion is engineering, not statute.
2. **Revenue as bounds, not a guess.** The statute's revenue is one line only on `IS.CALC.A.R2025` statements
   (44). Everywhere else the filed lines bound it. *Recommended:* each statement gives revenue a lower and an upper
   bound, from rules in the inputs config. A criterion is decided when both bounds are on the same side of its
   threshold, and is unknown otherwise, as for missing employment. Per body:
   - **Calculation, from wariant 2:** `IS.CALC.A.R2025`, exact.
   - **Calculation, before:** at least `IS.CALC.A.I` (products, if filed), at most `IS.CALC.A` (it adds goods
     *and materials*).
   - **Full comparative:** at least `A.I` (products), at most `A.I + A.IV` (`A.IV` is goods and materials before
     wariant 2). From wariant 2, `A.I + A.IV.R2025` is exact. A missing sub-line makes the upper bound unknown.
     The headline `IS.COMP.A` is never used: it adds the change in inventories, which can be negative.
   - **Small comparative:** at most `IS.COMP.MALA.A.I` (sales of products, goods and materials), at least
     nothing. So revenue can show that a company is within a threshold, never that it is above one.
   - **Micro:** at most `IS.MIKRO.A − IS.MIKRO.A.1` (the headline less the change in inventories; what is left
     adds own work and materials, both non-negative), at least nothing. Unknown if `A.1` is not filed.

   This adds and subtracts lines of **one statement** to bound a quantity the statute defines. AGENT_SPEC §4.1
   forbids deriving one *variant* from another, and `line_items_v3` sums no lines for features. *Recommended:*
   allowed here and only here, each rule listed in the inputs config and tested against the bodies, because the
   alternative leaves revenue unknown for nearly every statement before 2025. *Alternative:* only lines that are
   the quantity itself (44 statements), which leaves almost every year undetermined until employment is filed.
3. **The year before.** *Recommended:* the prior year's balance sheet total and revenue bounds come from the
   year's own filing: its `prior_year_restated` column where filed, else `prior_year`. That is the filer's
   latest statement of that year, known at the same time as the year itself. The prior year's employment comes
   from that year's own filing (no form carries it as a comparative), if it was known by the year's
   `known_from`. *Alternative:* everything from the prior year's own filing, which loses the year whenever that
   filing is missing.
4. **Which files.** *Recommended:* `pass` and `warn` only, as `restatement_events`: a quarantined file's figures
   are not trusted. A correction is a new row with its own later `known_from`, and the earlier row stays (§4.7).
   A year with only quarantined files has no row.
5. **The two-year rules.** *Recommended:*
   - **Before FY2024:** a class needs both years within its thresholds. The continuation rules (ust. 1b, 1d) are
     not applied: they read last year's form.
   - **From FY2024:** per boundary (micro/small, small/medium, medium/large), both years within → below; both
     beyond → above; one of each → the side the entity was on the year before, under the same regime. With no
     prior side (FY2024 itself, or the first year the entity has a row), the lower boundaries fall to "above"
     (the definitions are sequential: not micro, then small if within small's thresholds), and the top boundary
     is `undetermined` (the law defines neither medium nor large for that case).
   - **First year of activity:** the fiscal year that contains the entity's KRS registration is tested on itself
     alone.
   - **Unknowns** (decisions 1–3) are resolved by trying every value they could take. The class is the one they
     all give, or `undetermined`.
6. **Columns.** *Recommended:* §5's columns (`krs`, `fiscal_year`, `size_class`, `balance_sheet_total`,
   `net_revenue`, `average_employment`, `threshold_config_version`, `known_from`), with `net_revenue` set only
   when exact. Added: `size_class_basis` (`statutory` or `by_analogy`, decision 8); `net_revenue_low` and
   `net_revenue_high`; the prior year's three inputs; `undetermined_reason` (`employment_decides`,
   `revenue_bounds`, `no_prior_side`, `inputs_missing`); `regime` (the YAML entry); and lineage: `document_ref`,
   `source_document_hash`, `source_member`, `ingestion_run_id`. `size_class` gains `undetermined` beside §5's
   four values, and §5 is amended to say so.
8. **Medium and large before FY2024.** *Recommended:* above small, a pre-2024 year is classed medium or large
   against FY2024's bounds, by definition only (both years, no carry-over), with `size_class_basis = by_analogy`,
   so that no reader takes it for the statute. *Alternative:* a single class "above small" before FY2024, which
   is not one of §5's values.
9. **Using the class.** *Recommended:* not in this plan. A feature (`feature_set_v7`) and enforcing the
   segment's `size_classes` on the universe or the backtest population each change downstream results and are
   decisions of their own, taken once step E's measurements are read.

## Steps

### A. Thresholds and inputs config (decisions 0, 2, 7, 8)

- `config/statutory/size_thresholds.yaml`: one entry per regime, keyed by fiscal-year start dates, citing its
  article and Dziennik Ustaw position. Each entry has the classes it defines, their three thresholds as
  `Decimal`s and FTEs, whether a status carries over, and the by-analogy bounds for regimes that define no
  medium or large. A Pydantic loader refuses overlapping or gapped ranges and an unknown class, and gives no
  regime (refused, never defaulted) for a year before the first entry. Its tests cover each boundary date.
- `config/mappings/size_class_inputs_v1.yaml`: per statement body, the lower and upper revenue rules (decision
  2). The loader checks every code against `canonical_chart.yaml` and every rule against the bodies in
  `config/mappings/structures/bodies/`, so that a rule never reads a line its body cannot carry.
- Take `config/statutory/size_thresholds.yaml` off `tests/test_docs.py`'s `PLANNED`.

### B. The classifier (decisions 1, 3, 5)

- `parsing/size_class.py`, pure functions:
  - `criterion(bounds, threshold) → within | beyond | unknown`, exact on `Decimal`s, "within" being ≤.
  - `boundary_side(year, prior, regime, carried) → below | above | undetermined`, enumerating the unknowns.
  - `classify(entity_years, regime_of) → rows`, walking each entity's years in order, so that a carried side is
    the previous year's side under the same regime (decision 5), and a correction re-walks the years after it.
- Inputs in order of precedence, never mixed within one criterion: an exact line, then bounds. No fact is
  imputed.

### C. The dataset and asset (decisions 0, 4, 6)

- The asset `entity_size_class_history` (group `parsing`): reads the canonical facts (`pass`, `warn`),
  `statement_disclosures` and the registration events, and writes the dataset partitioned by `fiscal_year`. Its
  metadata gives counts per class and year, and `undetermined` by reason. Its docstring states inputs, outputs
  and partition scheme. Idempotent: the same inputs give the same bytes.
- A Pandera contract for the dataset in `parsing/contracts.py`.

### D. Tests

- The statute's cases, one test each, on hand-built entity-years: two years within; two beyond; one of each with
  and without a prior side; the first year of activity; the FY2023→FY2024 change of thresholds (FY2024 tested on
  FY2023's figures against the new thresholds); a pre-2024 year above small (`by_analogy`); a top-boundary split
  with no prior side (`undetermined`).
- Decision 1's exactness: missing employment decides nothing when both financial criteria agree; and a test that
  tries every employment value and asserts that the class never changes when the row says it is decided.
- Each revenue rule against a fixture statement of its body: a small-form revenue can show within, never beyond;
  a micro statement without `A.1` gives unknown.
- Point in time: a row's `known_from` is the latest of its inputs', and a later correction adds a row without
  changing the earlier one (added beside `tests/features/test_leakage.py`'s pattern).
- No form is read: the class of a statement filed on the micro form equals the class of the same figures filed on
  the full form.

### E. Run and measure (no decision taken from it here)

- Run the asset over the warehouse. Record here: rows per class and year; `undetermined` by reason, before and
  from FY2025; how often the statutory class differs from the form filed (micro form but not micro, and the
  reverse); and how many entities in the backtest population are micro in their latest decided year. These
  numbers are what decision 9's later step needs.

### F. Docs sweep

- AGENT_SPEC §4.4 (the exact rule, the bounds, the carry-over) and §5 (the columns, `undetermined`); line 436's
  "the size class still waits on employment" note; `docs/data_inventory.md` row 133 and gap 8's consequence;
  plan 0010 decision 4's reversal noted at its status; DIRECTORY_STRUCTURE (the new files); README. Then
  `make docs-check`, and the by-hand read.

## Definition of done

- [x] Owner decisions 1 and 7 accepted (2026-10-18).
- [x] Owner decisions 0, 2–6, 8 and 9 accepted (2026-10-18, as recommended).
- [x] Steps A–D built, with their tests in `make check` (2026-10-18).
- [x] Step E's measurements recorded here (2026-10-18).
- [x] Docs swept (step F, 2026-10-18).

## Risks

- **Bounds may decide little before 2025.** If most statements' revenue bounds straddle a threshold, most years
  stay `undetermined` until employment is filed for two years running (FY2026). Step E measures it. The answer
  is then more data (wariant 2 filings), never a looser rule.
- **A threshold read wrong is wrong everywhere.** Every value in the YAML cites its article and Dziennik Ustaw
  position, checked on 2026-10-18 from the ELI texts. Before 2018 nothing is configured, and the loader refuses
  such years rather than guessing.
- **The filer's figures may be in thousands.** The `-tys` structure versions (23 files) are scaled to złoty at
  parse time (§4.2, plan 0004). A test pins one.
- **The law will change again.** A new regime is a new YAML entry with its dates, never an edit of an old one.

## Out of scope

- Using the class as a feature, filtering the universe by it, or changing any backtest's population (decision 9).
- Employment from the notes' text or the management report (`docs/data_inventory.md` gap 8): separate sources,
  separate plans.
- The audit threshold (art. 64) and the bookkeeping limit (art. 2): other rules, not size classes.
- Capital groups (art. 3 ust. 1 pkt 1e): consolidated statements are not in v1.
