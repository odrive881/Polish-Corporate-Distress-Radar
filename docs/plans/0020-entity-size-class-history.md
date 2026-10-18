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

## Status: active (2026-10-18): owner decisions 0–9 accepted (1 and 7 as asked, the rest as recommended, 2026-10-18); the statute checked against the Dziennik Ustaw texts (§ "The statute"); building from step A

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
- [ ] Steps A–D built, with their tests in `make check`.
- [ ] Step E's measurements recorded here.
- [ ] Docs swept (step F).

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
