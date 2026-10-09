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
from distress_radar.models.backtest import POOLED, run_backtest
from distress_radar.models.baselines import load_classical_model
from distress_radar.models.dataset import load_feature_store, load_label_set
from distress_radar.models.population import Population, read_population
from distress_radar.models.registry import code_commit, data_snapshot, identifiers, log_backtest
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
    """I — the out-of-time backtest of the classical models and the logistic regression.

    Inputs: `feature_store` and the frozen label set pinned in
    `config/models/<BACKTEST_VERSION>.yaml` (by hash, not the latest `outcome_labels`), under
    `WAREHOUSE_DIR`; the label version's timing rules (`config/labels/`, the taxonomy's KRZ launch),
    which decide the training labels settled by each test year; the classical models'
    coefficients in `config/models/`; the code commit of a clean working tree, refused otherwise;
    for a config with a `population` rule (plan 0015 decision 10), Postgres
    `rdf_listed_entities`, `filing_index` and `universe_candidates`, which decide the entities
    modelled, refused unless they give the pinned hash.
    Outputs: the Markdown report at `WAREHOUSE_DIR/reports/backtest/<BACKTEST_VERSION>.md`,
    replaced whole, the same bytes for the same inputs; one MLflow run per model, horizon and
    run (`main`, `no_regime`) in `MLFLOW_TRACKING_URI`, each with the four identifiers.
    Partition scheme: none (unpartitioned).
    """
    settings = Settings()
    commit = code_commit(REPO_ROOT)  # before any work: a dirty tree is refused up front
    config = load_backtest_config(settings.backtest_version)
    feature_set = load_feature_set(config.feature_set_version).feature_set
    models = [load_classical_model(m, feature_set) for m in CLASSICAL_MODELS]
    labels = load_label_set(settings.warehouse_dir, config.label_set_hash)
    population: Population | None = None
    if config.population is not None:
        postgres = cast("PostgresResource", context.resources.postgres)
        with postgres.connect() as conn:
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
    )
    snapshot, files = data_snapshot(settings.warehouse_dir)
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
    scored = result.cells.filter(pl.col("evaluable"))
    context.log.info(
        f"backtest {config.backtest}: {result.cells.height} cells, {scored.height} scored, "
        f"{len(run_ids)} MLflow runs; seed output, not evidence"
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
        }
    )


models_assets = [backtest]
