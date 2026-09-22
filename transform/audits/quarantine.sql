/* Audits on `quarantine.quarantine` (plan 0007 step D). Each returns the rows that break it. */

AUDIT (
  name quarantine_has_reasons
);
/* Every quarantined key says why. */
SELECT *
FROM @this_model
WHERE reason_codes IS NULL OR LEN(reason_codes) = 0;

AUDIT (
  name quarantine_one_parsing_stage_per_file
);
/* A file is rejected by one parsing stage: C1 and C2 files never reach grading. */
SELECT source_document_hash, source_member
FROM @this_model
WHERE stage IN ('C1', 'C2', 'E2')
GROUP BY source_document_hash, source_member
HAVING COUNT(DISTINCT stage) > 1;

AUDIT (
  name quarantine_e2_matches_canonical
);
/* One E2 row per file the canonical table grades `quarantined`, no more, no less. */
WITH model AS (
  SELECT COUNT(*) AS n FROM @this_model WHERE stage = 'E2'
), canonical AS (
  SELECT COUNT(*) AS n
  FROM (
    SELECT DISTINCT source_document_hash, source_member
    FROM ext.financial_statements_canonical
    WHERE quality_grade = 'quarantined'
  )
)
SELECT model.n AS model_rows, canonical.n AS canonical_files
FROM model, canonical
WHERE model.n <> canonical.n;

AUDIT (
  name quarantine_c_matches_parsed_documents
);
/* One C1/C2 row per file the latest parsing run recorded `quarantined`. */
WITH model AS (
  SELECT COUNT(*) AS n FROM @this_model WHERE stage IN ('C1', 'C2')
), manifest AS (
  SELECT COUNT(*) AS n FROM staging.parsed_documents_current WHERE status = 'quarantined'
)
SELECT model.n AS model_rows, manifest.n AS manifest_files
FROM model, manifest
WHERE model.n <> manifest.n;
