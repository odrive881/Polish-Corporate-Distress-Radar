"""Metrics, entity bootstrap and reliability for the backtest (plan 0012 step E, owner decision 8).

Brier score and log loss judge the probabilities, AUC and top-decile precision the ranking; the
Brier score is never reported without the others (CLAUDE.md), nor AUC alone. Each metric is
written out here rather than taken from a library, so a hand-computed case pins it (the tests).

A cell (a model on one test year, or pooled over the years) is evaluable only when the model was
fitted and the distinct events it trained and was scored on both reach `min_events` (decisions 2
and 8); otherwise its metrics are None and the report prints `n/a (<k events)`.

Intervals resample entities, not rows: a company's month-ends move together, since one event
labels up to `horizon` of them. The draws come from a fixed seed, so a report is reproducible.

Weights (plan 0019 step D, ADR 0014 addendum): every metric, the base rate and the reliability
table take an optional per-row weight, each row counting as that many rows (an integer weight gives
exactly the result of repeating the row). Without one they compute exactly as before. A weighted
cell reads its rows' `draw_weight` and resamples entities within each `draw_stratum`, each stratum
keeping its size (decision 6); an unweighted cell resamples all entities as before.
"""

# polars's signatures reference types pyright cannot resolve; scoped to this module.
# pyright: reportUnknownMemberType=false

from __future__ import annotations

import math
from collections.abc import Callable

import numpy as np
import numpy.typing as npt
import polars as pl

from distress_radar.models.dataset import TARGET, WEIGHT
from distress_radar.models.splits import BootstrapConfig, events

Labels = npt.NDArray[np.bool_]
Probabilities = npt.NDArray[np.float64]
Weights = npt.NDArray[np.float64]
Metric = Callable[[Labels, Probabilities, Weights | None], float | None]

# A weighted cell's bootstrap stratum per row (`weights.py`), constant per entity.
STRATUM = "draw_stratum"

# Log loss clips probabilities away from 0 and 1, where it is infinite.
_EPSILON = 1e-15


def _check_weight(y: Labels, weight: Weights) -> None:
    if weight.shape != y.shape:
        raise ValueError(f"{weight.shape[0]} weights for {y.shape[0]} rows")
    if not np.all(np.isfinite(weight)) or bool((weight < 0).any()):
        raise ValueError("weights must be finite and non-negative")


def brier(y: Labels, p: Probabilities, weight: Weights | None = None) -> float:
    errors = (p - y.astype(np.float64)) ** 2
    if weight is None:
        return float(np.mean(errors))
    _check_weight(y, weight)
    return float(np.average(errors, weights=weight))


def log_loss(y: Labels, p: Probabilities, weight: Weights | None = None) -> float:
    q = np.clip(p, _EPSILON, 1 - _EPSILON)
    losses = np.where(y, np.log(q), np.log(1 - q))
    if weight is None:
        return float(-np.mean(losses))
    _check_weight(y, weight)
    return float(-np.average(losses, weights=weight))


def base_rate(y: Labels, weight: Weights | None = None) -> float | None:
    """The (weighted) share of distress rows; None with no rows or no weight."""
    if weight is None:
        return float(y.mean()) if y.size else None
    _check_weight(y, weight)
    total = float(weight.sum())
    return float(weight[y].sum()) / total if total > 0 else None


def _mid_ranks(values: Probabilities) -> Probabilities:
    """1-based ranks, ties sharing the mean of their ranks."""
    order = np.argsort(values, kind="stable")
    ordered = values[order]
    ranks = np.empty(values.size, dtype=np.float64)
    start = 0
    while start < values.size:
        end = start
        while end + 1 < values.size and ordered[end + 1] == ordered[start]:
            end += 1
        ranks[order[start : end + 1]] = (start + end) / 2 + 1
        start = end + 1
    return ranks


def auc(y: Labels, p: Probabilities, weight: Weights | None = None) -> float | None:
    """The Mann-Whitney AUC, ties counted half; None when only one class is present (or, weighted,
    carries weight). Weighted, each positive-negative pair counts the product of their weights."""
    positives = int(y.sum())
    negatives = y.size - positives
    if positives == 0 or negatives == 0:
        return None
    if weight is None:
        rank_sum = float(_mid_ranks(p)[y].sum())
        return (rank_sum - positives * (positives + 1) / 2) / (positives * negatives)
    _check_weight(y, weight)
    w_pos, w_neg = float(weight[y].sum()), float(weight[~y].sum())
    if w_pos == 0 or w_neg == 0:
        return None
    values, group = np.unique(p, return_inverse=True)
    pos = np.bincount(group, weights=np.where(y, weight, 0.0), minlength=values.size)
    neg = np.bincount(group, weights=np.where(y, 0.0, weight), minlength=values.size)
    below = np.concatenate([[0.0], np.cumsum(neg)[:-1]])  # negative weight under each value
    return float((pos * (below + neg / 2)).sum()) / (w_pos * w_neg)


def top_decile_precision(
    y: Labels, p: Probabilities, weight: Weights | None = None
) -> float | None:
    """The share of distress among the highest-scored tenth (rounded up; ties by row order).

    Weighted (decision 9): the weighted share of distress among the highest-scored rows whose
    weights make up a tenth of the total, the row on the boundary counting with the part of its
    weight that fits. Where a tenth of the total is a whole number, an integer weight gives exactly
    the result of repeating the row, and equal weights the unweighted result; elsewhere the two
    differ by the unweighted form's rounding up to a whole row."""
    if y.size == 0:
        return None
    order = np.argsort(-p, kind="stable")
    if weight is None:
        k = math.ceil(y.size / 10)
        return float(y[order[:k]].mean())
    _check_weight(y, weight)
    target = float(weight.sum()) / 10
    if target <= 0:
        return None
    w = weight[order]
    before = np.concatenate([[0.0], np.cumsum(w)[:-1]])
    taken = np.clip(target - before, 0.0, w)
    return float((taken * y[order]).sum()) / target


METRICS: dict[str, Metric] = {
    "brier": brier,
    "log_loss": log_loss,
    "auc": auc,
    "top_decile_precision": top_decile_precision,
}


def _arrays(predictions: pl.DataFrame) -> tuple[Labels, Probabilities]:
    return (
        predictions.get_column(TARGET).to_numpy().astype(np.bool_),
        predictions.get_column("probability").to_numpy().astype(np.float64),
    )


def _weights(predictions: pl.DataFrame) -> Weights | None:
    if WEIGHT not in predictions.columns:
        return None
    return predictions.get_column(WEIGHT).to_numpy().astype(np.float64)


def point_metrics(predictions: pl.DataFrame) -> dict[str, float | None]:
    """Every metric; weighted when the rows carry `draw_weight`."""
    y, p = _arrays(predictions)
    w = _weights(predictions)
    return {name: metric(y, p, w) for name, metric in METRICS.items()}


def resample_entities(strata: list[list[str]], rng: np.random.Generator) -> list[str]:
    """One bootstrap replicate: each stratum's entities drawn with replacement, as many as it
    holds, stratum by stratum. One stratum is the plain entity bootstrap."""
    return [
        members[i] for members in strata for i in rng.integers(0, len(members), size=len(members))
    ]


def entity_strata(predictions: pl.DataFrame) -> list[list[str]]:
    """The entities to resample, by `draw_stratum` (in stratum order) when the rows carry one, else
    all in one stratum: the plain entity bootstrap."""
    entities = sorted(set(predictions.get_column("krs").to_list()))
    if STRATUM not in predictions.columns:
        return [entities]
    stratum_of = dict(predictions.select("krs", STRATUM).unique().iter_rows())  # one per entity
    return [[e for e in entities if stratum_of[e] == s] for s in sorted(set(stratum_of.values()))]


def bootstrap_intervals(
    predictions: pl.DataFrame, config: BootstrapConfig
) -> dict[str, tuple[float, float] | None]:
    """Percentile intervals over entity resamples; None where under half the draws define it.

    With `draw_weight` and `draw_stratum` columns, the metrics are weighted and each stratum's
    entities are resampled within it, in stratum order, each keeping its size (decision 6)."""
    y, p = _arrays(predictions)
    w = _weights(predictions)
    krs = predictions.get_column("krs").to_list()
    rows_of = {e: np.flatnonzero(np.array(krs) == e) for e in sorted(set(krs))}
    strata = entity_strata(predictions)
    rng = np.random.default_rng(config.seed)
    draws: dict[str, list[float]] = {name: [] for name in METRICS}
    for _ in range(config.replicates):
        rows = np.concatenate([rows_of[e] for e in resample_entities(strata, rng)])
        for name, metric in METRICS.items():
            value = metric(y[rows], p[rows], None if w is None else w[rows])
            if value is not None:
                draws[name].append(value)
    tail = (1 - config.level) / 2
    intervals: dict[str, tuple[float, float] | None] = {}
    for name, values in draws.items():
        if len(values) * 2 < config.replicates:
            intervals[name] = None
        else:
            low, high = np.quantile(np.array(values), [tail, 1 - tail])
            intervals[name] = (float(low), float(high))
    return intervals


def reliability(predictions: pl.DataFrame, bins: int, weight: str | None = None) -> pl.DataFrame:
    """Rows, mean predicted probability and observed distress rate per probability bin. With
    `weight` (a column of `predictions`), the mean and the rate are weighted, and the bin's total
    weight is added as `weight_sum`; the row counts stay counts."""
    if weight is None:
        means = [
            pl.col("probability").mean().alias("mean_predicted"),
            pl.col(TARGET).cast(pl.Float64).mean().alias("observed_rate"),
        ]
        extra: list[str] = []
    else:
        w = pl.col(weight)
        _check_weight(
            predictions.get_column(TARGET).to_numpy().astype(np.bool_),
            predictions.get_column(weight).to_numpy().astype(np.float64),
        )
        means = [
            ((pl.col("probability") * w).sum() / w.sum()).alias("mean_predicted"),
            ((pl.col(TARGET).cast(pl.Float64) * w).sum() / w.sum()).alias("observed_rate"),
            w.sum().cast(pl.Float64).alias("weight_sum"),
        ]
        extra = ["weight_sum"]
    return (
        predictions.with_columns(
            (pl.col("probability") * bins).floor().clip(0, bins - 1).cast(pl.Int32).alias("bin")
        )
        .group_by("bin")
        .agg(
            pl.len().cast(pl.Int64).alias("rows"),
            pl.col(TARGET).sum().cast(pl.Int64).alias("distress_rows"),
            *means,
        )
        .with_columns(
            (pl.col("bin") / bins).alias("bin_low"), ((pl.col("bin") + 1) / bins).alias("bin_high")
        )
        .sort("bin")
        .select(
            "bin_low",
            "bin_high",
            "rows",
            "distress_rows",
            "mean_predicted",
            "observed_rate",
            *extra,
        )
    )


def cell(
    predictions: pl.DataFrame,
    *,
    fitted: bool,
    train_events: int,
    min_events: int,
    bootstrap: BootstrapConfig,
) -> dict[str, object]:
    """A metrics row: event counts, the verdict, point metrics and intervals (None when n/a)."""
    test_events = events(predictions).height
    ok = fitted and train_events >= min_events and test_events >= min_events
    row: dict[str, object] = {
        "scored_rows": predictions.height,
        "test_events": test_events,
        "train_events": train_events,
        "evaluable": ok,
    }
    values = point_metrics(predictions) if ok else dict.fromkeys(METRICS)
    intervals = bootstrap_intervals(predictions, bootstrap) if ok else dict.fromkeys(METRICS)
    for name in METRICS:
        interval = intervals[name]
        row[name] = values[name]
        row[f"{name}_low"] = None if interval is None else interval[0]
        row[f"{name}_high"] = None if interval is None else interval[1]
    return row
