"""What `make orchestrate` loads (plan 0018 step B): the whole repository, and the instance's settings."""

from __future__ import annotations

import shutil
from pathlib import Path

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
    }
