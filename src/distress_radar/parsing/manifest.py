"""C1 manifest in Postgres: what each stored statement file is, and when it was first parsed.

`parsed_documents` has one row per (stored download, file inside it, mapping
spec hash). Its `first_ingestion_run_id` becomes the `ingestion_run_id` of every
canonical fact from that file, so re-parsing unchanged input under unchanged
mapping config reproduces the canonical Parquet byte for byte (invariant 5).
A mapping-config change gives a new `spec_hash`, hence a new row and run id.

`status` is recomputed on every run and updated in place; the first run id is
never overwritten. Same DDL conventions as `acquisition/manifest.py` (ADR 0006).
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime
from typing import Literal, LiteralString

from psycopg import Connection

ParseStatus = Literal["valid", "not_yet_mapped", "needs_pdf_tier", "quarantined"]

SCHEMA_DDL: tuple[LiteralString, ...] = (
    """
    CREATE TABLE IF NOT EXISTS parsed_documents (
        sha256                  text NOT NULL REFERENCES raw_documents (sha256),
        source_member           text NOT NULL,
        spec_hash               text NOT NULL,
        krs                     char(10) NOT NULL,
        document_ref            text,
        member_kind             text NOT NULL,
        structure_key           text,
        structure_version       text,
        status                  text NOT NULL,
        first_ingestion_run_id  text NOT NULL,
        first_parsed_at         timestamptz NOT NULL,
        PRIMARY KEY (sha256, source_member, spec_hash)
    )
    """,
)


def ensure_schema(conn: Connection) -> None:
    for statement in SCHEMA_DDL:
        conn.execute(statement)


@dataclass(frozen=True)
class StatementSource:
    """A stored download in A3 scope, with the filing rows that point to it."""

    sha256: str
    krs: str
    nip: str | None
    regon: str | None
    rows: tuple[tuple[str, str | None, date | None, date, date], ...]
    # (document_ref, file_name, submission_date, period_start, period_end)


def statement_sources(conn: Connection, rdf_type_codes: Iterable[str]) -> list[StatementSource]:
    """Downloaded, not-deleted filings of the given RDF types, grouped by stored file."""
    rows = conn.execute(
        """
        SELECT f.sha256, f.krs, m.nip, m.regon, f.document_ref, f.file_name,
               f.submission_date, f.period_start, f.period_end
        FROM filing_index f
        JOIN entity_master m ON m.krs = f.krs
        WHERE f.sha256 IS NOT NULL
          AND f.deleted_on IS NULL
          AND f.rdf_type_code = ANY(%s)
        ORDER BY f.sha256, f.document_ref
        """,
        (list(rdf_type_codes),),
    ).fetchall()
    grouped: dict[str, StatementSource] = {}
    for sha, krs, nip, regon, ref, file_name, submitted, start, end in rows:
        source = grouped.get(sha)
        entry = (ref, file_name, submitted, start, end)
        if source is None:
            grouped[sha] = StatementSource(sha, str(krs), nip, regon, (entry,))
        else:
            if source.krs != str(krs):
                raise ValueError(f"stored file {sha} is filed under two entities")
            grouped[sha] = StatementSource(sha, source.krs, nip, regon, (*source.rows, entry))
    return list(grouped.values())


@dataclass(frozen=True)
class ParsedDocumentRow:
    sha256: str
    source_member: str
    spec_hash: str  # "" for files no spec maps
    krs: str
    document_ref: str | None
    member_kind: str
    structure_key: str | None
    structure_version: str | None
    status: ParseStatus


def record_parsed_document(
    conn: Connection, row: ParsedDocumentRow, run_id: str, now: datetime
) -> str:
    """Upsert `row`; return the run id that first parsed this file under this spec."""
    result = conn.execute(
        """
        INSERT INTO parsed_documents
            (sha256, source_member, spec_hash, krs, document_ref, member_kind,
             structure_key, structure_version, status, first_ingestion_run_id, first_parsed_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (sha256, source_member, spec_hash) DO UPDATE SET
            document_ref = EXCLUDED.document_ref,
            member_kind = EXCLUDED.member_kind,
            structure_key = EXCLUDED.structure_key,
            structure_version = EXCLUDED.structure_version,
            status = EXCLUDED.status
        RETURNING first_ingestion_run_id
        """,
        (
            row.sha256,
            row.source_member,
            row.spec_hash,
            row.krs,
            row.document_ref,
            row.member_kind,
            row.structure_key,
            row.structure_version,
            row.status,
            run_id,
            now,
        ),
    ).fetchone()
    if result is None:
        raise RuntimeError("upsert into parsed_documents returned no row")
    return str(result[0])
