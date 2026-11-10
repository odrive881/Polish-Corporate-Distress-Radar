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

Reading order (plan 0025, `READING_ORDER_VERSION`). A page is read with `sort=True`, top to bottom
across its width, except where that reads a two-column layout across itself: notes laid out as a
table, the item's heading on the left and the company's answer on the right, both wrapping. Such a
page is `reordered`: every text line is emitted once, a line inside a ruled table's cell with its
cell (rows top to bottom, cells left to right), the rest in bands split by vertical whitespace, each
band column by column. It holds exactly the characters `sort=True` read, in another order.
"""

# PyMuPDF's signatures reference types pyright cannot resolve; scoped to this module.
# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false
# pyright: reportUnknownArgumentType=false

from __future__ import annotations

import base64
import binascii
import re
import statistics
import unicodedata
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from itertools import pairwise
from typing import Any, Literal

import pymupdf
from lxml import etree

from distress_radar.parsing.containers import Element, safe_parser

AttachmentKind = Literal["pdf", "unsupported"]
PageStatus = Literal["text", "needs_ocr", "sparse"]
# Below this many characters a page counts as having no text layer (plan 0013 census: pages
# of real notes run to thousands; scans and blank pages to a handful).
TEXT_PAGE_CHARS = 200
# Version 1: every page with `sort=True`. Version 2: two-column pages reordered (plan 0025).
READING_ORDER_VERSION = "2"
# A segment of running text has at least this many words; a gutter is wider than this, in points.
_PROSE_WORDS = 3
_GUTTER = 2.0
# Two lines share a printed row when they overlap by more than this share of the lower one's height;
# a band of rows ends at a vertical gap wider than this share of the page's median line height.
_ROW_OVERLAP = 0.5
_BAND_GAP = 0.6
_WORD = re.compile(r"[^\W\d_]{2,}")
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
    reordered: bool = False  # read cell by cell or column by column, not with `sort=True`


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
                lines = _lines(page)
                if _reads_across(lines, text):
                    yield Page(number, "text", _by_cells_and_columns(page, lines), reordered=True)
                else:
                    yield Page(number, "text", text)
            elif page.get_images():
                yield Page(number, "needs_ocr", "")
            else:
                yield Page(number, "sparse", text)


# --- reading order (plan 0025) -------------------------------------------------------------------

Box = tuple[float, float, float, float]  # x0, y0, x1, y1
Line = tuple[Box, str]


def _lines(page: Any) -> list[Line]:
    """Every non-empty text line of the page with its box, top to bottom, then left to right."""
    out: list[Line] = []
    for block in page.get_text("dict")["blocks"]:
        for line in block.get("lines", []):
            text = "".join(str(span["text"]) for span in line["spans"]).strip()
            if text:
                x0, y0, x1, y1 = (float(v) for v in line["bbox"])
                out.append(((x0, y0, x1, y1), text))
    out.sort(key=lambda ln: (ln[0][1], ln[0][0]))
    return out


def _rows(lines: Sequence[Line]) -> list[list[Line]]:
    """Lines grouped into printed rows: each joins the first row whose first line it overlaps."""
    rows: list[list[Line]] = []
    for line in lines:
        box = line[0]
        for row in rows:
            first = row[0][0]
            overlap = min(first[3], box[3]) - max(first[1], box[1])
            height = min(first[3] - first[1], box[3] - box[1])
            if height > 0 and overlap > _ROW_OVERLAP * height:
                row.append(line)
                break
        else:
            rows.append([line])
    return rows


def _side_by_side(row: Sequence[Line]) -> list[tuple[str, str]]:
    """The pairs of running-text segments in a row with a gutter between them, left to right."""
    prose = sorted(
        (ln for ln in row if len(_WORD.findall(ln[1])) >= _PROSE_WORDS), key=lambda ln: ln[0][0]
    )
    return [
        (left[1], right[1]) for left, right in pairwise(prose) if right[0][0] - left[0][2] > _GUTTER
    ]


def _reads_across(lines: Sequence[Line], text: str) -> bool:
    """Whether `sort=True` read a two-column layout across itself: two consecutive rows hold running
    text side by side, and some output line joins a pair of such segments. A pair whose texts are
    equal, or one inside the other (a current-year and a prior-year column), cannot be told apart
    in the output and is not counted."""
    pairs: list[tuple[str, str]] = []
    run = longest = 0
    for row in _rows(lines):
        found = _side_by_side(row)
        pairs.extend(found)
        run = run + 1 if found else 0
        longest = max(longest, run)
    if longest < 2:
        return False
    out = text.split("\n")
    return any(
        not (left in right or right in left) and any(left in ln and right in ln for ln in out)
        for left, right in pairs
    )


def _centre(box: Box) -> tuple[float, float]:
    return ((box[0] + box[2]) / 2, (box[1] + box[3]) / 2)


def _inside(point: tuple[float, float], box: Box) -> bool:
    return box[0] <= point[0] <= box[2] and box[1] <= point[1] <= box[3]


def _columns(lines: Sequence[Line]) -> list[str]:
    """One band's lines, column by column: the columns are the runs of the band's x-extents that
    no gutter separates, left to right; each column's lines top to bottom."""
    spans: list[list[float]] = []
    for x0, x1 in sorted((ln[0][0], ln[0][2]) for ln in lines):
        if spans and x0 <= spans[-1][1] + _GUTTER:
            spans[-1][1] = max(spans[-1][1], x1)
        else:
            spans.append([x0, x1])
    out: list[str] = []
    for lo, hi in spans:
        mine = sorted(
            (ln for ln in lines if lo <= _centre(ln[0])[0] <= hi),
            key=lambda ln: (ln[0][1], ln[0][0]),
        )
        out.extend(ln[1] for ln in mine)
    return out


def _bands(lines: Sequence[Line]) -> list[str]:
    """Lines outside the tables, in bands split by vertical whitespace, each read by columns."""
    if not lines:
        return []
    rows = sorted(_rows(lines), key=lambda r: min(ln[0][1] for ln in r))
    height = statistics.median(ln[0][3] - ln[0][1] for ln in lines)
    out: list[str] = []
    band = [rows[0]]
    for previous, row in pairwise(rows):
        gap = min(ln[0][1] for ln in row) - max(ln[0][3] for ln in previous)
        if gap > _BAND_GAP * height:
            out.extend([*_columns([ln for r in band for ln in r]), ""])
            band = [row]
        else:
            band.append(row)
    out.extend([*_columns([ln for r in band for ln in r]), ""])
    return out


def _by_cells_and_columns(page: Any, lines: Sequence[Line]) -> str:
    """The page with every line once: a line inside a ruled table's cell with its cell, the rest by
    `_bands`, the free lines between two table rows kept between them."""
    taken: set[int] = set()
    table_rows: list[tuple[float, list[str]]] = []
    for table in page.find_tables().tables:
        for row in table.rows:
            cells: list[Box] = [
                (float(c[0]), float(c[1]), float(c[2]), float(c[3])) for c in row.cells if c
            ]
            texts: list[str] = []
            for cell in sorted(cells, key=lambda c: c[0]):
                mine = sorted(
                    (
                        i
                        for i, ln in enumerate(lines)
                        if i not in taken and _inside(_centre(ln[0]), cell)
                    ),
                    key=lambda i: (lines[i][0][1], lines[i][0][0]),
                )
                taken.update(mine)
                texts.extend(lines[i][1] for i in mine)
            if texts:
                table_rows.append((min(c[1] for c in cells), texts))
    table_rows.sort(key=lambda r: r[0])
    tops = [top for top, _texts in table_rows]
    free: list[list[Line]] = [[] for _ in range(len(table_rows) + 1)]
    for i, line in enumerate(lines):
        if i not in taken:
            free[sum(1 for top in tops if top <= line[0][1])].append(line)
    out: list[str] = []
    for k, group in enumerate(free):
        out.extend(_bands(group))
        if k < len(table_rows):
            out.extend([*table_rows[k][1], ""])
    return normalise("\n".join(out))
