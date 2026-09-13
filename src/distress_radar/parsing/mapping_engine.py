"""Parsing of financial statement filing XML documents (e.g. eD/StrukturyDanychSprFin).

These filings are namespaced XML (tns:/jin:/dtsf:/etd:/str: prefixes tied to
dated schema URLs that vary between filing versions), so lookups below match
elements by local tag name only and walk direct-child paths explicitly rather
than using namespace-qualified XPath.

TODO(C2): prototype. Replace ElementTree/float with lxml + Decimal and drive
extraction from config/mappings/structures/*.yaml per AGENT_SPEC.md §6C.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path


def _local_name(tag: str) -> str:
    """Strip the '{namespace-uri}' prefix ElementTree adds to tag names."""
    return tag.rsplit("}", 1)[-1]


def _child(elem: ET.Element, name: str) -> ET.Element:
    """Return the first direct child of `elem` whose local tag is `name`."""
    for child in elem:
        if _local_name(child.tag) == name:
            return child
    raise ValueError(f"expected child <{name}> not found under <{_local_name(elem.tag)}>")


def _path(elem: ET.Element, *names: str) -> ET.Element:
    """Walk a chain of direct-child lookups, e.g. _path(root, 'A', 'B') == root/A/B."""
    for name in names:
        elem = _child(elem, name)
    return elem


def _kwota_a(elem: ET.Element) -> float:
    """Read the 'KwotaA' (current period amount) direct child of `elem` as a float."""
    text = _child(elem, "KwotaA").text
    if text is None:
        raise ValueError("expected <KwotaA> to have text content")
    return float(text)


def parse_filing(xml_path: str | Path) -> dict[str, float]:
    """Parse a filing XML file into a summary dict of key financial figures.

    Args:
        xml_path: Path to the filing XML file.

    Returns:
        A dict of extracted figures:
        {
            "total_assets": float,
            "total_liabilities": float,
            "total_equity": float,
            "revenue": float,
        }
        See tests/fixtures/*_expected.json for concrete examples.
    """
    root = ET.parse(xml_path).getroot()
    jednostka = _path(root, "TrescDokumentu", "JednostkaInna")

    bilans = _child(jednostka, "Bilans")
    aktywa = _child(bilans, "Aktywa")
    pasywa = _child(bilans, "Pasywa")

    rzis_por = _path(jednostka, "RZiS", "RZiSPor")

    return {
        "total_assets": _kwota_a(aktywa),
        "total_liabilities": _kwota_a(_child(pasywa, "Pasywa_B")),
        "total_equity": _kwota_a(_child(pasywa, "Pasywa_A")),
        "revenue": _kwota_a(_child(rzis_por, "A")),
    }
