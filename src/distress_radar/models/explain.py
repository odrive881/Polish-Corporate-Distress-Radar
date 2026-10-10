"""Explanations of generation 3 (plan 0015 step E, owner decision 5).

`shap.TreeExplainer` on each fold's fitted LightGBM model, over that fold's test rows: each feature's
contribution to the row's raw score (log-odds, before the Platt map, which is monotone and so changes
no ranking). Two outputs, kept apart:

- **Global importance**, per run, horizon and test year: each feature's mean absolute SHAP value over the
  fold's test rows. It names no company, so the report may show it (top 15, the rest summed).
- **Per-row values**, which explain a named company's score: `WAREHOUSE_DIR/model_explanations/` only
  (local, gitignored, like every warehouse output), for the internal app later; never in the report,
  MLflow's artifacts or anything published (AGENT_SPEC §6K: company-level output is local only).
"""

# polars's and shap's signatures reference types pyright cannot resolve; scoped to this module.
# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false
# pyright: reportUnknownArgumentType=false, reportMissingTypeStubs=false

from __future__ import annotations

import warnings
from pathlib import Path

import lightgbm as lgb
import numpy as np
import numpy.typing as npt
import polars as pl
import shap

from distress_radar.warehouse import write_dataset

Array = npt.NDArray[np.float64]

DATASET = "model_explanations"
BASE = "base_value"  # the explainer's expected raw score; a row's values sum to its score less it
TOP = 15
# The per-row frame: these keys, `BASE`, then one Float64 column per feature in the model's order.
KEYS: dict[str, pl.DataType | type[pl.DataType]] = {
    "backtest": pl.String,
    "run": pl.String,
    "horizon_months": pl.Int32,
    "model": pl.String,
    "test_year": pl.Int32,
    "krs": pl.String,
    "as_of_date": pl.Date,
}
IMPORTANCE_SCHEMA: dict[str, pl.DataType | type[pl.DataType]] = {
    "run": pl.String,
    "horizon_months": pl.Int32,
    "model": pl.String,
    "test_year": pl.Int32,
    "feature": pl.String,
    "mean_abs_shap": pl.Float64,
    "rows": pl.Int64,
}


def shap_values(booster: lgb.Booster, x: Array) -> tuple[Array, float]:
    """Each row's per-feature contribution to its raw score, and the explainer's base value."""
    explainer = shap.TreeExplainer(booster)
    with warnings.catch_warnings():
        # shap notes that a binary LightGBM model's output shape changed between its versions;
        # the shape is checked here instead.
        warnings.filterwarnings(
            "ignore", message="LightGBM binary classifier", category=UserWarning
        )
        values = np.asarray(explainer.shap_values(x), dtype=np.float64)
    if values.shape != x.shape:
        raise ValueError(f"SHAP values of shape {values.shape} for inputs of shape {x.shape}")
    return values, float(np.asarray(explainer.expected_value, dtype=np.float64).ravel()[0])


def explanation_frame(
    rows: pl.DataFrame, columns: list[str], values: Array, base: float
) -> pl.DataFrame:
    """The scored rows' keys with their base value and per-feature contributions."""
    return rows.select("krs", "as_of_date").with_columns(
        pl.lit(base, dtype=pl.Float64).alias(BASE),
        *(pl.Series(name, values[:, j], dtype=pl.Float64) for j, name in enumerate(columns)),
    )


def importance(explanations: pl.DataFrame) -> pl.DataFrame:
    """Mean absolute SHAP per run, horizon, model, test year and feature (IMPORTANCE_SCHEMA),
    sorted by fold, then by importance (ties by name)."""
    keys = [k for k in KEYS if k not in ("backtest", "krs", "as_of_date")]
    features = [c for c in explanations.columns if c not in KEYS and c != BASE]
    if explanations.is_empty():
        return pl.DataFrame(schema=IMPORTANCE_SCHEMA)
    return (
        explanations.unpivot(index=keys, on=features, variable_name="feature", value_name="shap")
        .group_by(*keys, "feature")
        .agg(
            pl.col("shap").abs().mean().alias("mean_abs_shap"),
            pl.len().cast(pl.Int64).alias("rows"),
        )
        .sort(*keys, "mean_abs_shap", "feature", descending=[False] * len(keys) + [True, False])
        .select(list(IMPORTANCE_SCHEMA))
    )


def top_features(fold_importance: pl.DataFrame, top: int = TOP) -> pl.DataFrame:
    """One fold's table for the report: its `top` features, then the rest summed in one row."""
    ranked = fold_importance.sort("mean_abs_shap", "feature", descending=[True, False])
    head = ranked.head(top).select("feature", "mean_abs_shap")
    rest = ranked.slice(top)
    if rest.is_empty():
        return head
    return pl.concat(
        [
            head,
            pl.DataFrame(
                {
                    "feature": [f"{rest.height} other features"],
                    "mean_abs_shap": [float(rest.get_column("mean_abs_shap").sum())],
                }
            ),
        ]
    )


def write_explanations(explanations: pl.DataFrame, warehouse_dir: Path) -> list[Path]:
    """The per-row values to `WAREHOUSE_DIR/model_explanations/`, replaced whole, one file per test
    year; local only."""
    frame = explanations.sort("run", "horizon_months", "model", "test_year", "krs", "as_of_date")
    return write_dataset(frame, warehouse_dir, DATASET, "test_year")
