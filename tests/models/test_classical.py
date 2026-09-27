"""The logistic regression and the classical models' scores (plan 0012 step D)."""

# polars's signatures reference types pyright cannot resolve; scoped to this module.
# pyright: reportUnknownMemberType=false

from __future__ import annotations

from datetime import date

import numpy as np
import polars as pl
import pytest

from distress_radar.features.config import load_feature_set
from distress_radar.models.baselines import (
    fit_predict_classical,
    load_classical_model,
    score,
)
from distress_radar.models.classical import RankTransform, fit_predict_logistic
from distress_radar.models.dataset import TARGET
from distress_radar.models.splits import Fold, LogisticConfig

FEATURES = ("equity_to_assets", "working_capital_to_assets", "roa", "asset_turnover")
CONFIG = LogisticConfig(features=FEATURES, penalty="l2", C=1.0, class_weight="none")
POZNAN = ("roa", "quick_ratio", "long_term_capital_to_assets", "sales_margin")


def _rows(n: int, year: int, seed: int, distressed_every: int = 4) -> pl.DataFrame:
    """Rows whose distress goes with low ratios; every `distressed_every`-th is its own event."""
    rng = np.random.default_rng(seed)
    out: list[dict[str, object]] = []
    for i in range(n):
        bad = i % distressed_every == 0
        base = -0.3 if bad else 0.3
        values = {f: float(base + rng.normal(0, 0.1)) for f in {*FEATURES, *POZNAN}}
        out.append(
            {
                "krs": f"{i:010d}",
                "as_of_date": date(year, 6, 30),
                TARGET: bad,
                "outcome_class": "bankruptcy" if bad else "alive",
                "event_date": date(year + 1, 3, 1) if bad else None,
                "event_known_from": date(year + 1, 3, 10) if bad else None,
                "trigger_event_type": "bankruptcy_declared" if bad else None,
                "regime_flag": False,
                **values,
            }
        )
    return pl.DataFrame(out).with_columns(pl.col("event_date", "event_known_from").cast(pl.Date))


def _fold(train: pl.DataFrame, test: pl.DataFrame) -> Fold:
    return Fold(horizon_months=12, test_year=2022, train=train, test=test)


# --- the rank transform ------------------------------------------------------------------------


def test_ranks_are_mid_ranks_of_the_training_values() -> None:
    ranks = RankTransform.fit(np.array([[1.0], [2.0], [2.0], [3.0]]))
    got = ranks.transform(np.array([[1.0], [2.0], [3.0], [2.5], [10.0], [-5.0]]))[:, 0]
    assert got.tolist() == [0.125, 0.5, 0.875, 0.75, 1.0, 0.0]


def test_ranks_depend_on_the_training_values_only() -> None:
    train = np.array([[0.1], [0.4], [0.2]])
    first = RankTransform.fit(train).transform(np.array([[0.3]]))
    again = RankTransform.fit(train).transform(np.array([[0.3], [99.0]]))[:1]
    assert first.tolist() == again.tolist()


# --- the regression ------------------------------------------------------------------------------


def test_the_regression_fits_complete_cases_and_counts_the_rest() -> None:
    train = _rows(40, 2020, seed=1).with_columns(
        pl.when(pl.int_range(pl.len()) < 2).then(None).otherwise(pl.col("roa")).alias("roa")
    )
    test = _rows(12, 2022, seed=2).with_columns(
        pl.when(pl.int_range(pl.len()) == 5).then(None).otherwise(pl.col("roa")).alias("roa")
    )
    result = fit_predict_logistic(_fold(train, test), CONFIG)
    s = result.summary
    assert (s.train_rows, s.train_rows_left_out, s.test_rows, s.test_rows_left_out) == (
        38,
        2,
        11,
        1,
    )
    assert s.fitted and s.train_events == 9  # rows 0 and 1 dropped, row 0 was an event
    assert result.predictions.height == 11
    assert result.predictions.get_column("probability").is_null().sum() == 0
    assert [name for name, _ in s.coefficients] == list(FEATURES)


def test_the_regression_learns_the_direction() -> None:
    result = fit_predict_logistic(_fold(_rows(40, 2020, 1), _rows(12, 2022, 2)), CONFIG)
    p = result.predictions
    assert (
        p.filter(pl.col(TARGET))["probability"].mean()
        > p.filter(~pl.col(TARGET))[  # type: ignore[operator]
            "probability"
        ].mean()
    )
    assert all(c < 0 for _, c in result.summary.coefficients)  # higher ratios, less distress


def test_a_fold_with_one_class_is_reported_not_fitted() -> None:
    alive = _rows(20, 2020, 1).filter(~pl.col(TARGET))
    result = fit_predict_logistic(_fold(alive, _rows(8, 2022, 2)), CONFIG)
    assert not result.summary.fitted
    assert result.summary.note == "the training rows hold one class"
    assert result.predictions.is_empty()


def test_the_regression_is_deterministic() -> None:
    fold = _fold(_rows(40, 2020, 1), _rows(12, 2022, 2))
    first, second = fit_predict_logistic(fold, CONFIG), fit_predict_logistic(fold, CONFIG)
    assert first.summary == second.summary
    assert first.predictions.equals(second.predictions)


# --- the classical models ------------------------------------------------------------------------


@pytest.fixture(scope="module")
def poznan():
    return load_classical_model("poznan_2004", load_feature_set("feature_set_v3").feature_set)


def test_the_poznan_score_by_hand(poznan) -> None:
    row = pl.DataFrame(
        {
            "roa": [0.05, None],
            "quick_ratio": [1.2, 1.0],
            "long_term_capital_to_assets": [0.5, 0.5],
            "sales_margin": [0.03, 0.03],
        }
    )
    got = row.select(score(poznan)).get_column("score").to_list()
    # 3.562*0.05 + 1.588*1.2 + 4.288*0.5 + 6.719*0.03 - 2.368
    assert got[0] == pytest.approx(2.06127)
    assert got[1] is None  # a missing ratio leaves no score, never an imputed one


def test_a_classical_score_is_mapped_on_the_training_fold(poznan) -> None:
    result = fit_predict_classical(_fold(_rows(40, 2020, 1), _rows(12, 2022, 2)), poznan, CONFIG)
    p = result.predictions
    assert result.summary.fitted and result.summary.coefficients[0][1] < 0  # higher score, safer
    assert set(p.get_column("zone").unique().to_list()) <= {"sound", "at_risk"}
    # The mapping is monotone: a higher score never gets a higher probability.
    ordered = p.sort("score").get_column("probability").to_list()
    assert ordered == sorted(ordered, reverse=True)
