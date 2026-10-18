"""The sensors (plan 0018 step C, decision 2). Wiring only.

New data starts the chain: a successful registry check or RDF import rebuilds the features, new
features are scored, and both new features and new scores are checked for alerts. Scoring
follows features, not the calendar: the feature grid moves only when RDF does, and a re-score
on the same month-end writes nothing (plan 0018, constraint 2).
Each sensor fires once per upstream run (its run id is the run key), only on success: an import
that fails at its end, by design, starts nothing (plan 0014). `scoring` keeps its guards, so on
a dirty working tree the chain stops there. Nothing here starts `backtest`, `publish` or `text`:
each is a decision. Every sensor ships stopped; the owner turns each on in the UI.

One sensor retries instead of chaining (owner, 2026-10-18): `registry_check_retry` re-runs a
`legal_to_labels` run that stopped on a ministry maintenance page, once both sources answer again.
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from zoneinfo import ZoneInfo

import dagster as dg

from dagster_defs.jobs import alerting, features, legal_to_labels, monitoring, scoring
from distress_radar.acquisition.availability import probe_sources

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


# Plan 0018 step D: after features as well as after scoring. The feature rules (tripwires,
# going-concern flags) cover entities the model cannot score, so they must not wait on a scoring
# run that a dirty tree refuses; the second run adds the score rules' alerts. Each insert is
# idempotent, so running twice adds nothing twice.
@dg.run_status_sensor(
    name="alerts_after_features_or_scoring",
    run_status=dg.DagsterRunStatus.SUCCESS,
    monitored_jobs=[features, scoring],
    request_job=alerting,
    default_status=STOPPED,
    description="After a successful features or scoring run: compute the alerts.",
)
def alerts_after_features_or_scoring(context: dg.RunStatusSensorContext) -> dg.RunRequest:
    return dg.RunRequest(run_key=context.dagster_run.run_id)


# Plan 0018 step F: drift is measured on what was scored, so it follows scoring only.
@dg.run_status_sensor(
    name="monitoring_after_scoring",
    run_status=dg.DagsterRunStatus.SUCCESS,
    monitored_jobs=[scoring],
    request_job=monitoring,
    default_status=STOPPED,
    description="After a successful scoring run: the served model's input drift report.",
)
def monitoring_after_scoring(context: dg.RunStatusSensorContext) -> dg.RunRequest:
    return dg.RunRequest(run_key=context.dagster_run.run_id)


# A maintenance page all night (2026-10-17/18) failed the first unattended run, and nothing
# retried it until the next night. Retried only when the run stopped on `SourceUnavailable` (its
# failure says "is unavailable"), never for another failure, which needs a person; only once both
# sources answer a probe; at most `MAX_RETRIES` times after one scheduled night; and not in the
# hours before the next night's run, which would fetch everything again anyway.
UNAVAILABLE_MARK = " is unavailable: "
MAX_RETRIES = 3
RETRY_OF = "distress_radar/retry_of"
RETRY_NUMBER = "distress_radar/retry_number"
WARSAW = ZoneInfo("Europe/Warsaw")


def failure_texts(instance: dg.DagsterInstance, run_id: str) -> list[str]:
    """What a run's failed steps said: each step's message and its `Failure` description."""
    texts: list[str] = []
    for entry in instance.all_logs(run_id, of_type=dg.DagsterEventType.STEP_FAILURE):
        texts.append(entry.user_message)
        event = entry.dagster_event
        data = None if event is None else event.step_failure_data
        if data is not None:
            if data.error is not None:
                texts.append(data.error.message)
            if data.user_failure_data is not None and data.user_failure_data.description:
                texts.append(data.user_failure_data.description)
    return texts


def retry_number(failed: dg.DagsterRun, texts: list[str], now: datetime) -> int | dg.SkipReason:
    """Which retry this would be, or why there is none, before any request is sent."""
    if not any(UNAVAILABLE_MARK in t for t in texts):
        return dg.SkipReason(f"run {failed.run_id[:8]} failed for another reason: not retried")
    number = int(failed.tags.get(RETRY_NUMBER, "0")) + 1
    if number > MAX_RETRIES:
        return dg.SkipReason(f"{MAX_RETRIES} retries already after this night: not retried")
    # 22:00 to 02:00 Warsaw time: a run of about 2 hours would meet the night's own at 02:00.
    hour = now.astimezone(WARSAW).hour
    if hour >= 22 or hour < 2:
        return dg.SkipReason("the night's own run is near: not retried")
    return number


def retry_request(
    failed: dg.DagsterRun, number: int, probe: dict[str, str | None]
) -> dg.RunRequest | dg.SkipReason:
    """A run once every source answers; its run key makes it one retry per failed run."""
    down = {source: reason for source, reason in probe.items() if reason}
    if down:
        return dg.SkipReason(f"still unavailable: {down}")
    return dg.RunRequest(
        run_key=f"retry_{failed.run_id}",
        tags={RETRY_OF: failed.tags.get(RETRY_OF, failed.run_id), RETRY_NUMBER: str(number)},
    )


@dg.sensor(
    name="registry_check_retry",
    job=legal_to_labels,
    minimum_interval_seconds=3600,
    default_status=STOPPED,
    description="Hourly after a legal_to_labels run stopped on a maintenance page: re-run it "
    "once the KRS API and MSiG both answer (at most 3 times a night).",
)
def registry_check_retry(context: dg.SensorEvaluationContext) -> dg.RunRequest | dg.SkipReason:
    runs = context.instance.get_runs(filters=dg.RunsFilter(job_name=legal_to_labels.name), limit=1)
    if not runs or runs[0].status != dg.DagsterRunStatus.FAILURE:
        return dg.SkipReason("the latest legal_to_labels run did not fail")
    failed = runs[0]
    now = datetime.now(WARSAW)
    number = retry_number(failed, failure_texts(context.instance, failed.run_id), now)
    if isinstance(number, dg.SkipReason):
        return number
    return retry_request(failed, number, asyncio.run(probe_sources(now.date())))


sensors = [
    registry_check_retry,
    features_after_registry_check,
    features_after_rdf_import,
    scoring_after_features,
    alerts_after_features_or_scoring,
    monitoring_after_scoring,
]
