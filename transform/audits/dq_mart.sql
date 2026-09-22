/* Audits on the `dq_mart` models (plan 0007 step E). Each returns the rows that break it. */

AUDIT (
  name dq_no_cell_below_threshold_unsuppressed
);
/* With a threshold set, no published cell covers fewer entities than it (decision 9). */
SELECT *
FROM @this_model
WHERE NOT suppressed AND entities < @VAR('dq_mart_min_cell_entities');

AUDIT (
  name dq_no_suppression_without_threshold
);
/* The shipped setting is null: then nothing is suppressed. */
SELECT *
FROM @this_model
WHERE suppressed AND @VAR('dq_mart_min_cell_entities') IS NULL;

AUDIT (
  name dq_suppressed_cells_carry_no_measures
);
/* A suppressed cell is kept, with every measure null; `entities` is null exactly there. */
SELECT *
FROM @this_model
WHERE (suppressed AND NOT (@REDUCE(@EACH(@measures, m -> m IS NULL), (l, r) -> l AND r)))
  OR (entities IS NULL) <> suppressed;

AUDIT (
  name dq_mart_outcomes_add_up
);
/* Every file checked has exactly one outcome per check. */
SELECT *
FROM @this_model
WHERE NOT suppressed
  AND files <> passed + failed_material + failed_immaterial + not_applicable;

AUDIT (
  name dq_coverage_adds_up
);
/* Every stored file has one fate, and every parsed file one grade. */
SELECT *
FROM @this_model
WHERE NOT suppressed
  AND (
    files_stored <> parsed + not_yet_mapped + needs_pdf_tier + quarantined_before_grading
    OR parsed <> graded_pass + graded_warn + graded_quarantined
  );
