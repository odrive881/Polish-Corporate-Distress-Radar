/* Every (entity, as_of_date, horizon) the label grid covers, labelled or excluded (plan 0008 step G).

Parameters are SQLMesh variables read from `config/labels/<label_version>.yaml` and the
statutory taxonomy (`transform/config.py`), never restated here.

- **Grid.** Month-ends from `label_grid_start` to the entity's cutoff, from its KRS
  registration on, × each horizon.
- **Cutoff (`earliest_last_complete_fetch`).** Per entity, the earliest of the latest fetch
  dates of every source in `label_sources`. An entity missing a source has no cutoff and no
  rows (`outcome_label_exclusions` counts it).
- **Excluded, not labelled:**
  - `deregistered`: the entity is gone by `as_of_date`;
  - `in_proceeding`: a petition-stage or opening event of some class happened on or before
    `as_of_date`, with no closing event for that class since. An event on `as_of_date`
    itself counts as already happened.
- **Label.** The first event in `(as_of_date, window_end]` with a `label_class`, where
  `window_end` is the month-end `horizon` months on (29 Feb + 1 month is 31 Mar, not 29 Mar),
  by effective date, then by `label_precedence` on a tie. Its class is the label. A
  `merged_away` exit censors the row, since leaving by merger is not an outcome. With no
  event, the row is `alive` if the whole window is before the cutoff, and censored (class
  null) if not: never `alive` past the cutoff (§4.6).
- **Flags.**
  - `regime_flag`: the horizon window overlaps the regime window (decision 6).
  - `source_era`: `pre_krz` if the window ends before KRZ's launch, `krz` if it starts after
    it, `mixed` if it spans the launch.

Full rebuild; FULL rather than a view so the audits and the freeze read one fixed table. */
MODEL (
  name staging.outcome_label_grid,
  kind FULL,
  grain (krs, as_of_date, horizon_months),
  audits (
    unique_combination_of_columns(columns := (krs, as_of_date, horizon_months), blocking := false),
    not_null(columns := (krs, as_of_date, horizon_months, cutoff_date, label_version), blocking := false)
  )
);

WITH fetches AS (
  SELECT TRIM(krs) AS krs, source, MAX(CAST(fetched_at AT TIME ZONE 'UTC' AS DATE)) AS last_fetch
  FROM ext.legal_source_fetches
  WHERE source IN (SELECT UNNEST(STRING_SPLIT(@VAR('label_sources'), ',')))
  GROUP BY ALL
), cutoffs AS (
  SELECT TRIM(e.krs) AS krs, MIN(f.last_fetch) AS cutoff_date
  FROM ext.entity_master AS e
  JOIN fetches AS f
    ON f.krs = TRIM(e.krs)
  GROUP BY ALL
  HAVING COUNT(DISTINCT f.source) = LEN(STRING_SPLIT(@VAR('label_sources'), ','))
), registered AS (
  SELECT krs, MIN(effective_date) AS registered_on
  FROM staging.legal_events_canonical
  WHERE event_type = 'registered'
  GROUP BY krs
), month_ends AS (
  SELECT CAST(LAST_DAY(m) AS DATE) AS as_of_date
  FROM GENERATE_SERIES(
    CAST(DATE_TRUNC('month', CAST(@VAR('label_grid_start') AS DATE)) AS TIMESTAMP),
    CAST((SELECT COALESCE(MAX(cutoff_date), CAST(@VAR('label_grid_start') AS DATE)) FROM cutoffs) AS TIMESTAMP),
    INTERVAL 1 MONTH
  ) AS t(m)
), horizons AS (
  SELECT CAST(h AS INT) AS horizon_months
  FROM (SELECT UNNEST(STRING_SPLIT(@VAR('label_horizons'), ',')) AS h)
), grid AS (
  SELECT
    c.krs,
    m.as_of_date,
    h.horizon_months,
    LAST_DAY(m.as_of_date + TO_MONTHS(h.horizon_months)) AS window_end,  -- a month-end, as the grid
    c.cutoff_date
  FROM cutoffs AS c
  CROSS JOIN month_ends AS m
  CROSS JOIN horizons AS h
  LEFT JOIN registered AS r
    ON r.krs = c.krs
  WHERE m.as_of_date <= c.cutoff_date
    AND m.as_of_date >= COALESCE(r.registered_on, CAST(@VAR('label_grid_start') AS DATE))
), states AS (
  SELECT
    g.*,
    EXISTS (
      SELECT 1 FROM staging.legal_events_canonical AS d
      WHERE d.krs = g.krs AND d.event_type = 'deregistered' AND d.effective_date <= g.as_of_date
    ) AS deregistered,
    EXISTS (
      SELECT 1 FROM staging.legal_events_canonical AS o
      WHERE o.krs = g.krs
        AND o.stage IN ('petition', 'opening')
        AND o.outcome_class IN ('bankruptcy', 'restructuring', 'liquidation')
        AND o.effective_date <= g.as_of_date
        AND NOT EXISTS (
          SELECT 1 FROM staging.legal_events_canonical AS c
          WHERE c.krs = o.krs
            AND c.stage = 'closing'
            AND LIST_CONTAINS(c.ends, o.outcome_class)
            AND c.effective_date >= o.effective_date
            AND c.effective_date <= g.as_of_date
        )
    ) AS in_proceeding
  FROM grid AS g
), triggers AS (
  SELECT
    s.krs,
    s.as_of_date,
    s.horizon_months,
    e.label_class,
    e.event_type,
    e.event_date,
    e.known_from,
    e.effective_date,
    e.proceeding_id,
    ROW_NUMBER() OVER (
      PARTITION BY s.krs, s.as_of_date, s.horizon_months
      ORDER BY
        e.effective_date,
        LIST_POSITION(STRING_SPLIT(@VAR('label_precedence'), ','), e.label_class) NULLS LAST,
        e.dedup_group_id
    ) AS rank
  FROM states AS s
  JOIN staging.legal_events_canonical AS e
    ON e.krs = s.krs
    AND e.label_class IS NOT NULL
    AND e.effective_date > s.as_of_date
    AND e.effective_date <= s.window_end
)
SELECT
  s.krs,
  s.as_of_date,
  s.horizon_months,
  CASE WHEN s.deregistered THEN 'deregistered' WHEN s.in_proceeding THEN 'in_proceeding' END AS excluded_reason,
  CASE
    WHEN t.label_class = 'merged_away' THEN NULL
    WHEN t.label_class IS NOT NULL THEN t.label_class
    WHEN s.window_end <= s.cutoff_date THEN 'alive'
  END AS outcome_class,
  COALESCE(t.label_class = 'merged_away', FALSE)
    OR (t.label_class IS NULL AND s.window_end > s.cutoff_date) AS censored,
  t.event_date,
  t.known_from AS event_known_from,
  t.event_type AS trigger_event_type,
  t.proceeding_id,
  CASE
    WHEN t.label_class IS NULL OR t.label_class = 'merged_away' OR t.proceeding_id IS NOT NULL THEN NULL
    WHEN t.label_class IN ('liquidation', 'silent_exit') THEN 'procedure_has_no_case'
    WHEN t.event_type = 'simplified_restructuring_announced' THEN 'procedure_has_no_case'
    ELSE 'source_omits_signature'
  END AS proceeding_id_note,
  s.as_of_date < CAST(@VAR('regime_end') AS DATE)
    AND s.window_end >= CAST(@VAR('regime_start') AS DATE) AS regime_flag,
  CASE
    WHEN s.window_end < CAST(@VAR('krz_launch') AS DATE) THEN 'pre_krz'
    WHEN s.as_of_date >= CAST(CAST(@VAR('krz_launch') AS DATE) - INTERVAL 1 DAY AS DATE) THEN 'krz'
    ELSE 'mixed'
  END AS source_era,
  s.cutoff_date,
  @VAR('label_version') AS label_version
FROM states AS s
LEFT JOIN triggers AS t
  ON t.krs = s.krs AND t.as_of_date = s.as_of_date AND t.horizon_months = s.horizon_months AND t.rank = 1
