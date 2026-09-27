"""Metrics, entity bootstrap and reliability for the backtest (plan 0012 step E, owner decision 8).

Brier score and log loss judge the probabilities, AUC and top-decile precision the ranking; the
Brier score is never reported without the others (CLAUDE.md), nor AUC alone. Each metric is
written out here rather than taken from a library, so a hand-computed case pins it (the tests).

A cell (a model on one test year, or pooled over the years) is evaluable only when the model was
fitted and the distinct events it trained and was scored on both reach `min_events` (decisions 2
and 8); otherwise its metrics are None and the report prints `n/a (<k events)`.

Intervals resample entities, not rows: a company's month-ends move together, since one event
labels up to `horizon` of them. The draws come from a fixed seed, so a report is reproducible.
"""

# polars's signatures reference types pyright cannot resolve; scoped to this module.
# pyright: reportUnknownMemberType=false

from __future__ import annotations

import math
from collections.abc import Callable

import numpy as np
import numpy.typing as npt
import polars as pl

from distress_radar.models.dataset import TARGET
from distress_radar.models.splits import BootstrapConfig, events

Labels = npt.NDArray[np.bool_]
Probabilities = npt.NDArray[np.float64]
Metric = Callable[[Labels, Probabilities], float | None]

# Log loss clips probabilities away from 0 and 1, where it is infinite.
_EPSILON = 1e-15


def brier(y: Labels, p: Probabilities) -> float:
    return float(np.mean((p - y.astype(np.float64)) ** 2))


def log_loss(y: Labels, p: Probabilities) -> float:
    q = np.clip(p, _EPSILON, 1 - _EPSILON)
    return float(-np.mean(np.where(y, np.log(q), np.log(1 - q))))


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


def auc(y: Labels, p: Probabilities) -> float | None:
    """The Mann-Whitney AUC, ties counted half; None when only one class is present."""
    positives = int(y.sum())
    negatives = y.size - positives
    if positives == 0 or negatives == 0:
        return None
    rank_sum = float(_mid_ranks(p)[y].sum())
    return (rank_sum - positives * (positives + 1) / 2) / (positives * negatives)


def top_decile_precision(y: Labels, p: Probabilities) -> float | None:
    """The share of distress among the highest-scored tenth (rounded up; ties by row order)."""
    if y.size == 0:
        return None
    k = math.ceil(y.size / 10)
    top = np.argsort(-p, kind="stable")[:k]
    return float(y[top].mean())


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


def point_metrics(predictions: pl.DataFrame) -> dict[str, float | None]:
    y, p = _arrays(predictions)
    return {name: metric(y, p) for name, metric in METRICS.items()}


def bootstrap_intervals(
    predictions: pl.DataFrame, config: BootstrapConfig
) -> dict[str, tuple[float, float] | None]:
    """Percentile intervals over entity resamples; None where under half the draws define it."""
    y, p = _arrays(predictions)
    krs = predictions.get_column("krs").to_list()
    entities = sorted(set(krs))
    rows_of = {e: np.flatnonzero(np.array(krs) == e) for e in entities}
    rng = np.random.default_rng(config.seed)
    draws: dict[str, list[float]] = {name: [] for name in METRICS}
    for _ in range(config.replicates):
        picked = rng.integers(0, len(entities), size=len(entities))
        rows = np.concatenate([rows_of[entities[i]] for i in picked])
        for name, metric in METRICS.items():
            value = metric(y[rows], p[rows])
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


def reliability(predictions: pl.DataFrame, bins: int) -> pl.DataFrame:
    """Rows, mean predicted probability and observed distress rate per probability bin."""
    return (
        predictions.with_columns(
            (pl.col("probability") * bins).floor().clip(0, bins - 1).cast(pl.Int32).alias("bin")
        )
        .group_by("bin")
        .agg(
            pl.len().cast(pl.Int64).alias("rows"),
            pl.col(TARGET).sum().cast(pl.Int64).alias("distress_rows"),
            pl.col("probability").mean().alias("mean_predicted"),
            pl.col(TARGET).cast(pl.Float64).mean().alias("observed_rate"),
        )
        .with_columns(
            (pl.col("bin") / bins).alias("bin_low"), ((pl.col("bin") + 1) / bins).alias("bin_high")
        )
        .sort("bin")
        .select("bin_low", "bin_high", "rows", "distress_rows", "mean_predicted", "observed_rate")
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
