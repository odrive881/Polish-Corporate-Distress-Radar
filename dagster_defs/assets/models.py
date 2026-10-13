"""Dagster asset for the `models` group (plan 0012 step G): the out-of-time backtest.

A thin wrapper: the run is `distress_radar.models.backtest`, the report
`distress_radar.models.report`, the tracking `distress_radar.models.registry`. It runs only in
its own `backtest` job, by hand, never on a schedule: a backtest is a decision, not a refresh.
"""

from pathlib import Path
from tempfile import TemporaryDirectory
from typing import TYPE_CHECKING, cast

import dagster as dg
import polars as pl

from dagster_defs.assets.features import feature_store
from dagster_defs.assets.labels import outcome_labels
from distress_radar.acquisition.document_retrieval import load_document_types
from distress_radar.features.config import load_feature_set
from distress_radar.models.backtest import POOLED, run_backtest, write_predictions
from distress_radar.models.baselines import load_classical_model
from distress_radar.models.dataset import load_feature_store, load_label_set
from distress_radar.models.explain import write_explanations
from distress_radar.models.population import Population, read_lists, read_population
from distress_radar.models.registry import (
    code_commit,
    data_snapshot,
    identifiers,
    log_backtest,
    register_models,
)
from distress_radar.models.report import render_report, write_report
from distress_radar.models.splits import label_timing, load_backtest_config
from distress_radar.settings import Settings

if TYPE_CHECKING:
    from dagster_defs.definitions import PostgresResource

# The repository the code commit is read from: the one this module is part of.
REPO_ROOT = Path(__file__).resolve().parents[2]
CLASSICAL_MODELS = ("altman_z2_2000", "poznan_2004")


@dg.asset(
    group_name="models",
    deps=[feature_store, outcome_labels],
    required_resource_keys={"postgres"},
)
def backtest(context: dg.AssetExecutionContext) -> dg.MaterializeResult:
    """I — the out-of-time backtest: the classical models, the logistic regression and, for a config
    with generation 3 (plan 0015), LightGBM, the survival models and the champion rule.

    Inputs: `feature_store` and the frozen label set pinned in
    `config/models/<BACKTEST_VERSION>.yaml` (by hash, not the latest `outcome_labels`), under
    `WAREHOUSE_DIR`; the label version's timing rules (`config/labels/`, the taxonomy's KRZ launch),
    which decide the training labels settled by each test year; the classical models'
    coefficients in `config/models/`; the code commit of a clean working tree, refused otherwise;
    Postgres `universe_candidates`, whose lists every event count is broken down by; for a config
    with a `population` rule (plan 0015 decision 10), also `rdf_listed_entities` and
    `filing_index`, which decide the entities modelled, refused unless they give the pinned hash.
    Outputs: the Markdown report at `WAREHOUSE_DIR/reports/backtest/<BACKTEST_VERSION>.md`,
    replaced whole, the same bytes for the same inputs; one MLflow run per model, horizon and
    run (`main`, `no_regime`, `entity_disjoint`) in `MLFLOW_TRACKING_URI`, each with the four
    identifiers; every scored test row at `WAREHOUSE_DIR/model_predictions/` (local only, replaced
    whole, one file per test year; plan 0016 decision 3); for a config with generation 3, each
    scored row's SHAP values at `WAREHOUSE_DIR/model_explanations/` (local only, replaced whole,
    one file per test year; plan 0015 decision 5), a child run per fold Optuna tuned, and, with a champion rule, a registered
    model version per horizon of the incumbent (alias `incumbent`) and of a promoted challenger
    (alias `champion`) in MLflow's registry (plan 0015 step G).
    Partition scheme: none (unpartitioned).
    """
    settings = Settings()
    commit = code_commit(REPO_ROOT)  # before any work: a dirty tree is refused up front
    config = load_backtest_config(settings.backtest_version)
    feature_set = load_feature_set(config.feature_set_version).feature_set
    models = [load_classical_model(m, feature_set) for m in CLASSICAL_MODELS]
    labels = load_label_set(settings.warehouse_dir, config.label_set_hash)
    population: Population | None = None
    postgres = cast("PostgresResource", context.resources.postgres)
    with postgres.connect() as conn:
        lists = read_lists(conn)
        if config.population is not None:
            population = read_population(
                conn,
                labels.get_column("krs").unique().to_list(),
                config.population,
                load_document_types().download_codes,
            )
    result = run_backtest(
        load_feature_store(settings.warehouse_dir, config.feature_set_version),
        labels,
        config,
        label_timing(labels),
        models,
        population,
        lists,
    )
    snapshot, files = data_snapshot(settings.warehouse_dir)
    # Per-row predictions and SHAP values name companies: local only, never the report or MLflow.
    write_predictions(result, settings.warehouse_dir)
    if result.explanations is not None:
        write_explanations(result.explanations, settings.warehouse_dir)
    report = write_report(render_report(result, commit), settings.warehouse_dir, config.backtest)
    ids = identifiers(result, commit, snapshot)
    with TemporaryDirectory() as workdir:
        run_ids = log_backtest(
            result,
            ids,
            report,
            files,
            settings.mlflow_tracking_uri,
            settings.mlflow_artifact_dir,
            Path(workdir),
        )
        registered = register_models(
            result, ids, run_ids, settings.mlflow_tracking_uri, Path(workdir)
        )
    scored = result.cells.filter(pl.col("evaluable"))
    context.log.info(
        f"backtest {config.backtest}: {result.cells.height} cells, {scored.height} scored, "
        f"{len(run_ids)} MLflow runs, {len(registered)} model versions registered; "
        "seed output, not evidence"
    )
    return dg.MaterializeResult(
        metadata={
            **ids.params(),
            "backtest": config.backtest,
            "report": dg.MetadataValue.path(str(report)),
            "mlflow_runs": len(run_ids),
            "cells": result.cells.height,
            "scored_cells": scored.height,
            "scored": dg.MetadataValue.md(
                "\n".join(
                    f"- {r['model']} {r['horizon_months']}m {r['run']} "
                    f"{'pooled' if r['test_year'] == POOLED else r['test_year']}"
                    for r in scored.iter_rows(named=True)
                )
                or "none: every cell is below min_events"
            ),
            "events_by_horizon": {str(h): n for h, n in result.events.items()},
            "champion": result.champion.champion if result.champion is not None else "no rule",
            "registered": dg.MetadataValue.md(
                "\n".join(
                    f"- {r.name} v{r.version} @{r.alias}: {r.model}, fold {r.test_year}"
                    for r in registered
                )
                or "none"
            ),
        }
    )


models_assets = [backtest]
