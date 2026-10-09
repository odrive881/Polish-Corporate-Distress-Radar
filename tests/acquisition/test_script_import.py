"""A3 scripted downloads tier (plan 0014): the PAD script's listings and ZIPs.

Listings and ZIPs are synthetic: invented ids, a fixture statement with a fake signature, a PDF
with a fake author (no real report, name or signature). The Postgres tests are `integration`
(`make dev-up`); the readers, the type rule and the pairing rule run in `make check`.
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

from distress_radar.acquisition import manifest, script_import
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
from distress_radar.acquisition.script_import import (
    DOCUMENT_COLUMNS,
    ENTITY_COLUMNS,
    FETCH_TIER,
    OUTAGE_COLUMNS,
    ListedSearch,
    ListingFileError,
    complete_search,
    current_documents,
    filed_years,
    import_listing,
    pair_by_prepared_date,
    read_documents,
    read_entities,
    read_outages,
)
from distress_radar.settings import Settings

KRS = "0000209396"
OTHER_KRS = "0000000042"  # searched, not in entity_master
STATEMENT_ID, CORRECTION_ID, REPORT_ID, OTHER_ID = (
    "48972241680",
    "49011110001",
    "48972241679",
    "48972241650",
)
HAR_REF = "kQL-7bDLHvl-dIGIeLuLlQ=="
REPORT_REF = "B2opwZt-Ik8Yg4luKMAqQA=="
SHA = "ab" * 32
T0 = datetime(2026, 9, 16, 9, 0, tzinfo=UTC)
STATEMENT_NAME = "Roczne sprawozdanie finansowe"
REPORT_NAME = "Opinia biegłego rewidenta / Sprawozdanie z badania rocznego sprawozdania finansowego"
DS = "http://www.w3.org/2000/09/xmldsig#"
FIXTURE = (
    Path(__file__).parent.parent / "fixtures" / "statements" / "full_2018_v1_2_por_2022.xml"
).read_bytes()
SIGNATURE = (
    f'<ds:Signature xmlns:ds="{DS}"><ds:SignedInfo/><ds:KeyInfo><ds:X509Data>'
    "<ds:X509Certificate>MIIFakeCert</ds:X509Certificate></ds:X509Data></ds:KeyInfo>"
    "</ds:Signature>"
).encode()


def _statement(prepared: str = "2023-06-30", signed: bool = True) -> bytes:
    data = FIXTURE.replace(b">2023-06-30<", f">{prepared}<".encode(), 1)
    if not signed:
        return data
    end = data.rindex(b"</")
    return data[:end] + SIGNATURE + data[end:]


def _zip(**members: bytes) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, data in members.items():
            archive.writestr(zipfile.ZipInfo(name, date_time=(2026, 6, 30, 12, 0, 0)), data)
    return buffer.getvalue()


def _report_pdf() -> bytes:
    doc = pymupdf.open()
    doc.new_page().insert_text((72, 72), "Sprawozdanie niezaleznego bieglego rewidenta")
    doc.set_metadata({"author": "Jan Testowy"})
    return doc.tobytes()


def _tab(**values: str) -> dict[str, str]:
    document_id = values.get("document_id", STATEMENT_ID)
    row = {
        "krs": KRS,
        "document_id": document_id,
        "row_document_id": document_id,
        "type_name": STATEMENT_NAME,
        "period_start": "2025-01-01",
        "period_end": "2025-12-31",
        "prepared_date": "2026-05-28",
        "is_ifrs": "Nie",
        "is_correction": "Nie",
        "submission_date": "2026-06-29",
        "status": "NIEUSUNIĘTY",
        "deleted_on": "-",
        "file": f"{values.get('krs', KRS)}/{values.get('row_document_id', document_id)}.zip",
        "captured_at": "2026-10-07 14:03:22",
        "tab": "1 / 1",
        "language": "polski",
    }
    row.update(values)
    return row


def _csv(columns: tuple[str, ...], rows: list[dict[str, str]], *, bom: bool = True) -> bytes:
    lines = [";".join(columns), *(";".join(r[c] for c in columns) for r in rows)]
    return ("﻿" if bom else "").encode() + ("\r\n".join(lines) + "\r\n").encode()


def _documents(*rows: dict[str, str]) -> bytes:
    return _csv(DOCUMENT_COLUMNS, list(rows))


def _search(krs: str = KRS, complete: str = "Tak", found: str = "Tak") -> dict[str, str]:
    return {
        "krs": krs,
        "searched_at": "2026-10-07 14:00:00",
        "found": found,
        "list_rows": "12" if found == "Tak" else "",
        "complete": complete,
    }


def _entities(*rows: dict[str, str]) -> bytes:
    return _csv(ENTITY_COLUMNS, list(rows))


# --- Reading the listings ------------------------------------------------------------------


def test_a_tab_is_read_as_the_page_writes_it():
    tabs, problems = read_documents(
        _documents(
            _tab(),
            _tab(
                document_id=REPORT_ID,
                type_name=REPORT_NAME,
                submission_date="01.07.2026",
                is_ifrs="",
                status="USUNIĘTY",
                deleted_on="2026-08-01",
                file="",
                captured_at="2026-10-07T12:03:22+00:00",
            ),
        )
    )
    assert problems == []
    statement, report = tabs
    assert (statement.krs, statement.document_id, statement.is_correction) == (
        KRS,
        STATEMENT_ID,
        False,
    )
    assert (statement.status, statement.deleted_on, statement.is_ifrs) == (
        "NIEUSUNIETY",
        None,
        False,
    )
    assert statement.captured_at == datetime(2026, 10, 7, 12, 3, 22, tzinfo=UTC)  # Warsaw time
    assert (statement.tab, statement.tabs, statement.file) == (1, 1, f"{KRS}/{STATEMENT_ID}.zip")
    assert (report.submission_date, report.status, report.is_ifrs, report.file) == (
        date(2026, 7, 1),
        "USUNIETY",
        None,
        None,
    )


@pytest.mark.parametrize(
    "header",
    [
        DOCUMENT_COLUMNS[:-1],  # a column missing
        (*DOCUMENT_COLUMNS, "nazwa_dokumentu"),  # one not in the specification
        (*DOCUMENT_COLUMNS[:-1], "krs"),  # one twice
    ],
)
def test_a_bad_header_refuses_the_whole_file(header: tuple[str, ...]):
    data = ";".join(header).encode() + b"\r\n"
    with pytest.raises(ListingFileError, match="header"):
        read_documents(data)


@pytest.mark.parametrize(
    ("values", "reason"),
    [
        # "Data sporządzenia" read into the submission column: prepared after "submitted".
        ({"submission_date": "2026-05-20", "prepared_date": "2026-05-28"}, "prepared"),
        ({"submission_date": "2025-12-31"}, "outside"),  # not after the period end
        ({"submission_date": "2026-10-08"}, "outside"),  # after it was read
        ({"is_correction": "Tak"}, "is_correction"),  # its own row's tab
        ({"status": "AKTYWNY"}, "status"),
        ({"is_ifrs": "yes"}, "Tak or Nie"),
        ({"tab": "3 / 2"}, "tab"),
        ({"file": "../elsewhere.zip"}, "file"),
        ({"row_document_id": "4897x"}, "digits"),
    ],
)
def test_a_row_that_cannot_be_trusted_is_refused_with_its_line(values: dict[str, str], reason: str):
    tabs, problems = read_documents(_documents(_tab(), _tab(document_id=OTHER_ID, **values)))
    assert [t.document_id for t in tabs] == [STATEMENT_ID]
    assert len(problems) == 1 and "line 3" in problems[0] and reason in problems[0]


def test_two_readings_of_one_document_agree_or_are_both_refused():
    tabs, _ = read_documents(
        _documents(
            _tab(),
            _tab(
                captured_at="2026-10-09 10:00:00",
                status="USUNIĘTY",
                deleted_on="2026-10-01",
                file="",
            ),
            _tab(document_id=OTHER_ID),
            _tab(document_id=OTHER_ID, submission_date="2026-06-30", file=""),
        )
    )
    current, problems = current_documents(tabs)
    assert set(current) == {STATEMENT_ID}
    latest = current[STATEMENT_ID]
    assert (latest.status, latest.deleted_on) == ("USUNIETY", date(2026, 10, 1))
    assert latest.file == f"{KRS}/{STATEMENT_ID}.zip"  # the earlier reading's download
    assert len(problems) == 1 and OTHER_ID in problems[0]


def test_a_correction_without_its_original_is_refused():
    orphan = _tab(
        document_id=CORRECTION_ID,
        row_document_id=OTHER_ID,
        is_correction="Tak",
        tab="2 / 2",
        submission_date="2026-09-01",
    )
    tabs, _ = read_documents(_documents(_tab(), orphan))
    current, problems = current_documents(tabs)
    assert set(current) == {STATEMENT_ID}
    assert len(problems) == 1 and CORRECTION_ID in problems[0]


def test_searches_are_read_with_their_flags():
    searches, problems = read_entities(
        _entities(_search(), _search(OTHER_KRS, found="Nie"), _search(complete="może"))
    )
    assert [(s.krs, s.found, s.list_rows, s.complete) for s in searches] == [
        (KRS, True, 12, True),
        (OTHER_KRS, False, None, True),
    ]
    assert len(problems) == 1 and "line 4" in problems[0]


def test_with_no_flag_shown_the_ids_say_whether_a_tab_is_a_correction():
    # RDF shows no correction flag on older filings; the ids decide, as they would check a flag.
    tabs, problems = read_documents(
        _documents(
            _tab(is_correction="", tab="1 / 2"),
            _tab(
                document_id=CORRECTION_ID,
                row_document_id=STATEMENT_ID,
                is_correction="",
                tab="2 / 2",
                submission_date="2026-09-01",
            ),
        )
    )
    assert problems == []
    assert [t.is_correction for t in tabs] == [False, True]


def test_a_found_search_whose_count_was_not_read_is_kept_only_as_incomplete():
    unread = {"list_rows": ""}
    searches, problems = read_entities(
        _entities(_search(complete="Nie") | unread, _search(complete="Tak") | unread)
    )
    assert [(s.found, s.list_rows, s.complete) for s in searches] == [(True, None, False)]
    assert len(problems) == 1 and "line 3" in problems[0]


def test_a_search_is_complete_when_the_listing_holds_every_row_the_page_listed():
    def search(complete: bool, list_rows: int | None = 3, found: bool = True) -> ListedSearch:
        return ListedSearch(1, KRS, T0, found, list_rows, complete)

    assert complete_search(search(True), 0)
    assert complete_search(search(False), 3)  # flagged incomplete, yet every row is listed
    assert not complete_search(search(False), 2)
    assert not complete_search(search(False, list_rows=None), 5)


def test_outages_are_read_as_spans():
    data = _csv(
        OUTAGE_COLUMNS,
        [
            {"from": "2026-10-08 13:57:00", "to": "2026-10-08 23:52:00"},
            {"from": "2026-10-08 12:00:00", "to": "2026-10-08 11:00:00"},
        ],
    )
    spans, problems = read_outages(data)
    assert spans == [
        (datetime(2026, 10, 8, 11, 57, tzinfo=UTC), datetime(2026, 10, 8, 21, 52, tzinfo=UTC))
    ]
    assert len(problems) == 1 and "backwards" in problems[0]


def test_filed_years_count_periods_not_deleted():
    tabs, _ = read_documents(
        _documents(
            _tab(),
            _tab(
                document_id=OTHER_ID,
                period_start="2024-01-01",
                period_end="2024-12-31",
                submission_date="2025-06-30",
                prepared_date="2025-03-31",
            ),
            _tab(
                document_id=REPORT_ID,
                period_start="2023-01-01",
                period_end="2023-12-31",
                submission_date="2024-06-30",
                prepared_date="2024-03-31",
                status="USUNIĘTY",
                deleted_on="2024-07-01",
            ),
        )
    )
    assert filed_years(tabs) == {KRS: 2}


# --- Decision 5: type codes from names and periods -----------------------------------------


def test_every_configured_name_and_period_gives_its_own_code():
    types = load_document_types()
    for code, spec in types.types.items():
        period_end = (
            date(2017, 12, 31) if spec.period_end_before is not None else date(2024, 12, 31)
        )
        assert types.code_for(spec.name, period_end) == code
        assert types.code_for(f"  {spec.name.upper()} ", period_end) == code


def test_codes_sharing_a_name_split_by_period_and_an_unknown_name_has_none():
    types = load_document_types()
    assert types.code_for(STATEMENT_NAME, date(2017, 12, 31)) == "1"
    assert types.code_for(STATEMENT_NAME, date(2018, 1, 1)) == "18"
    assert types.code_for("Sprawozdanie z działalności", date(2019, 3, 31)) == "20"
    assert types.code_for(REPORT_NAME, date(2017, 12, 31)) is None  # code 2, not yet named
    assert types.code_for("Inny dokument", date(2024, 12, 31)) is None


# --- Decision 3: pairing a group's members by their header ---------------------------------


def test_members_pair_with_tabs_by_their_prepared_date():
    raw = _zip(**{"a.xml": _statement("2026-05-28"), "b.xml": _statement("2026-09-01")})
    assert pair_by_prepared_date(raw, {"id-1": date(2026, 5, 28), "id-2": date(2026, 9, 1)}) == {
        "id-1": "a.xml",
        "id-2": "b.xml",
    }


@pytest.mark.parametrize(
    "prepared",
    [
        {"id-1": date(2026, 5, 28), "id-2": date(2026, 5, 28)},  # two tabs, one date
        {"id-1": date(2026, 5, 28), "id-2": date(2026, 9, 2)},  # a member no tab names
    ],
)
def test_a_group_the_rule_cannot_pair_stays_unpaired(prepared: dict[str, date]):
    raw = _zip(**{"a.xml": _statement("2026-05-28"), "b.xml": _statement("2026-09-01")})
    assert pair_by_prepared_date(raw, prepared) is None


def test_a_scripted_key_is_a_token_and_a_name_is_not():
    stored = _zip(**{f"id-{STATEMENT_ID}.xml": _statement(signed=False)})
    assert personal_data_markers(stored, [f"id-{STATEMENT_ID}"]) == []
    named = _zip(**{"Jan Testowy.xml": _statement(signed=False)})
    assert personal_data_markers(named, [f"id-{STATEMENT_ID}"]) == [
        "zip:member[0]: file name is not a token"
    ]


# --- Against Postgres ----------------------------------------------------------------------


def _fetch(sha: str, source: str = "rdf") -> RawFetchRecord:
    return RawFetchRecord(
        sha256=sha,
        byte_size=1,
        meta=RawDocumentMeta(
            source=source,
            source_url="https://test",
            content_type="application/json",
            fetched_at=T0,
            http_headers={},
            ingestion_run_id="a3",
        ),
    )


@pytest.fixture
def conn() -> Iterator[psycopg.Connection]:
    schema = f"test_script_import_{uuid.uuid4().hex[:12]}"
    with psycopg.connect(Settings().postgres_conninfo) as connection:
        connection.execute(f'CREATE SCHEMA "{schema}"')  # type: ignore[arg-type]
        connection.execute(f'SET search_path TO "{schema}"')  # type: ignore[arg-type]
        connection.commit()
        try:
            manifest.ensure_schema(connection)
            manifest.record_a2_result(
                connection,
                A2Result(
                    krs=KRS,
                    raw_fetches=[_fetch(SHA, "gus_bir1")],
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
            connection.commit()
            yield connection
        finally:
            connection.rollback()
            connection.execute(f'DROP SCHEMA "{schema}" CASCADE')  # type: ignore[arg-type]
            connection.commit()


def _inbox(
    root: Path, documents: bytes | None, entities: bytes | None, zips: dict[str, bytes]
) -> Path:
    for name, data in zips.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    if documents is not None:
        (root / "documents.csv").write_bytes(documents)
    if entities is not None:
        (root / "entities.csv").write_bytes(entities)
    return root


def _import(conn: psycopg.Connection, inbox: Path, store: InMemoryObjectStore, run: str):
    return import_listing(
        inbox,
        conn=conn,
        store=store,
        ingestion_run_id=run,
        document_types=load_document_types(),
        resolved=set(manifest.resolved_entities(conn)),
    )


def _rows(conn: psycopg.Connection) -> dict[str, tuple[object, ...]]:
    return {
        ref: tuple(rest)
        for ref, *rest in conn.execute(
            """
            SELECT document_ref, rdf_type_code, rdf_document_id, submission_date, status,
                   correction_of, correction_refs, listing_sha256 IS NOT NULL, sha256, file_name
            FROM filing_index ORDER BY 1
            """
        )
    }


@pytest.mark.integration
def test_a_listing_creates_its_rows_and_stores_each_zip_redacted(
    conn: psycopg.Connection, tmp_path: Path
):
    documents = _documents(
        _tab(),
        _tab(document_id=REPORT_ID, type_name=REPORT_NAME, submission_date="2026-06-30"),
        _tab(document_id=OTHER_ID, type_name="Inny dokument", file=""),
        _tab(krs=OTHER_KRS, document_id="1", file=""),
    )
    statement_zip = _zip(**{"Jan Testowy sprawozdanie.xml": _statement()})
    report_zip = _zip(**{"Jan Testowy raport.pdf": _report_pdf()})
    inbox = _inbox(
        tmp_path,
        documents,
        _entities(_search(), _search(OTHER_KRS)),
        {
            f"{KRS}/{STATEMENT_ID}.zip": statement_zip,
            f"{KRS}/{REPORT_ID}.zip": report_zip,
            f"{KRS}/99.zip": report_zip,  # no row names it
            f"{KRS}/_originals/{KRS}_2025-12-31.zip": report_zip,  # the old layout: not read
        },
    )
    store = InMemoryObjectStore()

    report = _import(conn, inbox, store, "run-1")

    assert report.problems == []
    assert (report.indexed, report.downloads, report.searches) == (3, 2, 1)
    assert report.not_in_entity_master == [OTHER_KRS]
    assert len(report.unknown_types) == 1 and OTHER_ID in report.unknown_types[0]
    assert report.zips_without_row == [f"{KRS}/99.zip"]
    assert report.rows_without_zip == [f"{KRS} id-{OTHER_ID}: no file"]
    assert report.incomplete_entities == []
    rows = _rows(conn)
    statement, audit = rows[f"id-{STATEMENT_ID}"], rows[f"id-{REPORT_ID}"]
    assert statement[:7] == (
        "18",
        STATEMENT_ID,
        date(2026, 6, 29),
        "NIEUSUNIETY",
        None,
        [f"id-{STATEMENT_ID}"],
        True,
    )
    assert audit[:4] == ("19", REPORT_ID, date(2026, 6, 30), "NIEUSUNIETY")
    assert rows[f"id-{OTHER_ID}"][:2] == (None, OTHER_ID)  # indexed with no code
    assert rows[f"id-{OTHER_ID}"][7] is None  # nothing downloaded
    assert manifest.filing_documents(conn, [KRS])[0].needs_detail is False

    for ref in (f"id-{STATEMENT_ID}", f"id-{REPORT_ID}"):
        stored = store.get(raw_key(str(rows[ref][7])))
        assert b"Jan Testowy" not in stored and b"X509Certificate" not in stored
        assert personal_data_markers(stored, [ref]) == []
        with zipfile.ZipFile(io.BytesIO(stored)) as archive:
            assert archive.namelist() == [file_token(ref, archive.namelist()[0])]
        sidecar = json.loads(store.get(sidecar_key(str(rows[ref][7]))))
        assert sidecar["fetch_tier"] == FETCH_TIER and sidecar["received_sha256"] is not None
    assert store.get(raw_key(sha256_hex(documents))) == documents  # the listing as received
    listed = conn.execute(
        "SELECT krs, found, list_rows, complete FROM rdf_listed_entities"
    ).fetchall()
    assert listed == [(KRS, True, 12, True)]

    counts = manifest.table_counts(conn)
    again = _import(conn, inbox, store, "run-2")
    assert (again.indexed, again.completed, again.downloads, again.searches) == (0, 0, 0, 0)
    assert manifest.table_counts(conn) == counts
    assert _rows(conn) == rows


@pytest.mark.integration
def test_rows_match_by_id_then_by_the_single_candidate(conn: psycopg.Connection, tmp_path: Path):
    manifest.insert_filing_index_entries(
        conn,
        [
            FilingIndexRow(
                krs=KRS,
                document_ref=ref,
                rdf_type_code=code,
                status="NIEUSUNIETY",
                period_start=date(2025, 1, 1),
                period_end=date(2025, 12, 31),
                deleted_on=None,
                discovered_at=T0,
                ingestion_run_id="a3",
            )
            for ref, code in ((HAR_REF, "18"), (REPORT_REF, "19"))
        ],
    )
    detail = _fetch("cd" * 32)
    manifest.record_a3_detail(
        conn,
        A3Detail(
            krs=KRS,
            corrections_fetch=detail,
            detail_fetch=detail,
            detail=FilingDetail(
                document_ref=HAR_REF,
                rdf_type_id="18",
                rdf_type_name=STATEMENT_NAME,
                submission_date=date(2026, 6, 29),
                prepared_date=date(2026, 5, 28),
                is_correction=False,
                is_ifrs=False,
                file_name=f"{file_token(HAR_REF, None)}.xml",
                correction_refs=[HAR_REF],
                rdf_document_id=STATEMENT_ID,
            ),
        ),
    )
    conn.commit()
    documents = _documents(
        _tab(submission_date="2026-06-28", file=""),  # disagrees with the detail
        _tab(document_id=REPORT_ID, type_name=REPORT_NAME, submission_date="2026-06-30", file=""),
    )

    report = _import(
        conn,
        _inbox(tmp_path, documents, _entities(_search()), {}),
        store=InMemoryObjectStore(),
        run="run-1",
    )

    assert (report.indexed, report.completed) == (0, 1)
    assert len(report.disagreements) == 1 and "the detail's is kept" in report.disagreements[0]
    rows = _rows(conn)
    assert set(rows) == {HAR_REF, REPORT_REF}
    assert rows[HAR_REF][1:3] == (STATEMENT_ID, date(2026, 6, 29))  # the detail stands
    assert rows[HAR_REF][6] is False  # no listing source
    assert rows[REPORT_REF][1:3] == (REPORT_ID, date(2026, 6, 30))
    assert rows[REPORT_REF][6] is True


@pytest.mark.integration
def test_a_correction_group_is_stored_unpaired_unless_the_rule_pairs_it(
    conn: psycopg.Connection, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    documents = _documents(
        _tab(tab="1 / 2"),
        _tab(
            document_id=CORRECTION_ID,
            row_document_id=STATEMENT_ID,
            is_correction="Tak",
            prepared_date="2026-09-01",
            submission_date="2026-09-02",
            tab="2 / 2",
        ),
    )
    group = _zip(**{"sf.xml": _statement("2026-05-28"), "sf korekta.xml": _statement("2026-09-01")})
    inbox = _inbox(
        tmp_path / "a", documents, _entities(_search()), {f"{KRS}/{STATEMENT_ID}.zip": group}
    )

    monkeypatch.setattr(script_import, "PAIR_BY_PREPARED_DATE", False)
    report = _import(conn, inbox, InMemoryObjectStore(), "run-1")

    assert (report.indexed, report.downloads, report.unpaired_groups) == (2, 1, 1)
    rows = _rows(conn)
    original, correction = rows[f"id-{STATEMENT_ID}"], rows[f"id-{CORRECTION_ID}"]
    assert original[5] == [f"id-{STATEMENT_ID}", f"id-{CORRECTION_ID}"]
    assert correction[4] == f"id-{STATEMENT_ID}"
    assert original[7] == correction[7] is not None
    assert original[8] is None and correction[8] is None

    conn.execute("DELETE FROM filing_index")
    conn.commit()
    monkeypatch.setattr(script_import, "PAIR_BY_PREPARED_DATE", True)
    store = InMemoryObjectStore()
    paired = _import(conn, inbox, store, "run-2")
    assert paired.unpaired_groups == 0
    rows = _rows(conn)
    with zipfile.ZipFile(io.BytesIO(store.get(raw_key(str(rows[f"id-{STATEMENT_ID}"][7]))))) as z:
        names = z.namelist()
    assert names == [f"id-{STATEMENT_ID}.xml", f"id-{CORRECTION_ID}.xml"]
    assert rows[f"id-{STATEMENT_ID}"][8] == f"id-{STATEMENT_ID}.xml"
    assert rows[f"id-{CORRECTION_ID}"][8] == f"id-{CORRECTION_ID}.xml"


@pytest.mark.integration
def test_an_incomplete_listing_and_a_later_deletion_are_recorded(
    conn: psycopg.Connection, tmp_path: Path
):
    first = [_tab(file="")]
    inbox = _inbox(tmp_path, _documents(*first), _entities(_search(complete="Nie")), {})
    report = _import(conn, inbox, InMemoryObjectStore(), "run-1")
    assert report.incomplete_entities == [f"{KRS}: listing incomplete"]
    assert conn.execute("SELECT complete FROM rdf_listed_entities").fetchall() == [(False,)]

    later = _tab(
        file="", status="USUNIĘTY", deleted_on="2026-10-01", captured_at="2026-10-09 09:00:00"
    )
    (tmp_path / "documents.csv").write_bytes(_documents(*first, later))
    again = _import(conn, inbox, InMemoryObjectStore(), "run-2")
    assert again.updated == 1
    assert conn.execute("SELECT status, deleted_on FROM filing_index").fetchall() == [
        ("USUNIETY", date(2026, 10, 1))
    ]


@pytest.mark.integration
def test_a_later_har_detail_completes_the_scripted_row(conn: psycopg.Connection, tmp_path: Path):
    inbox = _inbox(tmp_path, _documents(_tab(file="")), _entities(_search()), {})
    _import(conn, inbox, InMemoryObjectStore(), "run-1")
    # What a HAR's list adds, then its detail of the same document.
    manifest.insert_filing_index_entries(
        conn,
        [
            FilingIndexRow(
                krs=KRS,
                document_ref=HAR_REF,
                rdf_type_code="18",
                status="NIEUSUNIETY",
                period_start=date(2025, 1, 1),
                period_end=date(2025, 12, 31),
                deleted_on=None,
                discovered_at=T0,
                ingestion_run_id="har",
            )
        ],
    )
    assert manifest.scripted_entities(conn) == {KRS}
    detail = _fetch("cd" * 32)
    aliases = manifest.record_a3_detail(
        conn,
        A3Detail(
            krs=KRS,
            corrections_fetch=detail,
            detail_fetch=detail,
            detail=FilingDetail(
                document_ref=HAR_REF,
                rdf_type_id="18",
                rdf_type_name=STATEMENT_NAME,
                submission_date=date(2026, 6, 29),
                prepared_date=date(2026, 5, 28),
                is_correction=False,
                is_ifrs=False,
                file_name=None,
                correction_refs=[HAR_REF],
                rdf_document_id=STATEMENT_ID,
            ),
        ),
    )
    assert aliases == {HAR_REF: f"id-{STATEMENT_ID}"}
    rows = conn.execute(
        "SELECT document_ref, detail_sha256, listing_sha256, correction_refs FROM filing_index"
    ).fetchall()
    assert rows == [(f"id-{STATEMENT_ID}", "cd" * 32, None, [f"id-{STATEMENT_ID}"])]


@pytest.mark.integration
def test_the_backfill_reads_the_stored_detail(conn: psycopg.Connection, tmp_path: Path):
    store = InMemoryObjectStore()
    body = json.dumps({"identyfikator": HAR_REF, "idDokumentu": STATEMENT_ID}).encode()
    detail = RawFetchRecord(
        sha256=sha256_hex(body),
        byte_size=len(body),
        meta=_fetch("x").meta,
    )
    store.put_if_absent(raw_key(detail.sha256), body, "application/json")
    manifest.insert_raw_fetch(conn, detail)
    manifest.insert_filing_index_entries(
        conn,
        [
            FilingIndexRow(
                krs=KRS,
                document_ref=HAR_REF,
                rdf_type_code="18",
                status="NIEUSUNIETY",
                period_start=date(2025, 1, 1),
                period_end=date(2025, 12, 31),
                deleted_on=None,
                discovered_at=T0,
                ingestion_run_id="a3",
            )
        ],
    )
    conn.execute(
        "UPDATE filing_index SET detail_sha256 = %s WHERE document_ref = %s",
        (detail.sha256, HAR_REF),
    )
    conn.commit()

    report = _import(conn, _inbox(tmp_path, None, None, {}), store, "run-1")

    assert report.backfilled == 1
    assert _rows(conn)[HAR_REF][1] == STATEMENT_ID
    assert _import(conn, tmp_path, store, "run-2").backfilled == 0


@pytest.mark.integration
def test_outages_completeness_young_entities_and_missing_tabs(
    conn: psycopg.Connection, tmp_path: Path
):
    def year(n: int, **values: str) -> dict[str, str]:
        y = 2025 - n
        return _tab(
            document_id=f"{STATEMENT_ID[:-2]}{n:02d}",
            period_start=f"{y}-01-01",
            period_end=f"{y}-12-31",
            prepared_date=f"{y + 1}-03-31",
            submission_date=f"{y + 1}-06-30",
            file=f"{KRS}/{y}.zip",
            **values,
        )

    one, two = (
        _zip(**{"sf.xml": _statement()}),
        _zip(**{"sf.xml": _statement(), "sf korekta.xml": _statement("2026-09-01")}),
    )
    # Three filed years, the newest a group whose correction tab the listing lacks.
    documents = _documents(year(0, tab="1 / 2"), year(1), year(2))
    searches = [
        _search(complete="Nie") | {"list_rows": "3", "searched_at": "2026-10-07 10:00:00"},
        _search(found="Nie") | {"searched_at": "2026-10-08 15:00:00"},  # in the outage
    ]
    _inbox(
        tmp_path,
        documents,
        _entities(*searches),
        {f"{KRS}/2025.zip": two, f"{KRS}/2024.zip": one, f"{KRS}/2023.zip": one},
    )
    (tmp_path / "outages.csv").write_bytes(
        _csv(OUTAGE_COLUMNS, [{"from": "2026-10-08 13:57:00", "to": "2026-10-08 23:52:00"}])
    )

    def run(name: str, min_years: int) -> script_import.ScriptImportReport:
        return import_listing(
            tmp_path,
            conn=conn,
            store=InMemoryObjectStore(),
            ingestion_run_id=name,
            document_types=load_document_types(),
            resolved=set(manifest.resolved_entities(conn)),
            min_history_years=min_years,
        )

    young = run("run-1", 4)
    assert young.too_few_years == [f"{KRS}: 3 filed years listed, fewer than 4; not imported"]
    assert young.indexed == 0 and young.incomplete_entities == []

    report = run("run-2", 3)
    assert report.outage_searches == 1 and report.too_few_years == []
    # Flagged incomplete, but the listing holds all 3 rows the page listed.
    assert conn.execute("SELECT found, complete FROM rdf_listed_entities").fetchall() == [
        (True, True)
    ]
    assert report.incomplete_entities == []
    assert (report.indexed, report.downloads) == (3, 2)
    assert report.incomplete_groups == [
        f"{KRS} {KRS}/2025.zip: 1 of 2 tabs listed; ZIP not imported"
    ]
