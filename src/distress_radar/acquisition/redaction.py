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
- ZIP: rebuilt with the same order, timestamps and compression.
- File names (version 2, ADR 0009 second addendum): a filer's file name is never kept as text.
  ZIP members are renamed to their filing's token (`file_token`: the `document_ref` in URL-safe
  base64 plus an allowlisted extension), or `unmatched-<n>` when no filing names them; directory
  entries are dropped. Attachment names inside a statement (`Plik/Nazwa`) become `plik-<n>`, an
  ePUAP attachment's `nazwaPliku` becomes `zalacznik-<n>`. The RDF detail's `nazwaPliku` becomes
  the token too (`redact_rdf_detail`).
- PDF document metadata (information dictionary and XMP) is removed: an author is usually the
  person who wrote the notes.

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
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from typing import cast

import pymupdf
from lxml import etree

REDACTION_VERSION = "2"
RDF_DETAIL_REDACTION_VERSION = "rdf-detail-1"
KEPT_EXTENSIONS = frozenset(
    {
        ".xml",
        ".xades",
        ".pdf",
        ".doc",
        ".docx",
        ".xls",
        ".xlsx",
        ".odt",
        ".ods",
        ".rtf",
        ".txt",
        ".zip",
    }
)
_EXT = r"(?:\.[a-z]{2,5})?"
# A token's stem is a 16-byte `document_ref` in URL-safe base64 without padding.
_TOKEN_NAME = re.compile(rf"[A-Za-z0-9_-]{{22}}{_EXT}")
_UNMATCHED_NAME = re.compile(rf"unmatched-\d+{_EXT}")
_ATTACHMENT_NAME = {
    "Plik": re.compile(rf"plik-\d+{_EXT}"),
    "Zalacznik": re.compile(rf"zalacznik-\d+{_EXT}"),
}
_PDF_METADATA_KEYS = (
    "title",
    "author",
    "subject",
    "keywords",
    "creator",
    "producer",
    "creationDate",
    "modDate",
)
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


def kept_extension(file_name: str | None) -> str:
    """The name's extension, lower-cased, when it is on the allowlist; "" otherwise."""
    suffix = PurePosixPath(file_name or "").suffix.lower()
    return suffix if suffix in KEPT_EXTENSIONS else ""


def file_token(document_ref: str, file_name: str | None) -> str:
    """What replaces a filer's file name: the filing's `document_ref`, path-safe, plus the extension."""
    stem = document_ref.replace("+", "-").replace("/", "_").rstrip("=")
    return stem + kept_extension(file_name)


def _attachment_name(kind: str, n: int, original: str | None) -> str:
    return f"{kind.lower()}-{n}{kept_extension(original)}"


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
    actions.extend(_rename_attachments(root, where))
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


def _rename_attachments(root: etree._Element, where: str) -> list[str]:  # pyright: ignore[reportPrivateUsage]
    """`Plik/Nazwa` -> `plik-<n>`, ePUAP `Zalacznik@nazwaPliku` -> `zalacznik-<n>`, in document order."""
    renamed = {"Plik": 0, "Zalacznik": 0}
    seen = {"Plik": 0, "Zalacznik": 0}
    for el in root.iter(etree.Element):
        kind = etree.QName(el).localname
        if kind == "Plik":
            name = next(
                (c for c in el.iterchildren(etree.Element) if etree.QName(c).localname == "Nazwa"),
                None,
            )
            if name is None:
                continue
            seen[kind] += 1
            new = _attachment_name(kind, seen[kind], name.text)
            if name.text != new:
                name.text = new
                renamed[kind] += 1
        elif kind == "Zalacznik" and el.get("nazwaPliku") is not None:
            seen[kind] += 1
            new = _attachment_name(kind, seen[kind], el.get("nazwaPliku"))
            if el.get("nazwaPliku") != new:
                el.set("nazwaPliku", new)
                renamed[kind] += 1
    return [f"{where}: renamed {n} {kind} names" for kind, n in renamed.items() if n]


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


def _pdf_metadata(doc: pymupdf.Document) -> list[str]:
    """The document-metadata fields a PDF carries: information dictionary entries and XMP."""
    info = doc.metadata or {}
    found = [key for key in _PDF_METADATA_KEYS if info.get(key)]
    if doc.get_xml_metadata():
        found.append("xmp")
    return found


def _redact_pdf(data: bytes, where: str) -> Redaction:
    if (
        b"/ByteRange" not in data
        and b"/Sig" not in data
        and b"/Info" not in data
        and (b"/Metadata" not in data)
    ):
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
        metadata = _pdf_metadata(doc)
        if metadata:
            doc.xref_set_key(-1, "Info", "null")
            doc.del_xml_metadata()
        if not removed and not cleared and not metadata:
            return Redaction(data)
        out = doc.tobytes(garbage=4, deflate=True, no_new_id=True)
    actions: list[str] = []
    if removed or cleared:
        actions.append(
            f"{where}: removed {removed} PDF signature fields, cleared {cleared} signature objects"
        )
    if metadata:
        actions.append(f"{where}: removed PDF metadata ({', '.join(metadata)})")
    return Redaction(out, actions)


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


def redact_download(raw: bytes, names: Mapping[str, str | None] | None = None) -> Redaction:
    """Redact a stored-to-be download (a ZIP of statement files, or a single file).

    `names` maps each filing the download holds (its `document_ref`) to the file name the filer
    gave it (the detail's `nazwaPliku`, `None` when not known). A ZIP's members are renamed from
    it: one content member for one filing takes that filing's token whatever its name; otherwise a
    member takes the token of the one filing whose name it bears, and any other member becomes
    `unmatched-<n>`, which parsing quarantines. The names are never stored.
    """
    if not zipfile.is_zipfile(io.BytesIO(raw)):
        single = redact_file(raw, "raw")
        if single is None:
            raise RedactionError("the download is only a signature")
        return single
    if not names:
        raise RedactionError("naming a ZIP's members needs the filings it holds")
    actions: list[str] = []
    content: list[tuple[zipfile.ZipInfo, bytes]] = []
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            for index, info in enumerate(archive.infolist()):
                if info.is_dir():
                    actions.append(f"zip:member[{index}]: dropped directory entry")
                    continue
                result = redact_file(archive.read(info), f"zip:member[{index}]")
                if result is None:
                    actions.append(f"zip:member[{index}]: dropped detached signature")
                    continue
                actions.extend(result.actions)
                content.append((info, result.data))
    except (zipfile.BadZipFile, zipfile.LargeZipFile) as exc:
        raise RedactionError(f"unreadable ZIP: {exc}") from exc
    renamed = _member_names([info.filename for info, _ in content], names)
    for (info, _), new in zip(content, renamed, strict=True):
        if info.filename != new:
            actions.append(f"zip:{new}: renamed")
    if not actions:
        return Redaction(raw)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as out:
        for (info, data), new in zip(content, renamed, strict=True):
            copy = zipfile.ZipInfo(new, date_time=info.date_time)
            copy.compress_type = info.compress_type
            copy.external_attr = info.external_attr
            copy.create_system = info.create_system
            out.writestr(copy, data)
    return Redaction(buffer.getvalue(), actions)


def _member_names(members: list[str], names: Mapping[str, str | None]) -> list[str]:
    """Each content member's stored name (ADR 0009 second addendum, rule 2)."""
    if len(members) == 1 and len(names) == 1:
        [(ref, name)] = names.items()
        return [file_token(ref, name or members[0])]
    bases = [PurePosixPath(m).name for m in members]
    out: list[str] = []
    unmatched = 0
    for member, base in zip(members, bases, strict=True):
        refs = [ref for ref, name in names.items() if name is not None and name == base]
        if len(refs) == 1 and bases.count(base) == 1:
            out.append(file_token(refs[0], base))
        else:
            unmatched += 1
            out.append(f"unmatched-{unmatched}{kept_extension(member)}")
    return out


def original_file_name(detail_body: bytes) -> str | None:
    """The filer's file name in an RDF detail as received (used in memory only, never stored)."""
    doc = json.loads(detail_body)
    name = doc.get("nazwaPliku") if isinstance(doc, dict) else None
    return name if isinstance(name, str) else None


def redact_rdf_detail(body: bytes) -> Redaction:
    """An RDF document detail with `nazwaPliku` replaced by its token; otherwise as received.

    RDF serialises details as compact UTF-8 JSON, which is re-serialised identically, so only the
    one value changes.
    """
    try:
        doc = json.loads(body)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RedactionError(f"unreadable RDF detail: {exc}") from exc
    if not isinstance(doc, dict) or doc.get("nazwaPliku") is None:  # pyright: ignore[reportUnknownMemberType]
        return Redaction(body)
    detail = cast(dict[str, object], doc)
    ref = detail.get("identyfikator")
    if not isinstance(ref, str) or not ref:
        raise RedactionError("RDF detail has a file name but no identyfikator to token it")
    name = detail["nazwaPliku"]
    token = file_token(ref, name if isinstance(name, str) else None)
    if name == token:
        return Redaction(body)
    detail["nazwaPliku"] = token
    out = json.dumps(detail, ensure_ascii=False, separators=(",", ":")).encode()
    return Redaction(out, ["detail: nazwaPliku replaced by its token"])


def personal_data_markers(raw: bytes, document_refs: Iterable[str] | None = None) -> list[str]:
    """Where natural persons' data is still detectable in a download (empty when clean).

    A ZIP member whose name is not a token (or `unmatched-<n>`) is a marker; with
    `document_refs`, a token must also be one of those filings'. An RDF detail whose
    `nazwaPliku` is not its token is a marker too. No marker quotes a name.
    """
    found: list[str] = []
    if zipfile.is_zipfile(io.BytesIO(raw)):
        stems = None if document_refs is None else {file_token(r, None) for r in document_refs}
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            for index, info in enumerate(archive.infolist()):
                where = f"zip:member[{index}]"
                if not _member_name_ok(info.filename, stems):
                    found.append(f"{where}: file name is not a token")
                if not info.is_dir():
                    found.extend(_file_markers(archive.read(info), where))
        return found
    if raw.lstrip().startswith(b"{") and b'"nazwaPliku"' in raw:
        return _detail_markers(raw)
    return _file_markers(raw, "raw")


def _member_name_ok(name: str, stems: set[str] | None) -> bool:
    if _UNMATCHED_NAME.fullmatch(name):
        return True
    if not _TOKEN_NAME.fullmatch(name) or PurePosixPath(name).suffix not in {"", *KEPT_EXTENSIONS}:
        return False
    return stems is None or PurePosixPath(name).stem in stems


def _detail_markers(raw: bytes) -> list[str]:
    try:
        doc = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError):
        return ["detail: unreadable"]
    if not isinstance(doc, dict):
        return []
    detail = cast(dict[str, object], doc)
    name, ref = detail.get("nazwaPliku"), detail.get("identyfikator")
    if name is None:
        return []
    if not isinstance(ref, str) or not isinstance(name, str) or name != file_token(ref, name):
        return ["detail: nazwaPliku is not a token"]
    return []


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
        found.extend(_attachment_name_markers(root, where))
        for el in root.iter(etree.Element):
            local = etree.QName(el).localname
            if local in _BASE64_PAYLOADS or el.get("Encoding") == f"{DS_NS}base64":
                payload = _b64decode(el.text)
                if payload is not None:
                    found.extend(_file_markers(payload, f"{where}>{local}"))
    return found


def _attachment_name_markers(root: etree._Element, where: str) -> list[str]:  # pyright: ignore[reportPrivateUsage]
    found: list[str] = []
    for el in root.iter(etree.Element):
        kind = etree.QName(el).localname
        if kind == "Plik":
            for child in el.iterchildren(etree.Element):
                if etree.QName(child).localname == "Nazwa" and not _ATTACHMENT_NAME[kind].fullmatch(
                    child.text or ""
                ):
                    found.append(f"{where}: Plik name is not a placeholder")
        elif (
            kind == "Zalacznik"
            and el.get("nazwaPliku") is not None
            and not _ATTACHMENT_NAME[kind].fullmatch(el.get("nazwaPliku") or "")
        ):
            found.append(f"{where}: Zalacznik name is not a placeholder")
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
        metadata = _pdf_metadata(doc)
        if metadata:
            found.append(f"{where}: PDF metadata ({', '.join(metadata)})")
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


def may_name_a_person(value: str) -> bool:
    """A role word (supervisor, trustee, notary…) with no legal-form marker after it."""
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
                if (
                    key in _PERSON_KEYS
                    and isinstance(value, str)
                    and value not in ("", PLACEHOLDER)
                ):
                    removed.add(value)
                    result.persons += 1
                    out[key] = PLACEHOLDER
                elif (
                    isinstance(value, str)
                    and len(value) > _TEXT_LIMIT
                    and (not _allowlisted(key, here) or may_name_a_person(value))
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
