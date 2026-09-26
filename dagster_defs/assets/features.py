"""Dagster assets for the `features` group (plan 0010 step G): the point-in-time feature store.

Thin wrappers: the build is `distress_radar.features.asof_assembly`, the checks
`distress_radar.features.leakage` and `distress_radar.features.coverage`. Nothing here fetches:
the `features` job rebuilds the stored datasets it reads and then the store (owner decision 4
for steps F–H).
"""

from datetime import date
from typing import TYPE_CHECKING, cast

import dagster as dg
import polars as pl

from dagster_defs.assets.legal import legal_events
from dagster_defs.assets.parsing import financial_statements_canonical, restatement_events
from distress_radar.features.asof_assembly import build_feature_store
from distress_radar.features.config import load_feature_set
from distress_radar.features.coverage import coverage, latest_forms
from distress_radar.features.leakage import known_from_violations, truncation_differences
from distress_radar.labels import load_label_config
from distress_radar.settings import Settings

if TYPE_CHECKING:
    from dagster_defs.definitions import PostgresResource

LEAKAGE_CHECK = "leakage"
COVERAGE_CHECK = "feature_coverage"


def _md_table(frame: pl.DataFrame, limit: int = 50) -> dg.MetadataValue:
    shown = frame.head(limit)
    lines = [
        "| " + " | ".join(shown.columns) + " |",
        "|" + "---|" * len(shown.columns),
        *(
            "| " + " | ".join("" if v is None else str(v) for v in row) + " |"
            for row in shown.rows()
        ),
    ]
    if frame.height > limit:
        lines.append(f"\n{frame.height - limit} more rows not shown")
    return dg.MetadataValue.md("\n".join(lines))


@dg.asset(
    group_name="features",
    deps=[financial_statements_canonical, restatement_events, legal_events],
    required_resource_keys={"postgres"},
    check_specs=[
        dg.AssetCheckSpec(
            LEAKAGE_CHECK,
            asset="feature_store",
            blocking=True,
            description=(
                "AGENT_SPEC §9.1 on every row, and each family recomputed from only what was "
                "public by each as_of_date. Blocking: a leak is a build failure (invariant 1)."
            ),
        ),
        dg.AssetCheckSpec(
            COVERAGE_CHECK,
            asset="feature_store",
            description="Share of non-null values by family and statement form. Report only.",
        ),
    ],
)
def feature_store(context: dg.AssetExecutionContext) -> dg.MaterializeResult:
    """H — `feature_store` (AGENT_SPEC §5): every feature as of each month-end, with its date.

    Inputs: `financial_statements_canonical`, `restatement_events` and `legal_events` Parquet
    under `WAREHOUSE_DIR`; Postgres `filing_index`, `parsed_documents`, `entity_master` and
    `legal_source_fetches`; `config/features/<FEATURE_SET_VERSION>.yaml` with its line-item
    map, `config/statutory/` (tripwires, filing deadlines, taxonomy); the grid's start from
    `config/labels/<LABEL_VERSION>.yaml`.
    Outputs: Parquet under `WAREHOUSE_DIR/feature_store/`, one file per `as_of_year`, rebuilt
    whole and replaced atomically (ADR 0008), validated by the feature-set contract; equal
    inputs write equal bytes.
    Checks: `leakage` (blocking) and `feature_coverage` (report only).
    Partition scheme: none (unpartitioned); files split by `as_of_year`.
    """
    postgres = cast("PostgresResource", context.resources.postgres)
    settings = Settings()
    config = load_feature_set(settings.feature_set_version)
    grid_start: date = load_label_config(settings.label_version).as_of_grid.start
    with postgres.connect() as conn:
        build = build_feature_store(conn, settings.warehouse_dir, config, grid_start=grid_start)
    frame = build.frame

    violations = known_from_violations(frame, config)
    differences = truncation_differences(build.grid, build.sources, config)
    leak_free = violations.is_empty() and differences.is_empty()
    if not leak_free:
        context.log.error(
            f"leakage: {violations.height} §9.1 violations, {differences.height} differences"
        )
    leakage = dg.AssetCheckResult(
        check_name=LEAKAGE_CHECK,
        passed=leak_free,
        severity=dg.AssetCheckSeverity.ERROR,
        metadata={
            "rows_checked": frame.height,
            "as_of_dates_recomputed": build.grid.get_column("as_of_date").n_unique(),
            "known_from_violations": violations.height,
            "truncation_differences": differences.height,
            "violations": _md_table(violations),
            "differences": _md_table(differences),
        },
    )

    forms = latest_forms(build.grid, build.inputs.panel, build.sources.canonical)
    shares = coverage(frame, forms, config)
    by_family = (
        shares.group_by("family")
        .agg(pl.col("non_null").sum(), pl.col("values").sum())
        .with_columns((pl.col("non_null") / pl.col("values")).round(3).alias("share"))
        .sort("family")
    )
    coverage_result = dg.AssetCheckResult(
        check_name=COVERAGE_CHECK,
        passed=True,
        metadata={
            "by_family": _md_table(by_family),
            "by_family_and_form": _md_table(shares.with_columns(pl.col("share").round(3))),
            "rows_by_form": dict(forms.group_by("form").len().sort("form").iter_rows()),
        },
    )

    excluded = dict(build.panel_excluded.group_by("reason").len().sort("reason").iter_rows())
    context.log.info(
        f"feature_store: {frame.height} rows, {len(config.feature_set.features)} features, "
        f"{'no leaks' if leak_free else 'LEAKS FOUND'}"
    )
    return dg.MaterializeResult(
        metadata={
            "rows": frame.height,
            "entities": frame.get_column("krs").n_unique(),
            "features": len(config.feature_set.features),
            "feature_set_version": config.feature_set.feature_set_version,
            "feature_set_hash": build.feature_set_hash,
            "partitions": len(build.written),
            "panel_excluded_by_reason": excluded,
        },
        check_results=[leakage, coverage_result],
    )


features_assets = [feature_store]
