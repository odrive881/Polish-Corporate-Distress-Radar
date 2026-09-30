"""The response store's Postgres manifest against a live Postgres (`make dev-up`; run via
`make test-integration`). Each test runs in a throwaway schema."""

import uuid
from collections.abc import Iterator
from datetime import UTC, datetime

import psycopg
import pytest

from distress_radar.extraction import response_store
from distress_radar.settings import Settings

pytestmark = pytest.mark.integration

NOW = datetime(2026, 9, 30, 12, 0, tzinfo=UTC)


@pytest.fixture
def conn() -> Iterator[psycopg.Connection]:
    schema = f"test_responses_{uuid.uuid4().hex[:12]}"
    with psycopg.connect(Settings().postgres_conninfo, autocommit=True) as connection:
        connection.execute(f'CREATE SCHEMA "{schema}"')  # type: ignore[arg-type]
        connection.execute(f'SET search_path TO "{schema}"')  # type: ignore[arg-type]
        try:
            yield connection
        finally:
            connection.execute(f'DROP SCHEMA "{schema}" CASCADE')  # type: ignore[arg-type]


def _meta(key: str, stop_reason: str) -> response_store.ResponseMeta:
    return response_store.ResponseMeta(
        key, "claude-opus-5-5", "litigation_v1", "1", "e" * 64, stop_reason, NOW
    )


def test_a_manifest_row_is_written_once_and_never_overwritten(conn: psycopg.Connection) -> None:
    response_store.ensure_schema(conn)
    response_store.ensure_schema(conn)
    key = "a" * 64
    assert response_store.record(conn, [_meta(key, "end_turn")]) == 1
    response_store.record(conn, [_meta(key, "refusal")])
    rows = conn.execute(
        "SELECT request_key, object_key, stop_reason FROM extraction_responses"
    ).fetchall()
    assert rows == [(key, response_store.object_key(key), "end_turn")]


def test_a_file_keeps_its_first_run_under_one_pipeline_and_gets_a_new_one_under_another(
    conn: psycopg.Connection,
) -> None:
    from datetime import date

    from distress_radar.acquisition import manifest as acquisition_manifest
    from distress_radar.acquisition.models import RawFetchRecord
    from distress_radar.acquisition.raw_store import RawDocumentMeta
    from distress_radar.extraction import manifest

    acquisition_manifest.ensure_schema(conn)
    manifest.ensure_schema(conn)
    sha = "ab" * 32
    acquisition_manifest.insert_raw_fetch(
        conn,
        RawFetchRecord(
            sha256=sha,
            byte_size=1,
            meta=RawDocumentMeta(
                source="rdf",
                source_url="https://rdf.test",
                content_type="application/zip",
                fetched_at=NOW,
                http_headers={},
                ingestion_run_id="fetch",
            ),
        ),
    )
    source = manifest.TextSource(
        sha, "zip:1.xml", "raw/x", "0000000001", "ref1", date(2023, 12, 31), date(2024, 6, 30)
    )
    assert manifest.record_text_extraction(conn, source, "p1", "run-1", NOW) == "run-1"
    assert manifest.record_text_extraction(conn, source, "p1", "run-2", NOW) == "run-1"
    assert manifest.record_text_extraction(conn, source, "p2", "run-3", NOW) == "run-3"
