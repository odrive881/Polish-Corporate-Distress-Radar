"""Invariant 6 for the legal-source fixtures (plan 0008 step A, ADR 0011).

`tests/fixtures/legal/` holds redacted registry extracts. This makes the
redaction a gate, the way `test_no_fixture_contains_personal_data` does for the
XML statements: a fixture added or regenerated later cannot quietly bring a name
or a PESEL number back.
"""

import json
import re
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from distress_radar.acquisition.redaction import may_name_a_person

LEGAL = Path(__file__).parent.parent / "fixtures" / "legal"
FIXTURES = sorted(LEGAL.rglob("*.json"))
PERSON_KEYS = {"imie", "imieDrugie", "nazwiskoICzlon", "nazwiskoIICzlon", "pesel"}
PLACEHOLDER = "[REDACTED]"
# A notary, supervisor or trustee named in free text: the role word followed by
# something that is not itself a role or preposition.
ROLE_THEN_NAME = re.compile(
    r"\b(NOTARIUSZ\w*|SYNDYK\w*|NADZORC\w*|KURATOR\w*|ADWOKAT\w*|RADC\w*|W OSOBIE)\s+"
    r"(?!SĄDOW|ZARZĄDU|MASY|TYMCZASOW|W\b|WE\b|Z\b|REP|NR\b|KANCELARII)[A-ZĄĆĘŁŃÓŚŹŻ]{2,}",
    re.IGNORECASE,
)


def _leaves(obj: Any, key: str = "") -> Iterator[tuple[str, Any]]:
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield from _leaves(v, k)
    elif isinstance(obj, list):
        for v in obj:
            yield from _leaves(v, key)
    else:
        yield key, obj


def test_there_are_legal_fixtures() -> None:
    assert FIXTURES, "tests/fixtures/legal/ is empty: the glob or the layout changed"


@pytest.mark.parametrize("fixture", FIXTURES, ids=lambda p: p.name)
def test_every_person_field_is_redacted(fixture: Path) -> None:
    doc = json.loads(fixture.read_text(encoding="utf-8"))
    leaked = [k for k, v in _leaves(doc) if k in PERSON_KEYS and v not in (PLACEHOLDER, "", None)]
    assert leaked == [], f"{fixture.name}: {len(leaked)} unredacted person fields"


@pytest.mark.parametrize("fixture", FIXTURES, ids=lambda p: p.name)
def test_no_pesel_or_named_role_in_free_text(fixture: Path) -> None:
    raw = fixture.read_text(encoding="utf-8")
    assert not re.search(r"\b\d{11}\b", raw), f"{fixture.name} has an 11-digit run (PESEL-shaped)"
    match = ROLE_THEN_NAME.search(raw)
    assert match is None, (
        f"{fixture.name}: a role word followed by a possible name ({match.group(1)})"
    )


# Long free text is where a person can hide outside the person keys; the production redactor
# reduces it unless it is allowlisted and names no role without a legal form (krs-json-2).
# The gate applies the same rule to every long string a fixture keeps. MSiG's `terms` are
# fixed vocabulary stems ("nadzorc"), checked by the vocabulary itself, not here.
LONG_TEXT = 40


def named_roles(doc: Any) -> list[str]:
    """Long strings in a fixture that may name a person by their role."""
    found: list[str] = []
    for key, value in _leaves(doc):
        if key in ("terms", "terms_before") or not isinstance(value, str):
            continue
        if len(value) > LONG_TEXT and may_name_a_person(value):
            found.append(f"{key}: {value[:60]}")
    return found


@pytest.mark.parametrize("fixture", FIXTURES, ids=lambda p: p.name)
def test_no_long_text_names_a_role_without_a_legal_form(fixture: Path) -> None:
    doc = json.loads(fixture.read_text(encoding="utf-8"))
    assert named_roles(doc) == [], fixture.name


def test_the_gate_catches_a_supervisor_named_after_the_court() -> None:
    """The case the role-word regex above lets through ("NADZORCY SĄDOWEGO …")."""
    order = "POSTANOWIENIE Z DNIA 25.03.2022 R. O USTANOWIENIU TYMCZASOWEGO NADZORCY SĄDOWEGO ALOJZEGO WYMYŚLONEGO"
    assert ROLE_THEN_NAME.search(order) is None  # the old gate misses it
    assert named_roles({"organWydajacy": order}) == [f"organWydajacy: {order[:60]}"]
    company = order.replace("ALOJZEGO WYMYŚLONEGO", "PRZYKŁADOWA SPÓŁKA Z O.O.")
    assert named_roles({"organWydajacy": company}) == []
