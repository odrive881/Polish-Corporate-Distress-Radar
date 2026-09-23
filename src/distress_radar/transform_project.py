"""The SQLMesh project in `transform/`, as a function Dagster (or a script) can call.

Plan 0007 step F. `build_dq_models` rebuilds the data-quality models over the
current canonical Parquet and manifest, then runs every audit on them and
returns one outcome per audit, so a caller can report each one (Dagster turns
them into asset checks) instead of a single pass/fail. `build_label_models`
does the same for the outcome-label models (plan 0008 step G), and
`read_model` hands a built table to Python (the label-set freeze).

The models are FULL (plan 0007 decision 4, amendment 9), and SQLMesh's `run`
only executes a model when its cron interval is due, so a rebuild is two plans:
an ordinary one that applies any change to the model code, then one that
restates the tables. The order matters: a restatement plan ignores local
changes and would rebuild the versions already in `prod`. Every call
recomputes the tables from whatever the upstream datasets hold now.

The audits are non-blocking in SQLMesh (`blocking false`): a failing audit
leaves the rebuilt table in place and is reported here, the way a failing
identity check leaves the canonical table written and flags it. A blocking
audit would make `plan` raise, and nothing would say which audit failed.

Needs the `local` gateway's services: Postgres for SQLMesh state and the
manifest tables, the Parquet under `WAREHOUSE_DIR`, and the `postgres` DuckDB
extension (`make transform-setup`). SQLMesh holds `transform.duckdb` open for
the duration of a call, so a concurrent `make transform-*` waits or fails.
"""

# sqlmesh's signatures reference types pyright cannot resolve; scoped to this module.
# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false, reportUnknownArgumentType=false

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

import polars as pl

if TYPE_CHECKING:
    from sqlmesh import Context

TRANSFORM_DIR = Path(__file__).resolve().parents[2] / "transform"

# SQLMesh model → the name its Dagster asset goes by. The staging view is an
# asset too, so its audits have somewhere to be reported.
DQ_MODELS: dict[str, str] = {
    "staging.parsed_documents_current": "parsed_documents_current",
    "quarantine.quarantine": "quarantine",
    "marts.dq_mart": "dq_mart",
    "marts.dq_mart_coverage": "dq_mart_coverage",
}
# Models a rebuild restates. The staging model is a view: recreating it is free
# and it is always current.
RESTATED = ("quarantine.quarantine", "marts.dq_mart", "marts.dq_mart_coverage")

# The outcome-label models (plan 0008 step G), built and audited the same way.
LABEL_MODELS: dict[str, str] = {
    "staging.legal_events_canonical": "legal_events_canonical",
    "staging.outcome_label_grid": "outcome_label_grid",
    "marts.outcome_labels": "outcome_labels_mart",
    "marts.outcome_label_exclusions": "outcome_label_exclusions",
    "marts.legal_event_coverage": "legal_event_coverage",
}
LABEL_RESTATED = (
    "staging.outcome_label_grid",
    "marts.outcome_labels",
    "marts.outcome_label_exclusions",
    "marts.legal_event_coverage",
)


@dataclass(frozen=True)
class AuditOutcome:
    model: str
    audit: str
    failing_rows: int | None  # None when the audit was skipped
    query: str

    @property
    def passed(self) -> bool:
        return self.failing_rows == 0


@dataclass(frozen=True)
class DqBuild:
    audits: tuple[AuditOutcome, ...]
    row_counts: dict[str, int]


def declared_audits(
    project: Path = TRANSFORM_DIR, gateway: str = "test", models: Iterable[str] = DQ_MODELS
) -> dict[str, list[str]]:
    """Audit names per DQ model, read from the project without running anything.

    Dagster needs its check specs when definitions load, before any run; this
    is what `dagster_defs` compares its static list against in a test.
    """
    from sqlmesh import Context

    context = Context(paths=project, gateway=gateway)
    try:
        return {
            name: [audit_name for audit_name, _ in context.get_model(name).audits]
            for name in models
        }
    finally:
        context.close()


def build_dq_models(project: Path = TRANSFORM_DIR, gateway: str = "local") -> DqBuild:
    """Rebuild the DQ models in `prod`, then audit them; raises only if the rebuild fails."""
    return _build(DQ_MODELS, RESTATED, project, gateway)


def build_label_models(project: Path = TRANSFORM_DIR, gateway: str = "local") -> DqBuild:
    """Rebuild the outcome-label models in `prod`, then audit them (plan 0008 step G)."""
    return _build(LABEL_MODELS, LABEL_RESTATED, project, gateway)


def read_model(name: str, warehouse_dir: Path) -> pl.DataFrame:
    """A built model's rows, read from `transform.duckdb` after SQLMesh has let go of it."""
    import duckdb

    with duckdb.connect((warehouse_dir / "transform.duckdb").as_posix(), read_only=True) as con:
        cursor = con.execute(f"SELECT * FROM transform.{name}")
        columns = [column[0] for column in cursor.description or []]
        rows = cursor.fetchall()
    # Plain rows, not `.pl()`: that needs pyarrow, which the project does not otherwise use.
    return pl.DataFrame(rows, schema=columns, orient="row", infer_schema_length=None)


def _build(
    models: dict[str, str], restated: tuple[str, ...], project: Path, gateway: str
) -> DqBuild:
    from sqlmesh import Context

    context = Context(paths=project, gateway=gateway)
    try:
        context.plan("prod", auto_apply=True, no_prompts=True)
    finally:
        context.close()
    context = Context(paths=project, gateway=gateway)
    try:
        context.plan("prod", restate_models=list(restated), auto_apply=True, no_prompts=True)
    finally:
        context.close()
    # A context keeps the snapshots it loaded; after a plan that changed a model
    # they are not the versions just applied, and SQLMesh refuses to audit them.
    context = Context(paths=project, gateway=gateway)
    try:
        return DqBuild(audits=_audit(context, models), row_counts=_row_counts(context, models))
    finally:
        context.close()


def _audit(context: Context, models: Iterable[str]) -> tuple[AuditOutcome, ...]:
    now = datetime.now(UTC)
    outcomes: list[AuditOutcome] = []
    for name in models:
        snapshot = context.get_snapshot(name, raise_if_missing=True)
        for result in context.snapshot_evaluator.audit(
            snapshot=snapshot,
            start=now,
            end=now,
            execution_time=now,
            snapshots=context.snapshots,
        ):
            outcomes.append(
                AuditOutcome(
                    model=name,
                    audit=result.audit.name,
                    failing_rows=None if result.skipped else int(result.count or 0),
                    query=result.query.sql(dialect="duckdb") if result.query else "",
                )
            )
    return tuple(outcomes)


def _row_counts(context: Context, models: Iterable[str]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for name in models:
        row = context.engine_adapter.fetchone(f"SELECT COUNT(*) FROM {name}")
        if row is None:
            raise RuntimeError(f"COUNT(*) on {name} returned no row")
        counts[name] = int(row[0])
    return counts
