"""Loading a registered version for scoring (plan 0016 step B): the alias rule, served = evaluated,
the regression's complete cases, and `top_drivers`; and the backtest's per-row predictions, the
score history's source (step C, decision 3)."""

# polars's and mlflow's signatures reference types pyright cannot resolve; scoped here.
# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false
# pyright: reportUnknownArgumentType=false, reportMissingTypeStubs=false

from __future__ import annotations

import json
from dataclasses import replace
from datetime import date
from pathlib import Path

import numpy as np
import polars as pl
import pytest
from mlflow.tracking import MlflowClient
from tests.models.frames import TIMING, panel

from distress_radar.features.config import load_feature_set
from distress_radar.models.backtest import (
    PREDICTIONS,
    BacktestResult,
    run_backtest,
    write_predictions,
)
from distress_radar.models.baselines import load_classical_model
from distress_radar.models.gbm import CALIBRATED
from distress_radar.models.registry import (
    CHAMPION_ALIAS,
    INCUMBENT_ALIAS,
    identifiers,
    log_backtest,
    register_models,
)
from distress_radar.models.report import render_report, write_report
from distress_radar.models.serving import (
    INPUTS_INCOMPLETE,
    TOP_DRIVERS,
    NoServedModelError,
    load_scorer,
    load_served,
    resolve,
)
from distress_radar.models.splits import BacktestConfig, BootstrapConfig, load_backtest_config
from distress_radar.models.survival import FOREST, HAZARD
from distress_radar.warehouse import read_dataset

COMMIT = "a" * 40
SNAPSHOT = "b" * 64
LOGISTIC = "logistic_regression"


def _config() -> BacktestConfig:
    config = load_backtest_config("backtest_v5")
    assert config.survival is not None and config.tuning is not None
    return config.model_copy(
        update={
            "population": None,
            "min_events": 3,
            # v1's regression: the panel holds ratios, not v5's text flag.
            "logistic_regression": load_backtest_config("backtest_v1").logistic_regression,
            "test_years": (2022, 2023),
            "bootstrap": BootstrapConfig(replicates=100, seed=7, level=0.95),
            "tuning": config.tuning.model_copy(update={"trials": 3}),
            "survival": config.survival.model_copy(
                update={
                    "comparison": config.survival.comparison.model_copy(update={"n_estimators": 10})
                }
            ),
        }
    )


@pytest.fixture(scope="module")
def features() -> pl.DataFrame:
    return panel(30, seed=3)[0]


@pytest.fixture(scope="module")
def result(features: pl.DataFrame) -> BacktestResult:
    _, labels = panel(30, seed=3)
    feature_set = load_feature_set("feature_set_v3").feature_set
    models = [load_classical_model("altman_z2_2000", feature_set)]
    return run_backtest(features, labels, _config(), TIMING, models)


def _registry(result: BacktestResult, tmp_path: Path) -> str:
    """A fresh MLflow store holding `result`'s runs and registered versions."""
    uri = f"sqlite:///{tmp_path / 'mlflow.db'}"
    report = write_report(render_report(result, COMMIT), tmp_path, result.config.backtest)
    ids = identifiers(result, COMMIT, SNAPSHOT)
    work = tmp_path / "work"
    work.mkdir()
    run_ids = log_backtest(
        result, ids, report, {"a.parquet": "e" * 64}, uri, tmp_path / "artifacts", work
    )
    register_models(result, ids, run_ids, uri, tmp_path / "reg")
    return uri


def _fold_rows(
    result: BacktestResult, features: pl.DataFrame, horizon: int, model: str, year: int
) -> pl.DataFrame:
    """The fold's scored test rows, their printed probability beside their features."""
    printed = result.predictions.filter(
        (pl.col("run") == "main")
        & (pl.col("horizon_months") == horizon)
        & (pl.col("model") == model)
        & (pl.col("test_year") == year)
    ).select("krs", "as_of_date", pl.col("probability").alias("printed"))
    assert not printed.is_empty()
    return printed.join(features, on=["krs", "as_of_date"], how="left")


@pytest.mark.parametrize("horizon", [12, 24])
def test_the_incumbent_serves_and_reproduces_its_fold(
    result: BacktestResult, features: pl.DataFrame, tmp_path: Path, horizon: int
) -> None:
    served, scorer = load_served(_registry(result, tmp_path), horizon)
    assert (served.alias, served.model, scorer.kind) == (INCUMBENT_ALIAS, LOGISTIC, "rank_logistic")
    assert served.test_year == max(result.config.test_years)
    assert served.training_eve == date(served.test_year - 1, 12, 31)
    assert served.tags["code_commit"] == COMMIT
    rows = _fold_rows(result, features, horizon, LOGISTIC, served.test_year)
    scores = scorer.score(rows)
    assert scores.select("krs", "as_of_date").equals(rows.select("krs", "as_of_date"))
    assert scores.get_column("not_scored_reason").is_null().all()
    np.testing.assert_allclose(scores.get_column("probability"), rows.get_column("printed"))


@pytest.mark.parametrize("horizon", [12, 24])
def test_a_champion_is_served_over_the_incumbent_and_reproduces_its_fold(
    result: BacktestResult, features: pl.DataFrame, tmp_path: Path, horizon: int
) -> None:
    assert result.champion is not None
    promoted = replace(result, champion=replace(result.champion, promoted=CALIBRATED))
    served, scorer = load_served(_registry(promoted, tmp_path), horizon)
    assert (served.alias, served.model, scorer.kind) == (
        CHAMPION_ALIAS,
        CALIBRATED,
        "lightgbm_binary",
    )
    year, _ = result.latest[(horizon, CALIBRATED)]
    assert served.test_year == year
    rows = _fold_rows(result, features, horizon, CALIBRATED, year)
    scores = scorer.score(rows)
    np.testing.assert_allclose(scores.get_column("probability"), rows.get_column("printed"))


def test_no_serving_alias_is_an_error(tmp_path: Path) -> None:
    client = MlflowClient(tracking_uri=f"sqlite:///{tmp_path / 'mlflow.db'}")
    with pytest.raises(NoServedModelError, match="distress_radar_12m"):
        resolve(client, 12)
    client.create_registered_model("distress_radar_12m")  # registered, but no alias set
    with pytest.raises(NoServedModelError, match="champion or incumbent"):
        resolve(client, 12)


@pytest.mark.parametrize("model", [HAZARD, FOREST])
@pytest.mark.parametrize("horizon", [12, 24])
def test_the_survival_kinds_reproduce_their_fold(
    result: BacktestResult, features: pl.DataFrame, model: str, horizon: int
) -> None:
    year, fitted = result.latest[(horizon, model)]
    scorer = load_scorer(fitted.files())
    assert scorer.horizon_months == horizon and scorer.contributions is None
    rows = _fold_rows(result, features, horizon, model, year)
    scores = scorer.score(rows)
    np.testing.assert_allclose(scores.get_column("probability"), rows.get_column("printed"))
    assert scores.get_column("top_drivers").is_null().all()


def test_the_regression_scores_complete_cases_only(
    result: BacktestResult, features: pl.DataFrame
) -> None:
    year, fitted = result.latest[(12, LOGISTIC)]
    scorer = load_scorer(fitted.files())
    rows = _fold_rows(result, features, 12, LOGISTIC, year).head(6)
    gap = scorer.inputs[0]
    holed = rows.with_columns(
        pl.when(pl.int_range(pl.len()) % 2 == 0).then(None).otherwise(pl.col(gap)).alias(gap)
    )
    scores = scorer.score(holed)
    assert scores.get_column("not_scored_reason").to_list() == [INPUTS_INCOMPLETE, None] * 3
    assert scores.get_column("probability").is_null().to_list() == [True, False] * 3
    assert scores.get_column("top_drivers").is_null().to_list() == [True, False] * 3
    # A scored row's probability does not depend on its neighbours being scored.
    np.testing.assert_allclose(
        scores.get_column("probability").drop_nulls(),
        scorer.score(rows).get_column("probability").gather([1, 3, 5]),
    )


@pytest.mark.parametrize("model", [LOGISTIC, CALIBRATED])
def test_contributions_add_up_to_the_log_odds(
    result: BacktestResult, features: pl.DataFrame, model: str
) -> None:
    """Exact contributions: base plus the inputs' values is the model's log-odds (the regression's
    logit, the booster's raw score before the Platt map)."""
    year, fitted = result.latest[(12, model)]
    files = fitted.files()
    scorer = load_scorer(files)
    assert scorer.contributions is not None
    rows = _fold_rows(result, features, 12, model, year)
    x = rows.select(pl.col(list(scorer.inputs)).cast(pl.Float64)).to_numpy()
    values, base = scorer.contributions(x)
    total = base + values.sum(axis=1)
    if model == LOGISTIC:
        np.testing.assert_allclose(1 / (1 + np.exp(-total)), rows.get_column("printed"))
    else:
        import lightgbm as lgb

        booster = lgb.Booster(model_str=files["model.txt"].decode())
        np.testing.assert_allclose(
            total, np.asarray(booster.predict(x, raw_score=True), dtype=np.float64), atol=1e-9
        )


def test_top_drivers_are_the_largest_contributions_with_their_value_and_known_from(
    result: BacktestResult, features: pl.DataFrame
) -> None:
    year, fitted = result.latest[(12, LOGISTIC)]
    scorer = load_scorer(fitted.files())
    rows = _fold_rows(result, features, 12, LOGISTIC, year).head(3)
    x = rows.select(pl.col(list(scorer.inputs)).cast(pl.Float64)).to_numpy()
    assert scorer.contributions is not None
    values, _ = scorer.contributions(x)
    for i, (text, row) in enumerate(
        zip(scorer.score(rows).get_column("top_drivers"), rows.iter_rows(named=True), strict=True)
    ):
        drivers = json.loads(text)
        assert len(drivers) == min(TOP_DRIVERS, len(scorer.inputs))
        expected = sorted(range(len(scorer.inputs)), key=lambda j: -abs(values[i, j]))
        assert [d["feature"] for d in drivers] == [scorer.inputs[j] for j in expected[:TOP_DRIVERS]]
        for d in drivers:
            assert d["value"] == row[d["feature"]]
            assert d["known_from"] == row[f"{d['feature']}__known_from"].isoformat()


def test_the_backtest_writes_its_predictions_locally_in_a_fixed_order(
    result: BacktestResult, tmp_path: Path
) -> None:
    """Decision 3's history: every scored row, one file per test year, the same bytes whatever
    order the rows arrive in."""
    first = write_predictions(result, tmp_path / "a")
    shuffled = replace(result, predictions=result.predictions.sample(fraction=1.0, seed=1))
    second = write_predictions(shuffled, tmp_path / "b")
    assert [p.parent.name for p in first] == [f"test_year={y}" for y in result.config.test_years]
    for a, b in zip(first, second, strict=True):
        assert a.read_bytes() == b.read_bytes()
    stored = read_dataset(tmp_path / "a", PREDICTIONS)
    assert stored.height == result.predictions.height
    assert set(stored.get_column("backtest").to_list()) == {result.config.backtest}
