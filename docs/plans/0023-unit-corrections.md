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

## Status: complete (2026-10-21): owner decision taken (correct through a reviewed list, flag the rest); steps A–D built and run

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

- Negative share capital (above): a sign-plausibility check, not built.
- Re-running `backtest_v9`: its features move for one population entity (`0000310926`), so its stored
  report stays a statement about the data it ran on; `backtest_v10` will run on the corrected store.
