"""Dagster assets for the `labels` group (plan 0008 step G): outcome labels, built and frozen.

Thin wrappers: the SQL is in `transform/` (the label models), the rebuild and audits in
`distress_radar.transform_project`, and the freeze in `distress_radar.labels`. One run from
`krs_extracts` goes from registry bytes to a frozen label set.

The audit list is static, like the DQ group's, so loading these definitions never starts
SQLMesh; `tests/transform/test_label_assets.py` holds it equal to the project's audits.
"""

from collections.abc import Iterator
from typing import TYPE_CHECKING, cast

import dagster as dg

from dagster_defs.assets.legal import legal_events
from distress_radar.labels import freeze_label_set, record_label_set
from distress_radar.settings import Settings
from distress_radar.transform_project import LABEL_MODELS, build_label_models, read_model

if TYPE_CHECKING:
    from dagster_defs.definitions import PostgresResource

# SQLMesh model → its audits, in declaration order.
LABEL_AUDITS: dict[str, tuple[str, ...]] = {
    "staging.legal_events_canonical": ("unique_combination_of_columns", "not_null"),
    "staging.outcome_label_grid": ("unique_combination_of_columns", "not_null"),
    "marts.outcome_labels": (
        "unique_combination_of_columns",
        "not_null",
        "labels_no_alive_past_cutoff",
        "labels_no_event_on_or_before_as_of",
        "labels_event_has_proceeding_or_reason",
        "labels_class_iff_not_censored",
        "labels_regime_flag_is_window_overlap",
    ),
    "marts.outcome_label_exclusions": ("unique_combination_of_columns",),
    "marts.legal_event_coverage": ("unique_combination_of_columns",),
}

_DESCRIPTIONS = {
    "staging.legal_events_canonical": "One row per legal event: the canonical reading of each dedup group.",
    "staging.outcome_label_grid": "Every (entity, as_of_date, horizon), labelled or excluded, with why.",
    "marts.outcome_labels": "The prediction targets: the grid's rows that are not excluded.",
    "marts.outcome_label_exclusions": "Grid rows excluded, and entities without a cutoff, by reason.",
    "marts.legal_event_coverage": "Legal events per source and year: the source break, visible.",
}


@dg.multi_asset(
    group_name="labels",
    specs=[
        dg.AssetSpec(
            asset,
            deps=[legal_events],
            description=_DESCRIPTIONS[model],
            metadata={"sqlmesh_model": model},
        )
        for model, asset in LABEL_MODELS.items()
    ],
    check_specs=[
        dg.AssetCheckSpec(audit, asset=LABEL_MODELS[model])
        for model, audits in LABEL_AUDITS.items()
        for audit in audits
    ],
)
def label_models(
    context: dg.AssetExecutionContext,
) -> Iterator[dg.MaterializeResult | dg.AssetCheckResult]:
    """F: rebuild the outcome-label models, then report every audit as an asset check.

    Inputs (the `ext` views, `transform/config.py`): `legal_events` Parquet under
    `WAREHOUSE_DIR`, and the Postgres `entity_master` and `legal_source_fetches`; the label
    parameters, as SQLMesh variables from `config/labels/` and the statutory taxonomy.
    Outputs, in `WAREHOUSE_DIR/transform.duckdb` (environment `prod`):
    `staging.legal_events_canonical` (view), `staging.outcome_label_grid`,
    `marts.outcome_labels`, `marts.outcome_label_exclusions`, `marts.legal_event_coverage`
    (tables, rebuilt in full each run).
    Partition scheme: none (unpartitioned).
    """
    build = build_label_models()
    for model, asset in LABEL_MODELS.items():
        yield dg.MaterializeResult(
            asset_key=asset, metadata={"rows": build.row_counts[model], "sqlmesh_model": model}
        )
    for outcome in build.audits:
        if outcome.failing_rows is None:
            context.log.warning(f"{outcome.model}: audit {outcome.audit} was skipped")
        yield dg.AssetCheckResult(
            asset_key=LABEL_MODELS[outcome.model],
            check_name=outcome.audit,
            passed=outcome.passed,
            metadata={
                "failing_rows": outcome.failing_rows if outcome.failing_rows is not None else -1,
                "query": dg.MetadataValue.md(f"```sql\n{outcome.query}\n```"),
            },
        )


@dg.asset(
    group_name="labels",
    deps=[LABEL_MODELS["marts.outcome_labels"]],
    required_resource_keys={"postgres"},
)
def outcome_labels(context: dg.AssetExecutionContext) -> dg.MaterializeResult:
    """`outcome_labels` (AGENT_SPEC §5), frozen: the label set a model trains on.

    Inputs: `marts.outcome_labels` in `WAREHOUSE_DIR/transform.duckdb`.
    Outputs: `WAREHOUSE_DIR/outcome_labels/label_set_hash=<hash>/part-0.parquet`, written
    once and never overwritten, with `label_set_hash` on every row (decision 7); a
    `label_sets` row in Postgres (hash, `label_version`, cutoff, row count, path, run).
    Re-freezing unchanged labels reproduces the same hash, writes nothing and adds no row.
    Partition scheme: none; one directory per label set.
    """
    postgres = cast("PostgresResource", context.resources.postgres)
    settings = Settings()
    frozen = freeze_label_set(
        read_model("marts.outcome_labels", settings.warehouse_dir), settings.warehouse_dir
    )
    with postgres.connect() as conn:
        record_label_set(conn, frozen, context.run_id)
        conn.commit()
    context.log.info(
        f"label set {frozen.label_set_hash[:12]} ({frozen.row_count} rows) "
        + ("frozen" if frozen.written else "already frozen")
    )
    return dg.MaterializeResult(
        metadata={
            "label_set_hash": frozen.label_set_hash,
            "label_version": frozen.label_version,
            "cutoff_date": frozen.cutoff_date.isoformat(),
            "rows": frozen.row_count,
            "newly_frozen": frozen.written,
            "path": frozen.path.as_posix(),
        }
    )


label_assets = [label_models, outcome_labels]
