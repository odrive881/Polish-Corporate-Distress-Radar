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
