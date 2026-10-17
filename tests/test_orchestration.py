"""What `make orchestrate` loads (plan 0018 step B): the whole repository, and the instance's settings."""

from __future__ import annotations

import shutil
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import dagster as dg
from dagster._core.run_coordinator import QueuedRunCoordinator
from dagster_defs.definitions import defs

SETTINGS = Path(__file__).parent.parent / "dagster_defs" / "dagster.yaml"


def test_the_instance_settings_load_send_no_telemetry_and_queue_one_run(tmp_path: Path):
    shutil.copy(SETTINGS, tmp_path / "dagster.yaml")

    with dg.DagsterInstance.from_config(str(tmp_path)) as instance:
        assert instance.telemetry_enabled is False
        coordinator = instance.run_coordinator
        assert isinstance(coordinator, QueuedRunCoordinator)
        assert coordinator.get_run_queue_config().max_concurrent_runs == 1
        # Storage is left to the default: SQLite under DAGSTER_HOME, nothing outside it.
        assert str(instance.root_directory) == str(tmp_path)


def test_the_whole_repository_resolves():
    """The UI and the daemon load every job at once; the CLI builds only the one it runs.

    Found 2026-10-17: an asset and a job both named `backtest` (and `publish`) passed every CLI
    run and failed the first `make orchestrate`.
    """
    repository = defs.get_repository_def()
    assert {job.name for job in repository.get_all_jobs()} >= {
        "legal_to_labels",
        "features",
        "backtest",
        "scoring",
        "publish",
        "text",
        "rdf_queue",
        "alerting",
        "monitoring",
    }


def test_every_schedule_and_sensor_ships_stopped():
    """The owner turns each on (plan 0018 decision 0); a fresh home starts nothing by itself."""
    repository = defs.get_repository_def()
    assert {s.default_status for s in repository.schedule_defs} == {
        dg.DefaultScheduleStatus.STOPPED
    }
    assert {s.default_status for s in repository.sensor_defs} == {dg.DefaultSensorStatus.STOPPED}


def test_the_chain_and_what_it_never_starts():
    """Registry check or import → features → scoring; a backtest, a publish or a model-reading
    `text` run is a decision, never started by a schedule or a sensor (plan 0018 decision 2)."""
    repository = defs.get_repository_def()
    started = {s.name: s.job_name for s in [*repository.schedule_defs, *repository.sensor_defs]}
    assert started == {
        "registry_check_nightly": "legal_to_labels",
        "rdf_queue_weekly": "rdf_queue",
        "rdf_queue_filing_wave": "rdf_queue",
        "features_after_registry_check": "features",
        "features_after_rdf_import": "features",
        "scoring_after_features": "scoring",
        "alerts_after_features_or_scoring": "alerting",
        "monitoring_after_scoring": "monitoring",
    }
    assert not set(started.values()) & {"backtest", "publish", "text"}


def test_the_queue_schedules_split_the_year_and_pass_their_tick_date():
    repository = defs.get_repository_def()
    weekly = repository.get_schedule_def("rdf_queue_weekly")
    wave = repository.get_schedule_def("rdf_queue_filing_wave")
    assert weekly.execution_timezone == wave.execution_timezone == "Europe/Warsaw"
    # Mondays outside June to August, every day inside: together, never twice on one day.
    tick = datetime(2026, 10, 19, 6, 0, tzinfo=ZoneInfo("Europe/Warsaw"))
    (request,) = (
        weekly.evaluate_tick(dg.build_schedule_context(scheduled_execution_time=tick)).run_requests
        or []
    )
    assert request.run_key == "rdf_queue_2026-10-19"
    assert request.run_config == {
        "ops": {"rdf_research_queue": {"config": {"on": date(2026, 10, 19).isoformat()}}}
    }
    assert weekly.cron_schedule == "0 6 * 1-5,9-12 1"
    assert wave.cron_schedule == "0 6 * 6-8 *"
