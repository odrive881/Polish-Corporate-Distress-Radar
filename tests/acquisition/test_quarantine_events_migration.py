"""The `quarantine` → `quarantine_events` migration (plan 0007 step C, decision 3).

Against a live Postgres (`make dev-up`; run via `make test-integration`), each
test in a throwaway schema. A database from before plan 0007 has a table named
`quarantine` with no `krs` / `document_ref`; `ensure_schema` must rename it in
place, keep every row, backfill both columns from the stage-specific
`entity_key`, and fail rather than skip on a key in no known format.
"""

import uuid
from collections.abc import Iterator
from datetime import UTC, datetime

import psycopg
import pytest

from distress_radar.acquisition import manifest
from distress_radar.acquisition.models import QuarantineRecord
from distress_radar.settings import Settings

pytestmark = pytest.mark.integration

NOW = datetime(2026, 9, 22, 12, 0, tzinfo=UTC)
SHA = "5a" * 32
KRS = "0000498679"
REF = "fZg20MFFBuh3c-Ks1yNHNg=="

# The table as every database before plan 0007 has it.
LEGACY_DDL = """
    CREATE TABLE quarantine (
        stage                 text NOT NULL,
        entity_key            text NOT NULL,
        reason_code           text NOT NULL,
        detail                text NOT NULL,
        source_document_hash  text,
        ingestion_run_id      text NOT NULL,
        created_at            timestamptz NOT NULL,
        UNIQUE NULLS NOT DISTINCT (stage, entity_key, reason_code, source_document_hash)
    )
"""

# (stage, entity_key) as each legacy writer built it, and the backfill expected.
LEGACY_ROWS: list[tuple[str, str, str | None, str | None]] = [
    ("A1", "seed.yaml#3", None, None),  # malformed entry: no KRS to record
    ("A1", "0000000123", "0000000123", None),  # well-formed KRS, e.g. a duplicate
    ("A2", "0000163893", "0000163893", None),
    ("A3", KRS, KRS, None),
    ("C1", f"{KRS}:{REF}", KRS, REF),
    ("C1", f"{KRS}:zip:SF.xml", KRS, None),  # no filing row: source_member, not a ref
    ("C2", f"{KRS}:{REF}", KRS, REF),
    ("E2", f"{KRS}:{REF}", KRS, REF),
]


@pytest.fixture
def conn() -> Iterator[psycopg.Connection]:
    schema = f"test_qe_{uuid.uuid4().hex[:12]}"
    with psycopg.connect(Settings().postgres_conninfo, autocommit=True) as connection:
        connection.execute(f'CREATE SCHEMA "{schema}"')  # type: ignore[arg-type]
        connection.execute(f'SET search_path TO "{schema}"')  # type: ignore[arg-type]
        try:
            yield connection
        finally:
            connection.execute(f'DROP SCHEMA "{schema}" CASCADE')  # type: ignore[arg-type]


def _legacy_database(conn: psycopg.Connection, rows: list[tuple[str, str]]) -> None:
    """Every other table as current, `quarantine` as it was, with one filing row."""
    manifest.ensure_schema(conn)
    conn.execute("DROP TABLE quarantine_events")
    conn.execute(LEGACY_DDL)  # type: ignore[arg-type]
    conn.execute(
        "INSERT INTO raw_documents VALUES (%s, 'raw/x', 1, 'application/zip', %s, 'run-a')",
        (SHA, NOW),
    )
    conn.execute(
        "INSERT INTO entity_master (krs, nip, regon, name, legal_form_code, status, pkd_codes, "
        "pkd_predominant, source_document_hash, pkd_source_document_hash, known_from, "
        "ingestion_run_id) VALUES (%s, NULL, '1', 'X', '117', 'active', '[]', NULL, %s, %s, "
        "'2026-09-14', 'run-a')",
        (KRS, SHA, SHA),
    )
    conn.execute(
        "INSERT INTO filing_index (krs, document_ref, rdf_type_code, status, period_start, "
        "period_end, discovered_at, ingestion_run_id) "
        "VALUES (%s, %s, '18', 'x', '2022-01-01', '2022-12-31', %s, 'run-a')",
        (KRS, REF, NOW),
    )
    for stage, key in rows:
        conn.execute(
            "INSERT INTO quarantine VALUES (%s, %s, 'reason', 'detail', %s, 'run-a', %s)",
            (stage, key, SHA, NOW),
        )


def test_rename_keeps_every_row_and_backfills_each_key_format(conn: psycopg.Connection) -> None:
    _legacy_database(conn, [(stage, key) for stage, key, _, _ in LEGACY_ROWS])
    before = conn.execute("SELECT count(*) FROM quarantine").fetchone()

    manifest.ensure_schema(conn)
    manifest.ensure_schema(conn)  # idempotent: a second run changes nothing

    assert conn.execute("SELECT to_regclass('quarantine')").fetchone() == (None,)
    assert conn.execute("SELECT count(*) FROM quarantine_events").fetchone() == before
    backfilled = conn.execute(
        "SELECT stage, entity_key, krs, document_ref FROM quarantine_events "
        "ORDER BY stage, entity_key"
    ).fetchall()
    assert backfilled == sorted(LEGACY_ROWS)


def test_rows_keep_their_detection_columns(conn: psycopg.Connection) -> None:
    _legacy_database(conn, [("E2", f"{KRS}:{REF}")])
    manifest.ensure_schema(conn)
    assert conn.execute(
        "SELECT reason_code, detail, source_document_hash, ingestion_run_id, created_at "
        "FROM quarantine_events"
    ).fetchall() == [("reason", "detail", SHA, "run-a", NOW)]


@pytest.mark.parametrize(
    ("stage", "key"),
    [
        ("A2", "not-a-krs"),
        ("C1", KRS),  # a parsing-stage key always carries `:<ref or member>`
        ("E2", f"{KRS}:unknown-ref"),  # E2/C2 always have a filing behind them
    ],
)
def test_a_key_in_no_known_format_fails_the_migration(
    conn: psycopg.Connection, stage: str, key: str
) -> None:
    _legacy_database(conn, [(stage, key)])
    with pytest.raises(psycopg.errors.RaiseException, match="matches no known format"):
        manifest.ensure_schema(conn)


def test_writers_record_krs_and_document_ref_after_the_migration(
    conn: psycopg.Connection,
) -> None:
    _legacy_database(conn, [])
    manifest.ensure_schema(conn)
    manifest.insert_quarantine(
        conn,
        [
            QuarantineRecord(
                stage="C1",
                entity_key=f"{KRS}:zip:SF.xml",
                reason_code="member_not_in_filing_index",
                detail="d",
                source_document_hash=SHA,
                ingestion_run_id="run-b",
                created_at=NOW,
                krs=KRS,
                document_ref=None,
            )
        ],
    )
    manifest.ensure_schema(conn)  # the backfill leaves writer-filled rows alone
    assert conn.execute("SELECT krs, document_ref FROM quarantine_events").fetchall() == [
        (KRS, None)
    ]
    assert manifest.table_counts(conn)["quarantine_events"] == 1
