/* Legal-event coverage per source and year (plan 0008 step G): the number that shows the
source break.

One row per source (`KRS`, `MSiG`, and `both` for events both sources describe) and year of
the event's effective date: events, entities, and events by outcome class. A year where MSiG
stops and only KRS remains (after KRZ's launch) is visible here, not only in the labels'
`source_era`. Kept apart from `dq_mart_coverage`, whose grain is a filing's fiscal year.

Registry dynamics (board, seat and capital changes, plan 0010 step B) are left out with the
registration: only the KRS extract records them, so counting them would show a source break that
is not one. */
MODEL (
  name marts.legal_event_coverage,
  kind FULL,
  grain (source, event_year),
  audits (
    unique_combination_of_columns(columns := (source, event_year), blocking := false)
  )
);

WITH events AS (
  SELECT
    dedup_group_id,
    krs,
    outcome_class,
    YEAR(effective_date) AS event_year,
    CASE WHEN LEN(sources) > 1 THEN 'both' ELSE sources[1] END AS source
  FROM staging.legal_events_canonical
  WHERE event_type NOT IN ('registered', 'board_changed', 'office_moved', 'capital_changed')
)
SELECT
  source,
  event_year,
  COUNT(*) AS events,
  COUNT(DISTINCT krs) AS entities,
  COUNT(*) FILTER (WHERE outcome_class = 'bankruptcy') AS bankruptcy_events,
  COUNT(*) FILTER (WHERE outcome_class = 'restructuring') AS restructuring_events,
  COUNT(*) FILTER (WHERE outcome_class = 'liquidation') AS liquidation_events,
  COUNT(*) FILTER (WHERE outcome_class IS NULL OR outcome_class = 'silent_exit') AS other_events
FROM events
GROUP BY ALL
