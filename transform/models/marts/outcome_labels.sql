/* `outcome_labels` (AGENT_SPEC §4.6, §5; plan 0008 step G): the prediction targets.

The grid's rows that are not excluded. `label_set_hash` is not here: the freeze step
(`distress_radar.labels.freeze_label_set`) hashes these rows and writes the frozen set to
`WAREHOUSE_DIR/outcome_labels/`, which is what a model trains on (decision 7).

`outcome_class` is null exactly when `censored`: a censored row has no class, and never
`alive` (§4.6). `event_date` is null for an undated event (the owner's rule, 2026-09-23);
`event_known_from` then bounds it from above. */
MODEL (
  name marts.outcome_labels,
  kind FULL,
  grain (krs, as_of_date, horizon_months),
  audits (
    unique_combination_of_columns(columns := (krs, as_of_date, horizon_months), blocking := false),
    not_null(columns := (krs, as_of_date, horizon_months, censored, regime_flag, source_era, label_version), blocking := false),
    labels_no_alive_past_cutoff,
    labels_no_event_on_or_before_as_of,
    labels_event_has_proceeding_or_reason,
    labels_class_iff_not_censored,
    labels_regime_flag_is_window_overlap
  )
);

SELECT
  krs,
  as_of_date,
  horizon_months,
  outcome_class,
  censored,
  event_date,
  event_known_from,
  trigger_event_type,
  proceeding_id,
  proceeding_id_note,
  regime_flag,
  source_era,
  cutoff_date,
  label_version
FROM staging.outcome_label_grid
WHERE excluded_reason IS NULL
