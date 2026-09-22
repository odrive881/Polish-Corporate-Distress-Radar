/* `dq_mart`, check grain (AGENT_SPEC §6E3; plan 0007 step E).

One row per structure version × filed body set × fiscal year × identity check,
from `identity_check_results`. A file counts once per check:
- it fails the check if any of its rows for that check fails, and the failure
  is material if any failing row is (material failures quarantine a file,
  immaterial ones only make it `warn`);
- the check is not applicable to it only if every one of its rows is
  `not_applicable` (a small or micro filing's cash-flow tie);
- otherwise it passed.
`pass_rate` is passed / (passed + failed): not-applicable files are not in the
denominator, because counting an exemption as a pass would inflate every rate.

The filed body set comes from `parsed_documents` (plan 0007 amendment 6): a
small envelope carrying full-form statements is a different parsing path from
one carrying its own, and the structure version alone hides it.

Publish-safe by construction: no entity identifiers leave this model, only
counts. Small-cell suppression (decision 9): when `dq_mart_min_cell_entities`
is set, a cell covering fewer distinct entities publishes every measure as null
with `suppressed` true, and is kept rather than dropped, so it stays visible
that data exists there. The shipped setting is null, which suppresses nothing.
It must be set before Phase 9 publishes anything (AGENT_SPEC §10).

Full rebuild on every run (amendment 9): a grading-rule change rewrites past
cells, so there is nothing to append to. */
MODEL (
  name marts.dq_mart,
  kind FULL,
  grain (structure_version, filed_bodies, fiscal_year, "check"),
  audits (
    not_null(columns := (structure_version, filed_bodies, fiscal_year, "check", suppressed), blocking := false),
    unique_combination_of_columns(columns := (structure_version, filed_bodies, fiscal_year, "check"), blocking := false),
    dq_no_cell_below_threshold_unsuppressed,
    dq_no_suppression_without_threshold,
    dq_suppressed_cells_carry_no_measures(
      measures := (files, passed, failed_material, failed_immaterial, not_applicable, pass_rate, entities)
    ),
    dq_mart_outcomes_add_up
  )
);

WITH per_file_check AS (
  SELECT
    structure_version,
    fiscal_year,
    krs,
    source_document_hash,
    source_member,
    "check",
    BOOL_OR(status = 'fail') AS failed,
    BOOL_OR(severity = 'material') AS material,
    BOOL_AND(status = 'not_applicable') AS not_applicable
  FROM ext.identity_check_results
  GROUP BY ALL
), cells AS (
  SELECT
    f.structure_version,
    p.filed_bodies,
    f.fiscal_year,
    f."check",
    COUNT(*) AS files,
    COUNT(*) FILTER (WHERE NOT f.failed AND NOT f.not_applicable) AS passed,
    COUNT(*) FILTER (WHERE f.failed AND f.material) AS failed_material,
    COUNT(*) FILTER (WHERE f.failed AND NOT f.material) AS failed_immaterial,
    COUNT(*) FILTER (WHERE f.not_applicable) AS not_applicable,
    COUNT(DISTINCT f.krs) AS entities
  FROM per_file_check AS f
  LEFT JOIN staging.parsed_documents_current AS p
    ON p.sha256 = f.source_document_hash AND p.source_member = f.source_member
  GROUP BY ALL
), flagged AS (
  SELECT
    *,
    COALESCE(entities < @VAR('dq_mart_min_cell_entities'), FALSE) AS suppressed
  FROM cells
)
SELECT
  structure_version::TEXT AS structure_version,
  filed_bodies::TEXT AS filed_bodies,
  fiscal_year::INT AS fiscal_year,
  "check"::TEXT AS "check",
  CASE WHEN suppressed THEN NULL ELSE files END::INT AS files,
  CASE WHEN suppressed THEN NULL ELSE passed END::INT AS passed,
  CASE WHEN suppressed THEN NULL ELSE failed_material END::INT AS failed_material,
  CASE WHEN suppressed THEN NULL ELSE failed_immaterial END::INT AS failed_immaterial,
  CASE WHEN suppressed THEN NULL ELSE not_applicable END::INT AS not_applicable,
  CASE
    WHEN suppressed THEN NULL
    ELSE passed / NULLIF(passed + failed_material + failed_immaterial, 0)
  END::DOUBLE AS pass_rate,
  CASE WHEN suppressed THEN NULL ELSE entities END::INT AS entities,
  suppressed::BOOLEAN AS suppressed
FROM flagged
ORDER BY structure_version, filed_bodies, fiscal_year, "check"
