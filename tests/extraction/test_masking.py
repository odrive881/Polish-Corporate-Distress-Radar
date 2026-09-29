"""The masker (plan 0013 step C; ADR 0009, third addendum). Every name below is invented."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest

from distress_radar.extraction.masking import MODEL_VERSION, TOKENS, load_model, mask


@dataclass
class _NoNames:
    """A stand-in pipeline that finds no entities, so the patterns are tested alone."""

    meta: dict[str, str] = field(default_factory=lambda: {"version": MODEL_VERSION})

    def __call__(self, text: str) -> Any:
        return type("Doc", (), {"ents": ()})()


# --- the patterns ------------------------------------------------------------------------------


def test_a_pesel_shaped_number_is_masked_and_other_numbers_are_not() -> None:
    got = mask("PESEL 90010112345, KRS 0000123456, NIP 5213000001.", _NoNames())
    assert got.text == "PESEL [pesel], KRS 0000123456, NIP 5213000001."
    assert got.counts == {"pesel": 1}


def test_an_email_and_a_marked_phone_number_are_masked() -> None:
    got = mask(
        "Kontakt: j.kowal@example.invalid, tel. +48 600 100 200 lub fax 22 555-01-02.", _NoNames()
    )
    assert got.text == "Kontakt: [email], tel. [telefon] lub fax [telefon]."
    assert got.counts == {"email": 1, "phone": 2}


def test_an_amount_written_with_spaces_is_not_a_phone_number() -> None:
    text = "Przychody wyniosły 123 456 789 zł, a zobowiązania 12 345 678,90 zł."
    assert mask(text, _NoNames()).text == text


# --- names, with the model ---------------------------------------------------------------------


@pytest.fixture(scope="module")
def nlp() -> Any:
    return load_model()


@pytest.mark.parametrize(
    "text",
    [
        "Sprawozdanie podpisał Prezes Zarządu Jan Kowalski.",
        "Zarząd powierzył Annie Wiśniewskiej prowadzenie ksiąg rachunkowych.",
        "Umowę pożyczki zawarto z Janem Kowalskim, wspólnikiem spółki.",
        "Członkiem zarządu jest Katarzyna Nowak-Zielińska.",
        "Sporządziła: Małgorzata Kamińska, główna księgowa\nZatwierdził: Piotr Lewandowski",
    ],
)
def test_planted_names_are_masked_in_their_inflected_forms(text: str, nlp: Any) -> None:
    got = mask(text, nlp)
    for surname in ("Kowalsk", "Wiśniewsk", "Nowak", "Zielińsk", "Kamińsk", "Lewandowsk"):
        assert surname not in got.text, got.text
    assert got.counts.get("person", 0) >= 1


def test_company_names_and_the_financial_text_stay(nlp: Any) -> None:
    text = (
        "Spółka zawarła umowę z Budimex S.A. na roboty budowlane. "
        "Zobowiązania wobec banku PKO BP zostały spłacone w terminie."
    )
    got = mask(text, nlp)
    assert "Budimex" in got.text and "PKO BP" in got.text and "roboty budowlane" in got.text


def test_masking_is_stable_and_idempotent(nlp: Any) -> None:
    text = "Prezes Zarządu Jan Kowalski, tel. 600100200, PESEL 90010112345."
    once = mask(text, nlp)
    assert mask(text, nlp) == once
    assert mask(once.text, nlp).text == once.text
    assert all(token in TOKENS.values() for token in ("[osoba]", "[pesel]", "[telefon]"))


def test_the_pinned_model_is_the_installed_one(nlp: Any) -> None:
    assert nlp.meta["version"] == MODEL_VERSION
