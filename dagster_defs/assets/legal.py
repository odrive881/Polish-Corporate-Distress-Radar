"""Dagster assets for the `legal` group (plan 0008): legal-event sources, then `legal_events`.

Thin wrappers: all logic lives in `distress_radar.acquisition` and `distress_radar.parsing`. Assets write to the B2
manifest in Postgres and return only materialization metadata. `ingestion_run_id` is the
Dagster run id.
"""

import asyncio
import json
from collections import Counter
from datetime import UTC, datetime
from typing import TYPE_CHECKING, cast

import dagster as dg

from dagster_defs.assets.acquisition import SEGMENTS_DIR, entity_master
from distress_radar.acquisition import manifest
from distress_radar.acquisition.base import PermanentSourceError, SourceError
from distress_radar.acquisition.krs_extract import fetch_extract
from distress_radar.acquisition.models import QuarantineRecord
from distress_radar.acquisition.msig_client import (
    extraction_key,
    fetch_entity_notices,
    load_vocabulary,
)
from distress_radar.acquisition.raw_store import raw_key
from distress_radar.parsing.contracts import LEGAL_EVENTS
from distress_radar.parsing.legal_acceptance import load_seed_hints, seed_acceptance
from distress_radar.parsing.legal_events import (
    Normalised,
    finalise,
    from_krs_extract,
    from_msig_notice,
    to_frame,
)
from distress_radar.parsing.legal_taxonomy import load_procedure_taxonomy
from distress_radar.parsing.msig_notice_kinds import load_notice_kinds
from distress_radar.settings import Settings
from distress_radar.warehouse import write_dataset

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


SEED = SEGMENTS_DIR / "construction_sme_v1_seed.yaml"
_REASON = "reason:"


def _quarantine_check(name: str, counts: Counter[str]) -> dg.AssetCheckResult:
    """This run's quarantined records by reason: a warning, since quarantine is by design."""
    reasons = {
        k.removeprefix(_REASON): v for k, v in sorted(counts.items()) if k.startswith(_REASON)
    }
    return dg.AssetCheckResult(
        check_name=name,
        passed=not reasons,
        severity=dg.AssetCheckSeverity.WARN,
        metadata={"quarantined": sum(reasons.values()), "by_reason": reasons},
    )


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
            level = (
                context.log.error if isinstance(exc, PermanentSourceError) else context.log.warning
            )
            level(f"KRS {krs}: {exc}")
            failures.append(krs)
            continue
        manifest.record_a4_result(conn, result)
        conn.commit()
        outcomes.update(f"{_REASON}{q.reason_code}" for q in result.quarantine)
        if result.fetch is None:
            outcomes["quarantined"] += 1
        else:
            outcomes["stored_new" if result.fetch.stored_new else "unchanged"] += 1
    return outcomes, failures


@dg.asset(
    group_name="legal",
    deps=[entity_master],
    required_resource_keys={"postgres", "raw_object_store", "krs_api"},
    check_specs=[dg.AssetCheckSpec("krs_extracts_quarantine", asset="krs_extracts")],
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
        },
        check_results=[_quarantine_check("krs_extracts_quarantine", outcomes)],
    )


@dg.asset(
    group_name="legal",
    deps=[entity_master],
    required_resource_keys={"postgres", "raw_object_store", "msig_api"},
    check_specs=[dg.AssetCheckSpec("msig_notices_quarantine", asset="msig_notices")],
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
                    outcomes.update(f"{_REASON}{q.reason_code}" for q in result.quarantine)
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
            **{k: v for k, v in outcomes.items() if not k.startswith(_REASON)},
            "msig_notices_rows": counts["msig_notices"],
        },
        check_results=[_quarantine_check("msig_notices_quarantine", outcomes)],
    )


legal_assets = [krs_extracts, msig_notices]


LEGAL_EVENTS_DATASET = "legal_events"


@dg.asset(
    group_name="legal",
    deps=[krs_extracts, msig_notices],
    required_resource_keys={"postgres", "raw_object_store"},
    check_specs=[
        dg.AssetCheckSpec("legal_events_quarantine", asset="legal_events"),
        dg.AssetCheckSpec(
            "seed_acceptance",
            asset="legal_events",
            description="Each seed entity with a status hint has a matching event, and no other does.",
        ),
    ],
)
def legal_events(context: dg.AssetExecutionContext) -> dg.MaterializeResult:
    """C (legal side) — `legal_events` from the stored KRS extracts and MSiG notice records.

    Inputs: the latest `krs_extracts` object per entity and every `msig_notices` record under
    the current extraction key (MinIO, via `legal_source_fetches` / `msig_notices`);
    `config/statutory/procedure_taxonomy.yaml`, `config/mappings/msig_notice_kinds.yaml`.
    Outputs: Parquet under `WAREHOUSE_DIR/legal_events/`, one file per `event_year`, rebuilt
    whole and replaced atomically (ADR 0008), checked by the `LEGAL_EVENTS` contract;
    C4 `quarantine_events` rows for records the taxonomy or the notice kinds do not map
    (`legal_event_type_unmapped`, `msig_notice_unclassified`, `krs_entry_date_missing`).
    `ingestion_run_id` on each row is the run that first stored its source document, so
    unchanged input re-materializes to identical bytes.
    Partition scheme: none (unpartitioned); files split by `event_year`.
    """
    postgres = cast("PostgresResource", context.resources.postgres)
    store = cast("RawObjectStoreResource", context.resources.raw_object_store).store()
    taxonomy = load_procedure_taxonomy()
    vocabulary = load_vocabulary()
    kinds = load_notice_kinds(vocabulary=vocabulary)
    settings = Settings()
    now = datetime.now(UTC)

    normalised = Normalised()
    with postgres.connect() as conn:
        manifest.ensure_schema(conn)
        for source, krs, sha256, first_run in manifest.legal_documents(
            conn, extraction_key(vocabulary)
        ):
            document = json.loads(store.get(raw_key(sha256)))
            if source == "KRS":
                part = from_krs_extract(
                    document,
                    krs=krs,
                    taxonomy=taxonomy,
                    source_document_hash=sha256,
                    ingestion_run_id=first_run,
                )
            else:
                part = from_msig_notice(
                    document,
                    taxonomy=taxonomy,
                    kinds=kinds,
                    source_document_hash=sha256,
                    ingestion_run_id=first_run,
                )
            normalised.extend(part)
        manifest.insert_quarantine(
            conn,
            [
                QuarantineRecord(
                    stage="C4",
                    entity_key=f"{r.krs}:{r.source}:{r.source_element_path}",
                    reason_code=r.reason_code,
                    detail=r.detail,
                    source_document_hash=r.source_document_hash,
                    ingestion_run_id=context.run_id,
                    created_at=now,
                    krs=r.krs,
                    document_ref=None,
                )
                for r in normalised.rejects
            ],
        )
        conn.commit()

    frame = LEGAL_EVENTS.validate(to_frame(finalise(normalised)))
    write_dataset(frame, settings.warehouse_dir, LEGAL_EVENTS_DATASET, "event_year")
    reasons = Counter(f"{_REASON}{r.reason_code}" for r in normalised.rejects)
    verdicts = seed_acceptance(frame, load_seed_hints(SEED))
    acceptance = dg.AssetCheckResult(
        check_name="seed_acceptance",
        passed=all(v.passed for v in verdicts),
        metadata={
            "matched": sum(v.status == "matched" for v in verdicts),
            "clear": sum(v.status == "clear" for v in verdicts),
            "missing": [v.krs for v in verdicts if v.status == "missing"],
            "unexpected": [v.krs for v in verdicts if v.status == "unexpected"],
            "verdicts": dg.MetadataValue.md(
                "| KRS | hint | status | classes found | first matching event |\n|---|---|---|---|---|\n"
                + "\n".join(
                    f"| {v.krs} | {v.hint or ''} | {v.status} | {', '.join(v.found_classes)} | "
                    f"{v.first_event or ''} |"
                    for v in verdicts
                )
            ),
        },
    )
    return dg.MaterializeResult(
        metadata={
            "rows": frame.height,
            "entities": frame["krs"].n_unique(),
            "dedup_groups": frame["dedup_group_id"].n_unique(),
            "rows_by_source": dict(Counter(frame["source"].to_list())),
        },
        check_results=[_quarantine_check("legal_events_quarantine", reasons), acceptance],
    )


legal_assets.append(legal_events)
