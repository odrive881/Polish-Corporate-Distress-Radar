"""MLflow tracking for the backtest (plan 0012 step F, owner decisions 6 and 7).

One MLflow run per model, horizon and run of the backtest (`main`, `no_regime`), each carrying the
four identifiers that make it reproducible (CLAUDE.md):
- **code commit:** `git rev-parse HEAD`, from a clean working tree; a tree with uncommitted or
  untracked changes has no commit for its code, and is refused;
- **data snapshot hash:** SHA-256 over the `feature_store` Parquet files in sorted path order,
  which the store writes byte-reproducibly (plan 0010); each file's own hash is logged beside it,
  so a changed snapshot can be traced to the file that changed;
- **label version:** `label_version` and the frozen set's `label_set_hash`;
- **feature-set version:** `feature_set_version` and `feature_set_hash`.

`RunIdentifiers` refuses to exist without all four, so a run missing one is never started, rather
than logged incomplete. The report goes with every run as an artifact.
"""

# polars's and mlflow's signatures reference types pyright cannot resolve; scoped to this module.
# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false
# pyright: reportUnknownArgumentType=false, reportMissingTypeStubs=false

from __future__ import annotations

import hashlib
import json
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

import polars as pl
from mlflow.entities import Metric, Param
from mlflow.tracking import MlflowClient

from distress_radar.models.backtest import POOLED, BacktestResult
from distress_radar.models.dataset import FEATURE_STORE
from distress_radar.models.evaluation import METRICS

EXPERIMENT = "phase6_backtest"
_COMMIT = re.compile(r"^[0-9a-f]{40}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class UntrackedCodeError(RuntimeError):
    """The working tree differs from its commit, so no commit identifies the code."""


def code_commit(repo_dir: Path) -> str:
    """HEAD of a clean working tree; refused when anything is modified or untracked."""
    status = subprocess.run(
        ["git", "status", "--porcelain", "--untracked-files=all"],
        cwd=repo_dir,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    if status.strip():
        changed = ", ".join(line[3:] for line in status.splitlines()[:5])
        raise UntrackedCodeError(f"the working tree has uncommitted changes: {changed}")
    return subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo_dir, capture_output=True, text=True, check=True
    ).stdout.strip()


def data_snapshot(warehouse_dir: Path) -> tuple[str, dict[str, str]]:
    """The snapshot hash of `feature_store`, and each file's hash, by path within the dataset."""
    root = warehouse_dir / FEATURE_STORE
    files = sorted(p for p in root.rglob("*.parquet") if p.is_file())
    if not files:
        raise FileNotFoundError(f"{root} holds no Parquet files")
    per_file = {
        p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest() for p in files
    }
    digest = hashlib.sha256()
    for path, file_hash in per_file.items():  # sorted by path above
        digest.update(f"{path}\0{file_hash}\n".encode())
    return digest.hexdigest(), per_file


@dataclass(frozen=True)
class RunIdentifiers:
    """The four identifiers every run carries; incomplete ones cannot be constructed."""

    code_commit: str
    data_snapshot_hash: str
    label_version: str
    label_set_hash: str
    feature_set_version: str
    feature_set_hash: str

    def __post_init__(self) -> None:
        problems: list[str] = []
        if not _COMMIT.match(self.code_commit):
            problems.append("code_commit is not a 40-character commit")
        for name in ("data_snapshot_hash", "label_set_hash", "feature_set_hash"):
            if not _SHA256.match(getattr(self, name)):
                problems.append(f"{name} is not a SHA-256")
        for name in ("label_version", "feature_set_version"):
            if not getattr(self, name).strip():
                problems.append(f"{name} is empty")
        if problems:
            raise ValueError("a run needs all four identifiers: " + "; ".join(problems))

    def params(self) -> dict[str, str]:
        return dict(self.__dict__)


def identifiers(result: BacktestResult, commit: str, snapshot_hash: str) -> RunIdentifiers:
    return RunIdentifiers(
        code_commit=commit,
        data_snapshot_hash=snapshot_hash,
        label_version=result.label_version,
        label_set_hash=result.label_set_hash,
        feature_set_version=result.feature_set_version,
        feature_set_hash=result.feature_set_hash,
    )


def _experiment(client: MlflowClient, artifact_dir: Path) -> str:
    found = client.get_experiment_by_name(EXPERIMENT)
    if found is not None:
        return str(found.experiment_id)
    artifact_dir.mkdir(parents=True, exist_ok=True)
    return str(
        client.create_experiment(EXPERIMENT, artifact_location=artifact_dir.resolve().as_uri())
    )


def _cell_metrics(cells: pl.DataFrame) -> list[tuple[str, float]]:
    """Counts for every cell; metrics and intervals only where the cell is scored."""
    out: list[tuple[str, float]] = []
    for row in cells.sort("test_year").iter_rows(named=True):
        year = "pooled" if row["test_year"] == POOLED else str(row["test_year"])
        for count in ("train_rows", "train_events", "scored_rows", "test_events"):
            out.append((f"{count}.{year}", float(row[count])))
        out.append((f"evaluable.{year}", float(row["evaluable"])))
        if not row["evaluable"]:
            continue
        for name in METRICS:
            for suffix in ("", "_low", "_high"):
                value = row[f"{name}{suffix}"]
                if value is not None:
                    out.append((f"{name}{suffix}.{year}", float(value)))
    return out


def log_backtest(
    result: BacktestResult,
    ids: RunIdentifiers,
    report: Path,
    snapshot_files: dict[str, str],
    tracking_uri: str,
    artifact_dir: Path,
    workdir: Path,
) -> list[str]:
    """One MLflow run per model, horizon and backtest run; returns the MLflow run ids."""
    client = MlflowClient(tracking_uri=tracking_uri)
    experiment_id = _experiment(client, artifact_dir)
    manifest = workdir / "data_snapshot.json"
    manifest.write_text(
        json.dumps({"feature_store": snapshot_files}, indent=2, sort_keys=True) + "\n", "utf-8"
    )
    config = result.config
    run_ids: list[str] = []
    keys = result.cells.select("run", "horizon_months", "model").unique(maintain_order=True)
    for run, horizon, model in keys.iter_rows():
        cells = result.cells.filter(
            (pl.col("run") == run)
            & (pl.col("horizon_months") == horizon)
            & (pl.col("model") == model)
        )
        params = {
            **ids.params(),
            "backtest": config.backtest,
            "model": model,
            "horizon_months": str(horizon),
            "run": run,
            "test_years": ",".join(map(str, config.test_years)),
            "min_events": str(config.min_events),
            "distress_classes": ",".join(config.distress_classes),
            # When a training label counts as settled (`splits.py`), from the label version.
            "label_alive_lag_months": str(result.timing.alive_lag_months),
            "krz_launch": result.timing.krz_launch.isoformat(),
        }
        if model == "logistic_regression":
            lr = config.logistic_regression
            params.update(
                features=",".join(lr.features), penalty=lr.penalty, C=str(lr.c), class_weight="none"
            )
        tags = {
            "mlflow.runName": f"{config.backtest}/{model}/{horizon}m/{run}",
            "mlflow.source.git.commit": ids.code_commit,
            "seed_output": "true",  # decision 0: machinery, not evidence
        }
        created = client.create_run(experiment_id, tags=tags)
        run_id = str(created.info.run_id)
        client.log_batch(
            run_id,
            metrics=[Metric(k, v, 0, 0) for k, v in _cell_metrics(cells)],
            params=[Param(k, v) for k, v in params.items()],
        )
        client.log_artifact(run_id, str(report))
        client.log_artifact(run_id, str(manifest))
        client.set_terminated(run_id)
        run_ids.append(run_id)
    return run_ids
