"""C1, first step: unwrap a stored RDF download into the statement files it carries.

RDF serves each filing as a ZIP (plan 0003). A member may be the statement XML
itself (possibly with an enveloped XAdES signature inside, which needs no
unwrapping), or a signature container around it:

- a `ds:Signature` root with the statement inline in a `ds:Object`;
- a `Signatures` root whose `ds:Object` holds the statement base64-encoded;
- an ePUAP `podpisanyPlik` envelope whose `Zalacznik` holds a file base64-encoded;
- a detached signature (a `Signatures`/`ds:Signature` root with no content).

`unwrap()` returns every file found, with the path that led to it
(`source_member`, part of each fact's lineage), and never modifies the stored
bytes: extracted files are new byte strings. Anything it cannot classify raises
`ContainerError`, which the caller quarantines.
"""

from __future__ import annotations

import base64
import binascii
import io
import zipfile
from dataclasses import dataclass
from typing import Literal

from lxml import etree

DS_NS = "http://www.w3.org/2000/09/xmldsig#"
EPUAP_SIGNED_FILE_NS = "http://epuap.gov.pl/fe-model-web/wzor_lokalny/EPUAP-----/podpisanyPlik/"
CRWDE_STRUCTURE_NS = "http://crd.gov.pl/xml/schematy/struktura/2009/11/16/"
CRWDE_TEMPLATE_PREFIX = "http://crd.gov.pl/wzor/"
UTF8_BOM = b"\xef\xbb\xbf"

Element = etree._Element  # pyright: ignore[reportPrivateUsage]  # lxml exposes no public alias

MemberKind = Literal["xml_statement", "pdf", "detached_signature"]


def safe_parser() -> etree.XMLParser:
    """The only lxml parser used on filed documents: no entities, DTDs or network.

    `huge_tree` is needed because statements embed their notes as base64 text
    nodes larger than libxml2's default limit.
    """
    return etree.XMLParser(
        huge_tree=True,
        resolve_entities=False,
        no_network=True,
        load_dtd=False,
        remove_comments=False,
    )


class ContainerError(Exception):
    """A stored file that cannot be unwrapped. `reason_code` goes to quarantine."""

    def __init__(self, reason_code: str, detail: str) -> None:
        super().__init__(f"{reason_code}: {detail}")
        self.reason_code = reason_code
        self.detail = detail


@dataclass(frozen=True)
class ContainerMember:
    source_member: str  # e.g. "zip:SF/SF2023.xml" or "zip:x.xades>ds:Object[2]>base64"
    member_name: str  # base name of the ZIP member, matched to filing_index.file_name
    kind: MemberKind
    data: bytes  # XML without BOM, or PDF bytes; empty for detached signatures


def _is_statement_root(el: Element) -> bool:
    q = etree.QName(el)
    if q.localname.startswith("Jednostka"):
        return True
    return q.localname == "Dokument" and (q.namespace or "").startswith(CRWDE_TEMPLATE_PREFIX)


def _b64(text: str | None, where: str) -> bytes:
    try:
        return base64.b64decode("".join((text or "").split()), validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ContainerError("unknown_container", f"{where}: invalid base64 ({exc})") from exc


def _classify(data: bytes, path: str, member_name: str) -> list[ContainerMember]:
    data = data.removeprefix(UTF8_BOM)
    if data.lstrip().startswith(b"%PDF"):
        return [ContainerMember(path, member_name, "pdf", data.lstrip())]
    if not data.lstrip().startswith(b"<"):
        raise ContainerError("unknown_container", f"{path}: neither XML nor PDF")
    try:
        root = etree.fromstring(data, safe_parser())
    except etree.XMLSyntaxError as exc:
        raise ContainerError("xml_malformed", f"{path}: {exc}") from exc
    if _is_statement_root(root):
        return [ContainerMember(path, member_name, "xml_statement", data)]
    q = etree.QName(root)
    if q.namespace == EPUAP_SIGNED_FILE_NS and q.localname == "Dokument":
        return _unwrap_epuap(root, path, member_name)
    if (q.namespace == DS_NS and q.localname == "Signature") or (
        not q.namespace and q.localname == "Signatures"
    ):
        return _unwrap_signatures(root, path, member_name)
    raise ContainerError(
        "unknown_container", f"{path}: unrecognised root {{{q.namespace}}}{q.localname}"
    )


def _unwrap_signatures(root: Element, path: str, member_name: str) -> list[ContainerMember]:
    found: list[ContainerMember] = []
    objects = root.iter(f"{{{DS_NS}}}Object")
    for index, obj in enumerate(objects, start=1):
        step = f"{path}>ds:Object[{index}]"
        inline = [c for c in obj.iterchildren(etree.Element) if _is_statement_root(c)]
        if inline:
            if len(inline) > 1:
                raise ContainerError("unknown_container", f"{step}: several statements inline")
            data = etree.tostring(inline[0], xml_declaration=True, encoding="UTF-8")
            found.append(ContainerMember(step, member_name, "xml_statement", data))
        elif obj.get("Encoding") == f"{DS_NS}base64":
            found.extend(_classify(_b64(obj.text, step), f"{step}>base64", member_name))
    if not found:
        return [ContainerMember(path, member_name, "detached_signature", b"")]
    return found


def _unwrap_epuap(root: Element, path: str, member_name: str) -> list[ContainerMember]:
    found: list[ContainerMember] = []
    for index, att in enumerate(root.iter(f"{{{CRWDE_STRUCTURE_NS}}}Zalacznik"), start=1):
        step = f"{path}>epuap:Zalacznik[{index}]"
        if att.get("kodowanie") != "base64":
            raise ContainerError("unknown_container", f"{step}: encoding {att.get('kodowanie')!r}")
        payload = att.find(f"{{{CRWDE_STRUCTURE_NS}}}DaneZalacznika")
        if payload is None:
            raise ContainerError("unknown_container", f"{step}: no DaneZalacznika")
        found.extend(_classify(_b64(payload.text, step), f"{step}>base64", member_name))
    if not found:
        raise ContainerError("unknown_container", f"{path}: ePUAP envelope without attachments")
    return found


def unwrap(raw: bytes) -> list[ContainerMember]:
    """Every file inside a stored download, in ZIP member order."""
    if not zipfile.is_zipfile(io.BytesIO(raw)):
        return _classify(raw, "raw", "")
    members: list[ContainerMember] = []
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            for info in archive.infolist():
                if info.is_dir():
                    continue
                name = info.filename
                members.extend(
                    _classify(archive.read(info), f"zip:{name}", name.rsplit("/", 1)[-1])
                )
    except (zipfile.BadZipFile, zipfile.LargeZipFile) as exc:
        raise ContainerError("unknown_container", f"unreadable ZIP: {exc}") from exc
    return members


@dataclass(frozen=True)
class FilingRow:
    """The `filing_index` rows that share one stored download."""

    document_ref: str
    file_name: str | None


def match_members(
    members: list[ContainerMember], rows: list[FilingRow]
) -> tuple[dict[str, FilingRow], list[ContainerMember]]:
    """Tie each statement-bearing member to its filing row.

    Returns `{source_member: row}` and the members that could not be tied (for
    quarantine as `member_not_in_filing_index`). Detached signatures are
    ignored. A ZIP with one statement and one row pairs them regardless of
    names (RDF's `nazwaPliku` is the uploader's name, not always the member's);
    otherwise the member's base name must equal exactly one row's `file_name`.
    """
    content = [m for m in members if m.kind != "detached_signature"]
    if len(content) == 1 and len(rows) == 1:
        return {content[0].source_member: rows[0]}, []
    by_name: dict[str, list[FilingRow]] = {}
    for row in rows:
        if row.file_name is not None:
            by_name.setdefault(row.file_name, []).append(row)
    matched: dict[str, FilingRow] = {}
    unmatched: list[ContainerMember] = []
    for member in content:
        candidates = by_name.get(member.member_name, [])
        if len(candidates) == 1:
            matched[member.source_member] = candidates[0]
        else:
            unmatched.append(member)
    # A row claimed by several members is ambiguous: none of them is trusted.
    claims: dict[str, list[str]] = {}
    for source_member, row in matched.items():
        claims.setdefault(row.document_ref, []).append(source_member)
    for sources in claims.values():
        if len(sources) > 1:
            for source_member in sources:
                del matched[source_member]
            unmatched.extend(m for m in content if m.source_member in sources)
    return matched, unmatched
