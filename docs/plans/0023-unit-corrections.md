# 0023 — Unit corrections: filings typed in the wrong unit, corrected by review and flagged by a check

**Stage:** C2 (AGENT_SPEC §4.2, §6C2: units) and E2 (§4.3, PROJECT_OVERVIEW stage 5: "suspected unit
mistakes (values off by a factor of 1,000)"). It closes plan 0020's outstanding item 2.
- **Domain rules:** the unit is fixed by the structure (§4.2), and a filer can contradict it without
  breaking any identity. Nothing is corrected without review (invariant 4: no imputation). A correction
  is versioned config, never code (invariant 7). A corrected fact keeps its source document hash and
  element path, and its file gets a run id of its own (invariant 3). Re-running reproduces the same
  output (invariant 5).
- **Inputs:** `financial_statements_canonical` and the stored statement files it is mapped from.
- **ADRs:** 0005 (structures; its fourth addendum records this), 0006 (no DDL here), 0009 (the
  corrections file names documents by hash and the pipeline's own member tokens, never a filer's file
  name).

## Status: complete (2026-10-21): owner decision taken (correct through a reviewed list, flag the rest); steps A–D built and run; 2026-10-23: the sign check built and run (§ "The sign check"); the Art. 233 tripwire read from equity, `feature_set_v8` (§ "The Art. 233 tripwire"), which `backtest_v10` names

## Findings (2026-10-21)

`notebooks/exploration/unit_error_census.py` (plan 0020) found 4 filings of 3 entities whose year sits
about 1,000 from the entity's other years. A second, independent test settled them. An sp. z o.o.'s
share capital (`BS.EQUITY_LIABILITIES.A.I`) rarely moves, and the statutory minimum (5,000 zł, 50,000
before 2009) bounds it from below.

- **Three filings are złoty typed into a thousands form**, so C multiplied every amount by 1,000:
  - `0000310926` FY2021 and FY2022 (`small-2018-v1-2-tys`): share capital 50,000,000 against 50,000 in
    every other year;
  - `0000602022` FY2020 (`micro-2018-v1-0-tys`, already quarantined for an identity failure): share
    capital 5,500,000 against 5,500.

  In each, the same balance sheet reported in a neighbouring złoty filing (current year, or the next
  filing's prior-year column) is 1,000 times smaller on 98–100% of its lines.
- **`0000790991` FY2025 is not an error.** Its share capital is 5,000 zł in every year, on both the
  full and the micro form. It is a company of about 700 zł of assets that grew, and its own prior-year
  column agrees. The census's "own year off from its other years" is not decisive on its own: the
  majority of years can be the wrong side.
- **Nothing goes the other way.** No share capital is impossibly small by a factor of 1,000: the two
  below 5,000 zł (4,000; 2,000 then 10,000) are steady, so they are not thousands typed into a złoty
  form. The other 20 thousands-form filings are plausible in size (two filers file only in thousands,
  with capitals of 52.6 million and 29 thousand złoty).
- **What they distorted:** not the ratios, since every line of a file is scaled alike, but what
  compares years: revenue and equity growth, the size class (`0000310926`'s two "large" years, plan
  0020 step E) and the restatement features (61 spurious restatement events).
- **Found beside it, not handled here:** 90 filings of 27 entities carry a negative share capital. All
  have negative equity and 83 still sum to it, so they are internally consistent. That is stage 5's
  "sign errors", a check of its own.

## Owner decision (2026-10-21)

**Correct through a reviewed list, and flag new suspects without correcting them.** *Alternatives
offered:* quarantine every flagged file (losing its years until refiled), flag only (wrong figures stay),
or leave as is.

## Steps

### A. The corrections file

`config/mappings/unit_corrections_v1.yaml`, the highest version in use, like the XSD tolerances. It holds
the check's thresholds and one entry per reviewed file: id, document hash, member, structure version,
declared and actual multiplier, evidence, reviewer and date. `canonical_schema.UnitCorrections`
refuses a correction that changes nothing, a file listed twice, an unknown structure version, or a
declared multiplier the structure does not have.

### B. Applying a correction (C2)

- `parse_statement` takes an optional correction and maps with its `actual_multiplier`. It refuses
  (`unit_correction_mismatch`, quarantined at C2) a correction whose structure version or declared
  multiplier is not the file's own, so a stale entry fails loudly.
- `MappingConfig.file_spec_hash`: a corrected file's spec hash is its version's hashed with the
  correction. The file gets its own `parsed_documents` row and run id, so its facts never claim a run
  that produced other values. Every other file's hash is unchanged.
- The asset logs `unit_corrected` (C2) in `quarantine_events` and caps the file's grade at `warn`,
  as a tolerated XSD violation is. The SQLMesh `quarantine` model leaves the reason out of the current
  set, with a test.

### C. The check (E2)

`parsing/unit_plausibility.py`. For each balance sheet reported twice, a filing's current year against
a later filing's prior-year column for the same period, a pair is scaled when at least
`min_shared_lines` non-zero lines are shared and at least `min_share_scaled` of them lie within
`tolerance_log10` of 10^±3. The suspect is the side whose share capital sits about 1,000 from the
median of the entity's other filings'; when that cannot tell, both sides are. A suspect is logged
`unit_suspected` (E2), capped at `warn`, and never corrected: the owner reviews it and, if confirmed,
lists it in a new corrections version. Thresholds: 0.5, 0.8, 5 lines.

### D. Run on the stores and rebuild downstream

## Progress

- **Run (2026-10-21):** `financial_statements_canonical` and `restatement_events` re-materialized.
  - 3 `unit_corrected` rows, one per listed file, and **no `unit_suspected`**: once corrected, nothing in
    the store disagrees by a factor of 1,000.
  - `0000310926` FY2021 and FY2022 now read 2.72 and 2.95 million zł of assets with 50,000 zł share
    capital, graded `warn`.
  - `0000602022` FY2020 reads 0.80 million zł with 5,500 zł, still quarantined for its earlier identity
    failure.
  - `restatement_events` 11,740 → 11,679 rows (`0000310926`: 71 → 10).
  - Grades: 2,041 files `pass`, 396 `warn`, 407 `quarantined`.
- **Downstream (2026-10-21):**
  - `entity_size_class_history`, `parsed_documents_current`, `quarantine`, `dq_mart` and
    `dq_mart_coverage` (run `50d67ee5`).
  - Then the `features` job (run `6c7af260`): `leakage` passed, `feature_store` 85,450 rows over 806
    entities (`feature_set_v6`).
  - `0000310926`'s FY2021 and FY2022 classes are no longer `large`, which came from the ×1,000 assets.
    They are `undetermined` (`employment_decides`): micro or small, and employment is not filed for
    those years. No entity in the store is classed large now (medium 50, small 309, micro 844,
    undetermined 1,220).
  - Scores are not re-run here; the next scheduled `scoring` reads the rebuilt store.
- **Tests:** `tests/parsing/test_unit_corrections.py` (the config's refusals, the corrected parse, the
  per-file hash, the check on invented filings, the tie-break and its fallback);
  `transform/tests/test_quarantine.yaml` (neither reason quarantines).

## Out of scope

- ~~Negative share capital (above): a sign-plausibility check, not built.~~ *Built 2026-10-23 (owner's
  request), § "The sign check".*
- Re-running `backtest_v9`: its features move for one population entity (`0000310926`), so its stored
  report stays a statement about the data it ran on; `backtest_v10` will run on the corrected store.

## The sign check (2026-10-23)

Asked for by the owner after plan 0013's K1-K3, as the cheap check to have before list v3's filers arrive.

- **What it checks.** The balance-sheet forms carry amounts, so only the lines in
  `config/mappings/sign_check_v1.yaml` may be below zero: total equity, the revaluation reserve, the
  prior years' and the year's result, the write-offs from net profit, and their `.USER` detail sums.
  `parsing/sign_plausibility.py` flags a file with any other balance-sheet line below zero, in any column:
  logged `sign_suspected` (E2), capped at `warn`, never corrected, left out of the current `quarantine` set
  (as `unit_suspected` is). `MappingConfig` refuses a listed code that is not a balance-sheet code.
- **On the store (2026-10-23):** 179 of 2,848 files, 70 entities; for 83 the one finding is the share
  capital. Run the same day (`financial_statements_canonical`, then `parsed_documents_current`,
  `quarantine`, `dq_mart`, `dq_mart_coverage`): 179 `sign_suspected` rows; grades 1,928 `pass`, 513
  `warn`, 407 `quarantined` (unchanged). Features read no `warn`, so nothing else moves.
- **What the negative share capitals are (finding).** Not flipped signs. In 82 of the 90 current-year
  cases the equity lines still sum to the total, with the prior years' result (`A.V`) at zero or null:
  the filer has put the accumulated losses into the share capital line (`0000270680`: each year's share
  capital is the year before's total equity). That misfiling reaches two features (§ "The Art. 233
  tripwire", below). *Corrected 2026-10-23:* this paragraph first said the tripwire read a profit year as
  not triggered; it does not. Half a negative capital is a negative threshold, so version 1 reads every
  such filing as triggered, which is right by accident, since all of them have negative equity.

## The Art. 233 tripwire (2026-10-23): `ksh_tripwires_v2`, `feature_set_v8`

Asked for by the owner after the sign check's finding: read the tripwire from equity when equity is
negative, and leave it null when a capital is negative and equity is not.

- **The rule.** Equity is the capitals the threshold names (share, supplementary, reserve) plus the
  revaluation reserve, the write-offs from net profit and the accumulated result. The law keeps the
  three capitals at or above zero (share capital at least 5,000 zł, KSH art. 154). So when equity is
  below zero and the revaluation reserve and write-offs sum to zero or more, the accumulated loss exceeds
  the capitals in full, let alone the threshold: triggered, whichever line the filer booked the loss on.
  With equity at or above zero, a capital filed below zero leaves the split unknown: null (invariant 4).
  An offset the statement does not carry counts as zero, since the equity total it sums to holds none.
- **Versions.** `config/statutory/ksh_tripwires_v2.yaml` (`negative_equity`, `non_negative`), named by a
  feature set's new `ksh_tripwires` key; a set without it reads and hashes `ksh_tripwires.yaml` as
  before, so v1–v7's hashes are unchanged (tested). `line_items_v4` is v3 plus `revaluation_reserve`
  (A.III) and `profit_write_offs` (A.VII). `feature_set_v8` is v7 with `ksh_tripwires_v2`,
  `line_items_v4`, and `retained_earnings_to_assets` null when the share capital is negative (the ratio's
  new `null_when_negative`): its numerator, A.V + A.VI, misses the losses booked on the capital line.
- **What it changes (measured on the store, current-year filings not quarantined, 2,438):** for the
  negative-capital filings with the full breakdown, nothing: version 1 already read them as triggered (by
  the negative threshold). 241 filings go from null to triggered: negative equity with a breakdown line
  not filed (micro forms, which carry none, or `A.V` left out); 30 of them have a negative capital. One
  goes from triggered to null. So the tripwire gains coverage where `negative_equity` already says the
  same, and is right for the right reason; the substantive fix is `retained_earnings_to_assets`, which
  read these filings' accumulated losses as zero.
- **`backtest_v10`** names `feature_set_v8`: recorded before any run, so it is its version, not a change.
  `feature_store` is rebuilt with `FEATURE_SET_VERSION=feature_set_v8` before it runs. The default
  (`feature_set_v6`, for the scheduled scoring) is unchanged.
- **Not done:** the KRS extract's registered share capital as the split's source, which would also give a
  null-capital filing its tripwire and its retained earnings. Worth it only if list v3 brings negative
  capitals with non-negative equity (none in the store).

