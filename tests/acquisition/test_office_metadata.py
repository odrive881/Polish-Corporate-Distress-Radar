"""Office files lose the people their metadata names (redaction version 5, ADR 0009).

Every file here is built in the test with an invented person, "Jan Testowy" (user "jtestowy"),
never from a real filing.
"""

import base64
import io
import struct
import zipfile

import pytest

from distress_radar.acquisition import office_metadata as om
from distress_radar.acquisition.redaction import (
    RedactionError,
    personal_data_markers,
    redact_download,
    redact_file,
)

PERSON = "Jan Testowy"
USER = "jtestowy"
BODY = "Informacja dodatkowa: zasady rachunkowości"
REF = "AAAAAAAAAAAAAAAAAAAA/w=="


def _leaks(data: bytes) -> bool:
    """Whether the invented person is still in the bytes, in any encoding used here."""
    return any(
        needle in data
        for text in (PERSON, USER)
        for needle in (text.encode(), text.encode("utf-16-le"))
    )


def _zip(members: list[tuple[str, bytes]], stored_first: bool = False) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for index, (name, data) in enumerate(members):
            info = zipfile.ZipInfo(name, date_time=(2023, 6, 30, 12, 0, 0))
            info.compress_type = (
                zipfile.ZIP_STORED if stored_first and index == 0 else zipfile.ZIP_DEFLATED
            )
            archive.writestr(info, data)
    return buffer.getvalue()


# --- OOXML, OpenDocument, flat XML ----------------------------------------------------------------

CORE = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties"'
    ' xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:dcterms="http://purl.org/dc/terms/">'
    f"<dc:creator>{PERSON}</dc:creator><cp:lastModifiedBy>{PERSON}</cp:lastModifiedBy>"
    "<dcterms:created>2023-03-01T10:00:00Z</dcterms:created></cp:coreProperties>"
).encode()
APP = (
    '<Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/extended-properties">'
    f"<Application>Microsoft Office Word</Application><Company>{PERSON}</Company><Pages>2</Pages>"
    "</Properties>"
).encode()
W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
DOCUMENT = (
    f'<w:document xmlns:w="{W}"><w:body><w:p><w:r><w:t>{BODY}</w:t></w:r></w:p>'
    f'<w:ins w:id="1" w:author="{PERSON}"><w:r><w:t>x</w:t></w:r></w:ins></w:body></w:document>'
).encode()
COMMENTS = (
    f'<w:comments xmlns:w="{W}"><w:comment w:id="0" w:author="{PERSON}" w:initials="JT">'
    "<w:p/></w:comment></w:comments>"
).encode()
RELS = (
    '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
    '<Relationship Id="rId1" Type="http://x/oleObject" TargetMode="External"'
    f' Target="file:///C:\\Users\\{USER}\\arkusz.xlsx"/>'
    '<Relationship Id="rId2" Type="http://x/hyperlink" TargetMode="External" Target="https://ms.gov.pl"/>'
    "</Relationships>"
).encode()


def _docx() -> bytes:
    return _zip(
        [
            ("[Content_Types].xml", b'<Types xmlns="http://x"/>'),
            ("docProps/core.xml", CORE),
            ("docProps/app.xml", APP),
            ("word/document.xml", DOCUMENT),
            ("word/comments.xml", COMMENTS),
            ("word/_rels/document.xml.rels", RELS),
        ]
    )


ODF_META = (
    '<office:document-meta xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0"'
    ' xmlns:meta="urn:oasis:names:tc:opendocument:xmlns:meta:1.0"'
    ' xmlns:dc="http://purl.org/dc/elements/1.1/"><office:meta>'
    f"<meta:initial-creator>{PERSON}</meta:initial-creator><dc:creator>{PERSON}</dc:creator>"
    "<meta:generator>LibreOffice</meta:generator></office:meta></office:document-meta>"
).encode()
ODF_CONTENT = (
    '<office:document-content xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0"'
    ' xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0"'
    ' xmlns:dc="http://purl.org/dc/elements/1.1/"><office:body><office:text>'
    f"<text:p>{BODY}<office:annotation><dc:creator>{PERSON}</dc:creator></office:annotation>"
    "</text:p></office:text></office:body></office:document-content>"
).encode()


def _odt() -> bytes:
    return _zip(
        [
            ("mimetype", b"application/vnd.oasis.opendocument.text"),
            ("meta.xml", ODF_META),
            ("content.xml", ODF_CONTENT),
        ],
        stored_first=True,
    )


def test_docx_loses_its_people_and_keeps_its_text() -> None:
    docx = _docx()
    assert om.markers(docx, "f")
    out = om.strip(docx, "f")
    assert out is not None and out.actions
    assert not _leaks(out.data)
    assert om.markers(out.data, "f") == []
    with zipfile.ZipFile(io.BytesIO(out.data)) as archive:
        with zipfile.ZipFile(io.BytesIO(docx)) as before:
            assert archive.namelist() == before.namelist()
        assert BODY.encode() in archive.read("word/document.xml")
        assert b"<Pages>2</Pages>" in archive.read("docProps/app.xml")
        assert b"https://ms.gov.pl" in archive.read("word/_rels/document.xml.rels")
    again, repeat = om.strip(out.data, "f"), om.strip(docx, "f")
    assert again is not None and (again.data, again.actions) == (out.data, [])
    assert repeat is not None and repeat.data == out.data  # deterministic


def test_odt_loses_its_people_and_keeps_mimetype_first_and_stored() -> None:
    out = om.strip(_odt(), "f")
    assert out is not None and out.actions
    assert not _leaks(out.data)
    assert om.markers(out.data, "f") == []
    with zipfile.ZipFile(io.BytesIO(out.data)) as archive:
        first = archive.infolist()[0]
        assert (first.filename, first.compress_type) == ("mimetype", zipfile.ZIP_STORED)
        assert BODY.encode() in archive.read("content.xml")
        assert b"LibreOffice" in archive.read("meta.xml")


def test_flat_word_and_flat_opendocument_lose_their_people() -> None:
    pkg = "http://schemas.microsoft.com/office/2006/xmlPackage"
    flat_word = (
        f'<?xml version="1.0"?><pkg:package xmlns:pkg="{pkg}">'
        f'<pkg:part pkg:name="/docProps/core.xml"><pkg:xmlData>{CORE.decode().split("?>", 1)[1]}'
        f'</pkg:xmlData></pkg:part><pkg:part pkg:name="/word/document.xml"><pkg:xmlData>'
        f"{DOCUMENT.decode()}</pkg:xmlData></pkg:part>"
        f'<pkg:part pkg:name="/word/embeddings/a.docx"><pkg:binaryData>'
        f"{base64.b64encode(_docx()).decode()}</pkg:binaryData></pkg:part></pkg:package>"
    ).encode()
    flat_odf = (
        '<office:document xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0"'
        ' xmlns:meta="urn:oasis:names:tc:opendocument:xmlns:meta:1.0"'
        ' xmlns:dc="http://purl.org/dc/elements/1.1/"><office:meta>'
        f"<meta:initial-creator>{PERSON}</meta:initial-creator></office:meta>"
        f"<office:body>{BODY}</office:body></office:document>"
    ).encode()
    for flat in (flat_word, flat_odf):
        assert personal_data_markers(flat)
        out = redact_file(flat, "raw")
        assert out is not None and out.changed
        assert not _leaks(out.data)
        assert personal_data_markers(out.data) == []
        assert BODY.encode() in out.data


RTF = (
    b"{\\rtf1\\ansi{\\fonttbl{\\f0 Arial;}}{\\info{\\title Nota}{\\author "
    + PERSON.encode()
    + b"}{\\operator "
    + PERSON.encode()
    + b"}{\\creatim\\yr2023}}{\\*\\revtbl{Unknown;}{"
    + PERSON.encode()
    + b";}}\\pard "
    + b"Tekst \\{w nawiasie\\} "
    + BODY.encode()
    + b"\\par}"
)


def test_rtf_loses_its_info_and_revision_authors_and_keeps_its_text() -> None:
    assert om.markers(RTF, "f")
    out = om.strip(RTF, "f")
    assert out is not None and out.actions
    assert not _leaks(out.data)
    assert om.markers(out.data, "f") == []
    assert b"{\\info}" in out.data and b"{\\*\\revtbl}" in out.data
    assert BODY.encode() in out.data and b"\\{w nawiasie\\}" in out.data


def test_unbalanced_rtf_is_refused() -> None:
    with pytest.raises(RedactionError):
        redact_file(b"{\\rtf1{\\info{\\author " + PERSON.encode() + b"}", "raw")


# --- OLE compound files ---------------------------------------------------------------------------

SECTOR, MINI, CUTOFF = 512, 64, 4096
FREE, END, FATSECT = 0xFFFFFFFF, 0xFFFFFFFE, 0xFFFFFFFD


def _compound(streams: list[tuple[str, bytes]]) -> bytes:
    """A version 3 compound file: small streams in the mini stream, the rest in sectors."""
    small = [(n, d) for n, d in streams if len(d) < CUTOFF]
    big = [(n, d) for n, d in streams if len(d) >= CUTOFF]
    mini_stream, mini_fat, mini_start = b"", list[int](), dict[str, int]()
    for name, data in small:
        count = max(1, -(-len(data) // MINI))
        mini_start[name] = len(mini_fat)
        mini_fat.extend([len(mini_fat) + i + 1 for i in range(count - 1)] + [END])
        mini_stream += data.ljust(count * MINI, b"\0")
    entries = 1 + len(streams)

    def sectors(n: int) -> int:
        return max(1, -(-n // SECTOR))

    dir_n, minifat_n = sectors(entries * 128), sectors(len(mini_fat) * 4)
    ministream_n = sectors(len(mini_stream))
    big_n = [sectors(len(d)) for _, d in big]
    total = dir_n + minifat_n + ministream_n + sum(big_n)
    fat_n = 1
    while fat_n * (SECTOR // 4) < total + fat_n:
        fat_n += 1
    fat = [FATSECT] * fat_n
    starts: list[int] = []
    for n in (dir_n, minifat_n, ministream_n, *big_n):
        starts.append(len(fat))
        fat.extend([len(fat) + i + 1 for i in range(n - 1)] + [END])
    fat.extend([FREE] * (fat_n * SECTOR // 4 - len(fat)))

    def entry(name: str, kind: int, start: int, size: int, child: int, right: int) -> bytes:
        raw = (name.encode("utf-16-le") + b"\0\0").ljust(64, b"\0")
        return (
            raw
            + struct.pack("<HBB", len(name) * 2 + 2, kind, 1)
            + struct.pack("<III", FREE, right, child)
            + bytes(36)
            + struct.pack("<IQ", start, size)
        )

    directory = entry("Root Entry", 5, starts[2], len(mini_stream), 1 if streams else FREE, FREE)
    big_start = dict(zip([n for n, _ in big], starts[3:], strict=True))
    for index, (name, data) in enumerate(streams, start=1):
        start = mini_start[name] if name in mini_start else big_start[name]
        right = index + 1 if index < len(streams) else FREE
        directory += entry(name, 2, start, len(data), FREE, right)
    header = (
        om.OLE_MAGIC
        + bytes(16)
        + struct.pack("<HHHHH", 0x3E, 3, 0xFFFE, 9, 6)
        + bytes(6)
        + struct.pack("<IIIIIIIII", 0, fat_n, starts[0], 0, CUTOFF, starts[1], minifat_n, END, 0)
    )
    difat = list(range(fat_n)) + [FREE] * (109 - fat_n)
    header += struct.pack("<109I", *difat)
    body = struct.pack(f"<{len(fat)}I", *fat)
    body += directory.ljust(dir_n * SECTOR, b"\0")
    body += struct.pack(f"<{len(mini_fat)}I", *mini_fat).ljust(minifat_n * SECTOR, b"\xff")
    body += mini_stream.ljust(ministream_n * SECTOR, b"\0")
    for (_, data), n in zip(big, big_n, strict=True):
        body += data.ljust(n * SECTOR, b"\0")
    return header + body


def _lpstr(text: str, padded: bool = True) -> bytes:
    raw = text.encode("cp1250") + b"\0"
    return struct.pack("<I", len(raw)) + (
        raw.ljust(-(-len(raw) // 4) * 4, b"\0") if padded else raw
    )


def _property_set(properties: list[tuple[int, int, bytes]]) -> bytes:
    """One section; each property as (id, type, value bytes after the type header)."""
    header_size = 8 + 8 * len(properties)
    values = b""
    offsets: list[int] = []
    for _, vt, value in properties:
        offsets.append(header_size + len(values))
        values += (struct.pack("<I", vt) + value).ljust(-(-(4 + len(value)) // 4) * 4, b"\0")
    section = struct.pack("<II", header_size + len(values), len(properties))
    section += b"".join(
        struct.pack("<II", pid, off) for (pid, _, _), off in zip(properties, offsets, strict=True)
    )
    section += values
    return (
        struct.pack("<HHI", 0xFFFE, 0, 0)
        + bytes(16)
        + struct.pack("<I", 1)
        + bytes(16)
        + struct.pack("<I", 48)
        + section
    )


SUMMARY = _property_set(
    [
        (1, 0x02, struct.pack("<H", 1250)),
        (4, 0x1E, _lpstr(PERSON)),
        (8, 0x1E, _lpstr(PERSON)),
        (14, 0x03, struct.pack("<I", 2)),
    ]
)
# Word's habit: the strings in HeadingPairs and TitlesOfParts unpadded.
DOC_SUMMARY = _property_set(
    [
        (1, 0x02, struct.pack("<H", 1250)),
        (15, 0x1E, _lpstr(PERSON)),
        (
            12,
            0x100C,
            struct.pack("<I", 2)
            + struct.pack("<I", 0x1E)
            + _lpstr("Tytuł", False)
            + struct.pack("<II", 3, 1),
        ),
        (13, 0x101E, struct.pack("<I", 2) + _lpstr("Nota", False) + _lpstr(PERSON, False)),
    ]
)


def _word() -> list[tuple[str, bytes]]:
    saved_by = [PERSON, f"C:\\Users\\{USER}\\nota.doc"]
    sttb = struct.pack("<HHH", 0xFFFF, len(saved_by), 0) + b"".join(
        struct.pack("<H", len(s)) + s.encode("utf-16-le") for s in saved_by
    )
    table = b"\0" * 16 + sttb + b"\0" * 16
    fib = bytearray(32 + 2 + 28 + 2 + 88 + 2 + 93 * 8)
    struct.pack_into("<HH", fib, 0, 0xA5EC, 0xC1)
    struct.pack_into("<H", fib, 0x0A, 0x0200)  # the table stream is 1Table
    struct.pack_into("<H", fib, 32, 14)
    struct.pack_into("<H", fib, 32 + 2 + 28, 22)
    struct.pack_into("<H", fib, 32 + 2 + 28 + 2 + 88, 93)
    struct.pack_into("<II", fib, 32 + 2 + 28 + 2 + 88 + 2 + 71 * 8, 16, len(sttb))
    document = bytes(fib) + BODY.encode("utf-16-le")
    return [
        ("WordDocument", document.ljust(CUTOFF + 100, b"\0")),  # a regular-sector stream
        ("1Table", table),
        ("\x05SummaryInformation", SUMMARY),
        ("\x05DocumentSummaryInformation", DOC_SUMMARY),
    ]


def _workbook() -> bytes:
    def record(kind: int, payload: bytes) -> bytes:
        return struct.pack("<HH", kind, len(payload)) + payload

    access = (struct.pack("<HB", len(USER), 0) + USER.encode()).ljust(112, b" ")
    path = b"\x01" + f"C:\\Users\\{USER}\\dane.xls".encode()
    supbook = struct.pack("<HHB", 1, len(path) - 1, 0) + path[1:]
    self_ref = struct.pack("<HH", 3, 0x0401)
    return (
        record(0x0809, struct.pack("<HHHH", 0x0600, 0x0005, 0, 0))
        + record(0x005C, access)
        + record(0x01AE, supbook)
        + record(0x01AE, self_ref)
        + record(0x000A, b"")
        + record(0x0204, BODY.encode())
    )


def test_a_word_file_loses_summary_and_saved_by_in_place() -> None:
    doc = _compound(_word())
    assert _leaks(doc) and om.markers(doc, "f")
    out = om.strip(doc, "f")
    assert out is not None and out.actions
    assert len(out.data) == len(doc)
    assert not _leaks(out.data)
    assert om.markers(out.data, "f") == []
    assert BODY.encode("utf-16-le") in out.data
    assert "Tytuł".encode("cp1250") not in out.data  # every summary string, as for a PDF
    again = om.strip(out.data, "f")
    assert again is not None and (again.data, again.actions) == (out.data, [])


def test_an_excel_file_loses_write_access_and_external_paths() -> None:
    xls = _compound(
        [("Workbook", _workbook().ljust(CUTOFF, b"\0")), ("\x05SummaryInformation", SUMMARY)]
    )
    out = om.strip(xls, "f")
    assert out is not None and out.actions
    assert not _leaks(out.data)
    assert om.markers(out.data, "f") == []
    assert BODY.encode() in out.data


def test_a_word_file_before_word_97_is_refused() -> None:
    streams = _word()
    document = bytearray(streams[0][1])
    struct.pack_into("<H", document, 2, 0x65)  # Word 6
    with pytest.raises(RedactionError):
        redact_file(_compound([("WordDocument", bytes(document)), *streams[1:]]), "raw")


def test_a_broken_compound_file_is_refused_and_flagged() -> None:
    doc = bytearray(_compound(_word()))
    struct.pack_into("<I", doc, 0x30, 0x00FFFFFF)  # the directory's first sector, past the end
    with pytest.raises(RedactionError):
        redact_file(bytes(doc), "raw")
    assert personal_data_markers(bytes(doc))


# --- Inside downloads -----------------------------------------------------------------------------


def test_office_files_inside_a_statement_and_an_epuap_attachment_are_redacted() -> None:
    doc = _compound(_word())
    statement = (
        '<JednostkaInna xmlns="urn:x"><Plik><Nazwa>plik-1.doc</Nazwa>'
        f"<Zawartosc>{base64.b64encode(doc).decode()}</Zawartosc></Plik>"
        "<Plik><Nazwa>plik-2.docx</Nazwa>"
        f"<Zawartosc>{base64.b64encode(_docx()).decode()}</Zawartosc></Plik></JednostkaInna>"
    ).encode()
    download = _zip([("sf.xml", statement), ("nota.rtf", RTF)])
    assert personal_data_markers(download)
    out = redact_download(download, {REF: "sf.xml", "BBBBBBBBBBBBBBBBBBBB+w==": "nota.rtf"})
    assert out.changed
    assert personal_data_markers(out.data, [REF, "BBBBBBBBBBBBBBBBBBBB+w=="]) == []
    with zipfile.ZipFile(io.BytesIO(out.data)) as archive:
        for info in archive.infolist():
            assert not _leaks(archive.read(info))
            assert b"<Zawartosc>" not in archive.read(info) or all(
                not _leaks(base64.b64decode(part.split(b"</Zawartosc>")[0]))
                for part in archive.read(info).split(b"<Zawartosc>")[1:]
            )


def test_an_office_file_with_nothing_to_remove_is_returned_byte_for_byte() -> None:
    clean = om.strip(_docx(), "f")
    assert clean is not None
    again = redact_file(clean.data, "raw")
    assert again is not None and not again.changed and again.data == clean.data
    plain_zip = _zip([("a.txt", b"x")])
    assert om.strip(plain_zip, "f") is None
