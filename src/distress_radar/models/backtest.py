"""The backtest run (plan 0012 steps E and G): every model on every fold, horizon and run.

Three runs, side by side: `main` on every labelled row; `no_regime` (plan 0012 owner decision 9),
which leaves out the rows whose label window overlaps 2020-21 from both training and test (the flag
is a row filter only; no model reads it); and `entity_disjoint` (plan 0015 owner decision 6),
`main`'s fitted models scored only on the test rows of entities no model of the fold has seen in
training, which shows how much of a result is recognising a company rather than reading its state.

With `weights` (plan 0019 step D, a config with a `weights` section), only one draw's entities are
modelled, every fit is weighted by its rows' 1/π, and every cell is weighted, its intervals
resampling entities within each draw stratum. Beside those headline cells, each pooled cell is also
given for the random group alone (self-weighting, the addendum's check) and unweighted (the
sample's, not the segment's), in `basis_cells`. The survival models' time-dependent metrics stay
unweighted.

With `lists` (the universe lists each entity came from), every event count is also given per list,
since each list is purposive. With a `champion` section, the rule of `champion.py` judges generation
3 against the regression on the pooled rows; with its `weighted` flag (plan 0019 step E), on the
rows' weights and draw strata, so its verdict is final rather than provisional.

The result holds each model's fit summary and scored rows per fold, the metrics per test year and
pooled, the reliability tables, and the fold reports. The scored rows, which name companies, are
also written locally (`write_predictions`, plan 0016 decision 3): each past month-end's out-of-time
prediction, from the fold whose test year holds it, for the explorer's score history. Pooled cells gather a model's test rows from
the folds whose fitted model trained on at least `min_events` events; a fold trained on fewer is
not allowed to speak for the pool.
"""

# polars's signatures reference types pyright cannot resolve; scoped to this module.
# pyright: reportUnknownMemberType=false

from __future__ import annotations

from dataclasses import dataclass, field, replace
from pathlib import Path

import polars as pl

from distress_radar.models.baselines import ClassicalModel, fit_predict_classical
from distress_radar.models.champion import ChampionVerdict, decide
from distress_radar.models.classical import (
    FittedModel,
    FoldPredictions,
    empty_predictions,
    fit_predict_logistic,
)
from distress_radar.models.dataset import WEIGHT, ModellingDataset, modelling_dataset
from distress_radar.models.evaluation import METRICS, STRATUM, cell, reliability
from distress_radar.models.explain import KEYS, importance
from distress_radar.models.gbm import fit_predict_gbm
from distress_radar.models.population import Population
from distress_radar.models.splits import (
    BacktestConfig,
    Fold,
    LabelTiming,
    events,
    fold_report,
    purged_folds,
)
from distress_radar.models.survival import (
    SURVIVAL_METRICS_SCHEMA,
    SurvivalFit,
    SurvivalRecords,
    fit_survival,
    fold_predictions,
    survival_records,
)
from distress_radar.models.weights import DrawWeights, nonresponse_adjusted
from distress_radar.warehouse import write_dataset

RUNS = ("main", "no_regime", "entity_disjoint")
# A weighted backtest's pooled cells beside the headline: the random group alone, and unweighted.
BASES = ("a_alone", "unweighted")
POOLED = 0  # the `test_year` of a pooled cell
PREDICTIONS = "model_predictions"  # the scored test rows' dataset: local only


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
    # The survival models' time-dependent metrics per run and test year (plan 0015 decision 4);
    # empty when the config has no survival section.
    survival: pl.DataFrame | None = None
    # Generation 3's SHAP values (plan 0015 decision 5): per scored row, local only (never the
    # report or MLflow), and the global mean absolute value per fold and feature, which may be shown.
    # None when the config has no `lightgbm` section.
    explanations: pl.DataFrame | None = None
    importance: pl.DataFrame | None = None
    # Entities per universe list among the labelled ones, and distinct events per horizon and list
    # (main run); empty when no lists are given.
    lists: dict[str, int] = field(default_factory=dict[str, int])
    events_by_list: dict[int, dict[str, int]] = field(default_factory=dict[int, dict[str, int]])
    # Generation 3 per fold and run: the inner year, its events, tuning and calibration (GBM_FOLDS).
    gbm_folds: pl.DataFrame | None = None
    champion: ChampionVerdict | None = None
    # Optuna's trials per run, horizon and tuned fold (TRIALS), for the registry's child runs.
    trials: pl.DataFrame | None = None
    # Per horizon and model, the `main` run's latest fold allowed to speak for the pool: its test
    # year and fitted model, the version the registry registers (plan 0015 step G).
    latest: dict[tuple[int, str], tuple[int, FittedModel]] = field(
        default_factory=dict[tuple[int, str], tuple[int, FittedModel]]
    )
    # A weighted backtest's draw (plan 0019 step D), and its pooled cells on the other bases
    # (`BASES`), one row per basis, run, horizon and model; None when unweighted.
    weights: DrawWeights | None = None
    basis_cells: pl.DataFrame | None = None


def _for_run(dataset: ModellingDataset, run: str) -> ModellingDataset:
    if run == "main":
        return dataset
    return replace(dataset, frame=dataset.frame.filter(~pl.col("regime_flag")))


GBM_FOLDS: dict[str, pl.DataType | type[pl.DataType]] = {
    "run": pl.String,
    "horizon_months": pl.Int32,
    "test_year": pl.Int32,
    "inner_year": pl.Int32,
    "inner_events": pl.Int64,
    "tuning": pl.String,
    "calibration": pl.String,
    "hyperparameters": pl.String,
}

TRIALS: dict[str, pl.DataType | type[pl.DataType]] = {
    "run": pl.String,
    "horizon_months": pl.Int32,
    "test_year": pl.Int32,
    "trial": pl.Int32,
    "parameter": pl.String,
    "value": pl.Float64,
    "inner_brier": pl.Float64,
}


def _fold_results(
    folds: list[Fold],
    config: BacktestConfig,
    timing: LabelTiming,
    models: list[ClassicalModel],
    survival: dict[int, SurvivalFit],
) -> list[FoldPredictions]:
    out: list[FoldPredictions] = []
    for fold in folds:
        out.append(fit_predict_logistic(fold, config.logistic_regression))
        if config.lightgbm is not None:
            out.extend(fit_predict_gbm(fold, config, timing))
        if fold.test_year in survival:
            out.extend(fold_predictions(survival[fold.test_year], fold))
        out.extend(
            fit_predict_classical(fold, model, config.logistic_regression) for model in models
        )
    return out


def _survival_fits(
    records: SurvivalRecords | None, config: BacktestConfig, timing: LabelTiming, run: str
) -> dict[int, SurvivalFit]:
    """The survival models of a run, one per test year, serving every horizon. `no_regime` leaves
    out the records whose `max_months` window overlaps the regime window."""
    if records is None:
        return {}
    if run != "main":
        records = replace(records, frame=records.frame.filter(~pl.col("regime_flag")))
    return {year: fit_survival(records, year, config, timing) for year in config.test_years}


def _entity_disjoint(
    folds: list[Fold], results: list[FoldPredictions], survival: dict[int, SurvivalFit]
) -> tuple[list[Fold], list[FoldPredictions]]:
    """`main`'s folds and fitted models, scored only on the test rows of entities no model of the
    fold trained on: not in the binary training rows, nor among the survival models' records."""
    seen: dict[int, set[str]] = {
        f.test_year: {str(k) for k in f.train.get_column("krs").to_list()}
        | (survival[f.test_year].train_entities if f.test_year in survival else frozenset())
        for f in folds
    }
    unseen = {year: ~pl.col("krs").is_in(sorted(krs)) for year, krs in seen.items()}
    kept = [replace(f, test=f.test.filter(unseen[f.test_year])) for f in folds]
    tests = {f.test_year: f.test for f in kept}
    out: list[FoldPredictions] = []
    for r in results:
        s = r.summary
        test = tests[s.test_year]
        scored = r.predictions.filter(unseen[s.test_year]) if s.fitted else empty_predictions()
        summary = replace(
            s,
            test_rows=scored.height if s.fitted else test.height,
            test_events=events(test).height,
            test_rows_left_out=test.height - scored.height if s.fitted else 0,
        )
        out.append(FoldPredictions(summary, scored))
    return kept, out


def run_backtest(
    features: pl.DataFrame,
    labels: pl.DataFrame,
    config: BacktestConfig,
    timing: LabelTiming,
    models: list[ClassicalModel],
    population: Population | None = None,
    lists: dict[str, frozenset[str]] | None = None,
    weights: DrawWeights | None = None,
) -> BacktestResult:
    """Every model on every fold. With a `population` rule in the config, only its included
    entities are modelled; `population` must be the rule's outcome and match its pinned hash.
    With a `weights` section, `weights` is the pinned draw's, and the population must be drawn
    from it."""
    if (config.population is None) != (population is None):
        raise ValueError("a population is given exactly when the config names a rule")
    if (config.weights is None) != (weights is None):
        raise ValueError("weights are given exactly when the config names a draw")
    if config.weights is not None and weights is not None:
        if weights.draw_spec_sha256 != config.weights.draw_spec_sha256:
            raise ValueError("the weights are not the pinned draw's")
        if population is None or not population.drawn:
            raise ValueError("a weighted backtest's population is a draw's")
        if not population.included <= weights.members:
            raise ValueError("the population holds entities the draw did not draw")
        if config.weights.nonresponse == "by_group":
            weights = nonresponse_adjusted(weights, population)
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
    if weights is not None:
        labels = labels.join(weights.frame.select("krs", WEIGHT), on="krs", how="left")

    def weighted(rows: pl.DataFrame) -> pl.DataFrame:
        """Rows with their weight and stratum, for a weighted cell; as they are otherwise."""
        if weights is None:
            return rows
        return rows.join(weights.frame.select("krs", WEIGHT, STRATUM), on="krs", how="left")

    random_group = sorted(weights.random_group) if weights is not None else []
    basis_cells: list[dict[str, object]] = []
    records = (
        survival_records(features, labels, config.survival.max_months, config.distress_classes)
        if config.survival is not None
        else None
    )
    survival = {run: _survival_fits(records, config, timing, run) for run in RUNS[:2]}
    labelled = set(labels.get_column("krs").unique().to_list())
    members = {name: krs & labelled for name, krs in (lists or {}).items()}
    events_by_list: dict[int, dict[str, int]] = {}
    gbm_folds: list[dict[str, object]] = []
    trials: list[dict[str, object]] = []
    latest: dict[tuple[int, str], tuple[int, FittedModel]] = {}
    cells: list[dict[str, object]] = []
    tables: list[pl.DataFrame] = []
    fold_tables: list[pl.DataFrame] = []
    scored: list[pl.DataFrame] = []
    explained: list[pl.DataFrame] = []
    totals: dict[int, int] = {}
    identity: ModellingDataset | None = None
    for horizon in config.horizons:
        base = modelling_dataset(features, labels, horizon, config.distress_classes)
        identity = base
        found = events(base.frame)
        totals[horizon] = found.height
        events_by_list[horizon] = {
            name: found.filter(pl.col("krs").is_in(sorted(krs))).height
            for name, krs in members.items()
        }
        main: tuple[list[Fold], list[FoldPredictions]] | None = None
        for run in RUNS:
            key = {"run": run, "horizon_months": horizon}
            folds: list[Fold]
            results: list[FoldPredictions]
            if run == "entity_disjoint":
                assert main is not None  # `main` runs first
                folds, results = _entity_disjoint(main[0], main[1], survival["main"])
            else:
                folds = purged_folds(_for_run(base, run), config.test_years, timing)
                results = _fold_results(folds, config, timing, models, survival[run])
                if run == "main":
                    main = (folds, results)
            fold_tables.append(
                fold_report(folds, config.distress_classes, config.min_events).select(
                    pl.lit(run).alias("run"), pl.all()
                )
            )
            for result in results:
                s = result.summary
                if s.tuning and run != "entity_disjoint" and s.model == "lightgbm":
                    gbm_folds.append(
                        {
                            **key,
                            "test_year": s.test_year,
                            "inner_year": s.inner_year,
                            "inner_events": s.inner_events,
                            "tuning": s.tuning,
                            "calibration": s.calibration,
                            "hyperparameters": ", ".join(
                                f"{k} {v:g}" for k, v in s.hyperparameters
                            ),
                        }
                    )
                    trials.extend(
                        dict[str, object](
                            **key,
                            test_year=s.test_year,
                            trial=t.number,
                            parameter=name,
                            value=value,
                            inner_brier=t.inner_brier,
                        )
                        for t in s.trials
                        for name, value in t.values
                    )
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
                            weighted(result.predictions),
                            fitted=s.fitted,
                            train_events=s.train_events,
                            min_events=config.min_events,
                            bootstrap=config.bootstrap,
                        ),
                    }
                )
                if result.explanation is not None:
                    explained.append(
                        result.explanation.select(
                            pl.lit(config.backtest).alias("backtest"),
                            *(pl.lit(v).alias(k) for k, v in key.items()),
                            pl.lit(s.model).alias("model"),
                            pl.lit(s.test_year).alias("test_year"),
                            pl.all(),
                        )
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
                registrable = [r for r in speaking if r.fitted_model is not None]
                if run == "main" and registrable:
                    last = max(registrable, key=lambda r: r.summary.test_year)
                    assert last.fitted_model is not None
                    latest[(horizon, model)] = (last.summary.test_year, last.fitted_model)
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
                            weighted(pooled),
                            fitted=bool(speaking),
                            train_events=min((r.summary.train_events for r in speaking), default=0),
                            min_events=config.min_events,
                            bootstrap=config.bootstrap,
                        ),
                    }
                )
                if weights is not None:
                    for basis, rows in (
                        ("a_alone", pooled.filter(pl.col("krs").is_in(random_group))),
                        ("unweighted", pooled),
                    ):
                        basis_cells.append(
                            {
                                "basis": basis,
                                **key,
                                "model": model,
                                "test_year": POOLED,
                                "fitted": bool(speaking),
                                "note": "",
                                "train_rows": sum(r.summary.train_rows for r in speaking),
                                "rows_left_out": 0,
                                **cell(
                                    rows,
                                    fitted=bool(speaking),
                                    train_events=min(
                                        (r.summary.train_events for r in speaking), default=0
                                    ),
                                    min_events=config.min_events,
                                    bootstrap=config.bootstrap,
                                ),
                            }
                        )
                if not pooled.is_empty():
                    tables.append(
                        reliability(
                            weighted(pooled),
                            config.reliability_bins,
                            WEIGHT if weights is not None else None,
                        ).select(
                            *(pl.lit(v).alias(k) for k, v in key.items()),
                            pl.lit(model).alias("model"),
                            pl.all(),
                        )
                    )
    assert identity is not None  # horizons are non-empty
    explanations = (
        pl.concat(explained).with_columns(pl.col(k).cast(v) for k, v in KEYS.items())
        if explained
        else None
    )
    label_versions = labels.get_column("label_version").unique().to_list()
    cell_frame = pl.DataFrame(cells, schema=_cell_schema())
    predictions = pl.concat(scored)
    verdict = (
        decide(
            weighted(predictions.drop(WEIGHT, strict=False))
            if config.champion.weighted
            else predictions,
            cell_frame.filter(
                (pl.col("test_year") != POOLED)
                & pl.col("fitted")
                & (pl.col("train_events") >= config.min_events)
            ).select("run", "horizon_months", "model", "test_year"),
            config.horizons,
            config.champion,
            config.bootstrap,
        )
        if config.champion is not None
        else None
    )
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
        cells=cell_frame,
        reliability=pl.concat(tables) if tables else pl.DataFrame(),
        folds=pl.concat(fold_tables),
        predictions=predictions,
        population=population,
        excluded_distress_entities=excluded_distress,
        survival=pl.DataFrame(
            [
                {"run": run, **row}
                for run, fits in survival.items()
                for fit in fits.values()
                for row in fit.metrics
            ],
            schema={"run": pl.String, **SURVIVAL_METRICS_SCHEMA},
        )
        if records is not None
        else None,
        explanations=explanations,
        importance=importance(explanations) if explanations is not None else None,
        lists={name: len(krs) for name, krs in members.items()},
        events_by_list=events_by_list if members else {},
        gbm_folds=pl.DataFrame(gbm_folds, schema=GBM_FOLDS)
        if config.lightgbm is not None
        else None,
        champion=verdict,
        trials=pl.DataFrame(trials, schema=TRIALS) if config.lightgbm is not None else None,
        latest=latest,
        weights=weights,
        basis_cells=pl.DataFrame(basis_cells, schema={"basis": pl.String, **_cell_schema()})
        if weights is not None
        else None,
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


def write_predictions(result: BacktestResult, warehouse_dir: Path) -> list[Path]:
    """Every scored test row, of every run, horizon, model and fold, to
    `WAREHOUSE_DIR/model_predictions/`, replaced whole, one file per test year; local only, like
    `model_explanations/` (company-level output: never the report, MLflow or anything published)."""
    frame = result.predictions.select(
        pl.lit(result.config.backtest).alias("backtest"), pl.all()
    ).sort("run", "horizon_months", "model", "test_year", "krs", "as_of_date")
    return write_dataset(frame, warehouse_dir, PREDICTIONS, "test_year")
