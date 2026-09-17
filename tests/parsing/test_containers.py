"""C1 container unwrapping and member-to-filing matching (plan 0004 step D).

The wrapper shapes mirror what the seed downloads contain (plan 0004 survey),
built here around a trimmed statement so no real signature is committed.
"""

import base64
import io
import zipfile
from pathlib import Path

import pytest

from distress_radar.parsing.containers import (
    DS_NS,
    ContainerError,
    ContainerMember,
    FilingRow,
    match_members,
    unwrap,
)

STATEMENT = (
    Path(__file__).parent.parent / "fixtures" / "statements" / "full_2018_v1_2_por_2022.xml"
).read_bytes()
STATEMENT_BODY = STATEMENT.split(b"?>", 1)[1]  # without the XML declaration, for inlining
PDF = b"%PDF-1.4\n%fixture\n"
DETACHED = f'<Signatures><ds:Signature xmlns:ds="{DS_NS}"><ds:Object/></ds:Signature></Signatures>'.encode()


def _zip(**members: bytes) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, data in members.items():
            archive.writestr(name.replace("__", "/"), data)
    return buffer.getvalue()


def _enveloping(inner: bytes) -> bytes:
    return (
        f'<ds:Signature xmlns:ds="{DS_NS}"><ds:SignedInfo/><ds:Object><xades/></ds:Object>'
        f'<ds:Object MimeType="text/xml">'.encode()
        + inner
        + b"</ds:Object></ds:Signature>"
    )


def _base64_signatures(inner: bytes) -> bytes:
    payload = base64.b64encode(inner).decode()
    return (
        f'<Signatures><ds:Signature xmlns:ds="{DS_NS}"><ds:Object/>'
        f'<ds:Object Encoding="{DS_NS}base64" MimeType="text/plain">{payload}</ds:Object>'
        f"</ds:Signature></Signatures>"
    ).encode()


def _epuap(inner: bytes) -> bytes:
    payload = base64.b64encode(inner).decode()
    return (
        '<wnio:Dokument xmlns:wnio="http://epuap.gov.pl/fe-model-web/wzor_lokalny/EPUAP-----/podpisanyPlik/" '
        'xmlns:str="http://crd.gov.pl/xml/schematy/struktura/2009/11/16/"><wnio:TrescDokumentu>'
        '<str:Zalaczniki><str:Zalacznik format="application/pdf" kodowanie="base64" nazwaPliku="eA==">'
        f"<str:DaneZalacznika>{payload}</str:DaneZalacznika></str:Zalacznik></str:Zalaczniki>"
        "</wnio:TrescDokumentu></wnio:Dokument>"
    ).encode()


def test_plain_statement_member() -> None:
    [member] = unwrap(_zip(**{"SF__report.xml": STATEMENT}))
    assert member == ContainerMember("zip:SF/report.xml", "report.xml", "xml_statement", STATEMENT)


def test_bom_is_stripped() -> None:
    [member] = unwrap(_zip(**{"report.xml": b"\xef\xbb\xbf" + STATEMENT}))
    assert member.data == STATEMENT


def test_enveloping_signature_with_inline_statement() -> None:
    [member] = unwrap(_zip(**{"report.xml.xades": _enveloping(STATEMENT_BODY)}))
    assert member.kind == "xml_statement"
    assert member.source_member == "zip:report.xml.xades>ds:Object[2]"
    assert b"<JednostkaInna" in member.data or b":JednostkaInna" in member.data


def test_base64_signatures_container() -> None:
    [member] = unwrap(_zip(**{"report.xml.XAdES": _base64_signatures(STATEMENT)}))
    assert member.kind == "xml_statement"
    assert member.source_member == "zip:report.xml.XAdES>ds:Object[2]>base64"
    assert member.data == STATEMENT


def test_epuap_envelope_with_pdf() -> None:
    [member] = unwrap(_zip(**{"SF2023__SF2023.xml": _epuap(PDF)}))
    assert member.kind == "pdf"
    assert member.source_member == "zip:SF2023/SF2023.xml>epuap:Zalacznik[1]>base64"
    assert member.data == PDF


def test_detached_signature_next_to_statement() -> None:
    members = unwrap(_zip(**{"report.xml": STATEMENT, "report.xml.XAdES": DETACHED}))
    assert [m.kind for m in members] == ["xml_statement", "detached_signature"]


def test_non_zip_input_is_one_member() -> None:
    [member] = unwrap(STATEMENT)
    assert member.source_member == "raw"


@pytest.mark.parametrize(
    ("data", "reason"),
    [
        (b"PK\x03\x04 not really a zip", "unknown_container"),
        (_zip(**{"x.bin": b"\x00\x01binary"}), "unknown_container"),
        (_zip(**{"x.xml": b"<other xmlns='urn:x'/>"}), "unknown_container"),
        (_zip(**{"x.xml": b"<unclosed>"}), "xml_malformed"),
        (
            _zip(
                **{
                    "x.xml": b"<Signatures><ds:Signature xmlns:ds='" + DS_NS.encode() + b"'>"
                    b"<ds:Object Encoding='" + DS_NS.encode() + b"base64'>!!notbase64</ds:Object>"
                    b"</ds:Signature></Signatures>"
                }
            ),
            "unknown_container",
        ),
    ],
)
def test_unrecognised_containers_raise(data: bytes, reason: str) -> None:
    with pytest.raises(ContainerError) as info:
        unwrap(data)
    assert info.value.reason_code == reason


def _member(name: str, kind: str = "xml_statement") -> ContainerMember:
    return ContainerMember(f"zip:{name}", name, kind, b"")  # type: ignore[arg-type]


def test_single_statement_and_row_pair_regardless_of_name() -> None:
    matched, unmatched = match_members(
        [_member("a.xml"), _member("a.XAdES", "detached_signature")],
        [FilingRow("ref1", "different.xml")],
    )
    assert matched == {"zip:a.xml": FilingRow("ref1", "different.xml")}
    assert unmatched == []


def test_statement_and_correction_match_by_name() -> None:
    rows = [FilingRow("orig", "sf.xml"), FilingRow("corr", "sf_korekta.xml")]
    matched, unmatched = match_members([_member("sf.xml"), _member("sf_korekta.xml")], rows)
    assert {k: v.document_ref for k, v in matched.items()} == {
        "zip:sf.xml": "orig",
        "zip:sf_korekta.xml": "corr",
    }
    assert unmatched == []


def test_unmatched_and_ambiguous_members_are_returned() -> None:
    rows = [FilingRow("orig", "sf.xml"), FilingRow("corr", "sf_korekta.xml")]
    matched, unmatched = match_members([_member("sf.xml"), _member("other.xml")], rows)
    assert list(matched) == ["zip:sf.xml"]
    assert [m.source_member for m in unmatched] == ["zip:other.xml"]

    duplicate = [_member("a/sf.xml"), _member("b/sf.xml")]
    duplicate = [
        ContainerMember(m.source_member, "sf.xml", "xml_statement", b"") for m in duplicate
    ]
    matched, unmatched = match_members(duplicate, rows)
    assert matched == {}
    assert len(unmatched) == 2
