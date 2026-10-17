"""Wiring for monitoring (plan 0018 steps F and G). Logic lives in `distress_radar.monitoring`.

No `from __future__ import annotations` here: Dagster inspects the `context` annotation at
definition time.
"""

from typing import TYPE_CHECKING, cast

import dagster as dg

from dagster_defs.assets.features import feature_store
from dagster_defs.assets.models import scores
from distress_radar.acquisition.document_retrieval import load_document_types
from distress_radar.models.dataset import load_feature_store, load_label_set
from distress_radar.models.population import read_population
from distress_radar.models.serving import load_served
from distress_radar.models.splits import label_timing, load_backtest_config
from distress_radar.monitoring.drift import (
    current_frame,
    drift_report,
    reference_frame,
    write_drift,
)
from distress_radar.settings import Settings

if TYPE_CHECKING:
    from dagster_defs.definitions import PostgresResource


@dg.asset(
    group_name="monitoring", deps=[feature_store, scores], required_resource_keys={"postgres"}
)
def drift(context: dg.AssetExecutionContext) -> dg.MaterializeResult:
    """L — input drift of the served model at the latest scored month-end, per horizon (plan 0018
    step F, decision 6).

    Inputs: per horizon of `config/models/<BACKTEST_VERSION>.yaml`, the served version (MLflow) and
    its inputs; the backtest it was registered by (its `backtest` tag): label set, test year,
    distress classes and population rule, which rebuild its fold's training rows (the reference)
    from the stored `feature_store` and label set; Postgres `scores_history` (the latest month-end
    the version scored, and whom) and the acquisition records the population rule reads.
    Outputs: `WAREHOUSE_DIR/reports/drift/<as_of>_<h>m.html` (Evidently, ids normalised: the same
    inputs give the same bytes) and `.json` (per input: test, statistic, drifted). Local only: never
    published (decision 6). Metadata says whether today's population still hashes to the backtest's
    pin; when it does not, the reference is the fold over today's population, and says so.
    Partition scheme: none (unpartitioned); one report per horizon and scored month-end.
    """
    settings = Settings()
    horizons = load_backtest_config(settings.backtest_version).horizons
    postgres = cast("PostgresResource", context.resources.postgres)
    metadata: dict[str, object] = {}
    with postgres.connect() as conn:
        for horizon in horizons:
            served, scorer = load_served(settings.mlflow_tracking_uri, horizon)
            config = load_backtest_config(served.tags["backtest"])
            labels = load_label_set(settings.warehouse_dir, served.tags["label_set_hash"])
            features = load_feature_store(
                settings.warehouse_dir, served.tags["feature_set_version"]
            )
            entities = None
            matches: bool | None = None
            if config.population is not None:
                population = read_population(
                    conn,
                    labels.get_column("krs").unique().to_list(),
                    config.population,
                    load_document_types().download_codes,
                )
                entities = population.included
                matches = population.entities_hash == config.population.entities_hash
            row = conn.execute(
                """
                SELECT max(as_of_date) FROM scores_history
                WHERE horizon_months = %s AND model_name = %s AND model_version = %s
                """,
                (horizon, served.name, served.version),
            ).fetchone()
            as_of = None if row is None else row[0]
            if as_of is None:
                context.log.warning(f"{served.name} v{served.version} has scored nothing yet")
                continue
            scored = frozenset(
                str(k).strip()
                for (k,) in conn.execute(
                    """
                    SELECT krs FROM scores_history
                    WHERE horizon_months = %s AND model_name = %s AND model_version = %s
                      AND as_of_date = %s
                    """,
                    (horizon, served.name, served.version, as_of),
                ).fetchall()
            )
            result = drift_report(
                reference_frame(
                    features,
                    labels,
                    horizon_months=horizon,
                    test_year=served.test_year,
                    distress_classes=config.distress_classes,
                    entities=entities,
                    timing=label_timing(labels),
                ),
                current_frame(features, scored, as_of),
                scorer.inputs,
                horizon_months=horizon,
                model=f"{served.name} v{served.version}",
                as_of_date=as_of,
                population_matches_pin=matches,
            )
            html, _ = write_drift(result, settings.warehouse_dir)
            drifted = sorted(c for c, v in result.columns.items() if v["drifted"])
            context.log.info(
                f"{horizon}m at {as_of}: {result.current_rows} rows against "
                f"{result.reference_rows}; drifted: {', '.join(drifted) or 'none'}; {html}"
            )
            metadata |= {
                f"{horizon}m_as_of_date": as_of.isoformat(),
                f"{horizon}m_reference_rows": result.reference_rows,
                f"{horizon}m_current_rows": result.current_rows,
                f"{horizon}m_drifted": ", ".join(drifted) or "none",
                f"{horizon}m_population_matches_pin": str(matches),
                f"{horizon}m_report": str(html),
            }
    return dg.MaterializeResult(metadata=metadata)


monitoring_assets = [drift]
