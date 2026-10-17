"""Wiring for the alerts (plan 0018 step D). Logic lives in `distress_radar.alerts`.

No `from __future__ import annotations` here: Dagster inspects the `context` annotation at
definition time.
"""

from collections import Counter
from datetime import UTC, datetime
from typing import TYPE_CHECKING, cast

import dagster as dg
import polars as pl

from dagster_defs.assets.features import feature_store
from dagster_defs.assets.models import scores
from distress_radar.acquisition.document_retrieval import load_document_types
from distress_radar.alerts import store
from distress_radar.alerts.rules import (
    ALERT_TYPES,
    feature_alerts,
    filing_alerts,
    load_alert_rules,
    score_alerts,
)
from distress_radar.features.statutory import load_filing_deadlines
from distress_radar.models.dataset import load_feature_store
from distress_radar.settings import Settings

if TYPE_CHECKING:
    from dagster_defs.definitions import PostgresResource


@dg.asset(group_name="alerts", deps=[feature_store, scores], required_resource_keys={"postgres"})
def alerts(context: dg.AssetExecutionContext) -> dg.MaterializeResult:
    """L — what changed since the previous month-end, score or search (plan 0018 step D,
    decisions 4 and 5).

    Inputs: `config/alerts/<DEFAULT_RULES>.yaml` (`alert_rules_v2` since 2026-10-17); Postgres
    `scores_history` (threshold crossings and jumps, per series of one entity, horizon and model
    version); the stored `feature_store` of `FEATURE_SET_VERSION` under `WAREHOUSE_DIR`
    (tripwires and going-concern flags turning true);
    Postgres `rdf_listed_entities` and `filing_index` with `config/statutory/filing_deadlines.yaml`
    (filings overdue at a complete search). Every input is point in time, and nothing is refitted.
    Outputs: Postgres `alerts`, inserted `ON CONFLICT DO NOTHING` on a deterministic id, so a rerun
    adds nothing and leaves acknowledgements alone. Every row names a company: local only, never
    sent anywhere (decision 5).
    Partition scheme: none (unpartitioned); every month-end from the rules' `alerts_from` on.
    """
    settings = Settings()
    rules = load_alert_rules()
    names = sorted({n for r in rules.feature_rules().values() for n in r.features})
    columns = ["krs", "as_of_date", *names, *(f"{n}__known_from" for n in names)]
    features = load_feature_store(settings.warehouse_dir, settings.feature_set_version)
    missing = [c for c in columns if c not in features.columns]
    if missing:
        raise ValueError(f"{settings.feature_set_version} lacks what the rules read: {missing}")
    features = features.select(columns).filter(
        pl.col("as_of_date") >= pl.lit(rules.alerts_from).dt.offset_by("-1mo")
    )
    postgres = cast("PostgresResource", context.resources.postgres)
    with postgres.connect() as conn:
        store.ensure_schema(conn)
        found = [
            *score_alerts(store.read_scores(conn), rules),
            *feature_alerts(features, rules),
            *filing_alerts(
                store.read_searches(conn),
                store.read_statements(conn, load_document_types().download_codes),
                load_filing_deadlines(),
                rules,
            ),
        ]
        inserted = store.insert_alerts(conn, found, datetime.now(UTC))
        conn.commit()
    counts = Counter(a.alert_type for a in found)
    context.log.info(f"{len(found)} alerts under {rules.version}, {inserted} new")
    return dg.MaterializeResult(
        metadata={
            "rule_version": rules.version,
            "alerts": len(found),
            "inserted": inserted,
            **{f"{t}_count": counts[t] for t in ALERT_TYPES},
        }
    )


alerts_assets = [alerts]
