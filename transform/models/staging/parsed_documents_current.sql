/* The statement files as the latest parsing run saw them (plan 0007 amendment 10).

`parsed_documents` is only ever upserted, so it also holds rows no run touches
any more: every row under an older mapping-config hash, and a `not_yet_mapped`
row under `spec_hash ''` for a file a later config maps. Every row one run
touches carries that run's `last_seen_run_id` and timestamp; the latest run is
the one with the newest `last_seen_at`, and its rows are the current set.

Joined to the filing each file came from, for `known_from` (the submission
date, as on the canonical table) and `fiscal_year` (the year the period ends,
the convention `mapping_engine` uses). A C1 file with no filing row has
neither. */
MODEL (
  name staging.parsed_documents_current,
  kind VIEW,
  grain (sha256, source_member),
  audits (
    unique_combination_of_columns(columns := (sha256, source_member), blocking := false),
    not_null(columns := (sha256, source_member, krs, status, last_seen_run_id), blocking := false)
  )
);

WITH latest_run AS (
  SELECT last_seen_run_id
  FROM ext.parsed_documents
  WHERE last_seen_at IS NOT NULL
  ORDER BY last_seen_at DESC, last_seen_run_id
  LIMIT 1
)
SELECT
  p.sha256,
  p.source_member,
  p.spec_hash,
  p.krs,
  p.document_ref,
  p.member_kind,
  p.structure_key,
  p.structure_version,
  p.status,
  p.filed_bodies,
  f.period_end,
  YEAR(f.period_end)::INT AS fiscal_year,
  f.submission_date AS known_from,
  p.last_seen_run_id,
  p.last_seen_at
FROM ext.parsed_documents AS p
JOIN latest_run USING (last_seen_run_id)
LEFT JOIN ext.filing_index AS f
  ON f.krs = p.krs AND f.document_ref = p.document_ref
