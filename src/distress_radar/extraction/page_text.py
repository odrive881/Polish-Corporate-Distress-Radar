"""The text layer of the documents embedded in a statement (plan 0013 step C), without I/O.

A statement carries its notes as attachments (`Plik`: a name token and base64 content,
ADR 0009's second addendum). This finds them in document order and reads each PDF's text layer
page by page with PyMuPDF: plan 0006's tier 1, text only, no statement figures. Nothing is
guessed from a page without text:
- `text`: the page has a text layer;
- `needs_ocr`: almost no text but an image, i.e. a scan; recorded and counted, never read;
- `sparse`: almost no text and no image (a blank or signature page); its text is kept.
Office documents and anything else embedded are `unsupported`, counted, not opened.

The text is raw: it can name people, so no caller may store, send or print it before
`extraction.masking` (ADR 0009, third addendum). Extraction is deterministic: the same bytes
give the same pages and the same strings.
"""

# PyMuPDF's signatures reference types pyright cannot resolve; scoped to this module.
# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false
# pyright: reportUnknownArgumentType=false

from __future__ import annotations

import base64
import binascii
import re
import unicodedata
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Literal

import pymupdf
from lxml import etree

from distress_radar.parsing.containers import Element, safe_parser

AttachmentKind = Literal["pdf", "unsupported"]
PageStatus = Literal["text", "needs_ocr", "sparse"]
# Below this many characters a page counts as having no text layer (plan 0013 census: pages
# of real notes run to thousands; scans and blank pages to a handful).
TEXT_PAGE_CHARS = 200
_SPACES = re.compile(r"[ \t ]+")
_BLANK_LINES = re.compile(r"\n{3,}")


@dataclass(frozen=True)
class Attachment:
    index: int  # 1-based, in document order: the n of its `plik-<n>` name token
    element_path: str  # where it sits in the statement, by local names
    kind: AttachmentKind
    data: bytes


@dataclass(frozen=True)
class Page:
    number: int  # 1-based
    status: PageStatus
    text: str  # normalised; empty for `needs_ocr`


class AttachmentError(Exception):
    """An attachment that cannot be read. `reason_code` goes to quarantine."""

    def __init__(self, reason_code: str, detail: str) -> None:
        super().__init__(f"{reason_code}: {detail}")
        self.reason_code = reason_code
        self.detail = detail


def _local(el: Element) -> str:
    return etree.QName(el).localname


def _path(el: Element) -> str:
    return "/" + "/".join([*(_local(a) for a in reversed(list(el.iterancestors()))), _local(el)])


def attachments(statement: bytes) -> list[Attachment]:
    """Every `Plik` with content in a statement's XML, in document order."""
    root = etree.fromstring(statement, safe_parser())
    out: list[Attachment] = []
    for el in root.iter("{*}Plik"):
        content = next(el.iterchildren("{*}Zawartosc"), None)
        payload = "".join((content.text or "").split()) if content is not None else ""
        if not payload:
            continue
        try:
            data = base64.b64decode(payload, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise AttachmentError("attachment_not_base64", _path(el)) from exc
        kind: AttachmentKind = "pdf" if data.startswith(b"%PDF") else "unsupported"
        out.append(Attachment(len(out) + 1, _path(el), kind, data))
    return out


def normalise(text: str) -> str:
    """NFC, one space for runs of spaces, no trailing spaces, at most one blank line."""
    lines = (
        _SPACES.sub(" ", line).strip() for line in unicodedata.normalize("NFC", text).split("\n")
    )
    return _BLANK_LINES.sub("\n\n", "\n".join(lines)).strip()


def pages(pdf: bytes) -> Iterator[Page]:
    """The PDF's pages with their text layer. Raises `AttachmentError` when it cannot be opened."""
    try:
        document = pymupdf.open(stream=pdf, filetype="pdf")
    except (RuntimeError, ValueError) as exc:  # PyMuPDF's FileDataError is a RuntimeError
        raise AttachmentError("pdf_unreadable", str(exc)[:200]) from exc
    with document:
        if bool(document.needs_pass):
            raise AttachmentError("pdf_encrypted", "a password is required")
        for index in range(int(document.page_count)):
            page = document.load_page(index)
            number = index + 1
            text = normalise(str(page.get_text("text", sort=True)))
            if len(text) >= TEXT_PAGE_CHARS:
                yield Page(number, "text", text)
            elif page.get_images():
                yield Page(number, "needs_ocr", "")
            else:
                yield Page(number, "sparse", text)
