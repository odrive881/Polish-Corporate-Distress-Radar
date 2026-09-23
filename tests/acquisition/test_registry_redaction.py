"""The KRS extract redactor (ADR 0009 addendum, plan 0008 decision 3). Every person here is made up."""

from __future__ import annotations

import copy
import json
from typing import Any

import pytest

from distress_radar.acquisition.redaction import (
    PLACEHOLDER,
    RedactionError,
    redact_registry_extract,
)

PESEL = "90010112345"


def synthetic_extract() -> dict[str, Any]:
    person = {
        "identyfikator": [{"nrWpisuWprow": "3", "pesel": PESEL}],
        "imiona": [{"imiona": {"imie": "ZENOBIUSZ", "imieDrugie": "KAZIMIERZ"}, "nrWpisuWprow": "3"}],
        "nazwisko": [{"nazwisko": {"nazwiskoICzlon": "PRZYKŁADOWSKI"}, "nrWpisuWprow": "3"}],
    }
    return {
        "odpis": {
            "rodzaj": "Pełny",
            "naglowekP": {
                "numerKRS": "0000000042",
                "dataCzasOdpisu": "23.09.2026 10:19:13",
                "wpis": [
                    {"numerWpisu": 1, "dataWpisu": "01.02.2010", "opis": "REJESTRACJA W KRAJOWYM REJESTRZE SĄDOWYM"},
                    {"numerWpisu": 3, "dataWpisu": "05.03.2015", "opis": "ZMIANA DANYCH W REJESTRZE"},
                    {"numerWpisu": 4, "dataWpisu": "10.09.2025", "opis": "WYKREŚLENIE Z KRAJOWEGO REJESTRU SĄDOWEGO"},
                ],
            },
            "dane": {
                "dzial1": {
                    "danePodmiotu": {"nazwa": [{"nazwa": "BUDOWLANKA PRZYKŁADOWA SPÓŁKA Z OGRANICZONĄ ODPOWIEDZIALNOŚCIĄ", "nrWpisuWprow": "1"}]},
                    "umowaStatut": {"pozycja": [{"zawarcieZmianaUmowyStatutu": "12.01.2010 R., NOTARIUSZ ALOJZY WYMYŚLONY, REP. A NR 1/2010", "nrWpisuWprow": "1"}]},
                },
                "dzial2": {"reprezentacja": {"sklad": [person]}},
                "dzial3": {
                    "przedmiotDzialalnosci": {
                        "przedmiotPrzewazajacejDzialalnosci": [
                            {"pozycja": [{"opis": "ROBOTY BUDOWLANE ZWIĄZANE ZE WZNOSZENIEM BUDYNKÓW MIESZKALNYCH I NIEMIESZKALNYCH", "kodDzial": "41"}]}
                        ]
                    }
                },
                "dzial6": {
                    "likwidacja": [
                        {
                            "likwidatorzy": [copy.deepcopy(person)],
                            "otwarcieLikwidacji": [{"otwarcieLikwidacji": "UCHWAŁA ZGROMADZENIA WSPÓLNIKÓW Z 01.08.2023, AKT NOTARIALNY", "nrWpisuWprow": "3"}],
                        }
                    ]
                },
                # Short free text naming the person outside any person key: the backstop's case.
                "dzial5": {"uwagi": [{"uwagi": "PEŁNOMOCNIK: PRZYKŁADOWSKI"}]},
            },
        }
    }


def _redacted() -> dict[str, Any]:
    return json.loads(redact_registry_extract(synthetic_extract()).data)


def test_person_fields_become_the_placeholder() -> None:
    result = redact_registry_extract(synthetic_extract())
    text = result.data.decode("utf-8")
    for value in ("ZENOBIUSZ", "KAZIMIERZ", "PRZYKŁADOWSKI", PESEL):
        assert value not in text
    assert result.persons == 8  # pesel, imie, imieDrugie, nazwiskoICzlon; two persons


def test_structure_entry_numbers_and_dates_survive() -> None:
    doc = _redacted()
    sklad = doc["odpis"]["dane"]["dzial2"]["reprezentacja"]["sklad"][0]
    assert sklad["identyfikator"][0] == {"nrWpisuWprow": "3", "pesel": PLACEHOLDER}
    assert [e["dataWpisu"] for e in doc["odpis"]["naglowekP"]["wpis"]] == [
        "01.02.2010",
        "05.03.2015",
        "10.09.2025",
    ]
    assert doc["odpis"]["naglowekP"]["numerKRS"] == "0000000042"


def test_free_text_is_reduced_to_its_date_unless_allowlisted() -> None:
    doc = _redacted()
    dane = doc["odpis"]["dane"]
    # A notary's name in the articles' citation: gone, the date kept.
    assert dane["dzial1"]["umowaStatut"]["pozycja"][0]["zawarcieZmianaUmowyStatutu"] == (
        f"{PLACEHOLDER} 12.01.2010"
    )
    opening = dane["dzial6"]["likwidacja"][0]["otwarcieLikwidacji"][0]["otwarcieLikwidacji"]
    assert opening == f"{PLACEHOLDER} 01.08.2023"
    # Legal entities' names, PKD descriptions and entry descriptions are generic: kept.
    assert dane["dzial1"]["danePodmiotu"]["nazwa"][0]["nazwa"].startswith("BUDOWLANKA")
    pkd = dane["dzial3"]["przedmiotDzialalnosci"]["przedmiotPrzewazajacejDzialalnosci"][0]
    assert pkd["pozycja"][0]["opis"].startswith("ROBOTY BUDOWLANE")
    descriptions = [e["opis"] for e in doc["odpis"]["naglowekP"]["wpis"]]
    assert "WYKREŚLENIE Z KRAJOWEGO REJESTRU SĄDOWEGO" in descriptions


def test_a_removed_name_elsewhere_is_blanked() -> None:
    result = redact_registry_extract(synthetic_extract())
    assert json.loads(result.data)["odpis"]["dane"]["dzial5"]["uwagi"][0]["uwagi"] == PLACEHOLDER
    assert result.blanked == 1


def test_deterministic_and_idempotent() -> None:
    first = redact_registry_extract(synthetic_extract())
    assert redact_registry_extract(synthetic_extract()).data == first.data
    again = redact_registry_extract(json.loads(first.data))
    assert again.data == first.data
    assert (again.persons, again.reduced, again.blanked) == (0, 0, 0)


def test_a_first_name_inside_a_place_name_is_not_a_leak() -> None:
    doc = synthetic_extract()
    doc["odpis"]["dane"]["dzial1"]["siedziba"] = {"miejscowosc": "ZENOBIUSZÓW"}
    assert b"ZENOBIUSZ\xc3\x93W" in redact_registry_extract(doc).data


@pytest.mark.parametrize(
    "where",
    [
        ("dzial1", "numerRachunku", PESEL),  # an unkeyed PESEL-shaped run
        ("dzial1", "kontakt", "12345678901"),
    ],
)
def test_fails_closed_on_a_pesel_shaped_run(where: tuple[str, str, str]) -> None:
    section, key, value = where
    doc = synthetic_extract()
    doc["odpis"]["dane"][section][key] = value
    with pytest.raises(RedactionError, match="11-digit"):
        redact_registry_extract(doc)


def test_an_allowlisted_field_naming_a_person_is_reduced() -> None:
    doc = synthetic_extract()
    doc["odpis"]["dane"]["dzial4"] = {
        "zabezpieczenieMajatkuOddalenieWnioskuOUpadlosc": [
            {
                "zabezpieczenieMajatkuDluznikaWPostepowaniuUpadlosciowym": [
                    {
                        "organWydajacy": "POSTANOWIENIE SĄDU REJONOWEGO Z DNIA 25.03.2022 R. O USTANOWIENIU "
                        "TYMCZASOWEGO NADZORCY SĄDOWEGO ALOJZEGO WYMYŚLONEGO",
                        "nrWpisuWprow": "3",
                    },
                    {
                        "organWydajacy": "POSTANOWIENIE SĄDU REJONOWEGO Z DNIA 25.03.2022 R. O USTANOWIENIU "
                        "TYMCZASOWEGO NADZORCY SĄDOWEGO PRZYKŁADOWA KANCELARIA SPÓŁKA Z OGRANICZONĄ "
                        "ODPOWIEDZIALNOŚCIĄ",
                        "nrWpisuWprow": "3",
                    },
                ]
            }
        ]
    }
    orders = json.loads(redact_registry_extract(doc).data)["odpis"]["dane"]["dzial4"][
        "zabezpieczenieMajatkuOddalenieWnioskuOUpadlosc"
    ][0]["zabezpieczenieMajatkuDluznikaWPostepowaniuUpadlosciowym"]
    assert orders[0]["organWydajacy"] == f"{PLACEHOLDER} 25.03.2022"  # a person may be named
    assert orders[1]["organWydajacy"].endswith("ODPOWIEDZIALNOŚCIĄ")  # a company is
