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

Generation 3 (plan 0015 step G): its runs also carry their configuration (LightGBM's settings, the
tuning gate, the calibration rule, the survival models'); each fold Optuna tuned gets a child run of
the `lightgbm` run, its trials as parameters and their inner Brier scores as a metric by trial.

The registry (`register_models`): one registered model per horizon, `distress_radar_<h>m`. Each
backtest registers a version of the incumbent there, with the alias `incumbent`, and, when the
champion rule promotes a challenger, a version of it with the alias `champion` (decision 7: only a
promotion sets it, and a backtest that promotes nothing leaves an earlier one where it is). A
version is the model the `main` run fitted for its latest fold allowed to speak for the pool, so it
was trained on the labels settled on that test year's eve; its files are logged under `model/` in
that MLflow run, beside the champion rule's comparisons (`champion.json`).

Provisional and final (plan 0019 step F, decision 8): every version registered carries the tags
`champion_basis` (`unweighted` or `weighted`) and `provisional` (`true` for an unweighted
verdict). A weighted verdict supersedes: a promotion sets `champion` on its own version (the same
model's, or another's), and no promotion removes `champion`, so `incumbent` serves. An unweighted
promotion never displaces a final champion (one registered from a weighted verdict). `incumbent`
is set by every backtest and never removed.
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
from distress_radar.models.champion import ChampionVerdict
from distress_radar.models.dataset import FEATURE_STORE
from distress_radar.models.evaluation import METRICS
from distress_radar.models.gbm import CALIBRATED, RAW
from distress_radar.models.splits import BacktestConfig
from distress_radar.models.survival import FOREST, HAZARD

EXPERIMENT = "phase6_backtest"
MAX_PARAMS = 100  # MLflow's limit per `log_batch`
INCUMBENT_ALIAS = "incumbent"
CHAMPION_ALIAS = "champion"
BASIS_TAG = "champion_basis"
PROVISIONAL_TAG = "provisional"
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


def model_params(config: BacktestConfig, model: str) -> dict[str, str]:
    """A model's configuration as run parameters."""
    if model == "logistic_regression":
        lr = config.logistic_regression
        return {
            "features": ",".join(lr.features),
            "penalty": lr.penalty,
            "C": str(lr.c),
            "class_weight": "none",
        }
    params: dict[str, str] = {}
    if model in (RAW, CALIBRATED, HAZARD) and config.lightgbm is not None:
        params.update({f"lightgbm.{k}": str(v) for k, v in config.lightgbm.model_dump().items()})
    if model in (RAW, CALIBRATED) and config.tuning is not None:
        tuning = config.tuning
        params.update(
            {
                "tuning.min_tuning_events": str(tuning.min_tuning_events),
                "tuning.trials": str(tuning.trials),
                "tuning.sampler": tuning.sampler,
                "tuning.seed": str(tuning.seed),
                "tuning.space": ",".join(sorted(tuning.space)),
            }
        )
    if model == CALIBRATED and config.calibration is not None:
        params.update(
            {
                "calibration.method": config.calibration.method,
                "calibration.fit_on": config.calibration.fit_on,
            }
        )
    if model in (HAZARD, FOREST) and config.survival is not None:
        params["survival.max_months"] = str(config.survival.max_months)
        params["survival.period"] = config.survival.period
    if model == FOREST and config.survival is not None:
        params.update(
            {f"forest.{k}": str(v) for k, v in config.survival.comparison.model_dump().items()}
        )
    return params


def _log_params(client: MlflowClient, run_id: str, params: dict[str, str]) -> None:
    items = [Param(k, v) for k, v in params.items()]
    for start in range(0, len(items), MAX_PARAMS):
        client.log_batch(run_id, params=items[start : start + MAX_PARAMS])


def _log_trials(
    client: MlflowClient,
    experiment_id: str,
    parent: str,
    name: str,
    ids: RunIdentifiers,
    trials: pl.DataFrame,
) -> list[str]:
    """One child run per tuned fold: each trial's settings as `trial_<n>.<setting>`, its inner
    Brier score as the metric `inner_brier` at step n."""
    out: list[str] = []
    for (year,), rows in trials.sort("test_year", "trial", "parameter").group_by(
        "test_year", maintain_order=True
    ):
        created = client.create_run(
            experiment_id,
            tags={
                "mlflow.parentRunId": parent,
                "mlflow.runName": f"{name}/tuning/{year}",
                "mlflow.source.git.commit": ids.code_commit,
            },
        )
        run_id = str(created.info.run_id)
        _log_params(
            client,
            run_id,
            {
                **ids.params(),
                "test_year": str(year),
                **{
                    f"trial_{r['trial']:03d}.{r['parameter']}": f"{r['value']:g}"
                    for r in rows.iter_rows(named=True)
                },
            },
        )
        scores = rows.unique("trial", keep="first").sort("trial")
        client.log_batch(
            run_id,
            metrics=[
                Metric("inner_brier", float(r["inner_brier"]), 0, int(r["trial"]))
                for r in scores.iter_rows(named=True)
            ],
        )
        client.set_terminated(run_id)
        out.append(run_id)
    return out


RunKey = tuple[str, int, str]  # backtest run, horizon, model


def log_backtest(
    result: BacktestResult,
    ids: RunIdentifiers,
    report: Path,
    snapshot_files: dict[str, str],
    tracking_uri: str,
    artifact_dir: Path,
    workdir: Path,
) -> dict[RunKey, str]:
    """One MLflow run per model, horizon and backtest run, and a child run per tuned fold;
    returns the MLflow run ids by backtest run, horizon and model (child runs not among them)."""
    client = MlflowClient(tracking_uri=tracking_uri)
    experiment_id = _experiment(client, artifact_dir)
    manifest = workdir / "data_snapshot.json"
    manifest.write_text(
        json.dumps({"feature_store": snapshot_files}, indent=2, sort_keys=True) + "\n", "utf-8"
    )
    config = result.config
    run_ids: dict[RunKey, str] = {}
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
            **model_params(config, model),
            # A weighted backtest's draw (plan 0019 step D), beside the four identifiers.
            **(
                {
                    "draw_spec_sha256": config.weights.draw_spec_sha256,
                    "weights_nonresponse": config.weights.nonresponse,
                }
                if config.weights is not None
                else {}
            ),
        }
        tags = {
            "mlflow.runName": f"{config.backtest}/{model}/{horizon}m/{run}",
            "mlflow.source.git.commit": ids.code_commit,
            "seed_output": "true",  # decision 0: machinery, not evidence
        }
        created = client.create_run(experiment_id, tags=tags)
        run_id = str(created.info.run_id)
        client.log_batch(run_id, metrics=[Metric(k, v, 0, 0) for k, v in _cell_metrics(cells)])
        _log_params(client, run_id, params)
        client.log_artifact(run_id, str(report))
        client.log_artifact(run_id, str(manifest))
        if model == CALIBRATED and result.trials is not None:
            tuned = result.trials.filter(
                (pl.col("run") == run) & (pl.col("horizon_months") == horizon)
            )
            if not tuned.is_empty():
                _log_trials(client, experiment_id, run_id, tags["mlflow.runName"], ids, tuned)
        client.set_terminated(run_id)
        run_ids[(run, horizon, model)] = run_id
    return run_ids


@dataclass(frozen=True)
class Registration:
    """A registered model version and the alias the backtest set on it."""

    name: str
    version: str
    model: str
    horizon_months: int
    test_year: int  # the fold whose model it is: trained on labels settled on this year's eve
    alias: str
    run_id: str


def registered_name(horizon: int) -> str:
    return f"distress_radar_{horizon}m"


def _comparisons(verdict: ChampionVerdict) -> str:
    body = {
        "incumbent": verdict.incumbent,
        "promoted": verdict.promoted,
        "basis": verdict.basis,
        "provisional": verdict.provisional,
        "verdicts": verdict.verdicts,
        "comparisons": [c.__dict__ for c in verdict.comparisons],
    }
    return json.dumps(body, indent=2, sort_keys=True) + "\n"


def register_models(
    result: BacktestResult,
    ids: RunIdentifiers,
    run_ids: dict[RunKey, str],
    tracking_uri: str,
    workdir: Path,
) -> list[Registration]:
    """Per horizon, a version of the incumbent (alias `incumbent`) and, on a promotion, of the
    champion (alias `champion`). Nothing for a config with no champion rule, nor for a model with
    no fold allowed to speak for the pool. A weighted verdict that promotes nothing removes the
    horizon's `champion`; an unweighted promotion leaves a final one in place (decision 8)."""
    verdict = result.champion
    if verdict is None:
        return []
    client = MlflowClient(tracking_uri=tracking_uri)
    workdir.mkdir(parents=True, exist_ok=True)
    comparisons = workdir / "champion.json"
    comparisons.write_text(_comparisons(verdict), "utf-8")
    roles = [(verdict.incumbent, INCUMBENT_ALIAS)]
    if verdict.promoted is not None:
        roles.append((verdict.promoted, CHAMPION_ALIAS))
    out: list[Registration] = []
    for horizon in result.config.horizons:
        name = registered_name(horizon)
        final = _final_champion(client, name)
        for model, alias in roles:
            if alias == CHAMPION_ALIAS and verdict.provisional and final:
                continue  # a provisional verdict never displaces a final champion
            found = result.latest.get((horizon, model))
            if found is None:
                continue
            test_year, fitted = found
            run_id = run_ids[("main", horizon, model)]
            files = workdir / f"{horizon}m_{model}" / "model"
            files.mkdir(parents=True)
            for file_name, data in fitted.files().items():
                (files / file_name).write_bytes(data)
            client.log_artifacts(run_id, str(files), "model")
            client.log_artifact(run_id, str(comparisons))
            if not client.search_registered_models(filter_string=f"name = '{name}'"):
                client.create_registered_model(
                    name, description=f"P(distress within {horizon} months), plan 0015"
                )
            source = f"{client.get_run(run_id).info.artifact_uri}/model"
            version = client.create_model_version(
                name,
                source,
                run_id=run_id,
                tags={
                    **ids.params(),
                    "backtest": result.config.backtest,
                    "model": model,
                    "test_year": str(test_year),
                    "verdict": verdict.verdicts.get(model, "incumbent"),
                    "seed_output": "true",  # machinery, not evidence (plan 0015)
                    BASIS_TAG: verdict.basis,
                    PROVISIONAL_TAG: str(verdict.provisional).lower(),
                },
            )
            client.set_registered_model_alias(name, alias, str(version.version))
            out.append(
                Registration(name, str(version.version), model, horizon, test_year, alias, run_id)
            )
        if not verdict.provisional and verdict.promoted is None and _has_champion(client, name):
            client.delete_registered_model_alias(name, CHAMPION_ALIAS)
    return out


def _aliases(client: MlflowClient, name: str) -> dict[str, str]:
    found = client.search_registered_models(filter_string=f"name = '{name}'")
    return dict(found[0].aliases) if found else {}


def _has_champion(client: MlflowClient, name: str) -> bool:
    return CHAMPION_ALIAS in _aliases(client, name)


def _final_champion(client: MlflowClient, name: str) -> bool:
    """Whether the registered model's `champion` came from a weighted verdict."""
    version = _aliases(client, name).get(CHAMPION_ALIAS)
    if version is None:
        return False
    return client.get_model_version(name, str(version)).tags.get(BASIS_TAG) == "weighted"
