"""Dagster assets for E3 and the first F models: the SQLMesh DQ models (plan 0007 step F).

Thin wrapper: the rebuild and the audits live in `distress_radar.transform_project`,
the SQL in `transform/`. One Dagster run takes the seed from stored bytes to
quality metrics: `financial_statements_canonical` → `restatement_events` →
this group.

Every SQLMesh audit is an asset check on the model it audits. The check list
is static so that loading these definitions never starts SQLMesh;
`tests/dagster_defs/test_dq_assets.py` holds it equal to the project's audits.
"""

from collections.abc import Iterator

import dagster as dg

from dagster_defs.assets.parsing import financial_statements_canonical, restatement_events
from distress_radar.transform_project import DQ_MODELS, build_dq_models

# SQLMesh model → its audits, in declaration order.
DQ_AUDITS: dict[str, tuple[str, ...]] = {
    "staging.parsed_documents_current": ("unique_combination_of_columns", "not_null"),
    "quarantine.quarantine": (
        "not_null",
        "unique_combination_of_columns",
        "quarantine_has_reasons",
        "quarantine_one_parsing_stage_per_file",
        "quarantine_e2_matches_canonical",
        "quarantine_c_matches_parsed_documents",
    ),
    "marts.dq_mart": (
        "not_null",
        "unique_combination_of_columns",
        "dq_no_cell_below_threshold_unsuppressed",
        "dq_no_suppression_without_threshold",
        "dq_suppressed_cells_carry_no_measures",
        "dq_mart_outcomes_add_up",
    ),
    "marts.dq_mart_coverage": (
        "not_null",
        "unique_combination_of_columns",
        "dq_no_cell_below_threshold_unsuppressed",
        "dq_no_suppression_without_threshold",
        "dq_suppressed_cells_carry_no_measures",
        "dq_coverage_adds_up",
    ),
}

_DESCRIPTIONS = {
    "staging.parsed_documents_current": "The latest parsing run's statement files, with their filings.",
    "quarantine.quarantine": "The current quarantined set (AGENT_SPEC §5), recomputed each run.",
    "marts.dq_mart": "Pass rates by structure version × filed body set × fiscal year × check.",
    "marts.dq_mart_coverage": "Files stored / parsed / unmapped / PDF / quarantined, per fiscal year.",
}


@dg.multi_asset(
    group_name="dq",
    specs=[
        dg.AssetSpec(
            asset,
            deps=[financial_statements_canonical, restatement_events],
            description=_DESCRIPTIONS[model],
            metadata={"sqlmesh_model": model},
        )
        for model, asset in DQ_MODELS.items()
    ],
    check_specs=[
        dg.AssetCheckSpec(audit, asset=DQ_MODELS[model])
        for model, audits in DQ_AUDITS.items()
        for audit in audits
    ],
)
def dq_models(
    context: dg.AssetExecutionContext,
) -> Iterator[dg.MaterializeResult | dg.AssetCheckResult]:
    """E3/F: rebuild the SQLMesh DQ models, then report every audit as an asset check.

    Inputs (the `ext` views, `transform/config.py`): `financial_statements_canonical`
    and `identity_check_results` Parquet under `WAREHOUSE_DIR`, and the Postgres
    `quarantine_events`, `parsed_documents` and `filing_index`.
    Outputs, in `WAREHOUSE_DIR/transform.duckdb` (SQLMesh environment `prod`):
    `staging.parsed_documents_current` (view), `quarantine.quarantine`,
    `marts.dq_mart` and `marts.dq_mart_coverage` (tables, rebuilt in full each
    run). SQLMesh state is in the Postgres schema `sqlmesh` (ADR 0010).
    Partition scheme: none (unpartitioned).
    """
    build = build_dq_models()
    for model, asset in DQ_MODELS.items():
        yield dg.MaterializeResult(
            asset_key=asset, metadata={"rows": build.row_counts[model], "sqlmesh_model": model}
        )
    for outcome in build.audits:
        if outcome.failing_rows is None:
            context.log.warning(f"{outcome.model}: audit {outcome.audit} was skipped")
        yield dg.AssetCheckResult(
            asset_key=DQ_MODELS[outcome.model],
            check_name=outcome.audit,
            passed=outcome.passed,
            metadata={
                "failing_rows": outcome.failing_rows if outcome.failing_rows is not None else -1,
                "query": dg.MetadataValue.md(f"```sql\n{outcome.query}\n```"),
            },
        )


dq_assets = [dq_models]
