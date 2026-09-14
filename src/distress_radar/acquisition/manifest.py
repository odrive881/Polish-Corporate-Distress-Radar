"""B2 — manifest in Postgres (AGENT_SPEC.md §6B, ADR 0006).

Plain idempotent DDL (`CREATE ... IF NOT EXISTS`) applied by `ensure_schema()`;
no migration tool. Every insert is `ON CONFLICT DO NOTHING`, so re-running a
stage never duplicates rows. Every row carries `ingestion_run_id`.

Functions take an open `psycopg.Connection` and do not commit; the caller owns
the transaction (a Dagster asset commits once per materialization).
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import LiteralString

from psycopg import Connection, sql
from psycopg.types.json import Jsonb

from distress_radar.acquisition.models import (
    EntityMasterRow,
    QuarantineRecord,
    RawFetchRecord,
    ReconciliationRecord,
    UniverseCandidate,
)
from distress_radar.acquisition.raw_store import raw_key
from distress_radar.acquisition.regon_client import A2Result

SCHEMA_DDL: tuple[LiteralString, ...] = (
    """
    CREATE TABLE IF NOT EXISTS raw_documents (
        sha256                  text PRIMARY KEY,
        object_key              text NOT NULL,
        byte_size               bigint NOT NULL,
        content_type            text NOT NULL,
        first_fetched_at        timestamptz NOT NULL,
        first_ingestion_run_id  text NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS raw_document_fetches (
        sha256            text NOT NULL REFERENCES raw_documents (sha256),
        source_url        text NOT NULL,
        fetched_at        timestamptz NOT NULL,
        ingestion_run_id  text NOT NULL,
        source            text NOT NULL,
        PRIMARY KEY (sha256, source_url, ingestion_run_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS universe_candidates (
        krs               char(10) NOT NULL,
        discovery_source  text NOT NULL,
        discovered_at     timestamptz NOT NULL,
        ingestion_run_id  text NOT NULL,
        regon_hint        text,
        nip_hint          text,
        PRIMARY KEY (krs, discovery_source)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS entity_master (
        krs                   char(10) PRIMARY KEY,
        nip                   text,
        regon                 text NOT NULL,
        name                  text NOT NULL,
        legal_form_code       text NOT NULL,
        status                text NOT NULL,
        pkd_codes             jsonb NOT NULL,
        pkd_predominant       text,
        source_document_hash  text NOT NULL REFERENCES raw_documents (sha256),
        pkd_source_document_hash text NOT NULL REFERENCES raw_documents (sha256),
        known_from            date NOT NULL,
        ingestion_run_id      text NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS entity_reconciliation_log (
        krs                   char(10) NOT NULL,
        field                 text NOT NULL,
        seed_value            text,
        bir1_value            text,
        resolution            text NOT NULL,
        source_document_hash  text NOT NULL REFERENCES raw_documents (sha256),
        ingestion_run_id      text NOT NULL,
        created_at            timestamptz NOT NULL,
        UNIQUE NULLS NOT DISTINCT (krs, field, seed_value, bir1_value)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS quarantine (
        stage                 text NOT NULL,
        entity_key            text NOT NULL,
        reason_code           text NOT NULL,
        detail                text NOT NULL,
        source_document_hash  text,
        ingestion_run_id      text NOT NULL,
        created_at            timestamptz NOT NULL,
        UNIQUE NULLS NOT DISTINCT (stage, entity_key, reason_code, source_document_hash)
    )
    """,
)


def ensure_schema(conn: Connection) -> None:
    for statement in SCHEMA_DDL:
        conn.execute(statement)


def insert_raw_fetch(conn: Connection, record: RawFetchRecord) -> None:
    meta = record.meta
    conn.execute(
        """
        INSERT INTO raw_documents
            (sha256, object_key, byte_size, content_type, first_fetched_at, first_ingestion_run_id)
        VALUES (%s, %s, %s, %s, %s, %s)
        ON CONFLICT DO NOTHING
        """,
        (
            record.sha256,
            raw_key(record.sha256),
            record.byte_size,
            meta.content_type,
            meta.fetched_at,
            meta.ingestion_run_id,
        ),
    )
    conn.execute(
        """
        INSERT INTO raw_document_fetches (sha256, source_url, fetched_at, ingestion_run_id, source)
        VALUES (%s, %s, %s, %s, %s)
        ON CONFLICT DO NOTHING
        """,
        (record.sha256, meta.source_url, meta.fetched_at, meta.ingestion_run_id, meta.source),
    )


def insert_universe_candidates(conn: Connection, rows: Iterable[UniverseCandidate]) -> None:
    for row in rows:
        conn.execute(
            """
            INSERT INTO universe_candidates
                (krs, discovery_source, discovered_at, ingestion_run_id, regon_hint, nip_hint)
            VALUES (%s, %s, %s, %s, %s, %s)
            ON CONFLICT DO NOTHING
            """,
            (
                row.krs,
                row.discovery_source,
                row.discovered_at,
                row.ingestion_run_id,
                row.regon_hint,
                row.nip_hint,
            ),
        )


def insert_entity_master(conn: Connection, row: EntityMasterRow) -> None:
    conn.execute(
        """
        INSERT INTO entity_master
            (krs, nip, regon, name, legal_form_code, status, pkd_codes, pkd_predominant,
             source_document_hash, pkd_source_document_hash, known_from, ingestion_run_id)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT DO NOTHING
        """,
        (
            row.krs,
            row.nip,
            row.regon,
            row.name,
            row.legal_form_code,
            row.status,
            Jsonb([p.model_dump(mode="json") for p in row.pkd_codes]),
            row.pkd_predominant,
            row.source_document_hash,
            row.pkd_source_document_hash,
            row.known_from,
            row.ingestion_run_id,
        ),
    )


def insert_reconciliation(conn: Connection, row: ReconciliationRecord) -> None:
    conn.execute(
        """
        INSERT INTO entity_reconciliation_log
            (krs, field, seed_value, bir1_value, resolution, source_document_hash,
             ingestion_run_id, created_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT DO NOTHING
        """,
        (
            row.krs,
            row.field,
            row.seed_value,
            row.bir1_value,
            row.resolution,
            row.source_document_hash,
            row.ingestion_run_id,
            row.created_at,
        ),
    )


def insert_quarantine(conn: Connection, rows: Iterable[QuarantineRecord]) -> None:
    for row in rows:
        conn.execute(
            """
            INSERT INTO quarantine
                (stage, entity_key, reason_code, detail, source_document_hash,
                 ingestion_run_id, created_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT DO NOTHING
            """,
            (
                row.stage,
                row.entity_key,
                row.reason_code,
                row.detail,
                row.source_document_hash,
                row.ingestion_run_id,
                row.created_at,
            ),
        )


def record_a2_result(conn: Connection, result: A2Result) -> None:
    """Write one KRS lookup's rows; raw documents first so foreign keys resolve."""
    for fetch in result.raw_fetches:
        insert_raw_fetch(conn, fetch)
    if result.entity is not None:
        insert_entity_master(conn, result.entity)
    for reconciliation in result.reconciliations:
        insert_reconciliation(conn, reconciliation)
    insert_quarantine(conn, result.quarantine)


def unresolved_candidates(conn: Connection) -> list[tuple[str, str | None, str | None]]:
    """`(krs, regon_hint, nip_hint)` with no A2 outcome yet (neither master nor quarantine).

    This is what makes A2 re-materialization a no-op: resolved entities are not
    re-fetched, so no new raw objects, fetch rows, or quarantine rows appear.
    """
    cur = conn.execute(
        """
        SELECT DISTINCT ON (c.krs) c.krs, c.regon_hint, c.nip_hint
        FROM universe_candidates c
        WHERE NOT EXISTS (SELECT 1 FROM entity_master m WHERE m.krs = c.krs)
          AND NOT EXISTS (
              SELECT 1 FROM quarantine q WHERE q.stage = 'A2' AND q.entity_key = c.krs
          )
        ORDER BY c.krs, c.discovered_at
        """
    )
    return [(str(krs).strip(), regon, nip) for krs, regon, nip in cur.fetchall()]


def table_counts(conn: Connection) -> dict[str, int]:
    counts: dict[str, int] = {}
    for table in (
        "raw_documents",
        "raw_document_fetches",
        "universe_candidates",
        "entity_master",
        "entity_reconciliation_log",
        "quarantine",
    ):
        query = sql.SQL("SELECT count(*) FROM {}").format(sql.Identifier(table))
        row = conn.execute(query).fetchone()
        counts[table] = int(row[0]) if row is not None else 0
    return counts
