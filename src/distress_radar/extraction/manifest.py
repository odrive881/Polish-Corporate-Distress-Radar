"""G manifest in Postgres: which statement files and auditor reports the text stage read, and when
first (plan 0013 step H). Same DDL conventions as `parsing/manifest.py` (ADR 0006).

`text_extractions` has one row per (stored download, statement inside it, pipeline hash). Its
`first_ingestion_run_id` is the `ingestion_run_id` of every `text_signals` and `text_coverage` row
from that file, so a rerun over unchanged input under an unchanged pipeline writes the same bytes
(invariant 5). A change to the configs, prompts, masking or the signals allowed to run is a new
pipeline hash, hence a new row and run id (`text_signals.pipeline_hash`).
"""

from __future__ import annotations

from collections.abc import Collection
from dataclasses import dataclass
from datetime import date, datetime
from typing import LiteralString

from psycopg import Connection

SCHEMA_DDL: tuple[LiteralString, ...] = (
    """
    CREATE TABLE IF NOT EXISTS text_extractions (
        sha256                  text NOT NULL REFERENCES raw_documents (sha256),
        source_member           text NOT NULL,
        pipeline_hash           text NOT NULL,
        krs                     char(10) NOT NULL,
        document_ref            text NOT NULL,
        first_ingestion_run_id  text NOT NULL,
        first_extracted_at      timestamptz NOT NULL,
        last_seen_run_id        text NOT NULL,
        last_seen_at            timestamptz NOT NULL,
        PRIMARY KEY (sha256, source_member, pipeline_hash)
    )
    """,
)


def ensure_schema(conn: Connection) -> None:
    for statement in SCHEMA_DDL:
        conn.execute(statement)


@dataclass(frozen=True)
class TextSource:
    """A statement file the parsing stage recorded, with its filing: the text stage's input."""

    sha256: str
    source_member: str
    object_key: str
    krs: str
    document_ref: str
    period_end: date
    submission_date: date


def text_sources(conn: Connection) -> list[TextSource]:
    """Every XML statement file `parsed_documents` holds with a filing that is dated and not
    deleted, once, whatever its parse status: a statement whose figures are quarantined still has
    notes. A statement filed as PDF carries no embedded notes to read (plan 0006)."""
    rows = conn.execute(
        """
        SELECT DISTINCT p.sha256, p.source_member, r.object_key, p.krs, p.document_ref,
               f.period_end, f.submission_date
        FROM parsed_documents p
        JOIN raw_documents r ON r.sha256 = p.sha256
        JOIN filing_index f ON f.document_ref = p.document_ref AND f.krs = p.krs
        WHERE p.member_kind = 'xml_statement'
          AND f.submission_date IS NOT NULL AND f.deleted_on IS NULL
        ORDER BY p.krs, f.period_end, p.document_ref, p.sha256, p.source_member
        """
    ).fetchall()
    return [
        TextSource(
            sha256=str(r[0]),
            source_member=str(r[1]),
            object_key=str(r[2]),
            krs=str(r[3]).strip(),
            document_ref=str(r[4]),
            period_end=r[5],
            submission_date=r[6],
        )
        for r in rows
    ]


@dataclass(frozen=True)
class ReportSource:
    """A stored auditor report (plan 0013 decision 0c) with its filing. `submission_date` is None
    when neither a detail nor the hand-collected list dated it: such a report is not read
    (decision 6), and the caller counts it."""

    sha256: str
    object_key: str
    krs: str
    document_ref: str
    period_end: date
    submission_date: date | None


def report_sources(conn: Connection, rdf_type_codes: Collection[str]) -> list[ReportSource]:
    """Every stored, not deleted `filing_index` row of the given auditor-report types, once."""
    rows = conn.execute(
        """
        SELECT f.sha256, r.object_key, f.krs, f.document_ref, f.period_end, f.submission_date
        FROM filing_index f
        JOIN raw_documents r ON r.sha256 = f.sha256
        WHERE f.rdf_type_code = ANY(%s) AND f.deleted_on IS NULL
        ORDER BY f.krs, f.period_end, f.document_ref
        """,
        (list(rdf_type_codes),),
    ).fetchall()
    return [
        ReportSource(
            sha256=str(r[0]),
            object_key=str(r[1]),
            krs=str(r[2]).strip(),
            document_ref=str(r[3]),
            period_end=r[4],
            submission_date=r[5],
        )
        for r in rows
    ]


def record_text_extraction(
    conn: Connection, source: TextSource, pipeline_hash: str, run_id: str, now: datetime
) -> str:
    """Upsert the file's row; return the run id that first read it under this pipeline."""
    result = conn.execute(
        """
        INSERT INTO text_extractions
            (sha256, source_member, pipeline_hash, krs, document_ref, first_ingestion_run_id,
             first_extracted_at, last_seen_run_id, last_seen_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (sha256, source_member, pipeline_hash) DO UPDATE SET
            last_seen_run_id = EXCLUDED.last_seen_run_id,
            last_seen_at = EXCLUDED.last_seen_at
        RETURNING first_ingestion_run_id
        """,
        (
            source.sha256,
            source.source_member,
            pipeline_hash,
            source.krs,
            source.document_ref,
            run_id,
            now,
            run_id,
            now,
        ),
    ).fetchone()
    if result is None:
        raise RuntimeError("upsert into text_extractions returned no row")
    return str(result[0])
