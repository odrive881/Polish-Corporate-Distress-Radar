"""ADR 0009 migration against a live Postgres (`make dev-up`; `make test-integration`).

Runs in a throwaway schema with an in-memory object store.
"""

import io
import json
import uuid
import zipfile
from collections.abc import Iterator
from datetime import UTC, datetime

import psycopg
import pytest

from distress_radar.acquisition import manifest
from distress_radar.acquisition.models import RawFetchRecord
from distress_radar.acquisition.raw_store import (
    InMemoryObjectStore,
    RawDocumentMeta,
    put_raw,
    raw_key,
    sidecar_key,
)
from distress_radar.acquisition.redaction import REDACTION_VERSION, personal_data_markers
from distress_radar.acquisition.redaction_migration import find_unredacted, replace_document
from distress_radar.parsing import manifest as parsing_manifest
from distress_radar.settings import Settings

pytestmark = pytest.mark.integration

NOW = datetime(2026, 9, 17, 12, 0, tzinfo=UTC)
DS = "http://www.w3.org/2000/09/xmldsig#"
CLEAN = b"<?xml version='1.0'?><JednostkaInna xmlns='urn:sf'><Bilans>1.00</Bilans></JednostkaInna>"
SIGNED = CLEAN.replace(
    b"</JednostkaInna>",
    f"<ds:Signature xmlns:ds='{DS}'><os:PESEL xmlns:os='urn:os'>00000000000</os:PESEL>"
    "</ds:Signature></JednostkaInna>".encode(),
)


REF = "AAAAAAAAAAAAAAAAAAAA/w=="  # shaped like an RDF document_ref
TOKEN = "AAAAAAAAAAAAAAAAAAAA_w.xml"  # its file name as stored (ADR 0009 second addendum)


def _zip(data: bytes, name: str = "sf.xml") -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(zipfile.ZipInfo(name, date_time=(2023, 6, 30, 0, 0, 0)), data)
    return buffer.getvalue()


def _meta(url: str) -> RawDocumentMeta:
    return RawDocumentMeta(
        source="rdf",
        source_url=url,
        content_type="application/octet-stream",
        fetched_at=NOW,
        http_headers={},
        ingestion_run_id="run-a",
        original_filename="sf.xml",
    )


@pytest.fixture
def conn() -> Iterator[psycopg.Connection]:
    schema = f"test_redaction_{uuid.uuid4().hex[:12]}"
    with psycopg.connect(Settings().postgres_conninfo) as connection:
        connection.execute(f'CREATE SCHEMA "{schema}"')  # type: ignore[arg-type]
        connection.execute(f'SET search_path TO "{schema}"')  # type: ignore[arg-type]
        try:
            manifest.ensure_schema(connection)
            parsing_manifest.ensure_schema(connection)
            connection.commit()
            yield connection
        finally:
            connection.rollback()
            connection.execute(f'DROP SCHEMA "{schema}" CASCADE')  # type: ignore[arg-type]
            connection.commit()


def _record(conn: psycopg.Connection, store: InMemoryObjectStore, raw: bytes, url: str) -> str:
    sha = put_raw(store, raw, _meta(url))
    manifest.insert_raw_fetch(
        conn,
        RawFetchRecord(sha256=sha, byte_size=len(raw), meta=_meta(url)),
    )
    return sha


def test_signed_document_is_replaced_and_references_follow(conn: psycopg.Connection) -> None:
    store = InMemoryObjectStore()
    signed_sha = _record(conn, store, _zip(SIGNED), "https://rdf/tresc#1")
    clean_sha = _record(conn, store, _zip(CLEAN, TOKEN), "https://rdf/tresc#2")
    conn.execute(
        "INSERT INTO entity_master (krs, nip, regon, name, legal_form_code, status, pkd_codes, "
        "pkd_predominant, source_document_hash, pkd_source_document_hash, known_from, ingestion_run_id) "
        "VALUES ('0000000001', NULL, '1', 'X', '117', 'active', '[]', '4120Z', %s, %s, '2026-09-14', 'run-a')",
        (clean_sha, clean_sha),
    )
    conn.execute(
        "INSERT INTO filing_index (krs, document_ref, rdf_type_code, status, period_start, period_end, "
        "sha256, discovered_at, ingestion_run_id) "
        "VALUES ('0000000001', %s, '18', 'x', '2022-01-01', '2022-12-31', %s, %s, 'run-a')",
        (REF, signed_sha, NOW),
    )
    conn.execute(
        "INSERT INTO quarantine_events (stage, entity_key, reason_code, detail, "
        "source_document_hash, ingestion_run_id, created_at, krs, document_ref) "
        "VALUES ('E2', '0000000001:ref', 'profit_ties', 'd', %s, 'run-a', %s, "
        "'0000000001', 'ref')",
        (signed_sha, NOW),
    )
    parsing_manifest.record_parsed_document(
        conn,
        parsing_manifest.ParsedDocumentRow(
            sha256=signed_sha,
            source_member="zip:sf.xml",
            spec_hash="h",
            krs="0000000001",
            document_ref="ref",
            member_kind="xml_statement",
            structure_key=None,
            structure_version=None,
            status="valid",
        ),
        "run-a",
        NOW,
    )
    conn.commit()

    assert find_unredacted(conn, store) == [signed_sha]
    replaced = replace_document(conn, store, signed_sha, run_id="redaction-1", now=NOW)
    new = replaced.new_sha256

    assert personal_data_markers(store.get(raw_key(new))) == []
    assert zipfile.ZipFile(io.BytesIO(store.get(raw_key(new)))).namelist() == [TOKEN]
    assert not store.exists(raw_key(signed_sha)) and not store.exists(sidecar_key(signed_sha))
    assert not any(b"00000000000" in data for data in store.objects.values())
    sidecar = json.loads(store.get(sidecar_key(new)))
    assert (sidecar["redaction_version"], sidecar["received_sha256"]) == (
        REDACTION_VERSION,
        signed_sha,
    )
    assert conn.execute("SELECT sha256 FROM filing_index").fetchall() == [(new,)]
    assert conn.execute("SELECT source_document_hash FROM quarantine_events").fetchall() == [(new,)]
    assert conn.execute("SELECT count(*) FROM parsed_documents").fetchone() == (0,)
    assert conn.execute(
        "SELECT sha256, source_url FROM raw_document_fetches ORDER BY source_url"
    ).fetchall() == [(new, "https://rdf/tresc#1"), (clean_sha, "https://rdf/tresc#2")]
    assert conn.execute("SELECT sha256 FROM raw_documents ORDER BY sha256").fetchall() == sorted(
        [(new,), (clean_sha,)]
    )
    assert conn.execute(
        "SELECT received_sha256, redacted_sha256 FROM raw_redactions"
    ).fetchall() == [(signed_sha, new)]
    assert find_unredacted(conn, store) == []
