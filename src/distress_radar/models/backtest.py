"""The backtest run (plan 0012 steps E and G): every model on every fold, horizon and run.

Two runs, side by side (owner decision 9): `main` on every labelled row, and `no_regime`, which
leaves out the rows whose label window overlaps 2020-21 from both training and test. The flag is
a row filter only; no model reads it.

The result holds each model's fit summary and scored rows per fold, the metrics per test year and
pooled, the reliability tables, and the fold reports. Pooled cells gather a model's test rows from
the folds whose fitted model trained on at least `min_events` events; a fold trained on fewer is
not allowed to speak for the pool.
"""

# polars's signatures reference types pyright cannot resolve; scoped to this module.
# pyright: reportUnknownMemberType=false

from __future__ import annotations

from dataclasses import dataclass, replace

import polars as pl

from distress_radar.models.baselines import ClassicalModel, fit_predict_classical
from distress_radar.models.classical import FoldPredictions, fit_predict_logistic
from distress_radar.models.dataset import ModellingDataset, modelling_dataset
from distress_radar.models.evaluation import METRICS, cell, reliability
from distress_radar.models.population import Population
from distress_radar.models.splits import (
    BacktestConfig,
    LabelTiming,
    events,
    fold_report,
    purged_folds,
)

RUNS = ("main", "no_regime")
POOLED = 0  # the `test_year` of a pooled cell


@dataclass(frozen=True)
class BacktestResult:
    config: BacktestConfig
    timing: LabelTiming  # when a training label counts as settled (`splits.py`)
    feature_set_version: str
    feature_set_hash: str
    label_set_hash: str
    label_version: str
    entities: int  # entities in the label set
    distress_entities: int  # of which ever labelled distress
    events: dict[int, int]  # distinct events per horizon, main run
    cells: pl.DataFrame  # one row per run, horizon, model and test year (0 = pooled)
    reliability: pl.DataFrame  # pooled, per run, horizon and model
    folds: pl.DataFrame  # the fold reports, per run and horizon
    predictions: pl.DataFrame  # every scored test row
    # The population rule's outcome (plan 0015 decision 10); None when the config has no rule.
    population: Population | None = None
    excluded_distress_entities: int = 0  # excluded entities ever labelled distress


def _for_run(dataset: ModellingDataset, run: str) -> ModellingDataset:
    if run == "main":
        return dataset
    return replace(dataset, frame=dataset.frame.filter(~pl.col("regime_flag")))


def _fold_results(
    dataset: ModellingDataset,
    config: BacktestConfig,
    timing: LabelTiming,
    models: list[ClassicalModel],
) -> list[FoldPredictions]:
    out: list[FoldPredictions] = []
    for fold in purged_folds(dataset, config.test_years, timing):
        out.append(fit_predict_logistic(fold, config.logistic_regression))
        out.extend(
            fit_predict_classical(fold, model, config.logistic_regression) for model in models
        )
    return out


def run_backtest(
    features: pl.DataFrame,
    labels: pl.DataFrame,
    config: BacktestConfig,
    timing: LabelTiming,
    models: list[ClassicalModel],
    population: Population | None = None,
) -> BacktestResult:
    """Every model on every fold. With a `population` rule in the config, only its included
    entities are modelled; `population` must be the rule's outcome and match its pinned hash."""
    if (config.population is None) != (population is None):
        raise ValueError("a population is given exactly when the config names a rule")
    excluded_distress = 0
    if config.population is not None and population is not None:
        if population.entities_hash != config.population.entities_hash:
            raise ValueError(
                f"population {population.entities_hash} is not the pinned "
                f"{config.population.entities_hash}: the acquisition records have changed, "
                "so this is a new backtest version"
            )
        distress = pl.col("outcome_class").is_in(config.distress_classes)
        excluded_distress = (
            labels.filter(~pl.col("krs").is_in(list(population.included)) & distress)
            .get_column("krs")
            .n_unique()
        )
        labels = labels.filter(pl.col("krs").is_in(list(population.included)))
    cells: list[dict[str, object]] = []
    tables: list[pl.DataFrame] = []
    fold_tables: list[pl.DataFrame] = []
    scored: list[pl.DataFrame] = []
    totals: dict[int, int] = {}
    identity: ModellingDataset | None = None
    for horizon in config.horizons:
        base = modelling_dataset(features, labels, horizon, config.distress_classes)
        identity = base
        totals[horizon] = events(base.frame).height
        for run in RUNS:
            dataset = _for_run(base, run)
            key = {"run": run, "horizon_months": horizon}
            fold_tables.append(
                fold_report(
                    purged_folds(dataset, config.test_years, timing),
                    config.distress_classes,
                    config.min_events,
                ).select(pl.lit(run).alias("run"), pl.all())
            )
            results = _fold_results(dataset, config, timing, models)
            for result in results:
                s = result.summary
                cells.append(
                    {
                        **key,
                        "model": s.model,
                        "test_year": s.test_year,
                        "fitted": s.fitted,
                        "note": s.note,
                        "train_rows": s.train_rows,
                        "rows_left_out": s.train_rows_left_out + s.test_rows_left_out,
                        **cell(
                            result.predictions,
                            fitted=s.fitted,
                            train_events=s.train_events,
                            min_events=config.min_events,
                            bootstrap=config.bootstrap,
                        ),
                    }
                )
                scored.append(
                    result.predictions.select(
                        *(pl.lit(v).alias(k) for k, v in key.items()),
                        pl.lit(s.model).alias("model"),
                        pl.lit(s.test_year).alias("test_year"),
                        pl.all(),
                    )
                )
            for model in dict.fromkeys(r.summary.model for r in results):
                speaking = [
                    r
                    for r in results
                    if r.summary.model == model
                    and r.summary.fitted
                    and r.summary.train_events >= config.min_events
                ]
                pooled = pl.concat(
                    [r.predictions for r in speaking] or [results[0].predictions.clear()]
                )
                cells.append(
                    {
                        **key,
                        "model": model,
                        "test_year": POOLED,
                        "fitted": bool(speaking),
                        "note": ""
                        if speaking
                        else f"no fold trained on {config.min_events}+ events",
                        "train_rows": sum(r.summary.train_rows for r in speaking),
                        "rows_left_out": sum(
                            r.summary.train_rows_left_out + r.summary.test_rows_left_out
                            for r in speaking
                        ),
                        **cell(
                            pooled,
                            fitted=bool(speaking),
                            train_events=min((r.summary.train_events for r in speaking), default=0),
                            min_events=config.min_events,
                            bootstrap=config.bootstrap,
                        ),
                    }
                )
                if not pooled.is_empty():
                    tables.append(
                        reliability(pooled, config.reliability_bins).select(
                            *(pl.lit(v).alias(k) for k, v in key.items()),
                            pl.lit(model).alias("model"),
                            pl.all(),
                        )
                    )
    assert identity is not None  # horizons are non-empty
    label_versions = labels.get_column("label_version").unique().to_list()
    return BacktestResult(
        config=config,
        timing=timing,
        feature_set_version=identity.feature_set_version,
        feature_set_hash=identity.feature_set_hash,
        label_set_hash=identity.label_set_hash,
        label_version=str(label_versions[0]),
        entities=labels.get_column("krs").n_unique(),
        distress_entities=labels.filter(pl.col("outcome_class").is_in(config.distress_classes))
        .get_column("krs")
        .n_unique(),
        events=totals,
        cells=pl.DataFrame(cells, schema=_cell_schema()),
        reliability=pl.concat(tables) if tables else pl.DataFrame(),
        folds=pl.concat(fold_tables),
        predictions=pl.concat(scored),
        population=population,
        excluded_distress_entities=excluded_distress,
    )


def _cell_schema() -> dict[str, pl.DataType | type[pl.DataType]]:
    schema: dict[str, pl.DataType | type[pl.DataType]] = {
        "run": pl.String,
        "horizon_months": pl.Int32,
        "model": pl.String,
        "test_year": pl.Int32,
        "fitted": pl.Boolean,
        "note": pl.String,
        "train_rows": pl.Int64,
        "rows_left_out": pl.Int64,
        "scored_rows": pl.Int64,
        "test_events": pl.Int64,
        "train_events": pl.Int64,
        "evaluable": pl.Boolean,
    }
    for name in METRICS:
        schema.update({name: pl.Float64, f"{name}_low": pl.Float64, f"{name}_high": pl.Float64})
    return schema
