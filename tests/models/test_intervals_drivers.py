"""Score intervals and driver movement (plan 0021 decisions 3 and 5)."""

from __future__ import annotations

import json
from datetime import date

import numpy as np
import polars as pl
import pytest

from distress_radar.models.classical import fit_rank_logistic, parameters
from distress_radar.models.dataset import TARGET
from distress_radar.models.drivers import driver_movement
from distress_radar.models.intervals import (
    NOT_REGRESSION,
    NOT_REPRODUCED,
    TOO_FEW,
    bootstrap_intervals,
    not_computed,
)
from distress_radar.models.splits import LogisticConfig

FEATURES = ("equity_to_assets", "roa")
CONFIG = LogisticConfig(features=FEATURES, penalty="l2", C=1.0, class_weight="none")


def _train(entities: int = 60, seed: int = 1) -> pl.DataFrame:
    """Three yearly rows per entity; every fourth entity in distress, with low ratios."""
    rng = np.random.default_rng(seed)
    rows: list[dict[str, object]] = []
    for i in range(entities):
        bad = i % 4 == 0
        for year in (2021, 2022, 2023):
            rows.append(
                {
                    "krs": f"{i:010d}",
                    "as_of_date": date(year, 6, 30),
                    TARGET: bad and year == 2023,
                    **{f: float((-0.2 if bad else 0.3) + rng.normal(0, 0.2)) for f in FEATURES},
                }
            )
    return pl.DataFrame(rows)


def _scored() -> pl.DataFrame:
    return pl.DataFrame(
        {
            "krs": ["0000009001", "0000009002"],
            "as_of_date": [date(2026, 9, 30)] * 2,
            "equity_to_assets": [0.4, -0.3],
            "roa": [0.3, -0.2],
        }
    )


def _served(train: pl.DataFrame) -> tuple[list[float], float]:
    x = train.select(pl.col(list(FEATURES)).cast(pl.Float64)).to_numpy()
    _, model = fit_rank_logistic(x, train.get_column(TARGET).to_numpy(), CONFIG)
    return parameters(model)


def _point(train: pl.DataFrame) -> np.ndarray:
    from distress_radar.models.classical import distress_probability

    x = train.select(pl.col(list(FEATURES)).cast(pl.Float64)).to_numpy()
    ranks, model = fit_rank_logistic(x, train.get_column(TARGET).to_numpy(), CONFIG)
    return distress_probability(model, ranks, _scored().select(list(FEATURES)).to_numpy())


def test_an_interval_holds_the_served_score_and_ranks_like_it() -> None:
    train = _train().sort("krs", "as_of_date")
    run = bootstrap_intervals(train, _scored(), CONFIG, _served(train), replicates=60)
    assert run.reason is None and run.replicates_fitted == 60
    point = _point(train)
    low = run.rows.get_column("probability_low").to_numpy()
    high = run.rows.get_column("probability_high").to_numpy()
    assert np.all(low <= point) and np.all(point <= high)
    assert np.all(low < high)
    assert high[0] < low[1]  # the healthy row's whole range is below the distressed row's
    assert run.rows.get_column("not_computed_reason").null_count() == 2


def test_the_same_inputs_give_the_same_interval() -> None:
    train = _train()
    first = bootstrap_intervals(train, _scored(), CONFIG, _served(train), replicates=30)
    again = bootstrap_intervals(train.reverse(), _scored(), CONFIG, _served(train), replicates=30)
    assert first.rows.equals(again.rows)


def test_rows_that_do_not_reproduce_the_served_model_give_no_interval() -> None:
    train = _train()
    coefficients, intercept = _served(train)
    run = bootstrap_intervals(train, _scored(), CONFIG, (coefficients, intercept + 0.5))
    assert run.reason == NOT_REPRODUCED
    assert run.rows.get_column("probability_low").null_count() == 2
    assert set(run.rows.get_column("not_computed_reason").to_list()) == {NOT_REPRODUCED}


def test_too_few_entities_to_resample_give_no_interval() -> None:
    # one distressed entity, with both classes over its years: fittable, but nothing to resample
    train = _train(entities=1)
    run = bootstrap_intervals(train, _scored(), CONFIG, _served(train), replicates=40)
    assert run.reason == TOO_FEW


def test_another_model_kind_says_why_it_has_none() -> None:
    run = not_computed(_scored())
    assert run.reason == NOT_REGRESSION and run.rows.height == 2


# --- driver movement -----------------------------------------------------------------------------


def _drivers(**contributions: float) -> str:
    return json.dumps(
        [{"feature": f, "contribution": c, "value": 1.0} for f, c in contributions.items()]
    )


def test_movement_is_the_change_in_each_contribution_largest_first() -> None:
    moved = driver_movement(_drivers(roa=0.5, equity=-0.1), _drivers(roa=0.1, equity=-0.2))
    assert moved is not None
    assert [m["feature"] for m in moved] == ["roa", "equity"]
    assert moved[0]["change"] == pytest.approx(0.4)
    assert moved[1]["change"] == pytest.approx(0.1)


def test_a_driver_on_one_side_only_has_no_change_and_comes_last() -> None:
    moved = driver_movement(_drivers(roa=0.5, gc=0.9), _drivers(roa=0.1))
    assert moved is not None
    assert [m["feature"] for m in moved] == ["roa", "gc"]
    assert moved[1]["change"] is None and moved[1]["previous_contribution"] is None


def test_no_previous_score_no_movement() -> None:
    assert driver_movement(_drivers(roa=0.5), None) is None
    assert driver_movement(None, _drivers(roa=0.5)) is None
