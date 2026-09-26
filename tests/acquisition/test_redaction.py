"""Natural-person redaction of downloads (ADR 0009, invariant 6).

Signed inputs are built here with fake identities (PESEL 00000000000, which is
not a valid number), never from real filings.
"""

import base64
import io
import json
import zipfile
from pathlib import Path

import pymupdf
import pytest

from distress_radar.acquisition.redaction import (
    RedactionError,
    file_token,
    original_file_name,
    personal_data_markers,
    redact_download,
    redact_file,
    redact_rdf_detail,
)

DS = "http://www.w3.org/2000/09/xmldsig#"
# Invented filings: a document_ref shaped like RDF's (16 bytes, base64, with a '/') and its correction.
REF, REF2 = "AAAAAAAAAAAAAAAAAAAA/w==", "BBBBBBBBBBBBBBBBBBBB+w=="
TOKEN, TOKEN2 = "AAAAAAAAAAAAAAAAAAAA_w", "BBBBBBBBBBBBBBBBBBBB-w"
NAMES = {REF: "sf.xml"}
STATEMENT = (
    Path(__file__).parent.parent / "fixtures" / "statements" / "full_2018_v1_2_por_2022.xml"
).read_bytes()
FAKE_CERT_HEX = "3082" + "504E4F504C2D" + "3" + "03" * 10 + "0"  # "PNOPL-" + 11 digits as DER hex
SIGNATURE = (
    f'<ds:Signature xmlns:ds="{DS}"><ds:SignedInfo/><ds:KeyInfo><ds:X509Data>'
    "<ds:X509Certificate>MIIFakeCert</ds:X509Certificate></ds:X509Data></ds:KeyInfo>"
    '<ds:Object><xades:QualifyingProperties xmlns:xades="http://uri.etsi.org/01903/v1.3.2#">'
    "<ppZP:DaneZPOsobyFizycznej xmlns:ppZP='urn:pz'><os:PESEL xmlns:os='urn:os'>00000000000</os:PESEL>"
    "<os:Nazwisko xmlns:os='urn:os'>TESTOWY</os:Nazwisko></ppZP:DaneZPOsobyFizycznej>"
    "</xades:QualifyingProperties></ds:Object></ds:Signature>"
).encode()


def _signed(statement: bytes) -> bytes:
    """The statement with an enveloped signature before its closing tag."""
    end = statement.rindex(b"</")
    return statement[:end] + SIGNATURE + statement[end:]


def _zip(**members: bytes) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, data in members.items():
            archive.writestr(zipfile.ZipInfo(name, date_time=(2023, 6, 30, 12, 0, 0)), data)
    return buffer.getvalue()


def _signed_pdf() -> bytes:
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 72), "Informacja dodatkowa: przychody 1000 PLN")
    widget = pymupdf.Widget()
    widget.field_type = pymupdf.PDF_WIDGET_TYPE_SIGNATURE
    widget.field_name = "Signature1"
    widget.rect = pymupdf.Rect(72, 100, 272, 150)
    page.add_widget(widget)
    sig = doc.get_new_xref()
    doc.update_object(
        sig,
        f"<< /Type /Sig /Filter /Adobe.PPKLite /Name (Jan Testowy) /ByteRange [0 0 0 0] "
        f"/Contents <{FAKE_CERT_HEX}> >>",
    )
    signed_widget = next(iter(page.widgets()))
    doc.xref_set_key(signed_widget.xref, "V", f"{sig} 0 R")
    return doc.tobytes()


def _with_notes(statement: bytes, attachment: bytes) -> bytes:
    marker = b"<dtsf:Zawartosc>"
    start = statement.index(marker) + len(marker)
    end = statement.index(b"</dtsf:Zawartosc>")
    return statement[:start] + base64.b64encode(attachment) + statement[end:]


def test_clean_download_is_returned_byte_for_byte() -> None:
    raw = _zip(**{f"{TOKEN}.xml": STATEMENT})
    result = redact_download(raw, NAMES)
    assert result.data == raw and not result.changed
    assert personal_data_markers(raw) == [] and personal_data_markers(raw, [REF]) == []


def test_enveloped_signature_is_removed_and_data_kept() -> None:
    raw = _zip(**{"sf.xml": _signed(STATEMENT)})
    assert personal_data_markers(raw)
    result = redact_download(raw, NAMES)
    assert result.changed and personal_data_markers(result.data) == []
    [info] = zipfile.ZipFile(io.BytesIO(result.data)).infolist()
    assert (info.filename, info.date_time) == (f"{TOKEN}.xml", (2023, 6, 30, 12, 0, 0))
    redacted = zipfile.ZipFile(io.BytesIO(result.data)).read(f"{TOKEN}.xml")
    assert b"<dtsf:KwotaA>39402504.45</dtsf:KwotaA>" in redacted
    assert b"PESEL" not in redacted and b"X509" not in redacted


def test_redaction_is_deterministic_and_stable() -> None:
    raw = _zip(**{"sf.xml": _signed(_with_notes(STATEMENT, _signed_pdf()))})
    first, second = redact_download(raw, NAMES), redact_download(raw, NAMES)
    assert first.data == second.data
    assert not redact_download(first.data, NAMES).changed


def test_signature_containers_are_replaced_by_the_statement() -> None:
    inline = (
        f'<ds:Signature xmlns:ds="{DS}"><ds:SignedInfo/><ds:Object>'.encode()
        + STATEMENT.split(b"?>", 1)[1]
        + b"</ds:Object></ds:Signature>"
    )
    encoded = (
        f'<Signatures><ds:Signature xmlns:ds="{DS}"><ds:Object/>'
        f'<ds:Object Encoding="{DS}base64">{base64.b64encode(_signed(STATEMENT)).decode()}</ds:Object>'
        "</ds:Signature></Signatures>"
    ).encode()
    for container in (inline, encoded):
        result = redact_file(container, "zip:sf.xades")
        assert result is not None and result.changed
        assert result.data.lstrip().startswith(b"<?xml")
        assert b"<dtsf:KwotaA>39402504.45</dtsf:KwotaA>" in result.data
        assert personal_data_markers(result.data) == []


def test_detached_signature_is_dropped_from_the_zip() -> None:
    detached = f'<Signatures><ds:Signature xmlns:ds="{DS}"><ds:Object/></ds:Signature></Signatures>'
    raw = _zip(**{"sf.xml": STATEMENT, "sf.xml.XAdES": detached.encode()})
    result = redact_download(raw, NAMES)
    assert zipfile.ZipFile(io.BytesIO(result.data)).namelist() == [f"{TOKEN}.xml"]
    assert zipfile.ZipFile(io.BytesIO(result.data)).read(f"{TOKEN}.xml") == STATEMENT


def test_signed_pdf_in_the_notes_is_cleaned_and_keeps_its_text() -> None:
    pdf = _signed_pdf()
    assert personal_data_markers(pdf)
    raw = _zip(**{"sf.xml": _with_notes(STATEMENT, pdf)})
    assert any("Zawartosc" in m for m in personal_data_markers(raw))
    result = redact_download(raw, NAMES)
    assert personal_data_markers(result.data) == []
    xml = zipfile.ZipFile(io.BytesIO(result.data)).read(f"{TOKEN}.xml")
    start = xml.index(b"<dtsf:Zawartosc>") + len(b"<dtsf:Zawartosc>")
    cleaned = base64.b64decode(xml[start : xml.index(b"</dtsf:Zawartosc>")])
    assert b"Jan Testowy" not in cleaned and b"PNOPL" not in cleaned.upper()
    with pymupdf.open(stream=cleaned, filetype="pdf") as doc:
        assert "przychody 1000 PLN" in doc[0].get_text()
        assert not list(doc[0].widgets())


def test_epuap_envelope_signatures_and_attachment_are_cleaned() -> None:
    envelope = (
        (
            '<wnio:Dokument xmlns:wnio="http://epuap.gov.pl/fe-model-web/wzor_lokalny/EPUAP-----/podpisanyPlik/" '
            'xmlns:str="http://crd.gov.pl/xml/schematy/struktura/2009/11/16/"><wnio:TrescDokumentu><str:Zalaczniki>'
            f'<str:Zalacznik kodowanie="base64"><str:DaneZalacznika>{base64.b64encode(_signed_pdf()).decode()}'
            "</str:DaneZalacznika></str:Zalacznik></str:Zalaczniki></wnio:TrescDokumentu>"
        ).encode()
        + SIGNATURE
        + b"</wnio:Dokument>"
    )
    result = redact_file(envelope, "zip:SF.xml")
    assert result is not None and result.changed
    assert personal_data_markers(result.data) == []
    assert b"DaneZalacznika" in result.data


def test_encrypted_signed_pdf_is_refused() -> None:
    with pymupdf.open(stream=_signed_pdf(), filetype="pdf") as doc:
        encrypted = doc.tobytes(encryption=pymupdf.PDF_ENCRYPT_AES_256, owner_pw="o", user_pw="u")
    with pytest.raises(RedactionError, match="encrypted"):
        redact_download(encrypted)


def test_markers_ignore_lookalikes() -> None:
    assert personal_data_markers(b"<x>PNOPLc base64 text 504e4f504c</x>") == []
    assert personal_data_markers(b"<x>PNOPL-00000000000</x>")


# --- file names and document metadata (ADR 0009 second addendum, plan 0011) -----------------------


def test_the_token_is_the_document_ref_made_path_safe_plus_a_kept_extension() -> None:
    assert file_token(REF, "Sprawozdanie Jan Testowy.XML") == f"{TOKEN}.xml"
    assert file_token(REF2, "notes.exe") == TOKEN2  # an unknown extension is dropped
    assert file_token(REF, None) == TOKEN


def test_a_bundles_members_take_the_token_of_the_filing_that_names_them() -> None:
    raw = _zip(**{"dir/SF Jan.xml": STATEMENT, "dir/SF Jan korekta.xml": STATEMENT})
    names = {REF: "SF Jan.xml", REF2: "SF Jan korekta.xml"}
    result = redact_download(raw, names)
    assert zipfile.ZipFile(io.BytesIO(result.data)).namelist() == [f"{TOKEN}.xml", f"{TOKEN2}.xml"]
    assert personal_data_markers(result.data, [REF, REF2]) == []
    assert not any("Jan" in action for action in result.actions)


def test_a_member_no_filing_names_is_unmatched_not_guessed() -> None:
    raw = _zip(**{"a.xml": STATEMENT, "b.xml": STATEMENT})
    result = redact_download(raw, {REF: "a.xml", REF2: None})
    assert zipfile.ZipFile(io.BytesIO(result.data)).namelist() == [
        f"{TOKEN}.xml",
        "unmatched-1.xml",
    ]
    # Two members bearing one filing's name: neither is trusted.
    twice = redact_download(
        _zip(**{"x/a.xml": STATEMENT, "y/a.xml": STATEMENT}), {REF: "a.xml", REF2: "b.xml"}
    )
    assert zipfile.ZipFile(io.BytesIO(twice.data)).namelist() == [
        "unmatched-1.xml",
        "unmatched-2.xml",
    ]


def test_one_member_for_one_filing_takes_its_token_whatever_its_name() -> None:
    result = redact_download(_zip(**{"anything.xml": STATEMENT}), {REF: None})
    assert zipfile.ZipFile(io.BytesIO(result.data)).namelist() == [f"{TOKEN}.xml"]


def test_a_zip_cannot_be_named_without_its_filings() -> None:
    with pytest.raises(RedactionError, match="filings it holds"):
        redact_download(_zip(**{"sf.xml": STATEMENT}))


def test_markers_flag_a_member_name_that_is_not_a_filings_token() -> None:
    assert personal_data_markers(_zip(**{"SF Jan Testowy.xml": STATEMENT}))
    other = _zip(**{f"{TOKEN2}.xml": STATEMENT})
    assert personal_data_markers(other) == []  # shaped like a token
    assert personal_data_markers(other, [REF])  # but not this filing's


def test_attachment_names_become_placeholders() -> None:
    named = STATEMENT.replace(
        b"<dtsf:Nazwa>plik-1.pdf</dtsf:Nazwa>", b"<dtsf:Nazwa>Notes_Jan_Testowy.pdf</dtsf:Nazwa>"
    )
    assert named != STATEMENT and personal_data_markers(named)
    result = redact_file(named, "sf.xml")
    assert result is not None and result.data.count(b"<dtsf:Nazwa>plik-1.pdf</dtsf:Nazwa>") == 1
    assert personal_data_markers(result.data) == []


def test_an_epuap_attachment_name_becomes_a_placeholder() -> None:
    envelope = (
        '<wnio:Dokument xmlns:wnio="urn:w" xmlns:str="urn:s"><str:Zalacznik nazwaPliku="SF Jan.xml" '
        f'kodowanie="base64"><str:DaneZalacznika>{base64.b64encode(STATEMENT).decode()}'
        "</str:DaneZalacznika></str:Zalacznik></wnio:Dokument>"
    ).encode()
    assert personal_data_markers(envelope)
    result = redact_file(envelope, "zip:x")
    assert result is not None and b'nazwaPliku="zalacznik-1.xml"' in result.data
    assert personal_data_markers(result.data) == []


def test_pdf_metadata_is_removed_and_the_page_kept() -> None:
    doc = pymupdf.open()
    doc.new_page().insert_text((72, 72), "Informacja dodatkowa")
    doc.set_metadata({"author": "Jan Testowy", "title": "Notes"})
    doc.set_xml_metadata('<x:xmpmeta xmlns:x="adobe:ns:meta/">Jan Testowy</x:xmpmeta>')
    pdf = doc.tobytes()
    assert any("PDF metadata" in m for m in personal_data_markers(pdf))
    result = redact_file(pdf, "notes.pdf")
    assert result is not None and b"Jan Testowy" not in result.data
    assert personal_data_markers(result.data) == []
    assert not redact_file(result.data, "notes.pdf").changed  # type: ignore[union-attr]
    with pymupdf.open(stream=result.data, filetype="pdf") as cleaned:
        assert "Informacja dodatkowa" in cleaned[0].get_text()


def test_an_rdf_detail_keeps_everything_but_the_file_name() -> None:
    body = json.dumps(
        {
            "identyfikator": REF,
            "nazwaPliku": "SF podpisane Jan.xml",
            "nazwaPodmiotu": "Firma Sp. z o.o.",
        },
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode()
    assert original_file_name(body) == "SF podpisane Jan.xml"
    assert personal_data_markers(body)
    result = redact_rdf_detail(body)
    assert json.loads(result.data) == {
        "identyfikator": REF,
        "nazwaPliku": f"{TOKEN}.xml",
        "nazwaPodmiotu": "Firma Sp. z o.o.",
    }
    assert personal_data_markers(result.data) == [] and not redact_rdf_detail(result.data).changed
    with pytest.raises(RedactionError, match="identyfikator"):
        redact_rdf_detail(b'{"nazwaPliku":"a.xml"}')
