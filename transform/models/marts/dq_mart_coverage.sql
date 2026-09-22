/* `dq_mart`, coverage grain (AGENT_SPEC §6E3; plan 0007 step E).

One row per fiscal year: how many statement files are stored, and what became
of them on the latest parsing run: parsed (and how they graded), recognised but
not yet mapped, waiting for the deferred PDF tier (plan 0006), or quarantined
before grading (C1/C2). This is the number that makes coverage legible, and
the context the check grain needs: a reader should see "28 files with filing
defects out of 129 parsed", not infer a broken parser.

`needs_pdf_tier_without_later_filing` counts plan 0006's trigger: a PDF-only
statement with no later stored statement for the same entity, whose figures
therefore exist nowhere else (a later filing's prior-year column would carry
them otherwise).

The fiscal year is the year the filing's period ends; a C1 file with no filing
row has none and counts under a null year. Same suppression rule and full
rebuild as `marts.dq_mart`. */
MODEL (
  name marts.dq_mart_coverage,
  kind FULL,
  grain fiscal_year,
  audits (
    not_null(columns := (suppressed)),
    unique_combination_of_columns(columns := (fiscal_year)),
    dq_no_cell_below_threshold_unsuppressed,
    dq_no_suppression_without_threshold,
    dq_suppressed_cells_carry_no_measures(
      measures := (
        files_stored, parsed, graded_pass, graded_warn, graded_quarantined, not_yet_mapped,
        needs_pdf_tier, needs_pdf_tier_without_later_filing, quarantined_before_grading, entities
      )
    ),
    dq_coverage_adds_up
  )
);

WITH grades AS (
  SELECT DISTINCT source_document_hash, source_member, quality_grade
  FROM ext.financial_statements_canonical
), files AS (
  SELECT
    p.*,
    g.quality_grade,
    p.status = 'needs_pdf_tier' AND NOT EXISTS (
      SELECT 1
      FROM staging.parsed_documents_current AS later
      WHERE later.krs = p.krs AND later.period_end > p.period_end
    ) AS pdf_only
  FROM staging.parsed_documents_current AS p
  LEFT JOIN grades AS g
    ON g.source_document_hash = p.sha256 AND g.source_member = p.source_member
), cells AS (
  SELECT
    fiscal_year,
    COUNT(*) AS files_stored,
    COUNT(*) FILTER (WHERE status = 'valid') AS parsed,
    COUNT(*) FILTER (WHERE status = 'valid' AND quality_grade = 'pass') AS graded_pass,
    COUNT(*) FILTER (WHERE status = 'valid' AND quality_grade = 'warn') AS graded_warn,
    COUNT(*) FILTER (WHERE status = 'valid' AND quality_grade = 'quarantined') AS graded_quarantined,
    COUNT(*) FILTER (WHERE status = 'not_yet_mapped') AS not_yet_mapped,
    COUNT(*) FILTER (WHERE status = 'needs_pdf_tier') AS needs_pdf_tier,
    COUNT(*) FILTER (WHERE pdf_only) AS needs_pdf_tier_without_later_filing,
    COUNT(*) FILTER (WHERE status = 'quarantined') AS quarantined_before_grading,
    COUNT(DISTINCT krs) AS entities
  FROM files
  GROUP BY fiscal_year
), flagged AS (
  SELECT
    *,
    COALESCE(entities < @VAR('dq_mart_min_cell_entities'), FALSE) AS suppressed
  FROM cells
)
SELECT
  fiscal_year::INT AS fiscal_year,
  CASE WHEN suppressed THEN NULL ELSE files_stored END::INT AS files_stored,
  CASE WHEN suppressed THEN NULL ELSE parsed END::INT AS parsed,
  CASE WHEN suppressed THEN NULL ELSE graded_pass END::INT AS graded_pass,
  CASE WHEN suppressed THEN NULL ELSE graded_warn END::INT AS graded_warn,
  CASE WHEN suppressed THEN NULL ELSE graded_quarantined END::INT AS graded_quarantined,
  CASE WHEN suppressed THEN NULL ELSE not_yet_mapped END::INT AS not_yet_mapped,
  CASE WHEN suppressed THEN NULL ELSE needs_pdf_tier END::INT AS needs_pdf_tier,
  CASE
    WHEN suppressed THEN NULL ELSE needs_pdf_tier_without_later_filing
  END::INT AS needs_pdf_tier_without_later_filing,
  CASE WHEN suppressed THEN NULL ELSE quarantined_before_grading END::INT AS quarantined_before_grading,
  CASE WHEN suppressed THEN NULL ELSE entities END::INT AS entities,
  suppressed::BOOLEAN AS suppressed
FROM flagged
ORDER BY fiscal_year NULLS LAST
