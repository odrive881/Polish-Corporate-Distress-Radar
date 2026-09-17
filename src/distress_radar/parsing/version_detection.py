"""C1: detect a statement's structure version from its content (AGENT_SPEC §6C1).

The key is the root element (namespace + local name) plus the `KodSprawozdania`
header's `kodSystemowy` and `wersjaSchemy` attributes. The namespace alone is
not enough: MF schemas 1-0 and 1-2 share it. File names are never used.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from lxml import etree

from distress_radar.parsing.canonical_schema import MappingConfig, StructureSpec
from distress_radar.parsing.containers import Element

DetectionStatus = Literal["mapped", "not_yet_mapped", "unknown"]


@dataclass(frozen=True)
class Detection:
    key: tuple[str, str, str, str]  # (root namespace, root name, kodSystemowy, wersjaSchemy)
    status: DetectionStatus
    spec: StructureSpec | None


class DetectionError(Exception):
    """The document has no readable `KodSprawozdania` header."""


def detection_key(root: Element) -> tuple[str, str, str, str]:
    q = etree.QName(root)
    headers = [
        el for el in root.iter(etree.Element) if etree.QName(el).localname == "KodSprawozdania"
    ]
    if len(headers) != 1:
        raise DetectionError(f"expected one KodSprawozdania element, found {len(headers)}")
    kod, wersja = headers[0].get("kodSystemowy"), headers[0].get("wersjaSchemy")
    if kod is None or wersja is None:
        raise DetectionError("KodSprawozdania lacks kodSystemowy or wersjaSchemy")
    return (q.namespace or "", q.localname, kod, wersja)


def detect(root: Element, config: MappingConfig) -> Detection:
    key = detection_key(root)
    spec = config.spec_for(key)
    if spec is not None:
        return Detection(key, "mapped", spec)
    if config.is_catalogued(key):
        return Detection(key, "not_yet_mapped", None)
    return Detection(key, "unknown", None)
