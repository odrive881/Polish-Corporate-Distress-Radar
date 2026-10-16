"""Dagster asset for the `publish` group (plan 0016 step F): the public site's only database.

A thin wrapper over `distress_radar.publish`. It runs in its own `publish` job, by hand, from a
clean working tree, and never deploys anything: building the site from it is `make site`, and
deploying is the owner's separate decision (decision 10).
"""

from pathlib import Path
from typing import TYPE_CHECKING, cast

import dagster as dg
import polars as pl
from mlflow import MlflowClient

from dagster_defs.assets.models import REPO_ROOT, backtest
from distress_radar import publish as pub
from distress_radar.acquisition.document_retrieval import load_document_types
from distress_radar.models.dataset import load_label_set
from distress_radar.models.population import read_lists, read_population
from distress_radar.models.registry import code_commit, data_snapshot
from distress_radar.models.splits import load_backtest_config
from distress_radar.settings import Settings
from distress_radar.transform_project import read_model
from distress_radar.warehouse import read_dataset

if TYPE_CHECKING:
    from dagster_defs.definitions import PostgresResource

NOT_SEARCHED = ("rdf_not_searched", "rdf_search_incomplete")


@dg.asset(
    group_name="publish",
    deps=["dq_mart", "dq_mart_coverage", backtest],
    required_resource_keys={"postgres"},
)
def publish(context: dg.AssetExecutionContext) -> dg.MaterializeResult:
    """K — the aggregate tables the public site reads (plan 0016 step F, decisions 0 and 9).

    Inputs: `DQ_MART_MIN_CELL_ENTITIES` (refused unset or below 5); the code commit of a clean
    working tree (refused otherwise); `config/models/<BACKTEST_VERSION>.yaml`; `marts.dq_mart` and
    `marts.dq_mart_coverage` in `WAREHOUSE_DIR/transform.duckdb`; the backtest's finished MLflow
    runs and `WAREHOUSE_DIR/model_predictions/`; the pinned label set; `feature_store` (its
    snapshot hash); `financial_statements_canonical`; Postgres `universe_candidates`,
    `entity_master`, `rdf_listed_entities` and `filing_index`.
    Outputs: `site/sources/publish/publish.duckdb`, replaced whole: `dq_checks`, `dq_coverage`,
    `pipeline_coverage`, `backtest_metrics`, `backtest_reliability`, `provenance`; every cell
    under the threshold suppressed, and no identifier in it (checked before the run succeeds).
    Partition scheme: none (unpartitioned).
    """
    settings = Settings()
    threshold = settings.publish_min_cell_entities()  # before any work
    commit = code_commit(REPO_ROOT)
    config = load_backtest_config(settings.backtest_version)
    if config.population is None:
        raise ValueError(
            f"{config.backtest} has no population rule: nothing to say who is modelled"
        )
    warehouse = settings.warehouse_dir

    client = MlflowClient(tracking_uri=settings.mlflow_tracking_uri)
    cells = pub.cells_from_mlflow(client, config.backtest)
    pooled = pub.pooled_tables(
        cells,
        read_dataset(warehouse, "model_predictions"),
        backtest=config.backtest,
        min_events=config.min_events,
        bins=config.reliability_bins,
        threshold=threshold,
    )
    labels = load_label_set(warehouse, config.label_set_hash)
    labelled = frozenset(labels.get_column("krs").unique().to_list())
    parsed = frozenset(
        pl.scan_parquet(str(warehouse / "financial_statements_canonical" / "**" / "*.parquet"))
        .select("krs")
        .unique()
        .collect()
        .get_column("krs")
        .to_list()
    )
    document_types = load_document_types()
    statement_codes = [c for c, t in document_types.types.items() if t.canonical == "statement"]

    postgres = cast("PostgresResource", context.resources.postgres)
    with postgres.connect() as conn:
        lists = read_lists(conn)
        everyone: list[str] = sorted(set[str]().union(*lists.values()))
        modelled = read_population(conn, labelled, config.population, document_types.download_codes)
        if modelled.entities_hash != config.population.entities_hash:
            raise ValueError(
                f"the population no longer hashes to {config.backtest}'s pin: publish the backtest "
                "that was run on it"
            )
        searched = read_population(conn, everyone, config.population, document_types.download_codes)
        identified = frozenset(
            str(k).strip() for (k,) in conn.execute("SELECT krs FROM entity_master").fetchall()
        )
        stored = frozenset(
            str(k).strip()
            for (k,) in conn.execute(
                """
                SELECT DISTINCT krs FROM filing_index
                WHERE sha256 IS NOT NULL AND rdf_type_code = ANY(%s)
                  AND status IS DISTINCT FROM 'USUNIETY' AND deleted_on IS NULL
                """,
                (statement_codes,),
            ).fetchall()
        )
        identifiers = [
            str(v).strip()
            for row in conn.execute("SELECT krs, nip, regon FROM entity_master").fetchall()
            for v in row
            if v is not None
        ] + everyone

    stages = {
        "identified": identified,
        "searched_on_rdf": frozenset(
            k for k in everyone if searched.excluded.get(k) not in NOT_SEARCHED
        ),
        "statements_stored": stored,
        "statements_parsed": parsed,
        "labelled": labelled,
        "modelled": modelled.included,
    }
    params = pub.backtest_params(client, config.backtest)
    snapshot, _ = data_snapshot(warehouse)
    tables = {
        **pub.dq_tables(
            read_model("marts.dq_mart", warehouse),
            read_model("marts.dq_mart_coverage", warehouse),
            threshold,
        ),
        "pipeline_coverage": pub.pipeline_coverage(lists, stages, threshold),
        "backtest_metrics": pooled.metrics,
        "backtest_reliability": pooled.reliability,
        "provenance": pub.provenance(
            {
                "code_commit": commit,
                "backtest": config.backtest,
                "backtest_code_commit": params["code_commit"],
                "data_snapshot_hash": snapshot,
                "backtest_data_snapshot_hash": params["data_snapshot_hash"],
                "label_version": params["label_version"],
                "label_set_hash": config.label_set_hash,
                "feature_set_version": params["feature_set_version"],
                "feature_set_hash": params["feature_set_hash"],
                "population_hash": config.population.entities_hash,
                "min_cell_entities": threshold,
            }
        ),
    }
    path = pub.write_publish_db(tables, REPO_ROOT / pub.PUBLISH_DB)
    pub.check_publishable(path, identifiers)
    context.log.info(f"publish.duckdb written; threshold {threshold}; nothing identifying in it")
    return dg.MaterializeResult(
        metadata={
            "path": str(Path(pub.PUBLISH_DB)),
            "code_commit": commit,
            "min_cell_entities": threshold,
            "tables": dg.MetadataValue.md(
                "\n".join(
                    f"- {name}: {frame.height} rows, "
                    f"{frame.get_column('suppressed').sum() if 'suppressed' in frame.columns else 0} suppressed"
                    for name, frame in sorted(tables.items())
                )
            ),
        }
    )


publish_assets = [publish]
