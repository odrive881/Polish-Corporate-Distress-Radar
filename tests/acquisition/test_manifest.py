"""B2 manifest against a live Postgres (`make dev-up`; run via `make test-integration`).

Each test runs in a throwaway schema so it never touches real manifest data.
"""

import uuid
from collections.abc import Iterator
from datetime import UTC, date, datetime
from typing import Any

import psycopg
import pytest
from psycopg.types.json import Jsonb

from distress_radar.acquisition import manifest
from distress_radar.acquisition.document_retrieval import A3Detail, A3Download, A3IndexResult
from distress_radar.acquisition.krs_extract import A4Result, PreviousFetch
from distress_radar.acquisition.models import (
    Bir1PkdCode,
    EntityMasterRow,
    FilingDetail,
    FilingIndexRow,
    LegalSourceFetch,
    QuarantineRecord,
    RawFetchRecord,
    ReconciliationRecord,
    UniverseCandidate,
)
from distress_radar.acquisition.raw_store import RawDocumentMeta
from distress_radar.settings import Settings

pytestmark = pytest.mark.integration

NOW = datetime(2026, 9, 14, 12, 0, tzinfo=UTC)
SHA = "ab" * 32
PKD_SHA = "cd" * 32


@pytest.fixture
def conn() -> Iterator[psycopg.Connection]:
    schema = f"test_manifest_{uuid.uuid4().hex[:12]}"
    with psycopg.connect(Settings().postgres_conninfo, autocommit=True) as connection:
        connection.execute(f'CREATE SCHEMA "{schema}"')  # type: ignore[arg-type]
        connection.execute(f'SET search_path TO "{schema}"')  # type: ignore[arg-type]
        try:
            yield connection
        finally:
            connection.execute(f'DROP SCHEMA "{schema}" CASCADE')  # type: ignore[arg-type]


def _fetch(sha: str, run_id: str) -> RawFetchRecord:
    return RawFetchRecord(
        sha256=sha,
        byte_size=42,
        meta=RawDocumentMeta(
            source="gus_bir1",
            source_url="https://bir1.test#op",
            content_type="application/xml",
            fetched_at=NOW,
            http_headers={},
            ingestion_run_id=run_id,
        ),
    )


def _write_everything(conn: psycopg.Connection, run_id: str) -> None:
    manifest.insert_raw_fetch(conn, _fetch(SHA, run_id))
    manifest.insert_raw_fetch(conn, _fetch(PKD_SHA, run_id))
    manifest.insert_universe_candidates(
        conn,
        [
            UniverseCandidate(
                krs="0000163893",
                discovery_source="manual_seed",
                discovered_at=NOW,
                ingestion_run_id=run_id,
            ),
            UniverseCandidate(
                krs="9999999999",
                discovery_source="manual_seed",
                discovered_at=NOW,
                ingestion_run_id=run_id,
            ),
        ],
    )
    manifest.insert_entity_master(
        conn,
        EntityMasterRow(
            krs="0000163893",
            nip="7160004884",
            regon="430036025",
            name="MARBUD",
            legal_form_code="117",
            status="active",
            pkd_codes=[Bir1PkdCode(code="4120Z", version="2007", predominant=True)],
            pkd_predominant="4120Z",
            source_document_hash=SHA,
            pkd_source_document_hash=PKD_SHA,
            known_from=date(2026, 9, 14),
            ingestion_run_id=run_id,
        ),
    )
    manifest.insert_reconciliation(
        conn,
        ReconciliationRecord(
            krs="0000163893",
            field="regon",
            seed_value="1",
            bir1_value="430036025",
            resolution="bir1_precedence",
            source_document_hash=SHA,
            ingestion_run_id=run_id,
            created_at=NOW,
        ),
    )
    manifest.insert_quarantine(
        conn,
        [
            QuarantineRecord(
                stage="A2",
                entity_key="0000000000",
                reason_code="natural_person",
                detail="nothing persisted",
                source_document_hash=None,  # NULL must still dedupe
                ingestion_run_id=run_id,
                created_at=NOW,
                krs="0000000000",
                document_ref=None,
            )
        ],
    )


def test_ensure_schema_is_idempotent(conn: psycopg.Connection):
    manifest.ensure_schema(conn)
    manifest.ensure_schema(conn)

    assert manifest.table_counts(conn) == {
        "raw_documents": 0,
        "raw_document_fetches": 0,
        "universe_candidates": 0,
        "entity_master": 0,
        "entity_reconciliation_log": 0,
        "quarantine_events": 0,
        "filing_index": 0,
        "legal_source_fetches": 0,
    }


def test_reinserts_are_noops(conn: psycopg.Connection):
    manifest.ensure_schema(conn)
    _write_everything(conn, "run-1")
    first = manifest.table_counts(conn)

    _write_everything(conn, "run-1")

    assert manifest.table_counts(conn) == first
    assert first == {
        "raw_documents": 2,
        "raw_document_fetches": 2,
        "universe_candidates": 2,
        "entity_master": 1,
        "entity_reconciliation_log": 1,
        "quarantine_events": 1,
        "filing_index": 0,
        "legal_source_fetches": 0,
    }


def test_repeat_fetch_in_a_new_run_keeps_lineage_without_new_document(conn: psycopg.Connection):
    manifest.ensure_schema(conn)
    manifest.insert_raw_fetch(conn, _fetch(SHA, "run-1"))
    manifest.insert_raw_fetch(conn, _fetch(SHA, "run-2"))

    counts = manifest.table_counts(conn)
    assert counts["raw_documents"] == 1
    assert counts["raw_document_fetches"] == 2


def _a4(krs: str, run_id: str, sha: str, *, stored_new: bool, at: datetime) -> A4Result:
    meta = RawDocumentMeta(
        source="krs_api",
        source_url=f"https://api-krs.ms.gov.pl/api/krs/OdpisPelny/{krs}?rejestr=P&format=json",
        content_type="application/json",
        fetched_at=at,
        http_headers={},
        ingestion_run_id=run_id,
        redaction_version="krs-json-1",
        received_sha256="ef" * 32 if stored_new else None,
    )
    return A4Result(
        krs,
        raw_fetches=[RawFetchRecord(sha256=sha, byte_size=10, meta=meta)] if stored_new else [],
        fetch=LegalSourceFetch(
            krs=krs, source="KRS", sha256=sha, content_sha256="c1" * 32,
            fetched_at=at, ingestion_run_id=run_id, stored_new=stored_new,
        ),
    )


def test_a4_fetches_reuse_unchanged_content_and_track_the_latest(conn: psycopg.Connection):
    manifest.ensure_schema(conn)
    krs = "0000000042"
    assert manifest.latest_legal_fetch(conn, krs, "KRS") is None
    manifest.record_a4_result(conn, _a4(krs, "run-1", SHA, stored_new=True, at=NOW))
    manifest.record_a4_result(conn, _a4(krs, "run-1", SHA, stored_new=True, at=NOW))  # re-run: no-op
    later = NOW.replace(hour=13)
    manifest.record_a4_result(conn, _a4(krs, "run-2", SHA, stored_new=False, at=later))

    counts = manifest.table_counts(conn)
    assert counts["raw_documents"] == 1
    assert counts["legal_source_fetches"] == 2
    assert manifest.latest_legal_fetch(conn, krs, "KRS") == PreviousFetch(SHA, "c1" * 32)
    redactions = conn.execute("SELECT redaction_version FROM raw_redactions").fetchall()
    assert redactions == [("krs-json-1",)]


def test_a4_not_found_is_quarantined_with_the_entity(conn: psycopg.Connection):
    manifest.ensure_schema(conn)
    record = QuarantineRecord(
        stage="A4", entity_key="0000000001", reason_code="krs_extract_not_found",
        detail="HTTP 404", source_document_hash=None, ingestion_run_id="run-1",
        created_at=NOW, krs="0000000001", document_ref=None,
    )
    manifest.record_a4_result(conn, A4Result("0000000001", quarantine=[record]))
    row = conn.execute("SELECT stage, krs, reason_code FROM quarantine_events").fetchone()
    assert row == ("A4", "0000000001", "krs_extract_not_found")


def test_unresolved_candidates_excludes_resolved_entities(conn: psycopg.Connection):
    manifest.ensure_schema(conn)
    _write_everything(conn, "run-1")

    assert manifest.unresolved_candidates(conn) == [("9999999999", None, None)]


# --- A3 filing_index ---------------------------------------------------------------------------

LIST_SHA = "ef" * 32
DOC_SHA = "12" * 32
CORR_SHA = "34" * 32
DETAIL_SHA = "56" * 32
OTHER_DETAIL_SHA = "78" * 32
KRS = "0000163893"
SCOPE = ["18"]


def _row(ref: str, code: str, period_end: date, status: str = "NIEUSUNIETY") -> FilingIndexRow:
    return FilingIndexRow(
        krs=KRS,
        document_ref=ref,
        rdf_type_code=code,
        status=status,  # type: ignore[arg-type]
        period_start=date(period_end.year, 1, 1),
        period_end=period_end,
        deleted_on=date(2026, 1, 5) if status == "USUNIETY" else None,
        discovered_at=NOW,
        ingestion_run_id="run-1",
    )


def _index_result(run_id: str) -> A3IndexResult:
    return A3IndexResult(
        krs=KRS,
        raw_fetches=[_fetch(LIST_SHA, run_id)],
        entries=[
            _row("aud-2025", "19", date(2025, 12, 31)),
            _row("sf-2024", "18", date(2024, 12, 31)),
            _row("sf-2025", "18", date(2025, 12, 31)),
            _row("sf-2023-deleted", "18", date(2023, 12, 31), status="USUNIETY"),
        ],
    )


def _filing_detail(
    ref: str, type_id: str, submitted: date, bundle: list[str] | None = None, **extra: Any
) -> FilingDetail:
    return FilingDetail(
        document_ref=ref,
        rdf_type_id=type_id,
        rdf_type_name="Roczne sprawozdanie finansowe" if type_id == "18" else "Opinia",
        submission_date=submitted,
        prepared_date=date(2026, 5, 28),
        is_correction=extra.pop("is_correction", False),
        is_ifrs=False,
        file_name=f"{ref}.xml",
        correction_refs=bundle or [ref],
        **extra,
    )


def _detail(
    ref: str,
    type_id: str,
    detail_sha: str,
    submitted: date,
    related: list[tuple[FilingDetail, RawFetchRecord]] | None = None,
) -> A3Detail:
    bundle = [ref, *(d.document_ref for d, _ in related or [])]
    return A3Detail(
        krs=KRS,
        detail=_filing_detail(ref, type_id, submitted, bundle),
        corrections_fetch=_fetch(CORR_SHA, "run-2"),
        detail_fetch=_fetch(detail_sha, "run-2"),
        related=related or [],
    )


def _pending(conn: psycopg.Connection, **kwargs: bool) -> list[tuple[str, str | None, bool]]:
    return [
        (p.document_ref, p.rdf_type_id, p.downloaded)
        for p in manifest.pending_filing_documents(conn, SCOPE, **kwargs)
    ]


def test_filing_index_reinserts_are_noops_and_counted(conn: psycopg.Connection):
    manifest.ensure_schema(conn)
    manifest.ensure_schema(conn)
    _write_everything(conn, "run-1")
    manifest.record_a3_index_result(conn, _index_result("run-1"))
    first = manifest.table_counts(conn)

    manifest.record_a3_index_result(conn, _index_result("run-1"))

    assert manifest.table_counts(conn) == first
    assert first["filing_index"] == 4


def test_pending_documents_put_download_scope_first_and_skip_deleted(conn: psycopg.Connection):
    manifest.ensure_schema(conn)
    _write_everything(conn, "run-1")
    assert manifest.unindexed_entities(conn) == [KRS]

    manifest.record_a3_index_result(conn, _index_result("run-1"))

    assert manifest.unindexed_entities(conn) == []
    assert _pending(conn) == [
        ("sf-2025", None, False),
        ("sf-2024", None, False),
        ("aud-2025", None, False),
    ]
    assert _pending(conn, download_scope_only=True) == [
        ("sf-2025", None, False),
        ("sf-2024", None, False),
    ]


def test_detail_and_download_settle_rows_once(conn: psycopg.Connection):
    manifest.ensure_schema(conn)
    _write_everything(conn, "run-1")
    manifest.record_a3_index_result(conn, _index_result("run-1"))

    # an out-of-scope document is settled by its detail alone
    manifest.record_a3_detail(conn, _detail("aud-2025", "19", OTHER_DETAIL_SHA, date(2026, 6, 29)))
    # an in-scope one still owes its download
    manifest.record_a3_detail(conn, _detail("sf-2025", "18", DETAIL_SHA, date(2026, 6, 29)))
    manifest.record_a3_detail(conn, _detail("sf-2025", "18", OTHER_DETAIL_SHA, date(2030, 1, 1)))
    assert _pending(conn) == [("sf-2025", "18", False), ("sf-2024", None, False)]
    row = conn.execute(
        """
        SELECT rdf_type_name, submission_date, file_name, correction_refs, detail_sha256
        FROM filing_index WHERE document_ref = 'sf-2025'
        """
    ).fetchone()
    assert row == (
        "Roczne sprawozdanie finansowe",
        date(2026, 6, 29),  # the first detail stands; a later one does not overwrite it
        "sf-2025.xml",
        ["sf-2025"],
        DETAIL_SHA,
    )

    download = A3Download(krs=KRS, document_refs=["sf-2025"], raw_fetch=_fetch(DOC_SHA, "run-2"))
    manifest.record_a3_download(conn, download)
    manifest.record_a3_download(conn, download)
    assert _pending(conn) == [("sf-2024", None, False)]
    row = conn.execute("SELECT sha256 FROM filing_index WHERE document_ref = 'sf-2025'").fetchone()
    assert row == (DOC_SHA,)


def test_no_rdf_filings_quarantine_marks_entity_indexed(conn: psycopg.Connection):
    manifest.ensure_schema(conn)
    _write_everything(conn, "run-1")
    manifest.record_a3_index_result(
        conn,
        A3IndexResult(
            krs=KRS,
            raw_fetches=[_fetch(LIST_SHA, "run-1")],
            quarantine=[
                QuarantineRecord(
                    stage="A3",
                    entity_key=KRS,
                    reason_code="no_rdf_filings",
                    detail="RDF filing list is empty",
                    source_document_hash=LIST_SHA,
                    ingestion_run_id="run-1",
                    created_at=NOW,
                    krs=KRS,
                    document_ref=None,
                )
            ],
        ),
    )

    assert manifest.unindexed_entities(conn) == []


CORRECTION_DETAIL_SHA = "9a" * 32


def test_corrections_get_their_own_rows_and_share_the_bundle(conn: psycopg.Connection):
    manifest.ensure_schema(conn)
    _write_everything(conn, "run-1")
    manifest.record_a3_index_result(conn, _index_result("run-1"))
    # sf-2025 was detailed before corrections were handled: its row lacks one.
    manifest.record_a3_detail(conn, _detail("sf-2025", "18", DETAIL_SHA, date(2026, 6, 29)))
    conn.execute(
        "UPDATE filing_index SET correction_refs = %s WHERE document_ref = 'sf-2025'",
        (Jsonb(["sf-2025", "sf-2025-k"]),),
    )
    assert _pending(conn)[0] == ("sf-2025", "18", False)
    assert manifest.pending_filing_documents(conn, SCOPE)[0].needs_detail is True

    correction = _filing_detail(
        "sf-2025-k",
        "18",
        date(2026, 8, 1),
        ["sf-2025", "sf-2025-k"],
        is_correction=True,
        status="NIEUSUNIETY",
        period_end=date(2025, 12, 31),
    )
    expanded = _detail(
        "sf-2025",
        "18",
        DETAIL_SHA,
        date(2026, 6, 29),
        related=[(correction, _fetch(CORRECTION_DETAIL_SHA, "run-3"))],
    )
    manifest.record_a3_detail(conn, expanded)
    manifest.record_a3_detail(conn, expanded)

    row = conn.execute(
        """
        SELECT rdf_type_code, status, period_start, period_end, correction_of,
               is_correction, submission_date, detail_sha256, ingestion_run_id
        FROM filing_index WHERE document_ref = 'sf-2025-k'
        """
    ).fetchone()
    assert row == (
        "18",
        "NIEUSUNIETY",
        date(2025, 1, 1),  # not in the detail: taken from the corrected document
        date(2025, 12, 31),
        "sf-2025",
        True,
        date(2026, 8, 1),
        CORRECTION_DETAIL_SHA,
        "run-3",
    )
    pending = manifest.pending_filing_documents(conn, SCOPE)
    assert [(p.document_ref, p.needs_detail, p.download_ref) for p in pending[:2]] == [
        ("sf-2025", False, "sf-2025"),
        ("sf-2025-k", False, "sf-2025"),
    ]
    assert pending[1].bundle == ["sf-2025", "sf-2025-k"]

    bundle = A3Download(
        krs=KRS, document_refs=["sf-2025", "sf-2025-k"], raw_fetch=_fetch(DOC_SHA, "run-3")
    )
    manifest.record_a3_download(conn, bundle)
    shas = conn.execute(
        "SELECT document_ref, sha256 FROM filing_index WHERE document_ref LIKE 'sf-2025%' ORDER BY 1"
    ).fetchall()
    assert shas == [("sf-2025", DOC_SHA), ("sf-2025-k", DOC_SHA)]
    assert [p.document_ref for p in manifest.pending_filing_documents(conn, SCOPE)] == [
        "sf-2024",
        "aud-2025",  # still owed its detail
    ]
