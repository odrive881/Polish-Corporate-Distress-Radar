/* One row per legal event: the canonical reading of each `legal_events` dedup group (plan 0008).

`legal_events` keeps every source's row; this derives the event they describe:
- `event_date`: the decision date, from whichever source gives one. Sources agree within a
  group, so MIN only matters when one of them has no date.
- `known_from`: the earliest publication. MSiG often publishes before the registry enters.
- `effective_date`: the decision date, else the date the event was known. That is the latest
  day an undated event can have happened, and the owner's rule treats it as on or before then.
- `event_type`: the most specific type in the group. The registry's generic
  `restructuring_proceedings_opened` yields to MSiG's `remedial_proceedings_opened`.
- `label_class`: what the event means for a label. For every event but a deregistration it is
  the §4.6 outcome class. A deregistration is a `silent_exit` only if nothing came before it
  that rules one out (decision 5). After a bankruptcy, restructuring or liquidation it takes
  the latest such class and its proceeding. After a merger it is a `merged_away` exit: no
  outcome, and labels censor it.

Signal-only events carry no `label_class`. */
MODEL (
  name staging.legal_events_canonical,
  kind VIEW,
  grain dedup_group_id,
  audits (
    unique_combination_of_columns(columns := (dedup_group_id), blocking := false),
    not_null(columns := (dedup_group_id, krs, event_type, stage, known_from, effective_date), blocking := false)
  )
);

WITH groups AS (
  SELECT
    dedup_group_id,
    MIN(krs) AS krs,
    FIRST(event_type ORDER BY event_type = 'restructuring_proceedings_opened', event_type) AS event_type,
    MIN(outcome_class) AS outcome_class,
    MIN(stage) AS stage,
    FIRST(ends ORDER BY event_type) AS ends,
    BOOL_OR(precludes_silent_exit) AS precludes_silent_exit,
    MIN(event_date) AS event_date,
    MIN(known_from) AS known_from,
    COALESCE(MIN(event_date), MIN(known_from)) AS effective_date,
    MIN(proceeding_id) AS proceeding_id,
    LIST(DISTINCT source ORDER BY source) AS sources
  FROM ext.legal_events
  GROUP BY dedup_group_id
), deregistrations AS (
  SELECT
    d.dedup_group_id,
    (
      SELECT p.outcome_class
      FROM groups AS p
      WHERE p.krs = d.krs AND p.outcome_class IN ('bankruptcy', 'restructuring', 'liquidation')
        AND p.effective_date <= d.effective_date
      ORDER BY p.effective_date DESC, p.dedup_group_id
      LIMIT 1
    ) AS prior_class,
    (
      SELECT p.proceeding_id
      FROM groups AS p
      WHERE p.krs = d.krs AND p.outcome_class IN ('bankruptcy', 'restructuring', 'liquidation')
        AND p.effective_date <= d.effective_date
      ORDER BY p.effective_date DESC, p.dedup_group_id
      LIMIT 1
    ) AS prior_proceeding_id,
    EXISTS (
      SELECT 1
      FROM groups AS p
      WHERE p.krs = d.krs AND p.precludes_silent_exit AND p.effective_date <= d.effective_date
        AND p.dedup_group_id <> d.dedup_group_id
    ) AS precluded
  FROM groups AS d
  WHERE d.event_type = 'deregistered'
)
SELECT
  g.dedup_group_id::TEXT AS dedup_group_id,
  g.krs::TEXT AS krs,
  g.event_type::TEXT AS event_type,
  g.outcome_class::TEXT AS outcome_class,
  g.stage::TEXT AS stage,
  g.ends::TEXT[] AS ends,
  g.precludes_silent_exit::BOOLEAN AS precludes_silent_exit,
  g.event_date::DATE AS event_date,
  g.known_from::DATE AS known_from,
  g.effective_date::DATE AS effective_date,
  COALESCE(g.proceeding_id, d.prior_proceeding_id)::TEXT AS proceeding_id,
  g.sources::TEXT[] AS sources,
  CASE
    WHEN g.event_type <> 'deregistered' THEN g.outcome_class
    WHEN d.prior_class IS NOT NULL THEN d.prior_class
    WHEN d.precluded THEN 'merged_away'
    ELSE 'silent_exit'
  END::TEXT AS label_class
FROM groups AS g
LEFT JOIN deregistrations AS d
  USING (dedup_group_id)
