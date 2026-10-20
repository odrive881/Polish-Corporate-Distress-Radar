"""Generation 3 in MLflow (plan 0015 step G): its parameters, Optuna's trials as child runs, and the
registered model versions with their aliases."""

# polars's, LightGBM's and mlflow's signatures reference types pyright cannot resolve; scoped here.
# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false
# pyright: reportUnknownArgumentType=false, reportMissingTypeStubs=false

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import lightgbm as lgb
import numpy as np
import polars as pl
import pytest
from mlflow.tracking import MlflowClient
from tests.models.frames import TIMING, panel

from distress_radar.features.config import load_feature_set
from distress_radar.models.backtest import BacktestResult, run_backtest
from distress_radar.models.baselines import load_classical_model
from distress_radar.models.gbm import CALIBRATED, feature_matrix
from distress_radar.models.registry import (
    BASIS_TAG,
    CHAMPION_ALIAS,
    INCUMBENT_ALIAS,
    PROVISIONAL_TAG,
    Registration,
    RunIdentifiers,
    identifiers,
    log_backtest,
    register_models,
    registered_name,
)
from distress_radar.models.report import render_report, write_report
from distress_radar.models.splits import BacktestConfig, BootstrapConfig, load_backtest_config
from distress_radar.models.survival import FOREST, HAZARD

COMMIT = "a" * 40
SNAPSHOT = "b" * 64


def _config(min_tuning_events: int = 30, min_events: int = 3) -> BacktestConfig:
    config = load_backtest_config("backtest_v5")
    assert config.survival is not None and config.tuning is not None
    return config.model_copy(
        update={
            "population": None,
            "min_events": min_events,
            # v1's regression: the panel holds ratios, not v5's text flag.
            "logistic_regression": load_backtest_config("backtest_v1").logistic_regression,
            "test_years": (2022, 2023),
            "bootstrap": BootstrapConfig(replicates=100, seed=7, level=0.95),
            "tuning": config.tuning.model_copy(
                update={"min_tuning_events": min_tuning_events, "trials": 3}
            ),
            "survival": config.survival.model_copy(
                update={
                    "comparison": config.survival.comparison.model_copy(update={"n_estimators": 10})
                }
            ),
        }
    )


def _run(config: BacktestConfig) -> BacktestResult:
    features, labels = panel(30, seed=3)
    feature_set = load_feature_set("feature_set_v3").feature_set
    models = [load_classical_model("altman_z2_2000", feature_set)]
    return run_backtest(features, labels, config, TIMING, models)


@pytest.fixture(scope="module")
def result() -> BacktestResult:
    return _run(_config())


@pytest.fixture(scope="module")
def tuned() -> BacktestResult:
    return _run(_config(min_tuning_events=1, min_events=2))


def _log(
    result: BacktestResult, tmp_path: Path, uri: str
) -> tuple[RunIdentifiers, dict[tuple[str, int, str], str]]:
    report = write_report(render_report(result, COMMIT), tmp_path, result.config.backtest)
    ids = identifiers(result, COMMIT, SNAPSHOT)
    work = tmp_path / "work"
    work.mkdir(exist_ok=True)
    run_ids = log_backtest(
        result, ids, report, {"a.parquet": "e" * 64}, uri, tmp_path / "artifacts", work
    )
    return ids, run_ids


def test_the_runs_carry_generation_3_configuration(result: BacktestResult, tmp_path: Path) -> None:
    uri = f"sqlite:///{tmp_path / 'mlflow.db'}"
    _, run_ids = _log(result, tmp_path, uri)
    client = MlflowClient(tracking_uri=uri)
    params = {m: client.get_run(run_ids[("main", 12, m)]).data.params for m in (CALIBRATED, FOREST)}
    assert params[CALIBRATED]["lightgbm.num_leaves"] == "7"
    assert params[CALIBRATED]["tuning.min_tuning_events"] == "30"
    assert params[CALIBRATED]["calibration.method"] == "platt"
    assert params[FOREST]["forest.n_estimators"] == "10"
    assert "lightgbm.num_leaves" not in params[FOREST]
    # Below the gate nothing is tuned, so no child run.
    children = client.search_runs(
        [client.get_run(run_ids[("main", 12, CALIBRATED)]).info.experiment_id],
        filter_string="tags.mlflow.parentRunId != ''",
    )
    assert not children


def test_each_tuned_fold_is_a_child_run_with_its_trials(
    tuned: BacktestResult, tmp_path: Path
) -> None:
    assert tuned.trials is not None and not tuned.trials.is_empty()
    uri = f"sqlite:///{tmp_path / 'mlflow.db'}"
    ids, run_ids = _log(tuned, tmp_path, uri)
    client = MlflowClient(tracking_uri=uri)
    for (run, horizon), rows in tuned.trials.group_by("run", "horizon_months"):
        parent = run_ids[(str(run), int(horizon), CALIBRATED)]  # type: ignore[arg-type]
        children = client.search_runs(
            [client.get_run(parent).info.experiment_id],
            filter_string=f"tags.mlflow.parentRunId = '{parent}'",
        )
        assert {int(c.data.params["test_year"]) for c in children} == set(
            rows.get_column("test_year").to_list()
        )
        for child in children:
            for name, value in ids.params().items():
                assert child.data.params[name] == value
            year = int(child.data.params["test_year"])
            mine = rows.filter(pl.col("test_year") == year)
            assert {k for k in child.data.params if k.startswith("trial_")} == {
                f"trial_{r['trial']:03d}.{r['parameter']}" for r in mine.iter_rows(named=True)
            }
            history = client.get_metric_history(child.info.run_id, "inner_brier")
            assert [m.step for m in history] == sorted(set(mine.get_column("trial").to_list()))


def test_the_incumbent_is_registered_per_horizon_and_nothing_is_crowned(
    result: BacktestResult, tmp_path: Path
) -> None:
    assert result.champion is not None and result.champion.promoted is None
    uri = f"sqlite:///{tmp_path / 'mlflow.db'}"
    ids, run_ids = _log(result, tmp_path, uri)
    registered = register_models(result, ids, run_ids, uri, tmp_path / "reg")
    client = MlflowClient(tracking_uri=uri)
    assert [(r.horizon_months, r.model, r.alias) for r in registered] == [
        (12, "logistic_regression", INCUMBENT_ALIAS),
        (24, "logistic_regression", INCUMBENT_ALIAS),
    ]
    for r in registered:
        version = client.get_model_version_by_alias(registered_name(r.horizon_months), "incumbent")
        assert str(version.version) == r.version
        assert version.run_id == run_ids[("main", r.horizon_months, "logistic_regression")]
        assert version.tags["test_year"] == str(max(result.config.test_years))
        for name, value in ids.params().items():
            assert version.tags[name] == value
        artifacts = {a.path for a in client.list_artifacts(r.run_id, "model")}
        assert artifacts == {"model/model.json"}
        assert "champion.json" in {a.path for a in client.list_artifacts(r.run_id)}
        with pytest.raises(Exception, match="champion"):
            client.get_model_version_by_alias(registered_name(r.horizon_months), CHAMPION_ALIAS)


def test_a_promotion_sets_the_champion_and_a_later_non_promotion_leaves_it(
    result: BacktestResult, tmp_path: Path
) -> None:
    assert result.champion is not None
    promoted = replace(result, champion=replace(result.champion, promoted=CALIBRATED))
    uri = f"sqlite:///{tmp_path / 'mlflow.db'}"
    ids, run_ids = _log(promoted, tmp_path, uri)
    first = register_models(promoted, ids, run_ids, uri, tmp_path / "first")
    champions = {r.horizon_months: r for r in first if r.alias == CHAMPION_ALIAS}
    assert {r.model for r in champions.values()} == {CALIBRATED}
    client = MlflowClient(tracking_uri=uri)
    for horizon, r in champions.items():
        files = {a.path for a in client.list_artifacts(r.run_id, "model")}
        assert files == {"model/model.json", "model/model.txt"}
        champion = client.get_model_version_by_alias(registered_name(horizon), CHAMPION_ALIAS)
        assert str(champion.version) == r.version
    # A later backtest that promotes nothing moves the incumbent and leaves the champion alone.
    _, later_ids = _log(result, tmp_path, uri)
    later = register_models(result, ids, later_ids, uri, tmp_path / "later")
    for r in later:
        name = registered_name(r.horizon_months)
        incumbent = client.get_model_version_by_alias(name, INCUMBENT_ALIAS)
        champion = client.get_model_version_by_alias(name, CHAMPION_ALIAS)
        assert str(incumbent.version) == r.version
        assert str(champion.version) == champions[r.horizon_months].version


def _scored(result: BacktestResult, horizon: int, model: str, year: int) -> pl.DataFrame:
    features, _ = panel(30, seed=3)
    rows = result.predictions.filter(
        (pl.col("run") == "main")
        & (pl.col("horizon_months") == horizon)
        & (pl.col("model") == model)
        & (pl.col("test_year") == year)
    ).select("krs", "as_of_date", "probability")
    return rows.join(features, on=["krs", "as_of_date"], how="left")


@pytest.mark.parametrize("horizon", [12, 24])
def test_the_registered_files_reproduce_the_scores_the_backtest_printed(
    result: BacktestResult, horizon: int
) -> None:
    year, fitted = result.latest[(horizon, "logistic_regression")]
    body = json.loads(fitted.files()["model.json"])
    rows = _scored(result, horizon, "logistic_regression", year)
    x = rows.select(pl.col(body["inputs"]).cast(pl.Float64)).to_numpy()
    ranks = np.column_stack(
        [
            (
                np.searchsorted(ref := np.asarray(values), x[:, j], side="left")
                + np.searchsorted(ref, x[:, j], side="right")
            )
            / (2.0 * ref.size)
            for j, values in enumerate(body["rank_reference"])
        ]
    )
    logit = ranks @ np.asarray(body["coefficients"]) + body["intercept"]
    np.testing.assert_allclose(1 / (1 + np.exp(-logit)), rows.get_column("probability"))

    year, fitted = result.latest[(horizon, CALIBRATED)]
    files = fitted.files()
    body = json.loads(files["model.json"])
    booster = lgb.Booster(model_str=files["model.txt"].decode())
    rows = _scored(result, horizon, CALIBRATED, year)
    x = feature_matrix(rows, body["inputs"])
    if body["platt"] is None:
        expected = booster.predict(x)
    else:
        score = booster.predict(x, raw_score=True)
        expected = 1 / (1 + np.exp(-(body["platt"]["slope"] * score + body["platt"]["intercept"])))
    np.testing.assert_allclose(expected, rows.get_column("probability"))


def test_every_challenger_has_a_registrable_model(result: BacktestResult) -> None:
    for horizon in result.config.horizons:
        for model in (CALIBRATED, HAZARD, FOREST):
            _, fitted = result.latest[(horizon, model)]
            files = fitted.files()
            assert json.loads(files["model.json"])["horizon_months"] == horizon
            assert set(files) - {"model.json"} == (
                {"model.pkl"} if model == FOREST else {"model.txt"}
            )


# --- provisional and final (plan 0019 step F, decision 8) ------------------------------------------


def _verdict(result: BacktestResult, promoted: str | None, basis: str) -> BacktestResult:
    assert result.champion is not None
    return replace(result, champion=replace(result.champion, promoted=promoted, basis=basis))


def _register(result: BacktestResult, tmp_path: Path, uri: str, tag: str) -> list[Registration]:
    ids, run_ids = _log(result, tmp_path, uri)
    return register_models(result, ids, run_ids, uri, tmp_path / tag)


def _alias_version(client: MlflowClient, horizon: int, alias: str) -> str | None:
    found = client.search_registered_models(filter_string=f"name = '{registered_name(horizon)}'")
    version = dict(found[0].aliases).get(alias) if found else None
    return None if version is None else str(version)


def test_every_version_carries_its_basis(result: BacktestResult, tmp_path: Path) -> None:
    uri = f"sqlite:///{tmp_path / 'mlflow.db'}"
    client = MlflowClient(tracking_uri=uri)
    for tag, basis, provisional in (("u", "unweighted", "true"), ("w", "weighted", "false")):
        for r in _register(_verdict(result, CALIBRATED, basis), tmp_path, uri, tag):
            tags = client.get_model_version(r.name, r.version).tags
            assert (tags[BASIS_TAG], tags[PROVISIONAL_TAG]) == (basis, provisional)


def test_a_weighted_verdict_supersedes_a_provisional_champion(
    result: BacktestResult, tmp_path: Path
) -> None:
    uri = f"sqlite:///{tmp_path / 'mlflow.db'}"
    client = MlflowClient(tracking_uri=uri)
    _register(_verdict(result, CALIBRATED, "unweighted"), tmp_path, uri, "provisional")
    # The same model promoted on a weighted run: the alias moves to the weighted run's version.
    final = _register(_verdict(result, CALIBRATED, "weighted"), tmp_path, uri, "final")
    for r in (r for r in final if r.alias == CHAMPION_ALIAS):
        assert _alias_version(client, r.horizon_months, CHAMPION_ALIAS) == r.version
    # A weighted run that promotes nothing removes it; the incumbent stays, and serves.
    nothing = _register(_verdict(result, None, "weighted"), tmp_path, uri, "nothing")
    for r in nothing:
        assert _alias_version(client, r.horizon_months, CHAMPION_ALIAS) is None
        assert _alias_version(client, r.horizon_months, INCUMBENT_ALIAS) == r.version


def test_a_provisional_promotion_never_displaces_a_final_champion(
    result: BacktestResult, tmp_path: Path
) -> None:
    uri = f"sqlite:///{tmp_path / 'mlflow.db'}"
    client = MlflowClient(tracking_uri=uri)
    final = _register(_verdict(result, CALIBRATED, "weighted"), tmp_path, uri, "final")
    champions = {r.horizon_months: r.version for r in final if r.alias == CHAMPION_ALIAS}
    later = _register(_verdict(result, CALIBRATED, "unweighted"), tmp_path, uri, "later")
    assert all(r.alias == INCUMBENT_ALIAS for r in later)
    for horizon, version in champions.items():
        assert _alias_version(client, horizon, CHAMPION_ALIAS) == version
