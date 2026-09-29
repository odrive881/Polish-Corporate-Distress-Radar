"""MLflow tracking with the four identifiers (plan 0012 step F, owner decisions 6 and 7)."""

# polars's and mlflow's signatures reference types pyright cannot resolve; scoped to this module.
# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false
# pyright: reportUnknownArgumentType=false, reportMissingTypeStubs=false

from __future__ import annotations

import subprocess
from dataclasses import replace
from pathlib import Path

import polars as pl
import pytest
from mlflow.tracking import MlflowClient
from tests.models.frames import TIMING, panel

from distress_radar.features.config import load_feature_set
from distress_radar.models.backtest import BacktestResult, run_backtest
from distress_radar.models.baselines import load_classical_model
from distress_radar.models.registry import (
    RunIdentifiers,
    UntrackedCodeError,
    code_commit,
    data_snapshot,
    identifiers,
    log_backtest,
)
from distress_radar.models.report import render_report, write_report
from distress_radar.models.splits import BootstrapConfig, load_backtest_config

GOOD = RunIdentifiers(
    code_commit="a" * 40,
    data_snapshot_hash="b" * 64,
    label_version="outcome_labels_v2",
    label_set_hash="c" * 64,
    feature_set_version="feature_set_v3",
    feature_set_hash="d" * 64,
)


def _git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid", *args],
        cwd=repo,
        check=True,
        capture_output=True,
    )


def test_a_clean_tree_names_its_commit_and_a_dirty_one_is_refused(tmp_path: Path) -> None:
    _git(tmp_path, "init", "-q")
    (tmp_path / "code.py").write_text("x = 1\n")
    _git(tmp_path, "add", "code.py")
    _git(tmp_path, "commit", "-q", "-m", "c")
    assert len(code_commit(tmp_path)) == 40
    (tmp_path / "code.py").write_text("x = 2\n")
    with pytest.raises(UntrackedCodeError, match="code.py"):
        code_commit(tmp_path)
    _git(tmp_path, "checkout", "-q", "code.py")
    (tmp_path / "new.py").write_text("")  # untracked code has no commit either
    with pytest.raises(UntrackedCodeError, match="new.py"):
        code_commit(tmp_path)


def test_the_snapshot_hash_follows_the_bytes_and_names_each_file(tmp_path: Path) -> None:
    store = tmp_path / "feature_store"
    for year in (2021, 2020):  # written out of order: the hash sorts by path
        (store / f"as_of_year={year}").mkdir(parents=True)
        pl.DataFrame({"x": [year]}).write_parquet(store / f"as_of_year={year}" / "part-0.parquet")
    digest, files = data_snapshot(tmp_path)
    assert list(files) == ["as_of_year=2020/part-0.parquet", "as_of_year=2021/part-0.parquet"]
    assert data_snapshot(tmp_path) == (digest, files)
    pl.DataFrame({"x": [0]}).write_parquet(store / "as_of_year=2021" / "part-0.parquet")
    changed, changed_files = data_snapshot(tmp_path)
    assert changed != digest
    assert (
        changed_files["as_of_year=2020/part-0.parquet"] == files["as_of_year=2020/part-0.parquet"]
    )
    with pytest.raises(FileNotFoundError):
        data_snapshot(tmp_path / "empty")


@pytest.mark.parametrize(
    "field,value",
    [
        ("code_commit", ""),
        ("code_commit", "HEAD"),
        ("data_snapshot_hash", ""),
        ("label_version", " "),
        ("label_set_hash", "066d18bb"),
        ("feature_set_version", ""),
        ("feature_set_hash", "x" * 64),
    ],
)
def test_a_run_missing_an_identifier_cannot_be_started(field: str, value: str) -> None:
    with pytest.raises(ValueError, match="all four identifiers"):
        replace(GOOD, **{field: value})


@pytest.fixture(scope="module")
def result() -> BacktestResult:
    features, labels = panel(30, seed=3)
    config = load_backtest_config("backtest_v1").model_copy(
        update={
            "test_years": (2021, 2022, 2023, 2024),
            "bootstrap": BootstrapConfig(replicates=100, seed=7, level=0.95),
        }
    )
    feature_set = load_feature_set("feature_set_v3").feature_set
    models = [load_classical_model(m, feature_set) for m in ("altman_z2_2000", "poznan_2004")]
    return run_backtest(features, labels, config, TIMING, models)


def test_every_model_horizon_and_run_is_logged_with_its_identifiers(
    result: BacktestResult, tmp_path: Path
) -> None:
    uri = f"sqlite:///{tmp_path / 'mlflow.db'}"
    report = write_report(render_report(result, GOOD.code_commit), tmp_path, "backtest_v1")
    ids = identifiers(result, GOOD.code_commit, GOOD.data_snapshot_hash)
    run_ids = log_backtest(
        result, ids, report, {"a.parquet": "e" * 64}, uri, tmp_path / "artifacts", tmp_path
    )
    assert len(run_ids) == 3 * 2 * 2  # models x horizons x runs

    client = MlflowClient(tracking_uri=uri)
    for run_id in run_ids:
        run = client.get_run(run_id)
        params = run.data.params
        for name, value in ids.params().items():
            assert params[name] == value
        assert run.data.tags["mlflow.source.git.commit"] == GOOD.code_commit
        assert run.info.status == "FINISHED"
        artifacts = {a.path for a in client.list_artifacts(run_id)}
        assert artifacts == {"backtest_v1.md", "data_snapshot.json"}
        metrics = run.data.metrics
        assert "test_events.pooled" in metrics
        cells = result.cells.filter(
            (pl.col("run") == params["run"])
            & (pl.col("horizon_months") == int(params["horizon_months"]))
            & (pl.col("model") == params["model"])
        )
        for row in cells.iter_rows(named=True):
            year = "pooled" if row["test_year"] == 0 else str(row["test_year"])
            # Metrics only where the cell is scored: an n/a cell logs counts, never numbers.
            assert (f"brier.{year}" in metrics) == row["evaluable"]
