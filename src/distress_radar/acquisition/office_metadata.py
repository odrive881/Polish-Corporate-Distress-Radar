"""Invariant 6 for office files: the people an office document names in its metadata (ADR 0009).

Statement notes and some auditor reports are filed as Word, Excel, OpenDocument or RTF files,
attached inside a statement's XML, an ePUAP envelope or a ZIP. Like a PDF's information dictionary
(ADR 0009 second addendum, rule 4), these formats record who wrote and who last saved the file.
Redaction version 5 removes that before a file is hashed and stored, and `markers` is the
independent check (ADR 0009 fourth addendum):

- **OOXML** (`.docx`, `.xlsx`, `.pptx`; a ZIP with `[Content_Types].xml`): external links to
  local or network files lose their target (a path can hold a Windows user name); `docProps/core.xml`
  loses every property, `docProps/custom.xml` every custom property, `docProps/app.xml` its
  company, manager and hyperlink base; in the other parts, comment and revision `author` and
  `initials` attributes, the comment author list (`xl/comments*.xml`) and the people parts'
  `displayName` / `userId` / `name` are blanked.
- **OpenDocument** (a ZIP whose `mimetype` is `application/vnd.oasis.opendocument.*`): in every
  XML part, `dc:creator` (the document's, a comment's or a tracked change's), `meta:initial-creator`,
  `meta:printed-by` and the user-defined properties are blanked, and `meta.xml` loses its title,
  subject, description and keywords.
- **Flat XML** of either family (Word's `pkg:package`, `office:document`): the same, part by part.
- **RTF**: the `{\\info …}` group, annotation authors (`{\\*\\atnauthor …}`, `{\\*\\atnid …}`) and
  the revision author table (`{\\*\\revtbl …}`) lose their content.
- **OLE compound files** (`.doc`, `.xls`, `.ppt`), changed in place so every offset stays valid:
  every string of the `SummaryInformation` and `DocumentSummaryInformation` property sets
  (anywhere in the file, embedded objects included) is zeroed; Word's saved-by history, revision
  and comment author tables, autosave source and linked file names (the FIB's `SttbSavedBy`,
  `SttbfRMark`, `GrpXstAtnOwners`, `AutosaveSource`, `SttbFnm`) keep their lengths and lose their
  characters; Excel's `WriteAccess` record (the user who saved) is blanked; PowerPoint's
  `Current User` stream loses its user name; an Excel link to another workbook (`SupBook`) loses
  its path.

Embedded files (OOXML `embeddings/`, binary parts of flat XML) are redacted the same way. A file
of one of these formats that cannot be read safely raises `OfficeMetadataError`, which refuses
the download. Body text is not touched: a person named in the notes is ADR 0009's free-text
residual, as for PDFs.
"""

from __future__ import annotations

import base64
import binascii
import io
import re
import struct
import zipfile
from collections.abc import Callable
from dataclasses import dataclass

from lxml import etree

OLE_MAGIC = bytes.fromhex("d0cf11e0a1b11ae1")
_RTF_MAGIC = b"{\\rtf"
_ODF_MIME = b"application/vnd.oasis.opendocument"

DC_NS = "http://purl.org/dc/elements/1.1/"
ODF_META_NS = "urn:oasis:names:tc:opendocument:xmlns:meta:1.0"
ODF_OFFICE_NS = "urn:oasis:names:tc:opendocument:xmlns:office:1.0"
PKG_NS = "http://schemas.microsoft.com/office/2006/xmlPackage"

# OOXML parts outside docProps: attributes and elements naming a comment or revision author.
_AUTHOR_ATTRIBUTES = frozenset({"author", "initials"})
_PEOPLE_ATTRIBUTES = frozenset({"displayName", "userId", "name", "providerId"})
# An external relationship to a local or network file: its path can hold a Windows user name.
_LOCAL_PATH = re.compile(r"(?i)file:|[a-z]:[\\/]|\\\\|/(?:users|home)/")
_APP_REMOVED = frozenset({"Company", "Manager", "HyperlinkBase", "HLinks"})
# OpenDocument: what is blanked in any part, and what only in meta.xml.
_ODF_PERSON = frozenset(
    {
        (DC_NS, "creator"),
        (ODF_META_NS, "initial-creator"),
        (ODF_META_NS, "printed-by"),
        (ODF_META_NS, "user-defined"),
    }
)
_ODF_META_TEXT = frozenset(
    {(DC_NS, "title"), (DC_NS, "subject"), (DC_NS, "description"), (ODF_META_NS, "keyword")}
)


class OfficeMetadataError(Exception):
    """An office file whose metadata cannot be read safely; it must not be stored."""


@dataclass
class Stripped:
    data: bytes
    actions: list[str]


def _parser() -> etree.XMLParser:
    return etree.XMLParser(huge_tree=True, resolve_entities=False, no_network=True, load_dtd=False)


def kind(data: bytes) -> str | None:
    """`ole`, `rtf`, `ooxml` or `odf` when the bytes are an office file; else None."""
    if data.startswith(OLE_MAGIC):
        return "ole"
    if data.startswith(_RTF_MAGIC):
        return "rtf"
    if data.startswith(b"PK"):
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as archive:
                names = set(archive.namelist())
                if "[Content_Types].xml" in names:
                    return "ooxml"
                if "mimetype" in names and archive.read("mimetype").startswith(_ODF_MIME):
                    return "odf"
        except (zipfile.BadZipFile, zipfile.LargeZipFile, KeyError, ValueError):
            return None
    return None


def strip(data: bytes, where: str) -> Stripped | None:
    """The file without its people metadata; None when it is not an office file.

    Returns the bytes unchanged (no actions) when there was nothing to remove.
    """
    found = kind(data)
    if found == "ole":
        return _strip_ole(data, where)
    if found == "rtf":
        return _strip_rtf(data, where)
    if found in ("ooxml", "odf"):
        return _strip_package(data, where, found)
    return None


def markers(data: bytes, where: str) -> list[str] | None:
    """What people metadata is left in an office file (never its value); None if not one."""
    found = kind(data)
    if found == "ole":
        return _ole_markers(data, where)
    if found == "rtf":
        return _rtf_markers(data, where)
    if found in ("ooxml", "odf"):
        return _package_markers(data, where, found)
    return None


# --- XML parts (zipped or flat) -----------------------------------------------------------------


def _local(el: etree._Element) -> str:  # pyright: ignore[reportPrivateUsage]
    return etree.QName(el).localname


def _qname(el: etree._Element) -> tuple[str, str]:  # pyright: ignore[reportPrivateUsage]
    q = etree.QName(el)
    return (q.namespace or "", q.localname)


def _ooxml_part(root: etree._Element, part: str) -> int:  # pyright: ignore[reportPrivateUsage]
    """Blank one OOXML part's people metadata in place; the number of values removed."""
    removed = 0
    name = part.lstrip("/")
    if name == "docProps/core.xml" or name == "docProps/custom.xml":
        for child in list(root):
            root.remove(child)
            removed += 1
        return removed
    if name == "docProps/app.xml":
        for child in list(root.iterchildren(etree.Element)):
            if _local(child) in _APP_REMOVED:
                root.remove(child)
                removed += 1
        return removed
    if name.endswith(".rels"):
        for rel in root.iter(etree.Element):
            target = rel.get("Target") or ""
            if rel.get("TargetMode") == "External" and target and _LOCAL_PATH.match(target):
                rel.set("Target", "")
                removed += 1
        return removed
    people = "person" in name.rsplit("/", 1)[-1].lower() or name.endswith("commentAuthors.xml")
    for el in root.iter(etree.Element):
        for attribute, value in list(el.attrib.items()):
            local = etree.QName(attribute).localname
            if value and (local in _AUTHOR_ATTRIBUTES or (people and local in _PEOPLE_ATTRIBUTES)):
                el.set(attribute, "")
                removed += 1
        if _local(el) == "author" and (el.text or "").strip():
            parent = el.getparent()
            if parent is not None and _local(parent) == "authors":
                el.text = ""
                removed += 1
    return removed


def _odf_part(root: etree._Element, part: str) -> int:  # pyright: ignore[reportPrivateUsage]
    removed = 0
    meta = part.lstrip("/").rsplit("/", 1)[-1] == "meta.xml"
    for el in root.iter(etree.Element):
        key = _qname(el)
        if (key in _ODF_PERSON or (meta and key in _ODF_META_TEXT)) and (
            (el.text or "").strip() or len(el)
        ):
            for child in list(el):
                el.remove(child)
            el.text = ""
            removed += 1
    return removed


def _odf_flat(root: etree._Element) -> int:  # pyright: ignore[reportPrivateUsage]
    """A flat OpenDocument file: `office:meta` is its meta.xml, the rest its content."""
    removed = _odf_part(root, "content.xml")
    for meta in root.iter(f"{{{ODF_OFFICE_NS}}}meta"):
        removed += _odf_part(meta, "meta.xml")
    return removed


def strip_flat(
    root: etree._Element,  # pyright: ignore[reportPrivateUsage]
    where: str,
    redact_payload: Callable[[bytes, str], bytes | None],
) -> list[str]:
    """Blank a flat Word (`pkg:package`) or OpenDocument (`office:document`) tree in place.

    `redact_payload` redacts an embedded binary part (`pkg:binaryData`): the redacted bytes, or
    None when nothing changed. Returns the actions; an empty list for any other XML.
    """
    key = _qname(root)
    if key == (ODF_OFFICE_NS, "document"):
        n = _odf_flat(root)
        return [f"{where}: blanked {n} OpenDocument people fields"] if n else []
    if key != (PKG_NS, "package"):
        return []
    actions: list[str] = []
    removed = 0
    for part in root.iter(f"{{{PKG_NS}}}part"):
        name = part.get(f"{{{PKG_NS}}}name") or ""
        for data in part.iter(f"{{{PKG_NS}}}xmlData"):
            for child in data.iterchildren(etree.Element):
                removed += _ooxml_part(child, name)
        for binary in part.iter(f"{{{PKG_NS}}}binaryData"):
            try:
                payload = base64.b64decode("".join((binary.text or "").split()), validate=True)
            except (binascii.Error, ValueError) as exc:
                raise OfficeMetadataError(f"{where}: {name}: invalid base64") from exc
            new = redact_payload(payload, f"{where}>{name}")
            if new is not None:
                binary.text = base64.b64encode(new).decode("ascii")
                actions.append(f"{where}>{name}: embedded file redacted")
    if removed:
        actions.insert(0, f"{where}: blanked {removed} OOXML people fields")
    return actions


def flat_markers(root: etree._Element, where: str) -> list[str]:  # pyright: ignore[reportPrivateUsage]
    """People metadata left in a flat office tree (embedded binaries are checked by the caller)."""
    copy = etree.fromstring(etree.tostring(root), _parser())
    key = _qname(copy)
    if key == (ODF_OFFICE_NS, "document"):
        return [f"{where}: OpenDocument people metadata"] if _odf_flat(copy) else []
    if key != (PKG_NS, "package"):
        return []
    found = 0
    for part in copy.iter(f"{{{PKG_NS}}}part"):
        name = part.get(f"{{{PKG_NS}}}name") or ""
        for data in part.iter(f"{{{PKG_NS}}}xmlData"):
            for child in data.iterchildren(etree.Element):
                found += _ooxml_part(child, name)
    return [f"{where}: OOXML people metadata"] if found else []


def flat_binaries(root: etree._Element) -> list[tuple[str, bytes]]:  # pyright: ignore[reportPrivateUsage]
    """A flat Word file's embedded binary parts, for the marker check."""
    if _qname(root) != (PKG_NS, "package"):
        return []
    out: list[tuple[str, bytes]] = []
    for part in root.iter(f"{{{PKG_NS}}}part"):
        for binary in part.iter(f"{{{PKG_NS}}}binaryData"):
            try:
                payload = base64.b64decode("".join((binary.text or "").split()), validate=True)
            except (binascii.Error, ValueError):
                continue
            out.append((part.get(f"{{{PKG_NS}}}name") or "", payload))
    return out


# --- Zipped packages (OOXML, OpenDocument) ------------------------------------------------------


def _strip_package(data: bytes, where: str, family: str) -> Stripped:
    actions: list[str] = []
    members: list[tuple[zipfile.ZipInfo, bytes]] = []
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            for info in archive.infolist():
                body = archive.read(info)
                step = f"{where}>{info.filename}"
                if (
                    not info.is_dir()
                    and info.filename.lower().endswith((".xml", ".rels"))
                    and body.lstrip()[:1] == b"<"
                ):
                    try:
                        root = etree.fromstring(body, _parser())
                    except etree.XMLSyntaxError as exc:
                        raise OfficeMetadataError(f"{step}: unreadable XML part") from exc
                    n = (
                        _ooxml_part(root, info.filename)
                        if family == "ooxml"
                        else _odf_part(root, info.filename)
                    )
                    if n:
                        body = etree.tostring(
                            root, xml_declaration=True, encoding="UTF-8", standalone=True
                        )
                        actions.append(f"{step}: blanked {n} people fields")
                elif not info.is_dir():
                    inner = strip(body, step)
                    if inner is not None and inner.actions:
                        body = inner.data
                        actions.extend(inner.actions)
                members.append((info, body))
    except (zipfile.BadZipFile, zipfile.LargeZipFile) as exc:
        raise OfficeMetadataError(f"{where}: unreadable {family} package: {exc}") from exc
    if not actions:
        return Stripped(data, [])
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as out:
        for info, body in members:
            copy = zipfile.ZipInfo(info.filename, date_time=info.date_time)
            copy.compress_type = info.compress_type
            copy.external_attr = info.external_attr
            copy.create_system = info.create_system
            out.writestr(copy, body)
    return Stripped(buffer.getvalue(), actions)


def _package_markers(data: bytes, where: str, family: str) -> list[str]:
    found: list[str] = []
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            for info in archive.infolist():
                if info.is_dir():
                    continue
                body = archive.read(info)
                step = f"{where}>{info.filename}"
                if info.filename.lower().endswith((".xml", ".rels")) and body.lstrip()[:1] == b"<":
                    try:
                        root = etree.fromstring(body, _parser())
                    except etree.XMLSyntaxError:
                        found.append(f"{step}: unreadable XML part")
                        continue
                    n = (
                        _ooxml_part(root, info.filename)
                        if family == "ooxml"
                        else _odf_part(root, info.filename)
                    )
                    if n:
                        found.append(f"{step}: office people metadata")
                else:
                    found.extend(markers(body, step) or [])
    except (zipfile.BadZipFile, zipfile.LargeZipFile):
        return [f"{where}: unreadable {family} package"]
    return found


# --- RTF ----------------------------------------------------------------------------------------

_RTF_GROUPS = re.compile(rb"\{\\(?:info|\*\\atnauthor|\*\\atnid|\*\\revtbl)(?![A-Za-z])")


def _group_end(data: bytes, start: int) -> int:
    """The index just past the group opened at `start` (a `{`), escapes respected."""
    depth = 0
    at = start
    while at < len(data):
        c = data[at]
        if c == 0x5C:  # backslash: skip the escaped character
            at += 2
            continue
        if c == 0x7B:
            depth += 1
        elif c == 0x7D:
            depth -= 1
            if depth == 0:
                return at + 1
        at += 1
    raise OfficeMetadataError("unbalanced RTF group")


def _rtf_groups(data: bytes) -> list[tuple[int, int, int]]:
    """(group start, content start, group end) of each people group, outermost only."""
    out: list[tuple[int, int, int]] = []
    at = 0
    while (m := _RTF_GROUPS.search(data, at)) is not None:
        end = _group_end(data, m.start())
        out.append((m.start(), m.end(), end))
        at = end
    return out


def _strip_rtf(data: bytes, where: str) -> Stripped:
    try:
        groups = _rtf_groups(data)
    except OfficeMetadataError as exc:
        raise OfficeMetadataError(f"{where}: {exc}") from exc
    emptied = [(s, c, e) for s, c, e in groups if data[c : e - 1].strip()]
    if not emptied:
        return Stripped(data, [])
    out: list[bytes] = []
    at = 0
    for _, content, end in emptied:
        out.append(data[at:content] + b"}")
        at = end
    out.append(data[at:])
    return Stripped(b"".join(out), [f"{where}: emptied {len(emptied)} RTF people groups"])


def _rtf_markers(data: bytes, where: str) -> list[str]:
    try:
        groups = _rtf_groups(data)
    except OfficeMetadataError:
        return [f"{where}: unbalanced RTF"]
    return (
        [f"{where}: RTF people metadata"]
        if any(data[c : e - 1].strip() for _, c, e in groups)
        else []
    )


# --- OLE compound files -------------------------------------------------------------------------

_FREE, _END, _FAT, _DIFAT = 0xFFFFFFFF, 0xFFFFFFFE, 0xFFFFFFFD, 0xFFFFFFFC
_SUMMARY_STREAMS = frozenset({"\x05SummaryInformation", "\x05DocumentSummaryInformation"})
_VT_LPSTR, _VT_LPWSTR, _VT_VARIANT, _VT_VECTOR = 0x1E, 0x1F, 0x0C, 0x1000
# MS-OLEPS fixed-size types: I2, I4, R4, R8, CY, DATE, BOOL, I1, UI1, UI2, UI4, I8, UI8, INT,
# UINT, ERROR, FILETIME.
_FIXED_SIZES = {
    0x02: 2,
    0x03: 4,
    0x04: 4,
    0x05: 8,
    0x06: 8,
    0x07: 8,
    0x0B: 2,
    0x10: 1,
    0x11: 1,
    0x12: 2,
    0x13: 4,
    0x14: 8,
    0x15: 8,
    0x16: 4,
    0x17: 4,
    0x0A: 4,
    0x40: 8,
}
# FibRgFcLcb97 pairs (MS-DOC 2.5.6) holding people: AutosaveSource (35), GrpXstAtnOwners (36),
# SttbfRMark (51), SttbSavedBy (71), SttbFnm (72).
_FIB_XSTZ, _FIB_XSTS, _FIB_STTBS = (35,), (36,), (51, 71, 72)
_WORD97_NFIB = 0x00C1
_WRITE_ACCESS = 0x005C
_SUPBOOK = 0x01AE
_BIFF_EOF = 0x000A


@dataclass
class _Entry:
    name: str
    kind: int
    start: int
    size: int


class _Compound:
    """An OLE compound file opened for in-place rewriting of whole streams (same length)."""

    def __init__(self, data: bytes) -> None:
        if len(data) < 512 or not data.startswith(OLE_MAGIC):
            raise OfficeMetadataError("not a compound file")
        self.buf = bytearray(data)
        self.shift = struct.unpack_from("<H", data, 0x1E)[0]
        self.mini_shift = struct.unpack_from("<H", data, 0x20)[0]
        if self.shift not in (9, 12) or self.mini_shift != 6:
            raise OfficeMetadataError("unexpected sector sizes")
        self.size = 1 << self.shift
        self.cutoff = struct.unpack_from("<I", data, 0x38)[0]
        dir_start, mini_fat_start, difat_start, difat_count = (
            struct.unpack_from("<I", data, 0x30)[0],
            struct.unpack_from("<I", data, 0x3C)[0],
            struct.unpack_from("<I", data, 0x44)[0],
            struct.unpack_from("<I", data, 0x48)[0],
        )
        fat_sectors = [s for s in struct.unpack_from("<109I", data, 0x4C) if s < _DIFAT]
        sector, seen = difat_start, 0
        while sector < _DIFAT and seen < difat_count:
            values = struct.unpack_from(f"<{self.size // 4}I", self._sector(sector))
            fat_sectors.extend(s for s in values[:-1] if s < _DIFAT)
            sector, seen = values[-1], seen + 1
        self.fat: list[int] = []
        for s in fat_sectors:
            self.fat.extend(struct.unpack_from(f"<{self.size // 4}I", self._sector(s)))
        directory = b"".join(self._sector(s) for s in self._chain(dir_start, self.fat))
        self.entries: list[_Entry] = []
        for at in range(0, len(directory) - 127, 128):
            length = struct.unpack_from("<H", directory, at + 64)[0]
            name = directory[at : at + max(0, min(length, 64) - 2)].decode("utf-16-le", "replace")
            kind_, start, size = directory[at + 66], *struct.unpack_from("<IQ", directory, at + 116)
            if self.shift == 9:
                size &= 0xFFFFFFFF
            self.entries.append(_Entry(name, kind_, start, size))
        if not self.entries or self.entries[0].kind != 5:
            raise OfficeMetadataError("no root entry")
        mini_fat_bytes = b"".join(self._sector(s) for s in self._chain(mini_fat_start, self.fat))
        self.mini_fat = list(struct.unpack_from(f"<{len(mini_fat_bytes) // 4}I", mini_fat_bytes))
        root = self.entries[0]
        self.mini_offsets = [self._offset(s) for s in self._chain(root.start, self.fat)]

    def _offset(self, sector: int) -> int:
        at = (sector + 1) << self.shift
        if at >= len(self.buf):
            raise OfficeMetadataError("sector beyond the file")
        return at

    def _sector(self, sector: int) -> bytes:
        at = self._offset(sector)
        return bytes(self.buf[at : at + self.size]).ljust(self.size, b"\0")  # a short last one

    def _chain(self, start: int, table: list[int]) -> list[int]:
        out: list[int] = []
        sector = start
        while sector < _DIFAT:
            if sector >= len(table) or len(out) > len(table):
                raise OfficeMetadataError("broken sector chain")
            out.append(sector)
            sector = table[sector]
        return out

    def _ranges(self, entry: _Entry) -> list[tuple[int, int]]:
        """Physical (offset, length) runs holding the stream's bytes, in order."""
        out: list[tuple[int, int]] = []
        left = entry.size
        if entry.size < self.cutoff:
            unit = 1 << self.mini_shift
            per = self.size // unit
            for mini in self._chain(entry.start, self.mini_fat):
                big = mini // per
                if big >= len(self.mini_offsets):
                    raise OfficeMetadataError("mini sector beyond the mini stream")
                n = min(unit, left)
                out.append((self.mini_offsets[big] + (mini % per) * unit, n))
                left -= n
        else:
            for sector in self._chain(entry.start, self.fat):
                n = min(self.size, left)
                out.append((self._offset(sector), n))
                left -= n
        if left > 0 or any(a + n > len(self.buf) for a, n in out):
            raise OfficeMetadataError(f"stream {entry.name!r} shorter than its size")
        return out

    def read(self, entry: _Entry) -> bytearray:
        return bytearray(b"".join(bytes(self.buf[a : a + n]) for a, n in self._ranges(entry)))

    def write(self, entry: _Entry, data: bytearray) -> None:
        at = 0
        for offset, n in self._ranges(entry):
            self.buf[offset : offset + n] = data[at : at + n]
            at += n

    def streams(self, name: str) -> list[_Entry]:
        return [e for e in self.entries if e.kind == 2 and e.name == name]


def _u16(buf: bytearray, at: int) -> int:
    if at + 2 > len(buf):
        raise OfficeMetadataError("truncated")
    return struct.unpack_from("<H", buf, at)[0]


def _u32(buf: bytearray, at: int) -> int:
    if at + 4 > len(buf):
        raise OfficeMetadataError("truncated")
    return struct.unpack_from("<I", buf, at)[0]


def _zero(buf: bytearray, start: int, length: int) -> bool:
    """Zero buf[start:start+length]; whether anything was not zero already."""
    end = start + length
    if length < 0 or end > len(buf):
        raise OfficeMetadataError("value beyond its stream")
    changed = any(buf[start:end])
    buf[start:end] = bytes(length)
    return changed


def _property_strings(buf: bytearray) -> list[tuple[int, int]]:
    """(offset, length) of every string value's characters in a property set stream."""
    out: list[tuple[int, int]] = []
    if _u16(buf, 0) != 0xFFFE:
        raise OfficeMetadataError("not a property set")
    sections = _u32(buf, 24)
    if sections > 16:
        raise OfficeMetadataError("implausible property set")
    for s in range(sections):
        base = _u32(buf, 28 + 20 * s + 16)
        count = _u32(buf, base + 4)
        if count > 4096:
            raise OfficeMetadataError("implausible section")
        for p in range(count):
            pid = _u32(buf, base + 8 + 8 * p)
            at = base + _u32(buf, base + 12 + 8 * p)
            if pid in (0, 1):  # the dictionary (property names) and the code page
                continue
            _value_strings(buf, at, _u32(buf, at) & 0xFFFF, out)
    return out


def _value_strings(buf: bytearray, at: int, vt: int, out: list[tuple[int, int]]) -> int | None:
    """Collect a typed value's strings; the offset just past it, None when its size is unknown.

    A value of unknown size inside a vector refuses the file: what follows it cannot be found.
    """
    base = vt & ~_VT_VECTOR
    if vt & _VT_VECTOR:
        n = _u32(buf, at + 4)
        if n > 65536:
            raise OfficeMetadataError("implausible vector")
        cursor = at + 8
        for _ in range(n):
            if base == _VT_VARIANT:
                element = _u32(buf, cursor) & 0xFFFF
                end = _value_strings(buf, cursor, element, out)
                if end is None:
                    raise OfficeMetadataError("vector element of unknown size")
                if element in (_VT_LPSTR, _VT_LPWSTR):
                    # Word writes a variant's string unpadded (`HeadingPairs`), against MS-OLEPS:
                    # the next element starts where a type header fits, padded or not.
                    unpadded = cursor + 8 + out[-1][1]
                    if unpadded != end and _is_type_header(buf, unpadded):
                        end = unpadded
                cursor = end
            elif base in (_VT_LPSTR, _VT_LPWSTR):
                return _string_vector(buf, cursor, n, base, out)
            elif base in _FIXED_SIZES:
                cursor += (_FIXED_SIZES[base] + 3) & ~3
            else:
                return None
        return cursor
    if base in (_VT_LPSTR, _VT_LPWSTR):
        return _string(buf, at, base, out)
    if base in _FIXED_SIZES:
        return at + 4 + ((_FIXED_SIZES[base] + 3) & ~3)
    return None


def _string_vector(buf: bytearray, at: int, n: int, vt: int, out: list[tuple[int, int]]) -> int:
    """A vector of `n` strings from `at`: MS-OLEPS pads each to 4 bytes, Word often does not.

    The layout taken is the one in which every string fits and ends in its terminator; when
    neither does, the file is refused.
    """
    unit = 2 if vt == _VT_LPWSTR else 1
    for padded in (True, False):
        spans: list[tuple[int, int]] = []
        cursor = at
        try:
            for _ in range(n):
                length = _u32(buf, cursor) * unit
                start = cursor + 4
                if (
                    length < unit
                    or start + length > len(buf)
                    or any(buf[start + length - unit : start + length])
                ):
                    raise OfficeMetadataError("not this layout")
                spans.append((start, length))
                cursor = start + (((length + 3) & ~3) if padded else length)
        except OfficeMetadataError:
            continue
        out.extend(spans)
        return cursor
    raise OfficeMetadataError("string vector in neither layout")


def _is_type_header(buf: bytearray, at: int) -> bool:
    if at + 4 > len(buf):
        return False
    vt, pad = struct.unpack_from("<HH", buf, at)
    return pad == 0 and (vt & ~_VT_VECTOR) in (*_FIXED_SIZES, _VT_LPSTR, _VT_LPWSTR, _VT_VARIANT)


def _string(buf: bytearray, at: int, vt: int, out: list[tuple[int, int]]) -> int:
    """A string whose 4-byte type header starts at `at`; returns the offset past its padding."""
    n = _u32(buf, at + 4)
    length = n * 2 if vt == _VT_LPWSTR else n
    start = at + 8
    if start + length > len(buf):
        raise OfficeMetadataError("string beyond its stream")
    out.append((start, length))
    return at + 8 + ((length + 3) & ~3)


def _word_strings(document: bytearray, table: bytearray) -> list[tuple[int, int]]:
    """(offset, length) in the table stream of the people tables' characters."""
    out: list[tuple[int, int]] = []
    at = 32
    csw = _u16(document, at)
    at += 2 + csw * 2
    cslw = _u16(document, at)
    at += 2 + cslw * 4
    pairs = _u16(document, at)
    at += 2

    def pair(index: int) -> tuple[int, int] | None:
        if index >= pairs:
            return None
        fc, lcb = _u32(document, at + 8 * index), _u32(document, at + 8 * index + 4)
        if lcb == 0:
            return None
        if fc + lcb > len(table):
            raise OfficeMetadataError("FIB table entry beyond the table stream")
        return fc, lcb

    for index in _FIB_STTBS:
        found = pair(index)
        if found is None:
            continue
        fc, lcb = found
        extended = _u16(table, fc) == 0xFFFF
        cursor = fc + (2 if extended else 0)
        count = _u16(table, cursor)
        extra = _u16(table, cursor + 2)
        cursor += 4
        for _ in range(count):
            if extended:
                n = _u16(table, cursor)
                out.append((cursor + 2, n * 2))
                cursor += 2 + n * 2 + extra
            else:
                n = table[cursor]
                out.append((cursor + 1, n))
                cursor += 1 + n + extra
            if cursor > fc + lcb:
                raise OfficeMetadataError("string table beyond its entry")
    for index in (*_FIB_XSTS, *_FIB_XSTZ):
        found = pair(index)
        if found is None:
            continue
        fc, lcb = found
        cursor = fc
        while cursor + 2 <= fc + lcb:
            n = _u16(table, cursor)
            if cursor + 2 + n * 2 > fc + lcb:
                raise OfficeMetadataError("string list beyond its entry")
            out.append((cursor + 2, n * 2))
            cursor += 2 + n * 2 + (2 if index in _FIB_XSTZ else 0)
            if index in _FIB_XSTZ:
                break
    return out


def _write_access(book: bytearray) -> list[tuple[int, int]]:
    """(offset, length) of the user who saved (WriteAccess) and the paths of external
    workbooks (SupBook) in a workbook's globals substream."""
    out: list[tuple[int, int]] = []
    at = 0
    while at + 4 <= len(book):
        record, size = struct.unpack_from("<HH", book, at)
        if record == _WRITE_ACCESS:
            out.append((at + 4, size))
        elif (
            record == _SUPBOOK
            and size > 4
            # An external workbook's path (it can hold a Windows user name); a self or add-in
            # reference carries none.
            and struct.unpack_from("<H", book, at + 6)[0] not in (0x0401, 0x3A01)
        ):
            out.append((at + 8, size - 4))
        if record == _BIFF_EOF:
            break
        at += 4 + size
    return out


def _current_user(stream: bytearray) -> list[tuple[int, int]]:
    """PowerPoint's CurrentUserAtom: (offset, length) of the ANSI and Unicode user names."""
    if len(stream) < 28:
        return []
    size = _u32(stream, 8)
    n = _u16(stream, 20)
    ansi = 8 + 4 + 4 + 4 + 2 + 2 + 2 + 2  # header, size, signature, offset, len, version, minors
    out = [(ansi, n)]
    del size
    unicode_at = ansi + n + 4  # relVersion, then the Unicode name
    if unicode_at + n * 2 <= len(stream):
        out.append((unicode_at, n * 2))
    return [(a, n) for a, n in out if a + n <= len(stream)]


def _ole_targets(cfb: _Compound) -> list[tuple[_Entry, list[tuple[int, int]]]]:
    targets: list[tuple[_Entry, list[tuple[int, int]]]] = []
    for name in _SUMMARY_STREAMS:
        for entry in cfb.streams(name):
            targets.append((entry, _property_strings(cfb.read(entry))))
    for document in cfb.streams("WordDocument"):
        data = cfb.read(document)
        if _u16(data, 0) != 0xA5EC:
            raise OfficeMetadataError("WordDocument without a Word FIB")
        if _u16(data, 2) < _WORD97_NFIB:
            raise OfficeMetadataError("pre-Word 97 document")
        table_name = "1Table" if _u16(data, 0x0A) & 0x0200 else "0Table"
        tables = cfb.streams(table_name)
        if len(tables) != 1:
            raise OfficeMetadataError(f"Word document without one {table_name}")
        targets.append((tables[0], _word_strings(data, cfb.read(tables[0]))))
    for name in ("Workbook", "Book"):
        for entry in cfb.streams(name):
            targets.append((entry, _write_access(cfb.read(entry))))
    for entry in cfb.streams("Current User"):
        targets.append((entry, _current_user(cfb.read(entry))))
    return targets


def _strip_ole(data: bytes, where: str) -> Stripped:
    try:
        cfb = _Compound(data)
        changed = 0
        for entry, spans in _ole_targets(cfb):
            if not spans:
                continue
            stream = cfb.read(entry)
            hit = sum(_zero(stream, a, n) for a, n in spans)
            if hit:
                cfb.write(entry, stream)
                changed += hit
    except (OfficeMetadataError, struct.error, IndexError) as exc:
        raise OfficeMetadataError(f"{where}: OLE file not readable safely: {exc}") from exc
    if not changed:
        return Stripped(data, [])
    return Stripped(bytes(cfb.buf), [f"{where}: zeroed {changed} OLE people values"])


def _ole_markers(data: bytes, where: str) -> list[str]:
    try:
        cfb = _Compound(data)
        left = sum(
            1
            for entry, spans in _ole_targets(cfb)
            for stream in [cfb.read(entry)]
            if spans
            for a, n in spans
            if any(stream[a : a + n])
        )
    except (OfficeMetadataError, struct.error, IndexError):
        return [f"{where}: OLE file not readable safely"]
    return [f"{where}: OLE people metadata ({left} values)"] if left else []
