/* What the label grid left out, and why (plan 0008 step G): the count the plan asks the audit
output to carry.

One row per horizon and reason:
- `deregistered` and `in_proceeding`: grid rows excluded, not labelled;
- `no_cutoff`: entities in the universe with no row at all, because a source in
  `label_sources` has never been fetched for them. Counted per entity, not per row. */
MODEL (
  name marts.outcome_label_exclusions,
  kind FULL,
  grain (horizon_months, reason),
  audits (
    unique_combination_of_columns(columns := (horizon_months, reason), blocking := false)
  )
);

WITH horizons AS (
  SELECT CAST(h AS INT) AS horizon_months
  FROM (SELECT UNNEST(STRING_SPLIT(@VAR('label_horizons'), ',')) AS h)
), unlabelled AS (
  SELECT TRIM(e.krs) AS krs
  FROM ext.entity_master AS e
  WHERE TRIM(e.krs) NOT IN (SELECT DISTINCT krs FROM staging.outcome_label_grid)
)
SELECT horizon_months, excluded_reason AS reason, COUNT(*) AS rows, COUNT(DISTINCT krs) AS entities
FROM staging.outcome_label_grid
WHERE excluded_reason IS NOT NULL
GROUP BY ALL
UNION ALL
SELECT h.horizon_months, 'no_cutoff' AS reason, 0 AS rows, COUNT(u.krs) AS entities
FROM horizons AS h
LEFT JOIN unlabelled AS u
  ON TRUE
GROUP BY ALL
HAVING COUNT(u.krs) > 0
