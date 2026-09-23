"""Dagster assets for the `legal` group (plan 0008): legal-event sources, then `legal_events`.

Thin wrappers: all logic lives in `distress_radar.acquisition`. Assets write to the B2
manifest in Postgres and return only materialization metadata. `ingestion_run_id` is the
Dagster run id.
"""

import asyncio
from collections import Counter
from datetime import UTC, datetime
from typing import TYPE_CHECKING, cast

import dagster as dg

from dagster_defs.assets.acquisition import entity_master
from distress_radar.acquisition import manifest
from distress_radar.acquisition.base import PermanentSourceError, SourceError
from distress_radar.acquisition.krs_extract import fetch_extract
from distress_radar.acquisition.msig_client import (
    extraction_key,
    fetch_entity_notices,
    load_vocabulary,
)

if TYPE_CHECKING:
    from psycopg import Connection

    from dagster_defs.definitions import (
        KrsApiResource,
        MsigApiResource,
        PostgresResource,
        RawObjectStoreResource,
    )
    from distress_radar.acquisition.base import SourceClient
    from distress_radar.acquisition.raw_store import ObjectStore


async def _fetch_all(
    context: dg.AssetExecutionContext,
    conn: "Connection",
    client: "SourceClient",
    store: "ObjectStore",
    entities: list[str],
) -> tuple[Counter[str], list[str]]:
    outcomes: Counter[str] = Counter()
    failures: list[str] = []
    for krs in entities:
        previous = manifest.latest_legal_fetch(conn, krs, "KRS")
        try:
            result = await fetch_extract(
                krs,
                client=client,
                store=store,
                ingestion_run_id=context.run_id,
                previous=previous,
            )
        except SourceError as exc:
            level = context.log.error if isinstance(exc, PermanentSourceError) else context.log.warning
            level(f"KRS {krs}: {exc}")
            failures.append(krs)
            continue
        manifest.record_a4_result(conn, result)
        conn.commit()
        if result.fetch is None:
            outcomes["quarantined"] += 1
        else:
            outcomes["stored_new" if result.fetch.stored_new else "unchanged"] += 1
    return outcomes, failures


@dg.asset(
    group_name="legal",
    deps=[entity_master],
    required_resource_keys={"postgres", "raw_object_store", "krs_api"},
)
def krs_extracts(context: dg.AssetExecutionContext) -> dg.MaterializeResult:
    """A4 — full KRS extracts from the open KRS API, redacted and content-addressed.

    Inputs: every entity in `entity_master`; the entity's latest `legal_source_fetches`
    row (source `KRS`) to recognise unchanged content.
    Outputs: redacted extract JSON in MinIO under `raw/sha256/...` with sidecars
    (ADR 0009 addendum); Postgres `raw_documents` / `raw_document_fetches` /
    `raw_redactions` for new content, one `legal_source_fetches` row per entity and run,
    and A4 `quarantine_events` rows (`krs_extract_not_found`, `krs_extract_unredactable`).
    Every run fetches every entity, since the latest fetch sets the source cutoff; an
    unchanged extract adds a fetch row and no raw object. Each entity commits on its own;
    a source error leaves it for the next run and fails the asset.
    Partition scheme: none (unpartitioned), seed scale (plan 0008 decision 8).
    """
    postgres = cast("PostgresResource", context.resources.postgres)
    object_store = cast("RawObjectStoreResource", context.resources.raw_object_store)
    krs_api = cast("KrsApiResource", context.resources.krs_api)
    store = object_store.store()

    with postgres.connect() as conn:
        manifest.ensure_schema(conn)
        conn.commit()
        entities = manifest.resolved_entities(conn)
        context.log.info(f"fetching {len(entities)} KRS extracts")

        async def run() -> tuple[Counter[str], list[str]]:
            async with krs_api.client() as client:
                return await _fetch_all(context, conn, client, store, entities)

        outcomes, failures = asyncio.run(run())
        counts = manifest.table_counts(conn)

    if failures:
        raise dg.Failure(
            description=f"KRS extract fetch failed for {len(failures)} entities",
            metadata={"failed_krs": failures},
        )
    return dg.MaterializeResult(
        metadata={
            "entities": len(entities),
            "stored_new": outcomes["stored_new"],
            "unchanged": outcomes["unchanged"],
            "quarantined": outcomes["quarantined"],
            "legal_source_fetches_rows": counts["legal_source_fetches"],
        }
    )


@dg.asset(
    group_name="legal",
    deps=[entity_master],
    required_resource_keys={"postgres", "raw_object_store", "msig_api"},
)
def msig_notices(context: dg.AssetExecutionContext) -> dg.MaterializeResult:
    """A4 — MSiG notices per entity, searched by KRS, stored in reduced, person-free form.

    Inputs: every entity in `entity_master`; the entity's `msig_notices` rows under the
    current extraction version and vocabulary (`extraction_key`), so only new notices are
    fetched in full;
    `config/mappings/msig_vocabulary.yaml`.
    Outputs: MSiG search pages in MinIO as received, and one reduced record per new notice
    (structured fields, chapter code, signatures, vocabulary terms, dated terms; never the
    text, ADR 0009 addendum); Postgres `raw_documents` / `raw_document_fetches` /
    `raw_redactions`, `msig_notices`, one `legal_source_fetches` row per entity and run
    (source `MSiG`), and A4 `quarantine_events` rows (`msig_krs_mismatch`,
    `msig_notice_unredactable`). Each entity commits on its own; a source error leaves it
    for the next run and fails the asset.
    Partition scheme: none (unpartitioned), seed scale (plan 0008 decision 8).
    """
    postgres = cast("PostgresResource", context.resources.postgres)
    object_store = cast("RawObjectStoreResource", context.resources.raw_object_store)
    msig_api = cast("MsigApiResource", context.resources.msig_api)
    store = object_store.store()
    vocabulary = load_vocabulary()
    today = datetime.now(UTC).date()

    outcomes: Counter[str] = Counter()
    failures: list[str] = []
    with postgres.connect() as conn:
        manifest.ensure_schema(conn)
        conn.commit()
        entities = manifest.resolved_entities(conn)
        context.log.info(f"searching MSiG for {len(entities)} entities")

        async def run() -> None:
            async with msig_api.client() as client:
                for krs in entities:
                    known = manifest.known_msig_notices(conn, krs, extraction_key(vocabulary))
                    try:
                        result = await fetch_entity_notices(
                            krs,
                            client=client,
                            store=store,
                            vocabulary=vocabulary,
                            ingestion_run_id=context.run_id,
                            known=known,
                            today=today,
                        )
                    except SourceError as exc:
                        context.log.error(f"KRS {krs}: {exc}")
                        failures.append(krs)
                        continue
                    manifest.record_a4_result(conn, result)
                    conn.commit()
                    outcomes["new_notices"] += len(result.notices)
                    outcomes["known_notices"] += len(known)
                    outcomes["quarantined"] += len(result.quarantine)
                    outcomes["entities_with_notices"] += bool(result.notices or known)

        asyncio.run(run())
        counts = manifest.table_counts(conn)

    if failures:
        raise dg.Failure(
            description=f"MSiG search failed for {len(failures)} entities",
            metadata={"failed_krs": failures},
        )
    return dg.MaterializeResult(
        metadata={
            "entities": len(entities),
            **dict(outcomes),
            "msig_notices_rows": counts["msig_notices"],
        }
    )


legal_assets = [krs_extracts, msig_notices]
