"""The schedules (plan 0018 step C, decisions 1 and 3). Wiring only.

Every schedule ships stopped: the owner turns each on in the UI (`make orchestrate`), and none
fires while Dagster is not running. A missed tick is not caught up. Times are Warsaw time.
"""

from __future__ import annotations

from zoneinfo import ZoneInfo

import dagster as dg

from dagster_defs.jobs import legal_to_labels, rdf_queue

WARSAW = "Europe/Warsaw"
STOPPED = dg.DefaultScheduleStatus.STOPPED

# Decision 1: the open registers (KRS API, MSiG) each night, at ADR 0011's pacing. About 2 hours
# for 806 entities (plan 0018 step A), so it ends before the morning. A night with no new event
# freezes no new label set (label sets are content-addressed), and none moves a backtest's pin.
registry_check_nightly = dg.ScheduleDefinition(
    name="registry_check_nightly",
    job=legal_to_labels,
    cron_schedule="0 2 * * *",
    execution_timezone=WARSAW,
    default_status=STOPPED,
    description="Nightly: KRS API and MSiG fetched, legal events and the label set rebuilt.",
)


def _queue_request(context: dg.ScheduleEvaluationContext) -> dg.RunRequest:
    """The queue for the tick's own date, so a rerun of that tick writes the same file."""
    on = context.scheduled_execution_time.astimezone(ZoneInfo(WARSAW)).date()
    return dg.RunRequest(
        run_key=f"rdf_queue_{on.isoformat()}",
        run_config={"ops": {"rdf_research_queue": {"config": {"on": on.isoformat()}}}},
    )


# Decision 3: weekly outside the filing wave, daily inside it (June to August: most calendar-year
# filers approve by 30 June and file within 15 days, AGENT_SPEC §6L).
@dg.schedule(
    name="rdf_queue_weekly",
    job=rdf_queue,
    cron_schedule="0 6 * 1-5,9-12 1",
    execution_timezone=WARSAW,
    default_status=STOPPED,
    description="Mondays at 06:00, September to May: the RDF re-search queue for the owner's script.",
)
def rdf_queue_weekly(context: dg.ScheduleEvaluationContext) -> dg.RunRequest:
    return _queue_request(context)


@dg.schedule(
    name="rdf_queue_filing_wave",
    job=rdf_queue,
    cron_schedule="0 6 * 6-8 *",
    execution_timezone=WARSAW,
    default_status=STOPPED,
    description="Daily at 06:00, June to August (the filing wave): the RDF re-search queue.",
)
def rdf_queue_filing_wave(context: dg.ScheduleEvaluationContext) -> dg.RunRequest:
    return _queue_request(context)


schedules = [registry_check_nightly, rdf_queue_weekly, rdf_queue_filing_wave]
