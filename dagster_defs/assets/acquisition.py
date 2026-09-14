"""Dagster assets for stage A (acquisition). See AGENT_SPEC.md §6A.

Thin wrappers: all logic lives in `distress_radar.acquisition`. Both assets
write to the B2 manifest in Postgres and return only materialization metadata.
`ingestion_run_id` is the Dagster run id.

No `from __future__ import annotations` here: Dagster inspects the `config`
parameter annotation at runtime.
"""

from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, cast

import dagster as dg

from distress_radar.acquisition import manifest
from distress_radar.acquisition.base import PermanentSourceError
from distress_radar.acquisition.regon_client import resolve_entity
from distress_radar.acquisition.universe_discovery import load_seed, load_segment

if TYPE_CHECKING:
    from dagster_defs.definitions import Bir1Resource, PostgresResource, RawObjectStoreResource

SEGMENTS_DIR = Path(__file__).resolve().parents[2] / "config" / "segments"


class SegmentConfig(dg.Config):
    segment: str = "construction_sme_v1"


@dg.asset(group_name="acquisition", required_resource_keys={"postgres"})
def universe_candidates(
    context: dg.AssetExecutionContext, config: SegmentConfig
) -> dg.MaterializeResult:
    """A1 — seed universe from `config/segments/<segment>_seed.yaml`.

    Inputs: the seed YAML (no network).
    Outputs: Postgres `universe_candidates` rows (`discovery_source=manual_seed`);
    malformed entries as `quarantine` rows (stage A1, reason-coded). Inserts are
    `ON CONFLICT DO NOTHING`, so re-materializing adds no rows.
    Partition scheme: none (unpartitioned) for the Phase 1 seed.
    """
    postgres = cast("PostgresResource", context.resources.postgres)
    seed = load_seed(
        SEGMENTS_DIR / f"{config.segment}_seed.yaml",
        ingestion_run_id=context.run_id,
        discovered_at=datetime.now(UTC),
    )
    with postgres.connect() as conn:
        manifest.ensure_schema(conn)
        manifest.insert_universe_candidates(conn, seed.candidates)
        manifest.insert_quarantine(conn, seed.quarantine)
        conn.commit()
        counts = manifest.table_counts(conn)
    return dg.MaterializeResult(
        metadata={
            "seed_candidates": len(seed.candidates),
            "seed_quarantined": len(seed.quarantine),
            "universe_candidates_rows": counts["universe_candidates"],
        }
    )


@dg.asset(
    group_name="acquisition",
    deps=[universe_candidates],
    required_resource_keys={"postgres", "raw_object_store", "bir1"},
)
def entity_master(context: dg.AssetExecutionContext, config: SegmentConfig) -> dg.MaterializeResult:
    """A2 — validate candidates against GUS BIR1.

    Inputs: `universe_candidates` rows with no A2 outcome yet (not in
    `entity_master`, no A2 `quarantine` row); `config/segments/<segment>.yaml`.
    Outputs: BIR1 XML payloads in MinIO under `raw/sha256/...` with sidecars;
    Postgres `raw_documents` / `raw_document_fetches`, `entity_master`,
    `entity_reconciliation_log`, and reason-coded A2 `quarantine` rows.
    Resolved candidates are skipped, so re-materializing makes no BIR1 calls
    and adds no objects or rows. Each entity commits on its own; a source
    error leaves that candidate unresolved for the next run and fails the asset.
    Partition scheme: none (unpartitioned).
    """
    postgres = cast("PostgresResource", context.resources.postgres)
    object_store = cast("RawObjectStoreResource", context.resources.raw_object_store)
    bir1 = cast("Bir1Resource", context.resources.bir1)
    segment = load_segment(SEGMENTS_DIR / f"{config.segment}.yaml")
    store = object_store.store()

    resolved = quarantined = 0
    failures: list[str] = []
    with postgres.connect() as conn:
        manifest.ensure_schema(conn)
        conn.commit()
        pending = manifest.unresolved_candidates(conn)
        context.log.info(f"{len(pending)} candidates without an A2 outcome")
        if pending:
            with bir1.service() as service:
                for krs, regon_hint, nip_hint in pending:
                    try:
                        result = resolve_entity(
                            krs,
                            service=service,
                            store=store,
                            segment=segment,
                            ingestion_run_id=context.run_id,
                            regon_hint=regon_hint,
                            nip_hint=nip_hint,
                        )
                    except PermanentSourceError as exc:
                        context.log.error(f"KRS {krs}: {exc}")
                        failures.append(krs)
                        continue
                    manifest.record_a2_result(conn, result)
                    conn.commit()
                    resolved += result.entity is not None
                    quarantined += bool(result.quarantine)
        counts = manifest.table_counts(conn)

    if failures:
        raise dg.Failure(
            description=f"BIR1 lookup failed for {len(failures)} KRS; left unresolved",
            metadata={"failed_krs": failures},
        )
    return dg.MaterializeResult(
        metadata={
            "resolved_this_run": resolved,
            "quarantined_this_run": quarantined,
            **{f"{table}_rows": n for table, n in counts.items()},
        }
    )


acquisition_assets = [universe_candidates, entity_master]
