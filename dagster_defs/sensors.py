"""The sensors (plan 0018 step C, decision 2). Wiring only.

New data starts the chain: a successful registry check or RDF import rebuilds the features, and
new features are scored. Scoring follows features, not the calendar: the feature grid moves only
when RDF does, and a re-score on the same month-end writes nothing (plan 0018, constraint 2).
Each sensor fires once per upstream run (its run id is the run key), only on success: an import
that fails at its end, by design, starts nothing (plan 0014). `scoring` keeps its guards, so on
a dirty working tree the chain stops there. Nothing here starts `backtest`, `publish` or `text`:
each is a decision. Every sensor ships stopped; the owner turns each on in the UI.
"""

from __future__ import annotations

import dagster as dg

from dagster_defs.jobs import features, legal_to_labels, scoring

STOPPED = dg.DefaultSensorStatus.STOPPED


@dg.run_status_sensor(
    name="features_after_registry_check",
    run_status=dg.DagsterRunStatus.SUCCESS,
    monitored_jobs=[legal_to_labels],
    request_job=features,
    default_status=STOPPED,
    description="After a successful legal_to_labels run: rebuild the features.",
)
def features_after_registry_check(context: dg.RunStatusSensorContext) -> dg.RunRequest:
    return dg.RunRequest(run_key=context.dagster_run.run_id)


@dg.asset_sensor(
    name="features_after_rdf_import",
    asset_key=dg.AssetKey("rdf_script_import"),
    job=features,
    default_status=STOPPED,
    description="After rdf_script_import materializes (a failed import does not): rebuild the features.",
)
def features_after_rdf_import(
    context: dg.SensorEvaluationContext, asset_event: dg.EventLogEntry
) -> dg.RunRequest:
    return dg.RunRequest(run_key=asset_event.run_id)


@dg.run_status_sensor(
    name="scoring_after_features",
    run_status=dg.DagsterRunStatus.SUCCESS,
    monitored_jobs=[features],
    request_job=scoring,
    default_status=STOPPED,
    description="After a successful features run: score the latest month-end.",
)
def scoring_after_features(context: dg.RunStatusSensorContext) -> dg.RunRequest:
    return dg.RunRequest(run_key=context.dagster_run.run_id)


sensors = [features_after_registry_check, features_after_rdf_import, scoring_after_features]
