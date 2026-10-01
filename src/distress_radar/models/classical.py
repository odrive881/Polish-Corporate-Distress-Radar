"""Regularised logistic regression on a fixed set of ratios (plan 0012 step D, owner decision 5).

Complete cases only: a row missing any of the configured inputs is left out and counted, never
imputed (invariant 4). A flag enters as 0 or 1, and its rank transform is then two mid-ranks. Each ratio is rank-transformed with the training fold's values alone, so
the test period never shapes the transform. The penalty and its strength come from the config
and are never tuned. The same fold-fitted rank transform and one-variable fit map a classical
model's score to a probability (`baselines.py`).

Every fit is deterministic: `lbfgs` has no random state, and rows arrive sorted.
"""

# polars's and scikit-learn's signatures reference types pyright cannot resolve; scoped here.
# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false
# pyright: reportUnknownArgumentType=false, reportMissingTypeStubs=false

from __future__ import annotations

from dataclasses import dataclass, field, replace

import numpy as np
import numpy.typing as npt
import polars as pl
from sklearn.linear_model import LogisticRegression

from distress_radar.models.dataset import TARGET
from distress_radar.models.splits import Fold, LogisticConfig, events

Array = npt.NDArray[np.float64]

# What every model's predictions carry: the row, its label, and the model's view of it.
PREDICTION_SCHEMA: dict[str, pl.DataType | type[pl.DataType]] = {
    "krs": pl.String,
    "as_of_date": pl.Date,
    TARGET: pl.Boolean,
    "outcome_class": pl.String,
    "event_date": pl.Date,
    "event_known_from": pl.Date,
    "trigger_event_type": pl.String,
    "probability": pl.Float64,
    "score": pl.Float64,  # a classical model's score; null for the regression
    "zone": pl.String,  # its published zone; null when there is none
}
LABEL_COLUMNS = [c for c in PREDICTION_SCHEMA if c not in ("probability", "score", "zone")]


@dataclass(frozen=True)
class RankTransform:
    """Each column mapped to its mid-rank among the training values, scaled to (0, 1)."""

    sorted_columns: tuple[Array, ...]

    @classmethod
    def fit(cls, x: Array) -> RankTransform:
        if x.ndim != 2 or x.shape[0] == 0:
            raise ValueError("a rank transform is fitted on a non-empty 2-D array")
        return cls(tuple(np.sort(x[:, j]) for j in range(x.shape[1])))

    def transform(self, x: Array) -> Array:
        columns: list[Array] = []
        for j, values in enumerate(self.sorted_columns):
            below = np.searchsorted(values, x[:, j], side="left")
            not_above = np.searchsorted(values, x[:, j], side="right")
            columns.append((below + not_above) / (2.0 * values.size))
        return np.column_stack(columns)


@dataclass(frozen=True)
class FitSummary:
    """What a model saw in one fold: the rows it used, the rows it could not, and its fit."""

    model: str
    horizon_months: int
    test_year: int
    train_rows: int  # the rows fitted on
    train_events: int
    train_rows_left_out: int  # missing an input: counted, not imputed
    test_rows: int  # the rows scored
    test_events: int
    test_rows_left_out: int
    fitted: bool
    note: str = ""  # why not fitted
    coefficients: tuple[tuple[str, float], ...] = field(default=())
    intercept: float | None = None


@dataclass(frozen=True)
class FoldPredictions:
    summary: FitSummary
    predictions: pl.DataFrame  # PREDICTION_SCHEMA, one row per scored test row


def fit_rank_logistic(
    x: Array, y: npt.NDArray[np.bool_], config: LogisticConfig
) -> tuple[RankTransform, LogisticRegression]:
    """The configured penalised fit on fold-fitted ranks."""
    ranks = RankTransform.fit(x)
    # scikit-learn 1.8 deprecated `penalty`: an L2 penalty is `l1_ratio=0` with strength 1/C.
    l1_ratio = {"l2": 0.0}[config.penalty]
    model = LogisticRegression(
        l1_ratio=l1_ratio, C=config.c, solver="lbfgs", class_weight=None, max_iter=1000
    )
    model.fit(ranks.transform(x), y)
    return ranks, model


def parameters(model: LogisticRegression) -> tuple[list[float], float]:
    """A fitted model's coefficients and intercept, as plain floats."""
    coefficients = np.asarray(model.coef_, dtype=np.float64).ravel()
    intercept = np.asarray(model.intercept_, dtype=np.float64).ravel()
    return [float(c) for c in coefficients], float(intercept[0])


def distress_probability(model: LogisticRegression, ranks: RankTransform, x: Array) -> Array:
    """P(distress) for rows `x`, through the training fold's rank transform."""
    return np.asarray(model.predict_proba(ranks.transform(x)), dtype=np.float64)[:, 1]


def one_class_note(y: npt.NDArray[np.bool_]) -> str:
    """Why a fold cannot be fitted, or "" when it can."""
    if y.size == 0:
        return "no complete training rows"
    if bool(y.all()) or not bool(y.any()):
        return "the training rows hold one class"
    return ""


def empty_predictions() -> pl.DataFrame:
    return pl.DataFrame(schema=PREDICTION_SCHEMA)


def _matrix(rows: pl.DataFrame, columns: list[str]) -> Array:
    """The inputs as floats: a boolean flag is 0 or 1."""
    return rows.select(pl.col(columns).cast(pl.Float64)).to_numpy()


def fit_predict_logistic(fold: Fold, config: LogisticConfig) -> FoldPredictions:
    columns = list(config.features)
    train = fold.train.drop_nulls(columns)
    test = fold.test.drop_nulls(columns)
    y = train.get_column(TARGET).to_numpy()
    note = one_class_note(y)
    summary = FitSummary(
        model="logistic_regression",
        horizon_months=fold.horizon_months,
        test_year=fold.test_year,
        train_rows=train.height,
        train_events=events(train).height,
        train_rows_left_out=fold.train.height - train.height,
        test_rows=test.height,
        test_events=events(test).height,
        test_rows_left_out=fold.test.height - test.height,
        fitted=not note,
        note=note,
    )
    if note:
        return FoldPredictions(summary, empty_predictions())
    ranks, model = fit_rank_logistic(_matrix(train, columns), y, config)
    coefficients, intercept = parameters(model)
    summary = replace(
        summary,
        coefficients=tuple(zip(columns, coefficients, strict=True)),
        intercept=intercept,
    )
    if test.is_empty():
        return FoldPredictions(summary, empty_predictions())
    probability = distress_probability(model, ranks, _matrix(test, columns))
    return FoldPredictions(summary, with_probability(test, probability))


def with_probability(
    rows: pl.DataFrame,
    probability: Array,
    score: pl.Series | None = None,
    zone: pl.Series | None = None,
) -> pl.DataFrame:
    """Scored rows in PREDICTION_SCHEMA."""
    return rows.select(LABEL_COLUMNS).with_columns(
        pl.Series("probability", probability, dtype=pl.Float64),
        score if score is not None else pl.lit(None, dtype=pl.Float64).alias("score"),
        zone if zone is not None else pl.lit(None, dtype=pl.String).alias("zone"),
    )
