"""Invariant 6 at the acquisition boundary: strip natural persons' data from downloads (ADR 0009).

Filed statements are signed by people. Their signatures carry the signer's
name and certificate, and Profil Zaufany signatures add the PESEL number
(`DaneZPOsobyFizycznej`). This module removes that data before a download is
hashed and stored, so the raw store never holds it:

- XML: every `ds:Signature` element is removed. A signature *container*
  (a `ds:Signature` or `Signatures` root wrapping the statement) is replaced
  by the statement it wraps; a detached signature file is dropped.
- Base64 payloads inside XML (attached notes, ePUAP attachments) are redacted
  recursively and re-encoded.
- PDF: signature fields, their appearance streams and signature dictionaries
  are removed and the file is rewritten (PyMuPDF).
- ZIP: rebuilt with the same member names, order, timestamps and compression.

A file with nothing to remove is returned byte for byte. The result is
deterministic, so re-downloading the same file stores the same object.
Free text (e.g. a board member named in the notes) is not touched; see ADR 0009.

KRS registry extracts (JSON) have their own redactor, `redact_registry_extract`: person-keyed
fields plus an allowlist for free text (ADR 0009 addendum, plan 0008 decision 3). MSiG notices
are never stored as text: `msig_client.reduce_notice` keeps only person-free extracts.

`personal_data_markers()` is the independent check: it lists what is left.
"""

# PyMuPDF's signatures reference types pyright cannot resolve; scoped to this module.
# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false, reportUnknownArgumentType=false

from __future__ import annotations

import base64
import binascii
import io
import json
import re
import zipfile
from dataclasses import dataclass, field
from typing import cast

import pymupdf
from lxml import etree

REDACTION_VERSION = "1"
_SIGNATURE_WIDGET: int = pymupdf.PDF_WIDGET_TYPE_SIGNATURE  # pyright: ignore[reportAttributeAccessIssue]

DS_NS = "http://www.w3.org/2000/09/xmldsig#"
_UTF8_BOM = b"\xef\xbb\xbf"
_BASE64_PAYLOADS = {"Zawartosc", "DaneZalacznika"}
_MARKERS = {
    "pesel": re.compile(rb"PESEL"),
    "trusted_profile_person": re.compile(rb"DaneZPOsobyFizycznej"),
    "certificate": re.compile(rb"X509Certificate"),
    # Qualified certificates name the holder as `PNOPL-<PESEL>`, as text or DER hex.
    "qualified_certificate_pesel": re.compile(rb"PNOPL-\d{11}|504[eE]4[fF]504[cC]2[dD](?:3\d){11}"),
}


class RedactionError(Exception):
    """A download that cannot be redacted safely; it must not be stored."""


@dataclass
class Redaction:
    data: bytes
    actions: list[str] = field(default_factory=list[str])

    @property
    def changed(self) -> bool:
        return bool(self.actions)


def _parser() -> etree.XMLParser:
    return etree.XMLParser(huge_tree=True, resolve_entities=False, no_network=True, load_dtd=False)


def _b64decode(text: str | None) -> bytes | None:
    try:
        return base64.b64decode("".join((text or "").split()), validate=True)
    except (binascii.Error, ValueError):
        return None


def _is_signature_container(root: etree._Element) -> bool:  # pyright: ignore[reportPrivateUsage]
    q = etree.QName(root)
    return (q.namespace == DS_NS and q.localname == "Signature") or (
        not q.namespace and q.localname == "Signatures"
    )


def redact_file(data: bytes, where: str) -> Redaction | None:
    """Redact one file. `None` means the file is only a signature and is dropped."""
    body = data.removeprefix(_UTF8_BOM)
    if body.lstrip().startswith(b"%PDF"):
        return _redact_pdf(data, where)
    if not body.lstrip().startswith(b"<"):
        return Redaction(data)
    try:
        root = etree.fromstring(body, _parser())
    except etree.XMLSyntaxError:
        return Redaction(data)  # not XML we can read; the marker check still applies
    if _is_signature_container(root):
        return _unwrap_container(root, where)
    return _redact_xml(root, data, where)


def _redact_xml(root: etree._Element, original: bytes, where: str) -> Redaction:  # pyright: ignore[reportPrivateUsage]
    actions: list[str] = []
    signatures = list(root.iter(f"{{{DS_NS}}}Signature"))
    for signature in signatures:
        parent = signature.getparent()
        if parent is not None:
            parent.remove(signature)
    if signatures:
        actions.append(f"{where}: removed {len(signatures)} ds:Signature")
    for el in root.iter(etree.Element):
        if etree.QName(el).localname not in _BASE64_PAYLOADS:
            continue
        payload = _b64decode(el.text)
        if payload is None:
            continue
        inner = redact_file(payload, f"{where}>{etree.QName(el).localname}")
        if inner is not None and inner.changed:
            el.text = base64.b64encode(inner.data).decode("ascii")
            actions.extend(inner.actions)
    if not actions:
        return Redaction(original)
    return Redaction(etree.tostring(root, xml_declaration=True, encoding="UTF-8"), actions)


def _unwrap_container(root: etree._Element, where: str) -> Redaction | None:  # pyright: ignore[reportPrivateUsage]
    contents: list[Redaction] = []
    for index, obj in enumerate(root.iter(f"{{{DS_NS}}}Object"), start=1):
        step = f"{where}>ds:Object[{index}]"
        inline = [
            child
            for child in obj.iterchildren(etree.Element)
            if etree.QName(child).namespace not in (DS_NS, None)
            and "uri.etsi.org" not in (etree.QName(child).namespace or "")
        ]
        if inline:
            if len(inline) > 1:
                raise RedactionError(f"{step}: several inline documents")
            document = etree.tostring(inline[0], xml_declaration=True, encoding="UTF-8")
            inner = redact_file(document, step)
        elif obj.get("Encoding") == f"{DS_NS}base64":
            payload = _b64decode(obj.text)
            if payload is None:
                raise RedactionError(f"{step}: invalid base64")
            inner = redact_file(payload, f"{step}>base64")
        else:
            continue
        if inner is not None:
            contents.append(inner)
    if not contents:
        return None
    if len(contents) > 1:
        raise RedactionError(f"{where}: signature container holds {len(contents)} documents")
    return Redaction(
        contents[0].data, [f"{where}: unwrapped from signature container", *contents[0].actions]
    )


def _redact_pdf(data: bytes, where: str) -> Redaction:
    if b"/ByteRange" not in data and b"/Sig" not in data:
        return Redaction(data)
    try:
        doc = pymupdf.open(stream=data, filetype="pdf")
    except (RuntimeError, ValueError) as exc:
        raise RedactionError(f"{where}: unreadable PDF ({exc})") from exc
    with doc:
        if doc.needs_pass:
            raise RedactionError(f"{where}: encrypted PDF")
        removed = 0
        for page in doc:
            for widget in _signature_widgets(page):
                page.delete_widget(widget)
                removed += 1
        catalog = doc.pdf_catalog()
        for key in ("Perms", "DSS"):
            if doc.xref_get_key(catalog, key)[0] != "null":
                doc.xref_set_key(catalog, key, "null")
        cleared = 0
        for xref in range(1, doc.xref_length()):
            source = _xref_source(doc, xref)
            if "/ByteRange" in source or re.search(r"/Type\s*/Sig\b", source):
                doc.update_object(xref, "<<>>")
                cleared += 1
        if not removed and not cleared:
            return Redaction(data)
        out = doc.tobytes(garbage=4, deflate=True, no_new_id=True)
    return Redaction(
        out,
        [f"{where}: removed {removed} PDF signature fields, cleared {cleared} signature objects"],
    )


def _signature_widgets(page: pymupdf.Page) -> list[pymupdf.Widget]:
    return [
        w
        for w in page.widgets() or []
        if isinstance(w, pymupdf.Widget) and w.field_type == _SIGNATURE_WIDGET
    ]


def _xref_source(doc: pymupdf.Document, xref: int) -> str:
    """An object's source, or "" for a dangling xref entry (broken but harmless PDFs)."""
    try:
        return doc.xref_object(xref, compressed=True)
    except RuntimeError:
        return ""


def _xref_stream(doc: pymupdf.Document, xref: int) -> bytes:
    """A non-image stream's decoded bytes ("" for images: pixels are not text)."""
    try:
        if not doc.xref_is_stream(xref) or doc.xref_get_key(xref, "Subtype")[1] == "/Image":
            return b""
        stream: bytes | None = doc.xref_stream(xref)
        return stream or b""
    except RuntimeError:
        return b""


def redact_download(raw: bytes) -> Redaction:
    """Redact a stored-to-be download (a ZIP of statement files, or a single file)."""
    if not zipfile.is_zipfile(io.BytesIO(raw)):
        single = redact_file(raw, "raw")
        if single is None:
            raise RedactionError("the download is only a signature")
        return single
    actions: list[str] = []
    members: list[tuple[zipfile.ZipInfo, bytes]] = []
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            for info in archive.infolist():
                data = archive.read(info)
                if info.is_dir():
                    members.append((info, data))
                    continue
                result = redact_file(data, f"zip:{info.filename}")
                if result is None:
                    actions.append(f"zip:{info.filename}: dropped detached signature")
                    continue
                actions.extend(result.actions)
                members.append((info, result.data))
    except (zipfile.BadZipFile, zipfile.LargeZipFile) as exc:
        raise RedactionError(f"unreadable ZIP: {exc}") from exc
    if not actions:
        return Redaction(raw)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as out:
        for info, data in members:
            copy = zipfile.ZipInfo(info.filename, date_time=info.date_time)
            copy.compress_type = info.compress_type
            copy.external_attr = info.external_attr
            copy.create_system = info.create_system
            out.writestr(copy, data)
    return Redaction(buffer.getvalue(), actions)


def personal_data_markers(raw: bytes) -> list[str]:
    """Where natural persons' data is still detectable in a download (empty when clean)."""
    found: list[str] = []
    if zipfile.is_zipfile(io.BytesIO(raw)):
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            for info in archive.infolist():
                found.extend(_file_markers(archive.read(info), f"zip:{info.filename}"))
        return found
    return _file_markers(raw, "raw")


def _file_markers(data: bytes, where: str) -> list[str]:
    body = data.removeprefix(_UTF8_BOM)
    found = [f"{where}: {name}" for name, pattern in _MARKERS.items() if pattern.search(body)]
    if body.lstrip().startswith(b"%PDF"):
        found.extend(_pdf_markers(body, where))
    elif body.lstrip().startswith(b"<"):
        try:
            root = etree.fromstring(body, _parser())
        except etree.XMLSyntaxError:
            return found
        if next(root.iter(f"{{{DS_NS}}}Signature"), None) is not None:
            found.append(f"{where}: xml_signature")
        for el in root.iter(etree.Element):
            local = etree.QName(el).localname
            if local in _BASE64_PAYLOADS or el.get("Encoding") == f"{DS_NS}base64":
                payload = _b64decode(el.text)
                if payload is not None:
                    found.extend(_file_markers(payload, f"{where}>{local}"))
    return found


def _pdf_markers(data: bytes, where: str) -> list[str]:
    found: list[str] = []
    try:
        doc = pymupdf.open(stream=data, filetype="pdf")
    except (RuntimeError, ValueError):
        return [f"{where}: unreadable PDF"]
    with doc:
        for xref in range(1, doc.xref_length()):
            source = _xref_source(doc, xref)
            if "/ByteRange" in source or re.search(r"/Type\s*/Sig\b", source):
                found.append(f"{where}: PDF signature object {xref}")
            stream = _xref_stream(doc, xref)
            if _MARKERS["qualified_certificate_pesel"].search(stream) or _MARKERS["pesel"].search(
                stream
            ):
                found.append(f"{where}: PDF stream {xref} names a person")
        for index in range(doc.page_count):
            if _signature_widgets(doc[index]):
                found.append(f"{where}: PDF signature field on page {index}")
    return found


# --- Registry JSON: the KRS full extract (ADR 0009 addendum, plan 0008 decision 3) ---------------

REGISTRY_REDACTION_VERSION = "krs-json-2"
PLACEHOLDER = "[REDACTED]"
_PERSON_KEYS = frozenset({"imie", "imieDrugie", "nazwiskoICzlon", "nazwiskoIICzlon", "pesel"})
_TEXT_LIMIT = 40
# Free text generic by construction: legal entities' names, courts and authorities, share
# counts, reporting periods, procedure types. Any other string over `_TEXT_LIMIT` can cite a
# notary or name a representative, so it is reduced to its first date.
_TEXT_ALLOWLIST = frozenset(
    {
        "nazwa",
        "organWydajacy",
        "organWydajacyTytulWykonawczy",
        "oznaczenieSaduDokonujacegoWpisu",
        "posiadaneUdzialy",
        "zaOkresOdDo",
        "sposobProwadzeniaPostepowania",
        "rodzajPostepowania",
    }
)
# `opis` is generic in two places only: PKD descriptions, and the registry's entry
# descriptions, which are the only deregistration signal (ADR 0011 decision 5).
_OPIS_PATHS = ("odpis.naglowekP.wpis", "odpis.dane.dzial3.przedmiotDzialalnosci.")
_DATE_IN_TEXT = re.compile(r"\b\d{2}\.\d{2}\.\d{4}\b")
# krs-json-2: an allowlisted field can still quote an order that appoints someone (0000225506's
# `organWydajacy` names a temporary court supervisor, a company there). A role word with no
# legal-form marker in the next `_ROLE_WINDOW` characters may name a person, so the value is
# reduced like any other free text: redaction errs towards removing.
_ROLE_WORD = re.compile(
    r"NADZORC|SYNDYK|KURATOR|NOTARIUSZ|ZARZĄDC|W OSOBIE|DORADC|ADWOKAT|RADC[AY]|LIKWIDATOR|PEŁNOMOCNIK",
    re.IGNORECASE,
)
_LEGAL_FORM = re.compile(
    r"SPÓŁK|SPÓŁDZIELNI|FUNDACJ|\bS\.\s?A\.|SP\.\s?Z\s?O\.\s?O\.|\bKRS\b", re.IGNORECASE
)
_ROLE_WINDOW = 80
_PESEL_SHAPED = re.compile(r"(?<!\d)\d{11}(?!\d)")


@dataclass
class RegistryRedaction:
    data: bytes  # canonical JSON: sorted keys, fixed indent, UTF-8
    persons: int = 0  # person-keyed values replaced
    reduced: int = 0  # free-text fields reduced to their first date
    blanked: int = 0  # further strings holding a removed name


def _allowlisted(key: str, path: str) -> bool:
    if key in _TEXT_ALLOWLIST:
        return True
    return key == "opis" and any(path.startswith(p) for p in _OPIS_PATHS)


def _may_name_a_person(value: str) -> bool:
    return any(
        not _LEGAL_FORM.search(value[m.end() : m.end() + _ROLE_WINDOW])
        for m in _ROLE_WORD.finditer(value)
    )


def redact_registry_extract(document: object) -> RegistryRedaction:
    """Redact a parsed KRS extract; deterministic, and a no-op on its own output.

    Raises `RedactionError` if anything PESEL-shaped or any removed value survives.
    """
    removed: set[str] = set()
    result = RegistryRedaction(b"")

    def redact(node: object, path: str) -> object:
        if isinstance(node, dict):
            out: dict[str, object] = {}
            for key, value in cast("dict[str, object]", node).items():
                here = f"{path}.{key}" if path else key
                if key in _PERSON_KEYS and isinstance(value, str) and value not in ("", PLACEHOLDER):
                    removed.add(value)
                    result.persons += 1
                    out[key] = PLACEHOLDER
                elif (
                    isinstance(value, str)
                    and len(value) > _TEXT_LIMIT
                    and (not _allowlisted(key, here) or _may_name_a_person(value))
                ):
                    dated = _DATE_IN_TEXT.search(value)
                    result.reduced += 1
                    out[key] = f"{PLACEHOLDER} {dated.group(0)}" if dated else PLACEHOLDER
                else:
                    out[key] = redact(value, here)
            return out
        if isinstance(node, list):
            return [redact(item, path) for item in cast("list[object]", node)]
        return node

    redacted = redact(document, "")
    tokens = sorted({w for v in removed if not v.isdigit() for w in v.split() if len(w) >= 3})
    patterns = [re.compile(rf"\b{re.escape(t)}\b", re.IGNORECASE) for t in tokens]

    def blank(node: object) -> object:
        if isinstance(node, dict):
            return {k: blank(v) for k, v in cast("dict[str, object]", node).items()}
        if isinstance(node, list):
            return [blank(v) for v in cast("list[object]", node)]
        if isinstance(node, str) and node != PLACEHOLDER and any(p.search(node) for p in patterns):
            result.blanked += 1
            return PLACEHOLDER
        return node

    text = json.dumps(blank(redacted), ensure_ascii=False, indent=1, sort_keys=True) + "\n"
    if _PESEL_SHAPED.search(text):
        raise RedactionError("an 11-digit (PESEL-shaped) run survives redaction")
    # Whole words, like the backstop: a first name inside a place name ("JAN" in "JANÓW") is not
    # the person. PESEL numbers are covered by the 11-digit check above.
    if any(
        re.search(rf"\b{re.escape(value)}\b", text, re.IGNORECASE)
        for value in removed
        if not value.isdigit()
    ):
        raise RedactionError("a removed person value survives redaction")
    result.data = text.encode("utf-8")
    return result
