"""L — realised-outcome calibration of the stored scores (plan 0018 step G, decision 7).

Each stored score is joined to its outcome in the latest frozen label set of its version's label
version, but only once its whole cohort is settled: its window has closed and, under the label
version's own rules, an `alive` label for it would be known (the lag allowance after KRZ's launch,
plan 0009). A distress event is often known earlier, mid-window; counting those rows before the
survivors beside them would fill the sample with failures, so a row waits for its window either way.

Per horizon and model version: rows, distinct events, Brier score, base rate and a reliability
table, on `models/evaluation.py`'s definitions, as the backtest computes them. Below `min_events`
distinct events (the backtest's own floor) a cell is "not evaluable", with the date the first
stored score's cohort settles. Scores began at 2026-09-30, so nothing settles before late 2027.
"""

# polars's signatures reference types pyright cannot resolve; scoped to this module.
# pyright: reportUnknownMemberType=false

from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import date
from pathlib import Path
from typing import Any

import polars as pl

from distress_radar.models.dataset import TARGET
from distress_radar.models.evaluation import brier, reliability
from distress_radar.models.splits import LabelTiming, window_end

CALIBRATION_DIR = Path("reports") / "calibration"


def settles_on(horizon_months: int, timing: LabelTiming) -> pl.Expr:
    """The day a row's whole cohort is settled: its window's close, plus the `alive` lag for a
    window closing on or after KRZ's launch."""
    closes = window_end(horizon_months)
    lagged = closes.dt.offset_by(f"{timing.alive_lag_months}mo").dt.month_end()
    if not timing.alive_lag_months:
        return closes
    return pl.when(closes >= timing.krz_launch).then(lagged).otherwise(closes)


def realised(
    scores: pl.DataFrame,
    labels: pl.DataFrame,
    *,
    distress_classes: Sequence[str],
    timing: LabelTiming,
    on: date,
    min_events: int,
    bins: int,
) -> list[dict[str, Any]]:
    """One cell per horizon and model version of `scores` (krs, as_of_date, horizon_months,
    probability, model_name, model_version; rows not scored are left out)."""
    scored = scores.filter(pl.col("probability").is_not_null())
    cells: list[dict[str, Any]] = []
    keys = scored.select("horizon_months", "model_name", "model_version").unique().sort(pl.all())
    for horizon, name, version in keys.iter_rows():
        own = scored.filter(
            (pl.col("horizon_months") == horizon)
            & (pl.col("model_name") == name)
            & (pl.col("model_version") == version)
        ).with_columns(settles_on(horizon, timing).alias("settles_on"))
        settled = own.filter(pl.col("settles_on") <= on)
        joined = settled.join(
            labels.filter(pl.col("horizon_months") == horizon).select(
                "krs", "as_of_date", "outcome_class", "censored"
            ),
            on=["krs", "as_of_date"],
            how="inner",
        ).filter(~pl.col("censored"))
        joined = joined.with_columns(pl.col("outcome_class").is_in(distress_classes).alias(TARGET))
        events = joined.filter(pl.col(TARGET)).get_column("krs").n_unique()
        cell: dict[str, Any] = {
            "horizon_months": horizon,
            "model": f"{name} v{version}",
            "scores": own.height,
            "settled_rows": joined.height,
            "events": events,
            "first_settles_on": own.get_column("settles_on").min().isoformat(),  # type: ignore[union-attr]
        }
        if events < min_events:
            cell["evaluable"] = False
            cell["reason"] = (
                f"{events} distinct events among settled scores, fewer than {min_events}; "
                f"the first stored score's cohort settles on {cell['first_settles_on']}"
            )
        else:
            y = joined.get_column(TARGET).to_numpy()
            p = joined.get_column("probability").to_numpy()
            cell["evaluable"] = True
            cell["brier"] = round(brier(y, p), 6)
            cell["base_rate"] = round(float(y.mean()), 6)
            cell["mean_predicted"] = round(float(p.mean()), 6)
            cell["reliability"] = reliability(joined, bins).to_dicts()
        cells.append(cell)
    return cells


def write_realised(cells: Sequence[dict[str, Any]], on: date, warehouse_dir: Path) -> Path:
    """`WAREHOUSE_DIR/reports/calibration/realised_<on>.json`: the same inputs on the same day
    give the same bytes."""
    directory = warehouse_dir / CALIBRATION_DIR
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"realised_{on.isoformat()}.json"
    body = {"on": on.isoformat(), "cells": list(cells)}
    path.write_text(
        json.dumps(body, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8"
    )
    return path
