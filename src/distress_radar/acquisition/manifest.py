"""B2 — manifest in Postgres (AGENT_SPEC.md §6B, ADR 0006).

Plain idempotent DDL (`CREATE ... IF NOT EXISTS`) applied by `ensure_schema()`;
no migration tool. Every insert is `ON CONFLICT DO NOTHING`, so re-running a
stage never duplicates rows. Every row carries `ingestion_run_id`.

Functions take an open `psycopg.Connection` and do not commit; the caller owns
the transaction (a Dagster asset commits once per materialization).
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from datetime import datetime
from typing import Any, LiteralString

from psycopg import Connection, sql
from psycopg.types.json import Jsonb

from distress_radar.acquisition.document_retrieval import (
    A3Detail,
    A3Download,
    A3IndexResult,
)
from distress_radar.acquisition.krs_extract import A4Result, PreviousFetch
from distress_radar.acquisition.models import (
    EntityMasterRow,
    FilingDetail,
    FilingDocumentState,
    FilingIndexRow,
    LegalSource,
    PendingFilingDocument,
    QuarantineRecord,
    RawFetchRecord,
    ReconciliationRecord,
    UniverseCandidate,
)
from distress_radar.acquisition.msig_client import MsigResult
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
    CREATE TABLE IF NOT EXISTS raw_redactions (
        received_sha256    text PRIMARY KEY,
        redacted_sha256    text NOT NULL REFERENCES raw_documents (sha256),
        redaction_version  text NOT NULL,
        redacted_at        timestamptz NOT NULL,
        ingestion_run_id   text NOT NULL
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
    # The append-only detection log. Named `quarantine` until plan 0007, which
    # gave that canonical name to the SQLMesh model of the *current* set
    # (decision 3); a database from before then is renamed in place, rows as
    # they are.
    """
    DO $$
    BEGIN
        IF to_regclass('quarantine') IS NOT NULL
           AND to_regclass('quarantine_events') IS NULL THEN
            ALTER TABLE quarantine RENAME TO quarantine_events;
        END IF;
    END $$
    """,
    """
    CREATE TABLE IF NOT EXISTS quarantine_events (
        stage                 text NOT NULL,
        entity_key            text NOT NULL,
        reason_code           text NOT NULL,
        detail                text NOT NULL,
        source_document_hash  text,
        ingestion_run_id      text NOT NULL,
        created_at            timestamptz NOT NULL,
        krs                   text,
        document_ref          text,
        UNIQUE NULLS NOT DISTINCT (stage, entity_key, reason_code, source_document_hash)
    )
    """,
    """
    ALTER TABLE quarantine_events
        ADD COLUMN IF NOT EXISTS krs text,
        ADD COLUMN IF NOT EXISTS document_ref text
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
    # Corrections are not in RDF's list: their rows come from the expanded row of
    # the document they correct, named here. Added after the table first shipped.
    """
    ALTER TABLE filing_index ADD COLUMN IF NOT EXISTS correction_of text
    """,
    # Backfill `krs` / `document_ref` on log rows written before plan 0007, from
    # the stage-specific `entity_key` they were packed into: the KRS alone for
    # A2/A3 (and a well-formed A1 key), `krs:document_ref` for C1/C2/E2 — where
    # a C1 file with no filing row has `krs:source_member` instead, so the part
    # after the colon is a `document_ref` only if `filing_index` knows it. A key
    # in no known format fails the schema step; it is never skipped. Writers
    # set both columns themselves from plan 0007 on, so this touches only
    # legacy rows and is a no-op once they are filled.
    """
    DO $$
    BEGIN
        IF EXISTS (
            SELECT 1 FROM quarantine_events q
            WHERE q.krs IS NULL AND (
                (q.stage IN ('A2', 'A3') AND q.entity_key !~ '^[0-9]{10}$')
                OR (q.stage IN ('C1', 'C2', 'E2') AND q.entity_key !~ '^[0-9]{10}:.+$')
                OR (q.stage IN ('C2', 'E2') AND NOT EXISTS (
                    SELECT 1 FROM filing_index f
                    WHERE f.krs = substring(q.entity_key FROM '^([0-9]{10})')
                      AND f.document_ref = substring(q.entity_key FROM '^[0-9]{10}:(.+)$')
                ))
            )
        ) THEN
            RAISE EXCEPTION 'quarantine_events: a legacy entity_key matches no known format';
        END IF;
        UPDATE quarantine_events q SET
            krs = substring(q.entity_key FROM '^([0-9]{10})'),
            document_ref = CASE WHEN q.stage IN ('C1', 'C2', 'E2') AND EXISTS (
                SELECT 1 FROM filing_index f
                WHERE f.krs = substring(q.entity_key FROM '^([0-9]{10})')
                  AND f.document_ref = substring(q.entity_key FROM '^[0-9]{10}:(.+)$')
            ) THEN substring(q.entity_key FROM '^[0-9]{10}:(.+)$') END
        WHERE q.krs IS NULL AND (q.stage <> 'A1' OR q.entity_key ~ '^[0-9]{10}$');
    END $$
    """,
    # A4 (plan 0008 step C). One row per entity, source and run. `sha256` is the object that
    # holds the content: new, or the previous one when `content_sha256` is unchanged. The
    # latest row per (krs, source) sets that source's cutoff.
    """
    CREATE TABLE IF NOT EXISTS legal_source_fetches (
        krs               char(10) NOT NULL,
        source            text NOT NULL,
        sha256            text NOT NULL REFERENCES raw_documents (sha256),
        content_sha256    text NOT NULL,
        fetched_at        timestamptz NOT NULL,
        ingestion_run_id  text NOT NULL,
        stored_new        boolean NOT NULL,
        PRIMARY KEY (krs, source, ingestion_run_id)
    )
    """,
    # A4 MSiG (plan 0008 step E). One row per notice and extraction key (the extraction version
    # plus the vocabulary's hash); `sha256` is the reduced, person-free record (ADR 0009
    # addendum), never the notice text.
    """
    CREATE TABLE IF NOT EXISTS msig_notices (
        krs                     char(10) NOT NULL,
        notice_id               bigint NOT NULL,
        extraction_version      text NOT NULL,
        sha256                  text NOT NULL REFERENCES raw_documents (sha256),
        published_on            date NOT NULL,
        chapter_code            text,
        first_ingestion_run_id  text NOT NULL,
        PRIMARY KEY (krs, notice_id, extraction_version)
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
    if meta.redaction_version is not None and meta.received_sha256 is not None:
        insert_redaction(
            conn,
            received_sha256=meta.received_sha256,
            redacted_sha256=record.sha256,
            redaction_version=meta.redaction_version,
            redacted_at=meta.fetched_at,
            ingestion_run_id=meta.ingestion_run_id,
        )


def insert_redaction(
    conn: Connection,
    *,
    received_sha256: str,
    redacted_sha256: str,
    redaction_version: str,
    redacted_at: datetime,
    ingestion_run_id: str,
) -> None:
    """Log that a received file was stored only in redacted form (ADR 0009).

    The received hash identifies bytes that were never stored; it is not personal data.
    """
    conn.execute(
        """
        INSERT INTO raw_redactions
            (received_sha256, redacted_sha256, redaction_version, redacted_at, ingestion_run_id)
        VALUES (%s, %s, %s, %s, %s)
        ON CONFLICT DO NOTHING
        """,
        (received_sha256, redacted_sha256, redaction_version, redacted_at, ingestion_run_id),
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
            INSERT INTO quarantine_events
                (stage, entity_key, reason_code, detail, source_document_hash,
                 ingestion_run_id, created_at, krs, document_ref)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
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
                row.krs,
                row.document_ref,
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
              SELECT 1 FROM quarantine_events q WHERE q.stage = 'A2' AND q.entity_key = c.krs
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


_UPDATE_DETAIL: LiteralString = """
    UPDATE filing_index SET
        rdf_type_id = %(type_id)s, rdf_type_name = %(type_name)s,
        submission_date = %(submitted)s, prepared_date = %(prepared)s,
        is_correction = %(is_correction)s, is_ifrs = %(is_ifrs)s, file_name = %(file_name)s,
        correction_refs = %(correction_refs)s, detail_sha256 = %(detail_sha256)s
    WHERE krs = %(krs)s AND document_ref = %(ref)s AND detail_sha256 IS NULL
"""


def _detail_params(krs: str, detail: FilingDetail, fetch: RawFetchRecord) -> dict[str, object]:
    return {
        "krs": krs,
        "ref": detail.document_ref,
        "type_id": detail.rdf_type_id,
        "type_name": detail.rdf_type_name,
        "submitted": detail.submission_date,
        "prepared": detail.prepared_date,
        "is_correction": detail.is_correction,
        "is_ifrs": detail.is_ifrs,
        "file_name": detail.file_name,
        "correction_refs": Jsonb(detail.correction_refs),
        "detail_sha256": fetch.sha256,
    }


def record_a3_detail(conn: Connection, fetched: A3Detail) -> None:
    """Record an expanded row: fill the listed document's detail columns (once), and add
    a row per correction, filled from its own detail.

    A correction's list columns come from its detail, falling back to the
    document it corrects where the detail lacks them.
    """
    insert_raw_fetch(conn, fetched.corrections_fetch)
    insert_raw_fetch(conn, fetched.detail_fetch)
    conn.execute(_UPDATE_DETAIL, _detail_params(fetched.krs, fetched.detail, fetched.detail_fetch))
    listed = fetched.detail.document_ref
    for detail, fetch in fetched.related:
        insert_raw_fetch(conn, fetch)
        conn.execute(
            """
            INSERT INTO filing_index
                (krs, document_ref, rdf_type_code, status, period_start, period_end,
                 deleted_on, correction_of, discovered_at, ingestion_run_id)
            SELECT o.krs, %(ref)s, %(type_id)s, COALESCE(%(status)s, o.status),
                   COALESCE(%(start)s, o.period_start), COALESCE(%(end)s, o.period_end),
                   %(deleted)s, o.document_ref, %(at)s, %(run)s
            FROM filing_index o
            WHERE o.krs = %(krs)s AND o.document_ref = %(listed)s
            ON CONFLICT DO NOTHING
            """,
            {
                "krs": fetched.krs,
                "listed": listed,
                "ref": detail.document_ref,
                "type_id": detail.rdf_type_id,
                "status": detail.status,
                "start": detail.period_start,
                "end": detail.period_end,
                "deleted": detail.deleted_on,
                "at": fetch.meta.fetched_at,
                "run": fetch.meta.ingestion_run_id,
            },
        )
        conn.execute(_UPDATE_DETAIL, _detail_params(fetched.krs, detail, fetch))


def record_a3_download(conn: Connection, download: A3Download) -> None:
    """Record a downloaded file and point every row it holds at it (once each)."""
    insert_raw_fetch(conn, download.raw_fetch)
    conn.execute(
        """
        UPDATE filing_index SET sha256 = %s
        WHERE krs = %s AND document_ref = ANY(%s) AND sha256 IS NULL
        """,
        (download.raw_fetch.sha256, download.krs, download.document_refs),
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
              SELECT 1 FROM quarantine_events q WHERE q.stage = 'A3' AND q.entity_key = m.krs
          )
        ORDER BY m.krs
        """
    )
    return [str(krs).strip() for (krs,) in cur.fetchall()]


_STATE_SQL: LiteralString = """
    SELECT f.krs, f.document_ref, f.rdf_type_code, f.status, f.rdf_type_id, f.file_name,
           f.sha256 IS NOT NULL AS downloaded,
           f.detail_sha256 IS NULL OR EXISTS (
               SELECT 1 FROM jsonb_array_elements_text(f.correction_refs) AS c (ref)
               WHERE NOT EXISTS (
                   SELECT 1 FROM filing_index g WHERE g.krs = f.krs AND g.document_ref = c.ref
               )
           ) AS needs_detail,
           f.correction_of, f.correction_refs, f.period_end
    FROM filing_index f
"""


def _states(rows: Iterable[tuple[Any, ...]]) -> list[FilingDocumentState]:
    return [
        FilingDocumentState(
            krs=str(krs).strip(),
            document_ref=ref,
            rdf_type_code=code,
            status=status,
            rdf_type_id=type_id,
            file_name=file_name,
            downloaded=downloaded,
            needs_detail=needs_detail,
            correction_of=correction_of,
            bundle=list(bundle or []),
        )
        for (
            krs,
            ref,
            code,
            status,
            type_id,
            file_name,
            downloaded,
            needs_detail,
            correction_of,
            bundle,
            _period_end,
        ) in rows
    ]


def pending_filing_documents(
    conn: Connection, download_codes: Sequence[str], *, download_scope_only: bool = False
) -> list[PendingFilingDocument]:
    """Not-deleted documents still owed a detail or (in scope) a download.

    "Owed a detail" includes a detailed document whose corrections have no rows
    yet. Documents whose list type is in the download scope come first, grouped
    by entity so the browser searches each KRS as few times as possible, each
    document before its corrections. `download_scope_only` skips the rest.
    """
    cur = conn.execute(
        """
        SELECT * FROM ("""
        + _STATE_SQL
        + """) s
        WHERE s.status = 'NIEUSUNIETY'
          AND (s.needs_detail OR (NOT s.downloaded AND s.rdf_type_id = ANY(%(codes)s)))
          AND (NOT %(scope_only)s OR s.rdf_type_code = ANY(%(codes)s))
        ORDER BY s.rdf_type_code = ANY(%(codes)s) DESC, s.krs, s.period_end DESC,
                 s.correction_of IS NOT NULL, s.document_ref
        """,
        {"codes": list(download_codes), "scope_only": download_scope_only},
    )
    return _states(cur.fetchall())


def filing_documents(conn: Connection, krs_numbers: Sequence[str]) -> list[FilingDocumentState]:
    """Every `filing_index` row of these entities, newest period first, documents before
    their corrections."""
    cur = conn.execute(
        _STATE_SQL
        + """
        WHERE f.krs = ANY(%s)
        ORDER BY f.krs, f.period_end DESC, f.correction_of IS NOT NULL, f.document_ref
        """,
        (list(krs_numbers),),
    )
    return _states(cur.fetchall())


def latest_legal_fetch(conn: Connection, krs: str, source: LegalSource) -> PreviousFetch | None:
    """The entity's latest fetch from `source`, whose content a new fetch is compared with."""
    row = conn.execute(
        """
        SELECT sha256, content_sha256 FROM legal_source_fetches
        WHERE krs = %s AND source = %s
        ORDER BY fetched_at DESC LIMIT 1
        """,
        (krs, source),
    ).fetchone()
    return None if row is None else PreviousFetch(sha256=row[0], content_sha256=row[1])


def record_a4_result(conn: Connection, result: A4Result | MsigResult) -> None:
    """Write one entity's A4 rows; raw documents first so foreign keys resolve."""
    for fetch in result.raw_fetches:
        insert_raw_fetch(conn, fetch)
    if isinstance(result, MsigResult):
        for notice in result.notices:
            conn.execute(
                """
                INSERT INTO msig_notices
                    (krs, notice_id, extraction_version, sha256, published_on, chapter_code,
                     first_ingestion_run_id)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT DO NOTHING
                """,
                (
                    notice.krs,
                    notice.notice_id,
                    notice.extraction_version,
                    notice.sha256,
                    notice.published_on,
                    notice.chapter_code,
                    notice.ingestion_run_id,
                ),
            )
    if result.fetch is not None:
        row = result.fetch
        conn.execute(
            """
            INSERT INTO legal_source_fetches
                (krs, source, sha256, content_sha256, fetched_at, ingestion_run_id, stored_new)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT DO NOTHING
            """,
            (
                row.krs,
                row.source,
                row.sha256,
                row.content_sha256,
                row.fetched_at,
                row.ingestion_run_id,
                row.stored_new,
            ),
        )
    insert_quarantine(conn, result.quarantine)


def known_msig_notices(conn: Connection, krs: str, extraction_version: str) -> dict[int, str]:
    """Notice id → stored record, for notices already reduced under `extraction_version`."""
    rows = conn.execute(
        "SELECT notice_id, sha256 FROM msig_notices WHERE krs = %s AND extraction_version = %s",
        (krs, extraction_version),
    ).fetchall()
    return {int(notice_id): str(sha) for notice_id, sha in rows}


def legal_documents(conn: Connection, msig_extraction_key: str) -> list[tuple[str, str, str, str]]:
    """`(source, krs, sha256, first_ingestion_run_id)` of every document `legal_events` reads.

    The latest KRS extract per entity (its history is complete), and every MSiG notice
    reduced under `msig_extraction_key`.
    """
    rows = conn.execute(
        """
        SELECT 'KRS', l.krs, l.sha256, d.first_ingestion_run_id
        FROM (
            SELECT DISTINCT ON (krs) krs, sha256 FROM legal_source_fetches
            WHERE source = 'KRS' ORDER BY krs, fetched_at DESC
        ) AS l
        JOIN raw_documents AS d USING (sha256)
        UNION ALL
        SELECT 'MSiG', n.krs, n.sha256, d.first_ingestion_run_id
        FROM msig_notices AS n
        JOIN raw_documents AS d USING (sha256)
        WHERE n.extraction_version = %s
        ORDER BY 1, 2, 3
        """,
        (msig_extraction_key,),
    ).fetchall()
    return [(str(s), str(k).strip(), str(h), str(r)) for s, k, h, r in rows]


def resolved_entities(conn: Connection) -> list[str]:
    cur = conn.execute("SELECT krs FROM entity_master ORDER BY krs")
    return [str(krs).strip() for (krs,) in cur.fetchall()]


def table_counts(conn: Connection) -> dict[str, int]:
    counts: dict[str, int] = {}
    for table in (
        "raw_documents",
        "raw_document_fetches",
        "universe_candidates",
        "entity_master",
        "entity_reconciliation_log",
        "quarantine_events",
        "filing_index",
        "legal_source_fetches",
        "msig_notices",
    ):
        query = sql.SQL("SELECT count(*) FROM {}").format(sql.Identifier(table))
        row = conn.execute(query).fetchone()
        counts[table] = int(row[0]) if row is not None else 0
    return counts
