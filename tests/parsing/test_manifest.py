"""C1 manifest against a live Postgres (`make dev-up`; run via `make test-integration`).

Each test runs in a throwaway schema so it never touches real manifest data.
"""

import uuid
from collections.abc import Iterator
from datetime import UTC, date, datetime

import psycopg
import pytest

from distress_radar.acquisition import manifest as acquisition_manifest
from distress_radar.parsing import manifest
from distress_radar.settings import Settings

pytestmark = pytest.mark.integration

NOW = datetime(2026, 9, 17, 12, 0, tzinfo=UTC)
SHA = "ef" * 32


@pytest.fixture
def conn() -> Iterator[psycopg.Connection]:
    schema = f"test_parsing_{uuid.uuid4().hex[:12]}"
    with psycopg.connect(Settings().postgres_conninfo, autocommit=True) as connection:
        connection.execute(f'CREATE SCHEMA "{schema}"')  # type: ignore[arg-type]
        connection.execute(f'SET search_path TO "{schema}"')  # type: ignore[arg-type]
        try:
            acquisition_manifest.ensure_schema(connection)
            manifest.ensure_schema(connection)
            manifest.ensure_schema(connection)  # idempotent DDL
            connection.execute(
                "INSERT INTO raw_documents VALUES (%s, %s, 1, 'application/zip', %s, 'run-a')",
                (SHA, f"raw/sha256/ef/{SHA}", NOW),
            )
            yield connection
        finally:
            connection.execute(f'DROP SCHEMA "{schema}" CASCADE')  # type: ignore[arg-type]


def _row(status: manifest.ParseStatus, spec_hash: str = "h1") -> manifest.ParsedDocumentRow:
    return manifest.ParsedDocumentRow(
        sha256=SHA,
        source_member="zip:a.xml",
        spec_hash=spec_hash,
        krs="0000498679",
        document_ref="ref",
        member_kind="xml_statement",
        structure_key="k",
        structure_version="full-2018-v1-2",
        status=status,
    )


def test_first_run_id_is_kept_and_status_updated(conn: psycopg.Connection) -> None:
    assert manifest.record_parsed_document(conn, _row("valid"), "run-a", NOW) == "run-a"
    assert manifest.record_parsed_document(conn, _row("quarantined"), "run-b", NOW) == "run-a"
    rows = conn.execute("SELECT status, first_ingestion_run_id FROM parsed_documents").fetchall()
    assert rows == [("quarantined", "run-a")]


def test_new_spec_hash_is_a_new_row(conn: psycopg.Connection) -> None:
    manifest.record_parsed_document(conn, _row("valid"), "run-a", NOW)
    assert (
        manifest.record_parsed_document(conn, _row("valid", spec_hash="h2"), "run-b", NOW)
        == "run-b"
    )
    count = conn.execute("SELECT count(*) FROM parsed_documents").fetchone()
    assert count == (2,)


def test_statement_sources_group_rows_by_stored_file(conn: psycopg.Connection) -> None:
    conn.execute(
        "INSERT INTO entity_master (krs, nip, regon, name, legal_form_code, status, pkd_codes, "
        "pkd_predominant, source_document_hash, pkd_source_document_hash, known_from, ingestion_run_id) "
        "VALUES ('0000498679', '5892013083', '192796505', 'X', '117', 'active', '[]', '4120Z', "
        "%s, %s, '2026-09-14', 'run-a')",
        (SHA, SHA),
    )
    for ref, name, deleted, code in (
        ("orig", "sf.xml", None, "18"),
        ("corr", "sf_k.xml", None, "18"),
        ("gone", "old.xml", date(2024, 1, 1), "18"),
        ("audit", "op.pdf", None, "19"),
    ):
        conn.execute(
            "INSERT INTO filing_index (krs, document_ref, rdf_type_code, status, period_start, period_end, "
            "deleted_on, file_name, submission_date, sha256, discovered_at, ingestion_run_id) "
            "VALUES ('0000498679', %s, %s, 'x', '2022-01-01', '2022-12-31', %s, %s, '2023-06-30', %s, %s, 'run-a')",
            (ref, code, deleted, name, SHA, NOW),
        )
    [source] = manifest.statement_sources(conn, ["18"])
    assert (source.sha256, source.krs, source.nip) == (SHA, "0000498679", "5892013083")
    assert [r[0] for r in source.rows] == ["corr", "orig"]
    assert source.rows[1] == (
        "orig",
        "sf.xml",
        date(2023, 6, 30),
        date(2022, 1, 1),
        date(2022, 12, 31),
    )
