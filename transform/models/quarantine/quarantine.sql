/* The current quarantined set (AGENT_SPEC §5 `quarantine`, §6E3; plan 0007 decision 4).

`quarantine_events` is an append-only log of first detection and is never
cleaned. This model recomputes what is quarantined *now*, from scratch on
every run, asking whichever source knows the current answer for each stage:

- E2: the canonical table's `quality_grade`, which grading recomputes from the
  current rules on every materialization. Reason codes are the checks with a
  `material` failure in `identity_check_results`: the ones that quarantine the
  file. (The log recorded every failing check of a quarantined file, so it can
  list more.)
- C1, C2: `parsed_documents.status` on the latest parsing run (these files have
  no canonical rows to grade), with stage and reason codes from the file's
  most recent C1/C2 detection in the log.
- A1, A2, A3: nothing downstream records these, so the log is the only source:
  the most recent detection per (stage, entity_key).

A log row describing a file the current rules no longer quarantine is simply
not selected; it stays in the log, which is the point. `first_detected_at` is
the key's earliest log row. Full rebuild, never incremental. */
MODEL (
  name quarantine.quarantine,
  kind FULL,
  grain (stage, entity_key, source_document_hash, source_member),
  audits (
    not_null(columns := (stage, entity_key), blocking := false),
    unique_combination_of_columns(columns := (stage, entity_key, source_document_hash, source_member), blocking := false),
    quarantine_has_reasons,
    quarantine_one_parsing_stage_per_file,
    quarantine_e2_matches_canonical,
    quarantine_c_matches_parsed_documents
  )
);

WITH e2_files AS (
  SELECT DISTINCT krs, document_ref, source_document_hash, source_member, known_from
  FROM ext.financial_statements_canonical
  WHERE quality_grade = 'quarantined'
), e2_reasons AS (
  SELECT
    source_document_hash,
    source_member,
    LIST(DISTINCT "check" ORDER BY "check") AS reason_codes
  FROM ext.identity_check_results
  WHERE severity = 'material'
  GROUP BY source_document_hash, source_member
), e2 AS (
  SELECT
    'E2' AS stage,
    f.krs || ':' || f.document_ref AS entity_key,
    f.krs,
    f.document_ref,
    f.source_document_hash,
    f.source_member,
    r.reason_codes,
    f.known_from
  FROM e2_files AS f
  LEFT JOIN e2_reasons AS r
    USING (source_document_hash, source_member)
), c_files AS (
  SELECT
    krs || ':' || COALESCE(document_ref, source_member) AS entity_key,
    krs,
    document_ref,
    sha256 AS source_document_hash,
    source_member,
    known_from
  FROM staging.parsed_documents_current
  WHERE status = 'quarantined'
), c_events AS (
  SELECT
    stage,
    entity_key,
    source_document_hash,
    reason_code,
    created_at,
    MAX(created_at) OVER (PARTITION BY entity_key, source_document_hash) AS latest_at
  FROM ext.quarantine_events
  WHERE stage IN ('C1', 'C2')
), c AS (
  SELECT
    e.stage,
    f.entity_key,
    f.krs,
    f.document_ref,
    f.source_document_hash,
    f.source_member,
    LIST(DISTINCT e.reason_code ORDER BY e.reason_code) FILTER (WHERE e.reason_code IS NOT NULL) AS reason_codes,
    f.known_from
  FROM c_files AS f
  LEFT JOIN c_events AS e
    ON e.entity_key = f.entity_key
    AND e.source_document_hash = f.source_document_hash
    AND e.created_at = e.latest_at
  GROUP BY ALL
), a_events AS (
  SELECT
    *,
    MAX(created_at) OVER (PARTITION BY stage, entity_key) AS latest_at
  FROM ext.quarantine_events
  WHERE stage IN ('A1', 'A2', 'A3')
), a AS (
  SELECT
    stage,
    entity_key,
    MIN(krs) AS krs,
    NULL AS document_ref,
    MIN(source_document_hash) AS source_document_hash,
    NULL AS source_member,
    LIST(DISTINCT reason_code ORDER BY reason_code) AS reason_codes,
    NULL AS known_from
  FROM a_events
  WHERE created_at = latest_at
  GROUP BY stage, entity_key
), current_set AS (
  SELECT * FROM e2
  UNION ALL
  SELECT * FROM c
  UNION ALL
  SELECT * FROM a
), first_detected AS (
  /* Parsing-stage keys are files (entity_key + hash); acquisition keys are the entity_key alone. */
  SELECT
    CASE WHEN stage IN ('C1', 'C2') THEN 'C' ELSE stage END AS stage_group,
    entity_key,
    CASE WHEN stage IN ('A1', 'A2', 'A3') THEN NULL ELSE source_document_hash END AS file_hash,
    MIN(created_at) AS first_detected_at
  FROM ext.quarantine_events
  GROUP BY ALL
)
SELECT
  s.stage::TEXT AS stage,
  s.entity_key::TEXT AS entity_key,
  s.krs::TEXT AS krs,
  s.document_ref::TEXT AS document_ref,
  s.source_document_hash::TEXT AS source_document_hash,
  s.source_member::TEXT AS source_member,
  s.reason_codes::TEXT[] AS reason_codes,
  s.known_from::DATE AS known_from,
  d.first_detected_at::TIMESTAMPTZ AS first_detected_at
FROM current_set AS s
LEFT JOIN first_detected AS d
  ON d.stage_group = CASE WHEN s.stage IN ('C1', 'C2') THEN 'C' ELSE s.stage END
  AND d.entity_key = s.entity_key
  AND d.file_hash IS NOT DISTINCT FROM CASE
    WHEN s.stage IN ('A1', 'A2', 'A3') THEN NULL
    ELSE s.source_document_hash
  END
ORDER BY stage, entity_key, source_document_hash, source_member
