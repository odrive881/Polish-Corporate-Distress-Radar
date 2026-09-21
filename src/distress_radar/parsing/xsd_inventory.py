"""Statutory line items declared by an MF financial-statement XSD (C2 support).

The MF `…StrukturyDanychSprFin` schemas declare each statement (`Bilans`,
`RZiS`, `ZestZmianWKapitale`, `RachPrzeplywow`) as a named complex type whose
nested, mostly anonymous elements are the statutory line items of the UoR
annex, e.g. `Aktywa/Aktywa_A/Aktywa_A_I`. This module lists them so that
mapping specs can be checked for completeness against the official schema
(`tests/parsing/test_mapping_coverage.py`) instead of against memory.

User-defined lines (`PozycjaUszczegolawiajaca*`) are not statutory items and
are not listed; `user_slots` says whether an element declares interleaved ones
(`PozycjaUszczegolawiajaca_N`) among its children.

It also holds the two ways this project compares statutory labels, which is
how a line's *meaning* is established when its element path cannot be trusted
(plan 0005 finding 3): `normalise_label` compares labels as written, and
`same_line` asks whether two labels name the same line.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from lxml import etree

from distress_radar.parsing.containers import Element

XSD = "{http://www.w3.org/2001/XMLSchema}"
STATEMENT_TYPES = ("Bilans", "RZiS", "ZestZmianWKapitale", "RachPrzeplywow")
# Complex types whose instances carry KwotaA/KwotaB(/KwotaB1).
_AMOUNT_TYPES = {
    "TKwotyPozycji",
    "TKwotyPozycjiTys",
    "TPozycjaSprawozdania",
    "TPozycjaSprawozdaniaTys",
}


@dataclass(frozen=True)
class XsdLineItem:
    """One statutory element: its path below the statement element, and label."""

    path: tuple[str, ...]  # local names, e.g. ("Aktywa", "Aktywa_A")
    label: str  # the element's xsd:documentation, whitespace-normalised
    has_amounts: bool  # False for section headers that only group children
    user_slots: bool  # declares interleaved `PozycjaUszczegolawiajaca_N` user-defined lines


def normalise_label(label: str) -> str:
    """A label as written, ignoring dash style, spacing, case and a trailing colon.

    Two labels equal under this are the same text. Use it to ask whether a
    label *changed* — e.g. classifying short-form lines against the full form's
    (`notebooks/exploration/short_form_line_classification.py`, ADR 0005 second
    addendum), where a differing label means the line is a different line.
    """
    return " ".join(label.replace("\u2013", "-").replace("\u2014", "-").split()).rstrip(":").lower()


def same_line(label: str, other: str) -> bool:
    """Whether two labels name the same statutory line.

    Tolerates, on top of `normalise_label`, the presentational differences that
    never change what an amount is: a list marker, an "of which X" note (a
    subset, so the total is unchanged), an applicability clause naming which
    entities a line is for, and spacing inside a formula. A `.R2025`-style
    narrowing changes the words BEFORE "w tym", so none of these can hide one.
    """
    return _denotation(label) == _denotation(other)


def _denotation(label: str) -> str:
    text = normalise_label(label)
    text = re.sub(r"^-\s*", "", text)
    text = re.sub(r"^[a-z]\)\s*", "", text)
    text = re.sub(r",?\s*w tym\b.*$", "", text)
    text = re.sub(r"\s*\(dla .*?\)\.?$", "", text)
    text = re.sub(r"\s*-\s*", "-", text)
    return text.rstrip(":").strip()


def _local(qname: str | None) -> str:
    return "" if qname is None else qname.rsplit(":", 1)[-1]


def _label(el: Element) -> str:
    doc = el.find(f"{XSD}annotation/{XSD}documentation")
    return " ".join((doc.text or "").split()) if doc is not None else ""


def _has_amounts(el: Element) -> bool:
    if _local(el.get("type")) in _AMOUNT_TYPES:
        return True
    ext = el.find(f"{XSD}complexType/{XSD}complexContent/{XSD}extension")
    return ext is not None and _local(ext.get("base")) in _AMOUNT_TYPES


def _has_user_slots(el: Element) -> bool:
    return any(
        re.fullmatch(r"PozycjaUszczegolawiajaca_\d+", child.get("name") or "")
        for child in _child_elements(el)
    )


def _child_elements(node: Element) -> list[Element]:
    """Element declarations nested in `node` with no element declaration between."""
    found: list[Element] = []
    for el in node.iter(f"{XSD}element"):
        if el is node:
            continue
        anc = el.getparent()
        while anc is not None and anc is not node and anc.tag != f"{XSD}element":
            anc = anc.getparent()
        if anc is node:
            found.append(el)
    return found


def statement_line_items(xsd_path: Path, type_name: str) -> list[XsdLineItem]:
    """Statutory items of the named complex type (e.g. `BilansJednostkaInna`), in schema order."""
    root = etree.parse(str(xsd_path)).getroot()
    matches = [ct for ct in root.iter(f"{XSD}complexType") if ct.get("name") == type_name]
    if len(matches) != 1:
        raise ValueError(
            f"{xsd_path.name}: expected one complexType {type_name!r}, found {len(matches)}"
        )
    items: list[XsdLineItem] = []

    def walk(node: Element, prefix: tuple[str, ...]) -> None:
        for el in _child_elements(node):
            name = el.get("name")
            if name is None or name.startswith("PozycjaUszczegolawiajaca"):
                continue
            path = (*prefix, name)
            items.append(
                XsdLineItem(
                    path=path,
                    label=_label(el),
                    has_amounts=_has_amounts(el),
                    user_slots=_has_user_slots(el),
                )
            )
            walk(el, path)

    walk(matches[0], ())
    return items
