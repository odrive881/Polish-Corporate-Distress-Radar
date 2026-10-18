"""Score intervals: how far a served regression's probability could move with other training
draws (plan 0021 decision 5).

The served version's fold's training rows are rebuilt (as the drift report rebuilds them), and
first refitted whole: the refit must reproduce the served coefficients, or the rows are not the
ones it was trained on and no interval is given. Then entities are resampled with replacement,
the same rank-logistic model is refitted on each draw, and each scored row is scored by every
refit. The 5th and 95th percentiles of those scores are a 90% interval for **estimation**
uncertainty only. It says how much the coefficients' sampling moves the score, on the lists the
model was trained on (ADR 0014), not how well the probability is calibrated for the segment.

Only the regression has one. A gradient-boosting or survival champion would need its own
procedure, so its scores get a reason instead.
"""

# polars's signatures reference types pyright cannot resolve; scoped to this module.
# pyright: reportUnknownMemberType=false

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import numpy.typing as npt
import polars as pl

from distress_radar.models.classical import distress_probability, fit_rank_logistic, parameters
from distress_radar.models.dataset import TARGET
from distress_radar.models.splits import LogisticConfig

METHOD = "entity_bootstrap_refit"
REPLICATES = 200
LEVEL = 0.90
SEED = 20261018
COEFFICIENT_TOLERANCE = 1e-6
NOT_REGRESSION = "no interval method for this model kind"
NOT_REPRODUCED = "the refit does not reproduce the served model: its training rows are not rebuilt"
TOO_FEW = "too few bootstrap draws could be fitted"

INTERVAL_SCHEMA: dict[str, pl.DataType | type[pl.DataType]] = {
    "krs": pl.String,
    "as_of_date": pl.Date,
    "probability_low": pl.Float64,
    "probability_high": pl.Float64,
    "not_computed_reason": pl.String,
}
Array = npt.NDArray[np.float64]


@dataclass(frozen=True)
class IntervalRun:
    rows: pl.DataFrame  # INTERVAL_SCHEMA, one row per scored row
    replicates_fitted: int
    reason: str | None  # set when no row has an interval


def _matrix(rows: pl.DataFrame, columns: list[str]) -> Array:
    return rows.select(pl.col(columns).cast(pl.Float64)).to_numpy()


def _none(scored: pl.DataFrame, reason: str) -> IntervalRun:
    return IntervalRun(
        pl.DataFrame(
            {
                "krs": scored.get_column("krs"),
                "as_of_date": scored.get_column("as_of_date"),
                "probability_low": [None] * scored.height,
                "probability_high": [None] * scored.height,
                "not_computed_reason": [reason] * scored.height,
            },
            schema=INTERVAL_SCHEMA,
        ),
        0,
        reason,
    )


def not_computed(scored: pl.DataFrame, reason: str = NOT_REGRESSION) -> IntervalRun:
    """Rows of the scores with no interval, and why."""
    return _none(scored, reason)


def bootstrap_intervals(
    train: pl.DataFrame,
    scored: pl.DataFrame,
    config: LogisticConfig,
    served_coefficients: tuple[list[float], float],
    *,
    replicates: int = REPLICATES,
    level: float = LEVEL,
    seed: int = SEED,
) -> IntervalRun:
    """`train`: the fold's training rows with `TARGET`; `scored`: the rows the served model scored
    (`krs`, `as_of_date` and its inputs, all present). One interval row per scored row."""
    columns = list(config.features)
    train = train.drop_nulls(columns).sort("krs", "as_of_date")
    x, y = _matrix(train, columns), train.get_column(TARGET).to_numpy()
    _, whole = fit_rank_logistic(x, y, config)
    coefficients, intercept = parameters(whole)
    expected, expected_intercept = served_coefficients
    if len(coefficients) != len(expected) or not np.allclose(
        [*coefficients, intercept], [*expected, expected_intercept], atol=COEFFICIENT_TOLERANCE
    ):
        return _none(scored, NOT_REPRODUCED)
    target = _matrix(scored, columns)
    krs = np.array(train.get_column("krs").to_list())
    entities = sorted(set(krs.tolist()))
    if len(entities) < 2:
        return _none(scored, TOO_FEW)
    rows_of: list[npt.NDArray[np.intp]] = [np.flatnonzero(krs == e) for e in entities]
    rng = np.random.default_rng(seed)
    draws: list[Array] = []
    for _ in range(replicates):
        picked: list[int] = rng.integers(0, len(entities), size=len(entities)).tolist()
        rows = np.concatenate([rows_of[i] for i in picked])
        if np.unique(y[rows]).size < 2:
            continue  # a draw with one class cannot be fitted
        ranks, model = fit_rank_logistic(x[rows], y[rows], config)
        draws.append(distress_probability(model, ranks, target))
    if len(draws) * 2 < replicates:
        return _none(scored, TOO_FEW)
    tail = (1 - level) / 2
    low, high = np.quantile(np.vstack(draws), [tail, 1 - tail], axis=0)
    return IntervalRun(
        pl.DataFrame(
            {
                "krs": scored.get_column("krs"),
                "as_of_date": scored.get_column("as_of_date"),
                "probability_low": low.astype(np.float64),
                "probability_high": high.astype(np.float64),
                "not_computed_reason": [None] * scored.height,
            },
            schema=INTERVAL_SCHEMA,
        ),
        len(draws),
        None,
    )
