"""Optional per-row weights (plan 0014 item 4, for list v2): an integer weight gives exactly the
result of repeating the row, and no weight changes nothing."""

# polars's and scikit-learn's signatures reference types pyright cannot resolve; scoped here.
# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false
# pyright: reportUnknownArgumentType=false, reportMissingTypeStubs=false

from __future__ import annotations

import numpy as np
import polars as pl
import pytest
from sklearn.linear_model import LogisticRegression

from distress_radar.models.classical import fit_rank_logistic, parameters
from distress_radar.models.evaluation import auc, base_rate, brier, log_loss, reliability
from distress_radar.models.splits import LogisticConfig

RNG = np.random.default_rng(11)
Y = RNG.random(40) < 0.3
P = np.round(RNG.random(40), 1)  # rounded: ties, which the weighted AUC must count half
W = RNG.integers(0, 4, size=40).astype(np.float64)  # zeros among them: rows left out
ONES = np.ones(40)


def _repeat(*arrays: np.ndarray) -> list[np.ndarray]:
    counts = W.astype(np.int64)
    return [np.repeat(a, counts, axis=0) for a in arrays]


@pytest.mark.parametrize("metric", [brier, log_loss, auc])
def test_an_integer_weight_is_a_repeated_row(metric) -> None:  # type: ignore[no-untyped-def]
    y, p = _repeat(Y, P)
    assert metric(Y, P, W) == pytest.approx(metric(y, p))
    assert metric(Y, P, ONES) == pytest.approx(metric(Y, P))


def test_the_weighted_base_rate() -> None:
    (y,) = _repeat(Y)
    assert base_rate(Y, W) == pytest.approx(y.mean())
    assert base_rate(Y) == pytest.approx(Y.mean())
    assert base_rate(Y, np.zeros(40)) is None
    assert base_rate(np.array([], dtype=bool)) is None


def test_the_weighted_auc_by_hand() -> None:
    # Pairs: (0.8 vs 0.5) weight 2*1 won, (0.8 vs 0.9) 2*3 lost, (0.5 vs 0.5) 1*1 tied,
    # (0.5 vs 0.9) 1*3 lost: (2 + 0.5) / (3 * 4).
    y = np.array([True, True, False, False])
    p = np.array([0.8, 0.5, 0.5, 0.9])
    w = np.array([2.0, 1.0, 1.0, 3.0])
    assert auc(y, p, w) == pytest.approx(2.5 / 12)
    assert auc(y, p, np.array([0.0, 0.0, 1.0, 1.0])) is None  # no positive weight


def test_bad_weights_are_refused() -> None:
    with pytest.raises(ValueError, match="weights for"):
        brier(Y, P, W[:-1])
    with pytest.raises(ValueError, match="non-negative"):
        log_loss(Y, P, -W)
    with pytest.raises(ValueError, match="finite"):
        auc(Y, P, np.full(40, np.nan))


def test_the_weighted_reliability_table_is_a_repeated_table() -> None:
    frame = pl.DataFrame({"distress": Y, "probability": P, "w": W})
    y, p = _repeat(Y, P)
    weighted = reliability(frame, 5, weight="w")
    repeated = reliability(pl.DataFrame({"distress": y, "probability": p}), 5)
    # Bins whose rows all weigh nothing stay as counted rows with no rate; compare the rest.
    kept = weighted.filter(pl.col("weight_sum") > 0)
    assert kept.get_column("bin_low").to_list() == repeated.get_column("bin_low").to_list()
    np.testing.assert_allclose(kept.get_column("mean_predicted"), repeated["mean_predicted"])
    np.testing.assert_allclose(kept.get_column("observed_rate"), repeated["observed_rate"])
    np.testing.assert_allclose(kept.get_column("weight_sum"), repeated["rows"].cast(pl.Float64))
    assert weighted.get_column("rows").sum() == 40  # counts stay counts
    assert reliability(frame, 5).columns == repeated.columns  # no weight: the table as before


def test_the_weighted_regression_is_a_fit_on_repeated_rows() -> None:
    x = RNG.normal(size=(40, 2))
    config = LogisticConfig(features=("a", "b"), penalty="l2", c=1.0, class_weight="none")
    ranks, weighted = fit_rank_logistic(x, Y, config, W)
    # The same ranks (the transform is fitted on the rows as drawn), the rows repeated.
    (repeated_ranks, y) = _repeat(ranks.transform(x), Y)
    plain = LogisticRegression(l1_ratio=0.0, C=1.0, solver="lbfgs", max_iter=1000)
    plain.fit(repeated_ranks, y)
    np.testing.assert_allclose(parameters(weighted)[0], parameters(plain)[0], rtol=1e-4)
    _, unweighted = fit_rank_logistic(x, Y, config)
    _, ones = fit_rank_logistic(x, Y, config, ONES)
    np.testing.assert_allclose(parameters(ones)[0], parameters(unweighted)[0], rtol=1e-6)
