"""A3 manual files tier: hand-downloaded auditor reports and their listed filing dates.

The inbox is built in a temporary folder from synthetic PDFs (no real report, name or
signature). The Postgres tests are `integration` (`make dev-up`); the list parser runs in
`make check`.
"""

import io
import json
import uuid
import zipfile
from collections.abc import Iterator
from datetime import UTC, date, datetime
from pathlib import Path

import psycopg
import pymupdf
import pytest

from distress_radar.acquisition import manifest
from distress_radar.acquisition.document_retrieval import A3Detail, load_document_types
from distress_radar.acquisition.models import (
    Bir1PkdCode,
    EntityMasterRow,
    FilingDetail,
    FilingIndexRow,
    RawFetchRecord,
)
from distress_radar.acquisition.raw_store import (
    InMemoryObjectStore,
    RawDocumentMeta,
    raw_key,
    sha256_hex,
    sidecar_key,
)
from distress_radar.acquisition.redaction import file_token, personal_data_markers
from distress_radar.acquisition.regon_client import A2Result
from distress_radar.acquisition.report_import import (
    FETCH_TIER,
    DatesFileError,
    import_reports,
    read_dates,
)
from distress_radar.settings import Settings

KRS = "0000209396"
REPORT_2024 = "JTBTxSxs6t0Uj-8BrC80mA=="
REPORT_2025 = "B2opwZt-Ik8Yg4luKMAqQA=="
STATEMENT_2025 = "kQL-7bDLHvl-dIGIeLuLlQ=="
SHA = "ab" * 32
T0 = datetime(2026, 9, 16, 9, 0, tzinfo=UTC)
HEADER = "doc_name;krs;filing_date;period;document_id"


def _csv(*lines: str, bom: bool = True) -> bytes:
    text = "\r\n".join((HEADER, *lines)) + "\r\n"
    return ("﻿" if bom else "").encode() + text.encode()


def _report_zip(author: str = "Jan Testowy") -> bytes:
    doc = pymupdf.open()
    doc.new_page().insert_text((72, 72), "Sprawozdanie niezaleznego bieglego rewidenta")
    doc.set_metadata({"author": author})
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(f"{author} raport.pdf", doc.tobytes())
    return buffer.getvalue()


def _xml_zip() -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("sprawozdanie.xml", "<?xml version='1.0'?><JednostkaMala/>")
    return buffer.getvalue()


def test_the_list_is_read_with_a_bom_and_unknown_values():
    rows = read_dates(
        _csv(
            f"{KRS}_2025-12-31.pdf;{KRS};2026-06-29;2025-12-31;48972241679",
            f"{KRS}_2024-12-31.pdf;{KRS};unknown;2024-12-31;unknown",
        )
    )
    assert [(r.krs, r.period_end, r.filing_date, r.line) for r in rows] == [
        (KRS, date(2025, 12, 31), date(2026, 6, 29), 2),
        (KRS, date(2024, 12, 31), None, 3),
    ]


@pytest.mark.parametrize(
    ("data", "match"),
    [
        (b"krs;date\r\n", "header"),
        (_csv(f"{KRS}_2025-12-31.pdf;{KRS};2026-06-29;2025-12-31"), "fields"),
        (_csv(f"{KRS}_2024-12-31.pdf;{KRS};2026-06-29;2025-12-31;1"), "doc_name"),
        (_csv(f"{KRS}_2025-12-31.pdf;{KRS};29.06.2026;2025-12-31;1"), "line 2"),
        (_csv(f"{KRS}_2025-12-31.pdf;{KRS};2026-06-29;2025-12-31;x1"), "document_id"),
    ],
)
def test_a_malformed_list_is_refused_whole(data: bytes, match: str):
    with pytest.raises(DatesFileError, match=match):
        read_dates(data)


@pytest.fixture
def conn() -> Iterator[psycopg.Connection]:
    schema = f"test_report_import_{uuid.uuid4().hex[:12]}"
    with psycopg.connect(Settings().postgres_conninfo) as connection:
        connection.execute(f'CREATE SCHEMA "{schema}"')  # type: ignore[arg-type]
        connection.execute(f'SET search_path TO "{schema}"')  # type: ignore[arg-type]
        connection.commit()
        try:
            manifest.ensure_schema(connection)
            fetch = RawFetchRecord(
                sha256=SHA,
                byte_size=1,
                meta=RawDocumentMeta(
                    source="gus_bir1",
                    source_url="https://bir1.test",
                    content_type="application/xml",
                    fetched_at=T0,
                    http_headers={},
                    ingestion_run_id="a2",
                ),
            )
            manifest.record_a2_result(
                connection,
                A2Result(
                    krs=KRS,
                    raw_fetches=[fetch],
                    entity=EntityMasterRow(
                        krs=KRS,
                        nip=None,
                        regon="932989044",
                        name="PTB",
                        legal_form_code="117",
                        status="active",
                        pkd_codes=[Bir1PkdCode(code="4120Z", version="2007", predominant=True)],
                        pkd_predominant="4120Z",
                        source_document_hash=SHA,
                        pkd_source_document_hash=SHA,
                        known_from=date(2026, 9, 14),
                        ingestion_run_id="a2",
                    ),
                ),
            )
            manifest.insert_filing_index_entries(
                connection,
                [
                    FilingIndexRow(
                        krs=KRS,
                        document_ref=ref,
                        rdf_type_code=code,
                        status="NIEUSUNIETY",
                        period_start=date(year, 1, 1),
                        period_end=date(year, 12, 31),
                        deleted_on=None,
                        discovered_at=T0,
                        ingestion_run_id="a3",
                    )
                    for ref, code, year in (
                        (REPORT_2024, "19", 2024),
                        (REPORT_2025, "19", 2025),
                        (STATEMENT_2025, "18", 2025),
                    )
                ],
            )
            connection.commit()
            yield connection
        finally:
            connection.rollback()
            connection.execute(f'DROP SCHEMA "{schema}" CASCADE')  # type: ignore[arg-type]
            connection.commit()


def _inbox(tmp_path: Path, dates: bytes | None, zips: dict[str, bytes]) -> Path:
    for name, data in zips.items():
        path = tmp_path / name[:10] / "_zips" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    if dates is not None:
        (tmp_path / "filing_dates.csv").write_bytes(dates)
    return tmp_path


def _import(conn: psycopg.Connection, inbox: Path, store: InMemoryObjectStore, run: str):
    return import_reports(
        inbox,
        conn=conn,
        store=store,
        ingestion_run_id=run,
        document_types=load_document_types(),
    )


def _rows(conn: psycopg.Connection) -> dict[str, tuple[object, ...]]:
    return {
        ref: (sha256, submitted, source)
        for ref, sha256, submitted, source in conn.execute(
            "SELECT document_ref, sha256, submission_date, submission_date_sha256 "
            "FROM filing_index ORDER BY 1"
        )
    }


@pytest.mark.integration
def test_reports_are_stored_redacted_and_dated_from_the_stored_list(
    conn: psycopg.Connection, tmp_path: Path
):
    dates = _csv(
        f"{KRS}_2025-12-31.pdf;{KRS};2026-06-29;2025-12-31;48972241679",
        f"{KRS}_2024-12-31.pdf;{KRS};unknown;2024-12-31;unknown",
    )
    raw = _report_zip()
    inbox = _inbox(tmp_path, dates, {f"{KRS}_2025-12-31.zip": raw, f"{KRS}_2024-12-31.zip": raw})
    store = InMemoryObjectStore()

    report = _import(conn, inbox, store, "run-1")

    assert (report.downloads, report.dated, report.problems) == (2, 1, [])
    assert report.undated == [f"{KRS} 2024-12-31 ({REPORT_2024})"]
    rows = _rows(conn)
    list_sha = sha256_hex(dates)
    assert rows[REPORT_2025][1:] == (date(2026, 6, 29), list_sha)
    assert rows[REPORT_2024][1:] == (None, None)
    assert rows[STATEMENT_2025] == (None, None, None)  # not an auditor-report row
    assert store.get(raw_key(list_sha)) == dates  # the list is stored as received
    stored = store.get(raw_key(str(rows[REPORT_2025][0])))
    assert b"Jan Testowy" not in stored
    assert personal_data_markers(stored, [REPORT_2025]) == []
    with zipfile.ZipFile(io.BytesIO(stored)) as archive:
        assert archive.namelist() == [file_token(REPORT_2025, "x.pdf")]
    sidecar = json.loads(store.get(sidecar_key(str(rows[REPORT_2025][0]))))
    assert sidecar["fetch_tier"] == FETCH_TIER and sidecar["received_sha256"] == sha256_hex(raw)
    assert sidecar["original_filename"] == file_token(REPORT_2025, "x.pdf")

    counts = manifest.table_counts(conn)
    again = _import(conn, inbox, store, "run-2")
    assert (again.downloads, again.dated) == (0, 0)
    assert manifest.table_counts(conn) == counts
    assert _rows(conn) == rows


@pytest.mark.integration
def test_what_cannot_be_used_is_listed_not_stored(conn: psycopg.Connection, tmp_path: Path):
    dates = _csv(
        f"{KRS}_2017-12-31.pdf;{KRS};2018-07-12;2017-12-31;841544",  # no auditor-report row
        f"{KRS}_2024-12-31.pdf;{KRS};2024-12-30;2024-12-31;9935341",  # before the period ends
        f"{KRS}_2025-12-31.pdf;{KRS};2026-06-29;2025-12-31;1",
        f"{KRS}_2025-12-31.pdf;{KRS};2026-07-01;2025-12-31;1",  # disagrees with the line above
    )
    inbox = _inbox(tmp_path, dates, {f"{KRS}_2025-12-31.zip": _xml_zip()})
    store = InMemoryObjectStore()

    report = _import(conn, inbox, store, "run-1")

    assert (report.downloads, report.dated) == (0, 0)
    assert len(report.not_a_report) == 1 and len(report.unmatched) == 1
    assert len(report.problems) == 2
    assert all(v == (None, None, None) for v in _rows(conn).values())
    assert store.objects == {}


@pytest.mark.integration
def test_a_detail_imported_later_replaces_the_listed_date(conn: psycopg.Connection, tmp_path: Path):
    dates = _csv(f"{KRS}_2025-12-31.pdf;{KRS};2026-06-29;2025-12-31;48972241679")
    _import(conn, _inbox(tmp_path, dates, {}), InMemoryObjectStore(), "run-1")
    detail_fetch = RawFetchRecord(
        sha256="cd" * 32,
        byte_size=1,
        meta=RawDocumentMeta(
            source="rdf",
            source_url="https://rdf.test",
            content_type="application/json",
            fetched_at=T0,
            http_headers={},
            ingestion_run_id="a3",
        ),
    )
    manifest.record_a3_detail(
        conn,
        A3Detail(
            krs=KRS,
            corrections_fetch=detail_fetch,
            detail_fetch=detail_fetch,
            detail=FilingDetail(
                document_ref=REPORT_2025,
                rdf_type_id="19",
                rdf_type_name="Opinia",
                submission_date=date(2026, 6, 30),
                prepared_date=None,
                is_correction=False,
                is_ifrs=False,
                file_name=None,
                correction_refs=[REPORT_2025],
            ),
        ),
    )
    assert _rows(conn)[REPORT_2025][1:] == (date(2026, 6, 30), None)
