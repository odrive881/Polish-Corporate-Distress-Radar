"""`make reproduce`: the offline chain run twice from the same inputs, its outputs compared.

Plan 0021 step A (invariant 5). `WAREHOUSE_DIR` is copied twice into a scratch directory, the
chain's assets are materialized into each copy in turn (the Dagster CLI with an ephemeral
instance, so nothing enters the live run queue), and every dataset's layout-independent digest
(`distress_radar.reproducibility`) is compared between the two runs, and with the stored
warehouse. The two runs must agree. A run that differs from the stored warehouse only shows that
the stored dataset was built from older inputs: a finding, not a failure.

Both runs read the same raw store and Postgres and write to Postgres only what any re-run writes
(idempotent inserts and upserts). Refused while a Dagster run is in progress, since one would
change the inputs between the two runs. Needs `make dev-up`. The scratch copies are removed when
the runs agree and kept for inspection when they do not.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

import dagster as dg

from distress_radar.reproducibility import Comparison, compare, warehouse_digests
from distress_radar.settings import Settings

# The chain (plan 0021 decision 1), in the order Dagster resolves anyway.
SELECTION = (
    "financial_statements_canonical",
    "restatement_events",
    "legal_events",
    "legal_events_canonical",
    "outcome_label_grid",
    "outcome_labels_mart",
    "outcome_label_exclusions",
    "legal_event_coverage",
    "outcome_labels",
    "entity_size_class_history",
    "feature_store",
)
REPORT_DIR = Path("reports") / "reproducibility"
_BUSY = (
    dg.DagsterRunStatus.QUEUED,
    dg.DagsterRunStatus.NOT_STARTED,
    dg.DagsterRunStatus.STARTING,
    dg.DagsterRunStatus.STARTED,
)


def _refuse_if_busy() -> None:
    """A run of the live instance in progress would change the inputs between the two runs."""
    if not os.environ.get("DAGSTER_HOME"):
        return
    with dg.DagsterInstance.get() as instance:
        busy = instance.get_runs(filters=dg.RunsFilter(statuses=list(_BUSY)), limit=1)
    if busy:
        raise SystemExit(f"a Dagster run is in progress ({busy[0].job_name}): try again after it")


def _materialize(warehouse: Path, log: Path) -> None:
    env = {k: v for k, v in os.environ.items() if k != "DAGSTER_HOME"}
    env["WAREHOUSE_DIR"] = str(warehouse)
    command = [
        sys.executable,
        "-m",
        "dagster",
        "asset",
        "materialize",
        "-m",
        "dagster_defs.definitions",
        "--select",
        ",".join(SELECTION),
        # One step at a time: two runs of parallel steps ran WSL out of memory (2026-10-18).
        "--config-json",
        '{"execution": {"config": {"multiprocess": {"max_concurrent": 1}}}}',
    ]
    with log.open("w", encoding="utf-8") as out:
        done = subprocess.run(command, env=env, stdout=out, stderr=subprocess.STDOUT, check=False)
    if done.returncode != 0:
        raise SystemExit(f"the chain failed in {warehouse.name}; its log: {log}")


def _report(rows: list[Comparison], started: datetime, scratch: Path) -> str:
    reproduced = all(r.reproduced for r in rows)
    lines = [
        f"# Reproducibility: {started:%Y-%m-%d %H:%M} UTC",
        "",
        "The offline chain materialized twice from the same stored inputs (plan 0021 step A): "
        + ("**every output reproduced.**" if reproduced else "**some outputs differ.**"),
        "",
        "| Dataset | Run 1 | Run 2 | Reproduced | Stored copy current |",
        "|---|---|---|---|---|",
    ]
    for r in rows:
        lines.append(
            f"| `{r.name}` | {(r.first or '–')[:12]} | {(r.second or '–')[:12]} | "
            f"{'yes' if r.reproduced else '**no**'} | "
            f"{'yes' if r.stored_is_current else 'no: built from older inputs'} |"
        )
    lines += [
        "",
        (
            "Digests are layout-independent (`distress_radar.reproducibility.dataset_digest`). "
            "'Stored copy current' compares the run with the warehouse as stored before it: a 'no' "
            "means that dataset was built before its inputs last changed, not that the chain varies."
        ),
    ]
    if not reproduced:
        lines.append(f"\nThe scratch copies are kept for inspection: `{scratch}`.")
    return "\n".join(lines) + "\n"


def main() -> int:
    _refuse_if_busy()
    settings = Settings()
    stored = settings.warehouse_dir.resolve()
    started = datetime.now(UTC)
    scratch = stored.parent / f"reproduce-{started:%Y%m%dT%H%M%S}"
    stored_digests = warehouse_digests(stored)
    runs: list[dict[str, str | None]] = []
    for name in ("first", "second"):
        copy = scratch / name
        shutil.copytree(stored, copy)
        print(f"run {name}: materializing the chain into {copy} …", flush=True)
        _materialize(copy, scratch / f"{name}.log")
        runs.append(warehouse_digests(copy))
    rows = compare(runs[0], runs[1], stored_digests)
    report = _report(rows, started, scratch)
    target = stored / REPORT_DIR / f"{started:%Y-%m-%d}.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(report, encoding="utf-8")
    print(report)
    print(f"report: {target}")
    if all(r.reproduced for r in rows):
        shutil.rmtree(scratch)
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
