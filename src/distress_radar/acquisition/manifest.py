"""B2 — manifest in Postgres (AGENT_SPEC.md §6B, ADR 0006).

Plain idempotent DDL (`CREATE ... IF NOT EXISTS`) applied by `ensure_schema()`;
no migration tool. Every insert is `ON CONFLICT DO NOTHING`, so re-running a
stage never duplicates rows. Every row carries `ingestion_run_id`.

Functions take an open `psycopg.Connection` and do not commit; the caller owns
the transaction (a Dagster asset commits once per materialization).
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from typing import LiteralString

from psycopg import Connection, sql
from psycopg.types.json import Jsonb

from distress_radar.acquisition.document_retrieval import (
    A3Detail,
    A3Download,
    A3IndexResult,
)
from distress_radar.acquisition.models import (
    EntityMasterRow,
    FilingIndexRow,
    PendingFilingDocument,
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
    # A3. List columns are always known; detail columns fill in when the row is
    # expanded (`detail_sha256`), `sha256` when the document is downloaded.
    """
    CREATE TABLE IF NOT EXISTS filing_index (
        krs               char(10) NOT NULL REFERENCES entity_master (krs),
        document_ref      text NOT NULL,
        rdf_type_code     text NOT NULL,
        status            text NOT NULL,
        period_start      date NOT NULL,
        period_end        date NOT NULL,
        deleted_on        date,
        rdf_type_id       text,
        rdf_type_name     text,
        submission_date   date,
        prepared_date     date,
        is_correction     boolean,
        is_ifrs           boolean,
        file_name         text,
        correction_refs   jsonb,
        detail_sha256     text REFERENCES raw_documents (sha256),
        sha256            text REFERENCES raw_documents (sha256),
        discovered_at     timestamptz NOT NULL,
        ingestion_run_id  text NOT NULL,
        PRIMARY KEY (krs, document_ref)
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


def insert_filing_index_entries(conn: Connection, rows: Iterable[FilingIndexRow]) -> None:
    for row in rows:
        conn.execute(
            """
            INSERT INTO filing_index
                (krs, document_ref, rdf_type_code, status, period_start, period_end,
                 deleted_on, discovered_at, ingestion_run_id)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT DO NOTHING
            """,
            (
                row.krs,
                row.document_ref,
                row.rdf_type_code,
                row.status,
                row.period_start,
                row.period_end,
                row.deleted_on,
                row.discovered_at,
                row.ingestion_run_id,
            ),
        )


def record_a3_index_result(conn: Connection, result: A3IndexResult) -> None:
    """Write one filing-list lookup's rows; raw documents first so foreign keys resolve."""
    for fetch in result.raw_fetches:
        insert_raw_fetch(conn, fetch)
    insert_filing_index_entries(conn, result.entries)
    insert_quarantine(conn, result.quarantine)


def record_a3_detail(conn: Connection, fetched: A3Detail) -> None:
    """Record the detail's raw responses and fill the row's detail columns (once)."""
    insert_raw_fetch(conn, fetched.corrections_fetch)
    insert_raw_fetch(conn, fetched.detail_fetch)
    detail = fetched.detail
    conn.execute(
        """
        UPDATE filing_index SET
            rdf_type_id = %s, rdf_type_name = %s, submission_date = %s, prepared_date = %s,
            is_correction = %s, is_ifrs = %s, file_name = %s, correction_refs = %s,
            detail_sha256 = %s
        WHERE krs = %s AND document_ref = %s AND detail_sha256 IS NULL
        """,
        (
            detail.rdf_type_id,
            detail.rdf_type_name,
            detail.submission_date,
            detail.prepared_date,
            detail.is_correction,
            detail.is_ifrs,
            detail.file_name,
            Jsonb(detail.correction_refs),
            fetched.detail_fetch.sha256,
            fetched.krs,
            detail.document_ref,
        ),
    )


def record_a3_download(conn: Connection, download: A3Download) -> None:
    """Record the downloaded bytes and point the `filing_index` row at them (once)."""
    insert_raw_fetch(conn, download.raw_fetch)
    conn.execute(
        """
        UPDATE filing_index SET sha256 = %s
        WHERE krs = %s AND document_ref = %s AND sha256 IS NULL
        """,
        (download.raw_fetch.sha256, download.krs, download.document_ref),
    )


def unindexed_entities(conn: Connection) -> list[str]:
    """Resolved entities with no A3 outcome yet (no `filing_index` rows, no A3 quarantine).

    Makes `filing_index` re-materialization a no-op for already-indexed entities.
    """
    cur = conn.execute(
        """
        SELECT m.krs
        FROM entity_master m
        WHERE NOT EXISTS (SELECT 1 FROM filing_index f WHERE f.krs = m.krs)
          AND NOT EXISTS (
              SELECT 1 FROM quarantine q WHERE q.stage = 'A3' AND q.entity_key = m.krs
          )
        ORDER BY m.krs
        """
    )
    return [str(krs).strip() for (krs,) in cur.fetchall()]


def pending_filing_documents(
    conn: Connection, download_codes: Sequence[str], *, download_scope_only: bool = False
) -> list[PendingFilingDocument]:
    """Listed, not-deleted documents still owed a detail or (in scope) a download.

    Documents whose list type is in the download scope come first, grouped by
    entity so the browser searches each KRS as few times as possible.
    `download_scope_only` skips the rest (their details can trail behind).
    """
    cur = conn.execute(
        """
        SELECT krs, document_ref, rdf_type_code, rdf_type_id, file_name, sha256 IS NOT NULL
        FROM filing_index
        WHERE status = 'NIEUSUNIETY'
          AND (
              detail_sha256 IS NULL
              OR (sha256 IS NULL AND rdf_type_id = ANY(%(codes)s))
          )
          AND (NOT %(scope_only)s OR rdf_type_code = ANY(%(codes)s))
        ORDER BY rdf_type_code = ANY(%(codes)s) DESC, krs, period_end DESC, document_ref
        """,
        {"codes": list(download_codes), "scope_only": download_scope_only},
    )
    return [
        PendingFilingDocument(
            krs=str(krs).strip(),
            document_ref=ref,
            rdf_type_code=code,
            rdf_type_id=type_id,
            file_name=file_name,
            downloaded=downloaded,
        )
        for krs, ref, code, type_id, file_name, downloaded in cur.fetchall()
    ]


def table_counts(conn: Connection) -> dict[str, int]:
    counts: dict[str, int] = {}
    for table in (
        "raw_documents",
        "raw_document_fetches",
        "universe_candidates",
        "entity_master",
        "entity_reconciliation_log",
        "quarantine",
        "filing_index",
    ):
        query = sql.SQL("SELECT count(*) FROM {}").format(sql.Identifier(table))
        row = conn.execute(query).fetchone()
        counts[table] = int(row[0]) if row is not None else 0
    return counts
