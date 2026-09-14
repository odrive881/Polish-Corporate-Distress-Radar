"""B2 manifest against a live Postgres (`make dev-up`; run via `make test-integration`).

Each test runs in a throwaway schema so it never touches real manifest data.
"""

import uuid
from collections.abc import Iterator
from datetime import UTC, date, datetime

import psycopg
import pytest

from distress_radar.acquisition import manifest
from distress_radar.acquisition.models import (
    Bir1PkdCode,
    EntityMasterRow,
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
        "quarantine": 0,
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
        "quarantine": 1,
    }


def test_repeat_fetch_in_a_new_run_keeps_lineage_without_new_document(conn: psycopg.Connection):
    manifest.ensure_schema(conn)
    manifest.insert_raw_fetch(conn, _fetch(SHA, "run-1"))
    manifest.insert_raw_fetch(conn, _fetch(SHA, "run-2"))

    counts = manifest.table_counts(conn)
    assert counts["raw_documents"] == 1
    assert counts["raw_document_fetches"] == 2


def test_unresolved_candidates_excludes_resolved_entities(conn: psycopg.Connection):
    manifest.ensure_schema(conn)
    _write_everything(conn, "run-1")

    assert manifest.unresolved_candidates(conn) == [("9999999999", None, None)]
