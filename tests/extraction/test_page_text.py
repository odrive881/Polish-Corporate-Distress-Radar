"""The text layer of embedded documents (plan 0013 step C). Synthetic PDFs only: real notes can
name people, and the committed fixtures carry a placeholder in their place (fixture README)."""

# PyMuPDF's signatures reference types pyright cannot resolve.
# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false
# pyright: reportUnknownArgumentType=false

from __future__ import annotations

import base64
from pathlib import Path

import pymupdf
import pytest

from distress_radar.extraction.page_text import (
    TEXT_PAGE_CHARS,
    AttachmentError,
    attachments,
    normalise,
    pages,
)

FIXTURES = Path(__file__).parent.parent / "fixtures" / "statements"
NS = "http://example.invalid/sf"
TEXT = (
    "Spółka kontynuuje działalność. Zarząd ocenia, że w okresie dwunastu miesięcy od dnia "
    "bilansowego nie występują okoliczności wskazujące na zagrożenie kontynuowania działalności. "
) * 3


def _pdf(*kinds: str) -> bytes:
    """A PDF with one page per kind: `text`, `image` (a scan) or `blank`."""
    doc = pymupdf.open()
    for kind in kinds:
        page = doc.new_page()
        if kind == "text":
            page.insert_textbox(pymupdf.Rect(50, 50, 550, 800), TEXT, fontname="helv")
        elif kind == "image":
            pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 20, 20), False)
            pix.set_rect(pix.irect, (200, 200, 200))
            page.insert_image(pymupdf.Rect(50, 50, 250, 250), pixmap=pix)
    data = doc.tobytes(garbage=3, deflate=True)
    doc.close()
    return data


def _statement(*payloads: bytes | str) -> bytes:
    """A statement-shaped XML with one `Plik` per payload (bytes are base64-encoded)."""
    pliki = "".join(
        f"<s:Plik><s:Nazwa>plik-{n}.pdf</s:Nazwa><s:Zawartosc>"
        f"{base64.b64encode(p).decode() if isinstance(p, bytes) else p}</s:Zawartosc></s:Plik>"
        for n, p in enumerate(payloads, start=1)
    )
    return f'<s:Sprawozdanie xmlns:s="{NS}"><s:Informacja>{pliki}</s:Informacja></s:Sprawozdanie>'.encode()


def test_attachments_are_found_in_document_order_with_their_kind() -> None:
    found = attachments(_statement(_pdf("text"), b"PK\x03\x04 office file", _pdf("blank")))
    assert [(a.index, a.kind) for a in found] == [(1, "pdf"), (2, "unsupported"), (3, "pdf")]
    assert found[0].element_path == "/Sprawozdanie/Informacja/Plik"


def test_the_fixtures_placeholder_is_not_a_pdf() -> None:
    found = attachments((FIXTURES / "full_2018_v1_2_por_2022.xml").read_bytes())
    assert [a.kind for a in found] == ["unsupported"]


def test_an_attachment_that_is_not_base64_is_an_error() -> None:
    with pytest.raises(AttachmentError, match="attachment_not_base64"):
        attachments(_statement("not base64 at all!"))


def test_pages_have_a_status_and_only_text_pages_carry_text() -> None:
    got = list(pages(_pdf("text", "image", "blank")))
    assert [(p.number, p.status) for p in got] == [(1, "text"), (2, "needs_ocr"), (3, "sparse")]
    assert len(got[0].text) >= TEXT_PAGE_CHARS and "kontynuowania" in got[0].text
    assert got[1].text == "" and got[2].text == ""


def test_the_same_bytes_give_the_same_pages() -> None:
    pdf = _pdf("text", "text")
    assert list(pages(pdf)) == list(pages(pdf))


def test_an_unreadable_or_encrypted_pdf_is_an_error_not_an_empty_document() -> None:
    with pytest.raises(AttachmentError, match="pdf_unreadable"):
        list(pages(b"%PDF-1.7 truncated"))
    doc = pymupdf.open(stream=_pdf("text"), filetype="pdf")
    locked = doc.tobytes(encryption=pymupdf.PDF_ENCRYPT_AES_256, user_pw="x", owner_pw="y")
    with pytest.raises(AttachmentError, match="pdf_encrypted"):
        list(pages(locked))


def test_normalise_is_stable_and_keeps_polish_letters() -> None:
    raw = "Zarząd  spółki \t ocenia \n\n\n\n  że   ZAŻÓŁĆ  \n"
    assert normalise(raw) == "Zarząd spółki ocenia\n\nże ZAŻÓŁĆ"
    assert normalise(normalise(raw)) == normalise(raw)
