"""SQLMesh's Postgres external models against the real DDL (plan 0007 step C).

`transform/external_models.yaml` declares the columns of the manifest tables
the `ext` views read. This builds the manifest schema in a throwaway Postgres
schema (`make dev-up`; run via `make test-integration`) and compares, so a
column added to the DDL cannot go missing on the SQL side.
"""

import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import psycopg
import pytest
import yaml

from distress_radar.acquisition import manifest as acquisition_manifest
from distress_radar.parsing import manifest as parsing_manifest
from distress_radar.settings import Settings

pytestmark = pytest.mark.integration

TRANSFORM = Path(__file__).parent.parent.parent / "transform"
POSTGRES_TABLES = ("quarantine_events", "parsed_documents")
# information_schema.data_type → the type DuckDB's postgres extension reads it as.
DUCKDB_TYPES = {
    "text": "TEXT",
    "character": "TEXT",
    "timestamp with time zone": "TIMESTAMPTZ",
}


@pytest.fixture
def conn() -> Iterator[psycopg.Connection]:
    schema = f"test_ext_{uuid.uuid4().hex[:12]}"
    with psycopg.connect(Settings().postgres_conninfo, autocommit=True) as connection:
        connection.execute(f'CREATE SCHEMA "{schema}"')  # type: ignore[arg-type]
        connection.execute(f'SET search_path TO "{schema}"')  # type: ignore[arg-type]
        try:
            acquisition_manifest.ensure_schema(connection)
            parsing_manifest.ensure_schema(connection)
            yield connection
        finally:
            connection.execute(f'DROP SCHEMA "{schema}" CASCADE')  # type: ignore[arg-type]


def test_postgres_external_models_match_the_ddl(conn: psycopg.Connection) -> None:
    models: list[dict[str, Any]] = yaml.safe_load((TRANSFORM / "external_models.yaml").read_text())
    declared = {m["name"].removeprefix("ext."): m["columns"] for m in models}
    for table in POSTGRES_TABLES:
        actual = conn.execute(
            "SELECT column_name, data_type FROM information_schema.columns "
            "WHERE table_schema = current_schema() AND table_name = %s ORDER BY ordinal_position",
            (table,),
        ).fetchall()
        assert declared[table] == {name: DUCKDB_TYPES[dtype] for name, dtype in actual}, table
