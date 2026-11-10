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


# --- reading order (plan 0025) --------------------------------------------------------------------

# ASCII only: the test font draws no Polish letters.
HEADINGS = (
    "Propozycje co do sposobu podzialu zysku lub pokrycia straty za rok obrotowy",
    "Dane o stanie rezerw wedlug celu ich utworzenia na poczatek roku obrotowego",
    "Podzial zobowiazan dlugoterminowych wedlug pozycji bilansu o pozostalym okresie splaty",
)
ANSWERS = (
    "Z uwagi na toczace sie postepowanie nie przedstawia sie sposobu pokrycia straty netto.",
    "Spolka nie tworzyla rezerw w okresie sprawozdawczym ani w okresie poprzedzajacym go.",
    "Wszystkie zobowiazania spolki staly sie wymagalne z dniem otwarcia postepowania sadowego.",
)
INTRO = (
    "Informacja dodatkowa do sprawozdania finansowego za rok obrotowy, sporzadzona zgodnie z "
    "zalacznikiem do ustawy o rachunkowosci, obejmuje ponizsze pozycje."
)


def _table_pdf(
    right: tuple[str, ...], *, ruled: bool, width: float = 150, mirror: bool = False
) -> bytes:
    """One page: a row per heading, the heading in a left cell `width` wide and `right` beside it in
    a right cell (200 wide, or as wide as the left with `mirror`), both wrapping onto several
    lines; `ruled` draws the cells' borders."""
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_textbox(pymupdf.Rect(50, 40, 550, 75), INTRO, fontname="helv", fontsize=9)
    y = 80.0
    for heading, answer in zip(HEADINGS, right, strict=True):
        left_cell = pymupdf.Rect(50, y, 50 + width, y + 80)
        right_cell = pymupdf.Rect(54 + width, y, 54 + width + (width if mirror else 200), y + 80)
        page.insert_textbox(left_cell + (2, 2, -2, -2), heading, fontname="helv", fontsize=10)
        page.insert_textbox(right_cell + (2, 2, -2, -2), answer, fontname="helv", fontsize=10)
        if ruled:
            page.draw_rect(left_cell, color=(0, 0, 0), width=0.5)
            page.draw_rect(right_cell, color=(0, 0, 0), width=0.5)
        y += 80
    data = doc.tobytes(garbage=3, deflate=True)
    doc.close()
    return data


def _sorted_text(pdf: bytes) -> str:
    with pymupdf.open(stream=pdf, filetype="pdf") as doc:
        return normalise(str(doc.load_page(0).get_text("text", sort=True)))


def _chars(text: str) -> list[str]:
    return sorted(c for c in text if not c.isspace())


def _joined(text: str) -> str:
    return " ".join(text.split())


@pytest.mark.parametrize("ruled", [True, False], ids=["ruled", "unruled"])
def test_a_two_column_table_is_read_cell_by_cell(ruled: bool) -> None:
    pdf = _table_pdf(ANSWERS, ruled=ruled)
    before = _sorted_text(pdf)
    assert not all(answer in _joined(before) for answer in ANSWERS)  # sort=True reads it across
    (page,) = pages(pdf)
    assert page.reordered
    flat = _joined(page.text)
    for heading, answer in zip(HEADINGS, ANSWERS, strict=True):
        assert heading in flat and answer in flat
        assert flat.index(heading) < flat.index(answer)  # each row: heading, then its answer
    assert [flat.index(h) for h in HEADINGS] == sorted(flat.index(h) for h in HEADINGS)
    assert _chars(page.text) == _chars(before)  # the same characters, none lost or doubled
    assert list(pages(pdf)) == [page]  # the same string every time


def test_a_one_line_answer_beside_a_heading_is_not_reordered() -> None:
    pdf = _table_pdf(("Nie dotyczy.",) * 3, ruled=True)
    (page,) = pages(pdf)
    assert not page.reordered and page.text == _sorted_text(pdf)


def test_the_same_text_in_both_columns_is_not_reordered() -> None:
    pdf = _table_pdf(HEADINGS, ruled=True, mirror=True)
    (page,) = pages(pdf)
    assert not page.reordered and page.text == _sorted_text(pdf)


def test_labels_beside_their_figures_are_not_reordered() -> None:
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_textbox(pymupdf.Rect(50, 40, 550, 120), INTRO, fontname="helv", fontsize=10)
    for n, label in enumerate(HEADINGS):
        y = 140 + 30 * n
        page.insert_textbox(pymupdf.Rect(50, y, 300, y + 28), label, fontname="helv", fontsize=10)
        page.insert_textbox(pymupdf.Rect(400, y, 550, y + 28), f"{n + 1} 234,56", fontname="helv")
    pdf = doc.tobytes()
    doc.close()
    (read,) = pages(pdf)
    assert not read.reordered and read.text == _sorted_text(pdf)


def test_a_single_column_page_reads_as_sort_true_did() -> None:
    pdf = _pdf("text")
    (page,) = pages(pdf)
    assert not page.reordered and page.text == _sorted_text(pdf)
