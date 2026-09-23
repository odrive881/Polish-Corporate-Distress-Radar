/* Audits on `marts.outcome_labels` (plan 0008 step G). Each returns the rows that break it.

All non-blocking: a failure leaves the table built and is reported as a Dagster asset check
(`distress_radar/transform_project.py`). */

AUDIT (
  name labels_no_alive_past_cutoff,
  blocking false
);
/* §4.6: a row whose horizon passes the cutoff with no event is censored, never `alive`. From
version 2 (plan 0009) the limit is the cutoff moved back by the lag allowance for a window
ending on or after KRZ's launch. */
WITH rows AS (
  SELECT
    *,
    LAST_DAY(as_of_date + TO_MONTHS(horizon_months)) AS window_end
  FROM @this_model
)
SELECT *
FROM rows
WHERE outcome_class = 'alive'
  AND window_end > CASE
    WHEN window_end >= CAST(@VAR('krz_launch') AS DATE)
    THEN CAST(cutoff_date - TO_MONTHS(@VAR('label_alive_lag_months', 0)) AS DATE)
    ELSE cutoff_date
  END;

AUDIT (
  name labels_no_event_on_or_before_as_of,
  blocking false
);
/* A label is an event after `as_of_date`; one on or before it was an exclusion. */
SELECT *
FROM @this_model
WHERE COALESCE(event_date, event_known_from) <= as_of_date;

AUDIT (
  name labels_event_has_proceeding_or_reason,
  blocking false
);
/* Every labelled event names its proceeding, or says why it cannot. */
SELECT *
FROM @this_model
WHERE outcome_class IS NOT NULL AND outcome_class <> 'alive'
  AND proceeding_id IS NULL AND proceeding_id_note IS NULL;

AUDIT (
  name labels_class_iff_not_censored,
  blocking false
);
/* A censored row has no class; every other row has one. */
SELECT *
FROM @this_model
WHERE censored = (outcome_class IS NOT NULL);

AUDIT (
  name labels_regime_flag_is_window_overlap,
  blocking false
);
/* The regime flag is set exactly on rows whose window overlaps the regime window. */
SELECT *
FROM @this_model
WHERE regime_flag <> (
  as_of_date < CAST(@VAR('regime_end') AS DATE)
  AND LAST_DAY(as_of_date + TO_MONTHS(horizon_months)) >= CAST(@VAR('regime_start') AS DATE)
);
