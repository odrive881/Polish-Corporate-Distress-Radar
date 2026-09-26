"""Dagster assets for stage A (acquisition). See AGENT_SPEC.md §6A.

Thin wrappers: all logic lives in `distress_radar.acquisition`. All assets
write to the B2 manifest in Postgres and return only materialization metadata.
`ingestion_run_id` is the Dagster run id.

No `from __future__ import annotations` here: Dagster inspects the `config`
parameter annotation at runtime.
"""

from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, cast

import dagster as dg
import psycopg

from distress_radar.acquisition import manifest
from distress_radar.acquisition.base import PermanentSourceError, SourceError
from distress_radar.acquisition.document_retrieval import (
    A3Detail,
    A3Download,
    CircuitBreaker,
    RdfCircuitOpen,
    index_filings,
    load_document_types,
    retrieve_document,
)
from distress_radar.acquisition.har_import import import_har
from distress_radar.acquisition.raw_store import S3ObjectStore
from distress_radar.acquisition.redaction_migration import stored_markers
from distress_radar.acquisition.regon_client import resolve_entity
from distress_radar.acquisition.universe_discovery import load_seed, load_segment
from distress_radar.settings import Settings

if TYPE_CHECKING:
    from dagster_defs.definitions import (
        Bir1Resource,
        PostgresResource,
        RawObjectStoreResource,
        RdfBrowserResource,
    )

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
    malformed entries as `quarantine_events` rows (stage A1, reason-coded). Inserts are
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
    `entity_master`, no A2 `quarantine_events` row); `config/segments/<segment>.yaml`.
    Outputs: BIR1 XML payloads in MinIO under `raw/sha256/...` with sidecars;
    Postgres `raw_documents` / `raw_document_fetches`, `entity_master`,
    `entity_reconciliation_log`, and reason-coded A2 `quarantine_events` rows.
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


def _circuit_open_failure(exc: RdfCircuitOpen, done: int, failures: list[str]) -> dg.Failure:
    return dg.Failure(
        description=f"RDF run stopped by circuit breaker: {exc}",
        metadata={"completed_this_run": done, "failed_before_stop": failures},
    )


class FilingIndexConfig(dg.Config):
    only_krs: list[str] | None = None  # restrict this run to these entities


@dg.asset(
    group_name="acquisition",
    deps=[entity_master],
    required_resource_keys={"postgres", "raw_object_store", "rdf_browser"},
)
def filing_index(
    context: dg.AssetExecutionContext, config: FilingIndexConfig
) -> dg.MaterializeResult:
    """A3 — search each resolved entity in RDF and index its filing list.

    Inputs: `entity_master` rows with no A3 outcome yet (no `filing_index` rows,
    no A3 `quarantine_events` row).
    Outputs: the raw entity-lookup and filing-list responses in MinIO (sidecar
    `fetch_tier: playwright`); Postgres `raw_documents` / `raw_document_fetches`,
    one `filing_index` row per listed document (detail columns and `sha256`
    NULL), and `rdf_entity_not_found` / `no_rdf_filings` A3 `quarantine_events` rows.
    Indexed entities are skipped, so re-materializing makes no RDF requests and
    adds no rows. Each entity commits on its own; a blocked or failed lookup
    leaves the entity unresolved and fails the asset; the circuit breaker stops
    the run at once. `only_krs` restricts a run to the listed entities.
    Partition scheme: none (unpartitioned).
    """
    postgres = cast("PostgresResource", context.resources.postgres)
    object_store = cast("RawObjectStoreResource", context.resources.raw_object_store)
    rdf = cast("RdfBrowserResource", context.resources.rdf_browser)
    store = object_store.store()
    breaker = CircuitBreaker()

    indexed = quarantined = 0
    failures: list[str] = []
    with postgres.connect() as conn:
        manifest.ensure_schema(conn)
        conn.commit()
        pending = manifest.unindexed_entities(conn)
        if config.only_krs:
            pending = [krs for krs in pending if krs in config.only_krs]
        context.log.info(f"{len(pending)} entities without an A3 filing-list outcome")
        if pending:
            with rdf.browser() as browser:
                for krs in pending:
                    try:
                        result = index_filings(
                            krs,
                            browser=browser,
                            store=store,
                            ingestion_run_id=context.run_id,
                            breaker=breaker,
                        )
                    except RdfCircuitOpen as exc:
                        raise _circuit_open_failure(exc, indexed + quarantined, failures) from exc
                    except SourceError as exc:
                        context.log.error(f"KRS {krs}: {exc}")
                        failures.append(krs)
                        continue
                    manifest.record_a3_index_result(conn, result)
                    conn.commit()
                    indexed += bool(result.entries)
                    quarantined += bool(result.quarantine)
        counts = manifest.table_counts(conn)

    if failures:
        raise dg.Failure(
            description=f"RDF filing-list lookup failed for {len(failures)} KRS; left unresolved",
            metadata={"failed_krs": failures},
        )
    return dg.MaterializeResult(
        metadata={
            "indexed_this_run": indexed,
            "quarantined_this_run": quarantined,
            **{f"{table}_rows": n for table, n in counts.items()},
        }
    )


class RdfDocumentsConfig(dg.Config):
    """Batching for the long-running document pass (3 RDF requests/minute)."""

    max_documents: int | None = None  # stop after this many pending documents
    download_scope_only: bool = False  # only documents whose type is in the download scope


PERSONAL_DATA_CHECK = "personal_data"


def _personal_data_spec(asset: str) -> dg.AssetCheckSpec:
    return dg.AssetCheckSpec(
        PERSONAL_DATA_CHECK,
        asset=asset,
        blocking=True,
        description=(
            "No stored object holds natural persons' data: signatures, file names, attachment "
            "names, PDF metadata (invariant 6, ADR 0009). Scans the whole store."
        ),
    )


def _personal_data_check(conn: psycopg.Connection, store: S3ObjectStore) -> dg.AssetCheckResult:
    """The store-wide scan, reported by hash and marker kind, never by content."""
    found = stored_markers(conn, store)
    kinds = Counter(
        m.split(": ", 1)[-1].split(" (")[0] for markers in found.values() for m in markers
    )
    return dg.AssetCheckResult(
        check_name=PERSONAL_DATA_CHECK,
        passed=not found,
        severity=dg.AssetCheckSeverity.ERROR,
        metadata={
            "objects_with_personal_data": len(found),
            "hashes": sorted(found)[:50],
            "marker_kinds": dict(kinds),
        },
    )


@dg.asset(
    group_name="acquisition",
    deps=[filing_index],
    required_resource_keys={"postgres", "raw_object_store", "rdf_browser"},
    check_specs=[_personal_data_spec("raw_filing_documents")],
)
def raw_filing_documents(
    context: dg.AssetExecutionContext, config: RdfDocumentsConfig
) -> dg.MaterializeResult:
    """A3 — expand each indexed RDF document; download those in the download scope.

    Inputs: not-deleted `filing_index` rows with no detail yet (or whose
    corrections have no rows yet), or with a detail whose type is in scope but
    no download yet; the scope is
    `config/mappings/rdf_document_types.yaml` (Phase 1: annual financial
    statements and their corrections). In-scope types go first.
    Outputs: the detail responses and the document bytes in MinIO under
    `raw/sha256/...`, unmodified, with sidecars (`fetch_tier: playwright`);
    Postgres `raw_documents` / `raw_document_fetches`, and on each
    `filing_index` row its detail columns (`submission_date` = known_from,
    `detail_sha256`) and, for in-scope types, `sha256`; a row per correction,
    which RDF only shows inside the corrected document's expanded row. One
    expanded row serves both steps, and one download holds a document and its
    corrections. Each step commits on its own, so a failed download keeps its
    detail; settled rows are skipped, so re-materializing makes no RDF requests
    and adds no objects or rows. Contents are not parsed here (C1–C3). A failed
    document stays pending and fails the asset; the circuit breaker stops the
    run at once. `max_documents` / `download_scope_only` split the multi-hour
    pass into shorter runs.
    Check: `personal_data`, blocking — no stored object holds natural persons' data (a store-wide
    scan, plan 0011 step F).
    Partition scheme: none (unpartitioned).
    """
    postgres = cast("PostgresResource", context.resources.postgres)
    object_store = cast("RawObjectStoreResource", context.resources.raw_object_store)
    rdf = cast("RdfBrowserResource", context.resources.rdf_browser)
    store = object_store.store()
    document_types = load_document_types()
    breaker = CircuitBreaker()

    detailed = downloaded = 0
    failures: list[str] = []
    with postgres.connect() as conn:
        manifest.ensure_schema(conn)
        conn.commit()
        pending = manifest.pending_filing_documents(
            conn,
            document_types.download_codes,
            download_scope_only=config.download_scope_only,
        )
        context.log.info(f"{len(pending)} indexed RDF documents owed a detail or download")
        if config.max_documents is not None:
            pending = pending[: config.max_documents]

        def on_detail(fetched: A3Detail) -> None:
            nonlocal detailed
            manifest.record_a3_detail(conn, fetched)
            conn.commit()
            detailed += 1

        saved_refs: set[str] = set()  # one file holds a document and its corrections

        def on_download(download: A3Download) -> None:
            nonlocal downloaded
            manifest.record_a3_download(conn, download)
            conn.commit()
            downloaded += 1
            saved_refs.update(download.document_refs)

        if pending:
            with rdf.browser() as browser:
                for document in pending:
                    if document.document_ref in saved_refs and not document.needs_detail:
                        continue
                    try:
                        retrieve_document(
                            document,
                            browser=browser,
                            store=store,
                            ingestion_run_id=context.run_id,
                            breaker=breaker,
                            document_types=document_types,
                            on_detail=on_detail,
                            on_download=on_download,
                        )
                    except RdfCircuitOpen as exc:
                        raise _circuit_open_failure(exc, detailed + downloaded, failures) from exc
                    except SourceError as exc:
                        context.log.error(
                            f"KRS {document.krs} document {document.document_ref}: {exc}"
                        )
                        failures.append(f"{document.krs}:{document.document_ref}")
                        continue
        counts = manifest.table_counts(conn)
        personal_data = _personal_data_check(conn, store)

    if failures:
        raise dg.Failure(
            description=f"RDF document retrieval failed for {len(failures)} documents; left pending",
            metadata={"failed_documents": failures},
        )
    return dg.MaterializeResult(
        metadata={
            "detailed_this_run": detailed,
            "downloaded_this_run": downloaded,
            **{f"{table}_rows": n for table, n in counts.items()},
        },
        check_results=[personal_data],
    )


class RdfManualImportConfig(dg.Config):
    inbox: str | None = None  # folder of HAR files; default RDF_MANUAL_INBOX


@dg.asset(
    group_name="acquisition",
    deps=[entity_master],
    required_resource_keys={"postgres", "raw_object_store"},
    check_specs=[_personal_data_spec("rdf_manual_import")],
)
def rdf_manual_import(
    context: dg.AssetExecutionContext, config: RdfManualImportConfig
) -> dg.MaterializeResult:
    """A3, manual tier — import RDF sessions a person recorded as HAR files.

    Inputs: every `*.har` in the inbox (`RDF_MANUAL_INBOX`, default
    `.cache/rdf_inbox`, gitignored), saved per README § "Manual RDF capture";
    `entity_master`; `config/mappings/rdf_document_types.yaml`.
    Outputs: the same as `filing_index` + `raw_filing_documents` for what the
    HARs contain — raw RDF API responses and document bytes in MinIO (sidecar
    `fetch_tier: manual_har`, `fetched_at` = capture time), `filing_index` rows
    with their detail columns and `sha256`, and A3 `quarantine_events` rows. Only
    pending work is done, so re-importing the same files adds nothing. The HAR
    files themselves are never stored. In-scope documents a capture did not
    complete are listed in the metadata (`missing_documents`); a file that could
    not be read or imported fails the asset after the other files are done.
    Check: `personal_data`, blocking — no stored object holds natural persons' data (a store-wide
    scan, plan 0011 step F).
    Partition scheme: none (unpartitioned).
    """
    postgres = cast("PostgresResource", context.resources.postgres)
    object_store = cast("RawObjectStoreResource", context.resources.raw_object_store)
    inbox = Path(config.inbox) if config.inbox else Settings().rdf_manual_inbox
    files = sorted(inbox.glob("*.har"))
    context.log.info(f"{len(files)} HAR files in {inbox}")
    store = object_store.store()
    document_types = load_document_types()

    indexed = topped_up = details = downloads = 0
    missing: dict[str, list[str]] = {}
    problems: list[str] = []
    with postgres.connect() as conn:
        manifest.ensure_schema(conn)
        conn.commit()
        resolved = set(manifest.resolved_entities(conn))
        for path in files:
            try:
                report = import_har(
                    path.name,
                    path.read_bytes(),
                    conn=conn,
                    store=store,
                    ingestion_run_id=context.run_id,
                    document_types=document_types,
                    resolved=resolved,
                )
            except SourceError as exc:
                problems.append(f"{path.name}: {exc}")
                continue
            context.log.info(
                f"{path.name}: indexed {report.indexed}, quarantined {report.quarantined}, "
                f"added to already-indexed {report.topped_up}, "
                f"{report.details} details, {report.downloads} downloads, "
                f"still missing {report.missing}"
            )
            for krs in report.not_resolved:
                context.log.warning(f"{path.name}: KRS {krs} is not in entity_master; skipped")
            problems.extend(f"{path.name}: {p}" for p in report.problems)
            indexed += len(report.indexed)
            topped_up += sum(report.topped_up.values())
            details += report.details
            downloads += report.downloads
            for krs, refs in report.missing.items():
                missing[krs] = refs  # later files see earlier imports, so the last word wins
        counts = manifest.table_counts(conn)
        personal_data = _personal_data_check(conn, store)

    for problem in problems:
        context.log.error(problem)
    if problems:
        raise dg.Failure(
            description=f"{len(problems)} problems importing RDF HAR files",
            metadata={"problems": problems, "missing_documents": missing},
        )
    return dg.MaterializeResult(
        metadata={
            "har_files": len(files),
            "indexed_this_run": indexed,
            "documents_added_to_indexed_entities": topped_up,
            "details_this_run": details,
            "downloads_this_run": downloads,
            "missing_documents": missing,
            **{f"{table}_rows": n for table, n in counts.items()},
        },
        check_results=[personal_data],
    )


acquisition_assets = [
    universe_candidates,
    entity_master,
    filing_index,
    raw_filing_documents,
    rdf_manual_import,
]
