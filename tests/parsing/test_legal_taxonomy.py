"""The procedure taxonomy (plan 0008 step B; AGENT_SPEC §4.6, §9.2 "Statutory config")."""

from __future__ import annotations

import copy
import json
import re
from datetime import date
from pathlib import Path
from typing import Any

import pytest
import yaml
from pydantic import ValidationError

from distress_radar.parsing.canonical_schema import CONFIG_DIR
from distress_radar.parsing.legal_taxonomy import (
    EVENT_OUTCOME_CLASSES,
    ProcedureTaxonomy,
    load_procedure_taxonomy,
)

KRS_FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "legal" / "krs"
BANKRUPTCY = "dane.dzial6.postepowanieUpadlosciowe.informacjaOOgloszeniuUpadlosci"
BANKRUPTCY_END = "dane.dzial6.postepowanieUpadlosciowe.opisZakonczeniaProcesuUpadlosci"
RESTRUCTURING = (
    "dane.dzial6.postepowanieRestrukturyzacyjneNaprawczePrzymusowaRestrukturyzacjaUporzadkowanaLikwidacja"
    ".otwarciePostepowaniaRestrukturyzacyjnegoNaprawczegoPrzymusowejRestrukturyzacjiUporzadkowanejLikwidacji"
)
RESTRUCTURING_SECURITY = (
    "dane.dzial4.zabezpieczenieMajatkuOddalenieWnioskuOUpadlosc"
    ".zabezpieczenieMajatkuDluznikaWPostepowaniuRestrukturyzacyjnym"
)
DATE = re.compile(r"\d{2}\.\d{2}\.\d{4}")


@pytest.fixture(scope="module")
def taxonomy() -> ProcedureTaxonomy:
    return load_procedure_taxonomy()


def _raw() -> dict[str, Any]:
    return yaml.safe_load((CONFIG_DIR / "statutory" / "procedure_taxonomy.yaml").read_text("utf-8"))


def test_repository_taxonomy_loads(taxonomy: ProcedureTaxonomy) -> None:
    classes = {e.outcome_class for e in taxonomy.event_types}
    assert classes - {None} == set(EVENT_OUTCOME_CLASSES)
    assert taxonomy.regime_window.start == date(2020, 1, 1)
    assert taxonomy.regime_window.end == date(2021, 12, 31)


# --- lookups across effective-date boundaries ---------------------------------------------------


def _resolved(
    taxonomy: ProcedureTaxonomy, locator: str, on: date, **fields: str
) -> tuple[str, str] | None:
    hit = taxonomy.resolve("KRS", locator, fields, on)
    return None if hit is None else (hit.event_type.event_type, hit.statute.id)


def test_bankruptcy_declaration_changes_statute_on_2003_10_01(taxonomy: ProcedureTaxonomy) -> None:
    assert _resolved(taxonomy, BANKRUPTCY, date(2003, 9, 30)) == ("bankruptcy_declared", "pu_1934")
    assert _resolved(taxonomy, BANKRUPTCY, date(2003, 10, 1)) == ("bankruptcy_declared", "pu_2003")


def test_remedial_proceedings_span_the_2016_reform(taxonomy: ProcedureTaxonomy) -> None:
    naprawcze = {"rodzajPostepowania": "POSTĘPOWANIE NAPRAWCZE"}
    assert _resolved(taxonomy, RESTRUCTURING, date(2015, 12, 31), **naprawcze) == (
        "remedial_proceedings_opened",
        "pu_2003_naprawcze",
    )
    assert _resolved(taxonomy, RESTRUCTURING, date(2016, 1, 1), **naprawcze) == (
        "remedial_proceedings_opened",
        "pr_2015",
    )


def test_restructuring_mappings_start_with_prawo_restrukturyzacyjne(
    taxonomy: ProcedureTaxonomy,
) -> None:
    generic = {"rodzajPostepowania": "POSTĘPOWANIE RESTRUKTURYZACYJNE"}
    assert _resolved(taxonomy, RESTRUCTURING, date(2015, 12, 31), **generic) is None
    assert _resolved(taxonomy, RESTRUCTURING, date(2016, 1, 1), **generic) == (
        "restructuring_proceedings_opened",
        "pr_2015",
    )
    assert _resolved(taxonomy, RESTRUCTURING_SECURITY, date(2015, 12, 31)) is None
    assert _resolved(taxonomy, RESTRUCTURING_SECURITY, date(2016, 1, 1)) is not None


def test_simplified_restructuring_window_edges() -> None:
    raw = _raw()
    raw["mappings"].append(
        {
            "source": "MSiG",
            "locator": "notice",
            "when": {"field": "kind", "equals": "UPR"},
            "event_type": "simplified_restructuring_announced",
            "statute": "upr_covid",
        }
    )
    taxonomy = ProcedureTaxonomy.model_validate(raw)
    fields = {"kind": "UPR"}
    assert taxonomy.resolve("MSiG", "notice", fields, date(2020, 6, 23)) is None
    assert taxonomy.resolve("MSiG", "notice", fields, date(2020, 6, 24)) is not None
    assert taxonomy.resolve("MSiG", "notice", fields, date(2021, 11, 30)) is not None
    assert taxonomy.resolve("MSiG", "notice", fields, date(2021, 12, 1)) is None


def test_source_era_changes_at_krz_launch(taxonomy: ProcedureTaxonomy) -> None:
    assert taxonomy.source_era(date(2021, 11, 30)) == "pre_krz"
    assert taxonomy.source_era(date(2021, 12, 1)) == "krz"


# --- unknown source events are not mapped by guess ----------------------------------------------


def test_unknown_procedure_kind_is_unmapped(taxonomy: ProcedureTaxonomy) -> None:
    # Bank resolution: outside sp. z o.o., so quarantined and looked at.
    bank = {"rodzajPostepowania": "PRZYMUSOWA RESTRUKTURYZACJA"}
    assert _resolved(taxonomy, RESTRUCTURING, date(2020, 1, 1), **bank) is None
    assert _resolved(taxonomy, "dane.dzial6.somethingNew", date(2020, 1, 1)) is None
    assert taxonomy.resolve("KRZ", BANKRUPTCY, {}, date(2023, 1, 1)) is None


def test_empty_bankruptcy_ending_is_not_an_event(taxonomy: ProcedureTaxonomy) -> None:
    on = date(2025, 8, 13)
    assert _resolved(taxonomy, BANKRUPTCY_END, on, nrWpisuWprow="44") is None
    assert _resolved(taxonomy, BANKRUPTCY_END, on, dataZakonczeniaPostepowania="27.07.2021") == (
        "bankruptcy_proceeding_ended",
        "pu_2003",
    )


def test_deregistration_read_from_entry_description(taxonomy: ProcedureTaxonomy) -> None:
    on = date(2022, 10, 31)
    assert _resolved(taxonomy, "naglowekP.wpis", on, opis="WYKREŚLENIE PODMIOTU Z REJESTRU") == (
        "deregistered",
        "krs_1997",
    )
    assert _resolved(taxonomy, "naglowekP.wpis", on, opis="ZMIANA DANYCH W REJESTRZE") is None


def test_silent_exit_rules(taxonomy: ProcedureTaxonomy) -> None:
    deregistered = taxonomy.event_type("deregistered")
    assert deregistered.outcome_class == "silent_exit"
    assert not deregistered.precludes_silent_exit
    precluding = {e.event_type for e in taxonomy.event_types if e.precludes_silent_exit}
    assert {"bankruptcy_declared", "liquidation_opened", "merger_division_transformation"} <= precluding


# --- the real extracts ---------------------------------------------------------------------------


def _records(node: Any, parts: list[str]) -> list[dict[str, Any]]:
    if isinstance(node, list):
        return [r for item in node for r in _records(item, parts)]  # pyright: ignore[reportUnknownVariableType]
    if not isinstance(node, dict):
        return []
    if not parts:
        return [node]  # pyright: ignore[reportUnknownVariableType]
    return _records(node.get(parts[0]), parts[1:])  # pyright: ignore[reportUnknownMemberType, reportUnknownArgumentType]


def _events(taxonomy: ProcedureTaxonomy, krs: str) -> set[tuple[str, date]]:
    odpis = json.loads((KRS_FIXTURES / f"odpis_pelny_{krs}.json").read_text("utf-8"))["odpis"]
    entry_dates = {str(e["numerWpisu"]): e["dataWpisu"] for e in odpis["naglowekP"]["wpis"]}
    found: set[tuple[str, date]] = set()
    for mapping in taxonomy.mappings:
        if mapping.source != "KRS":
            continue
        for record in _records(odpis, mapping.locator.split(".")):
            fields = {k: v for k, v in record.items() if isinstance(v, str)}
            text = fields.get(mapping.date_field) if mapping.date_field else None
            match = DATE.search(text) if text else None
            # A header entry carries its own date; any other record is dated by its entry.
            entry = fields.get("dataWpisu") or entry_dates.get(fields.get("nrWpisuWprow", ""))
            raw = match.group() if match else entry
            if raw is None:
                continue
            day, month, year = (int(p) for p in raw.split("."))
            on = date(year, month, day)
            hit = taxonomy.resolve("KRS", mapping.locator, fields, on)
            if hit is not None and hit.mapping == mapping:
                found.add((hit.event_type.event_type, on))
    return found


def test_seed_extracts_resolve_to_the_expected_events(taxonomy: ProcedureTaxonomy) -> None:
    assert _events(taxonomy, "0000181328") == {
        ("bankruptcy_petition_asset_security", date(2025, 2, 4)),
        ("bankruptcy_declared", date(2025, 5, 20)),
        ("liquidation_opened", date(2010, 2, 1)),
        ("liquidation_closed", date(2010, 5, 18)),
        ("dissolution_recorded", date(2010, 3, 15)),
    }
    assert _events(taxonomy, "0000277937") == {
        ("restructuring_petition_asset_security", date(2021, 4, 1)),
        ("restructuring_proceedings_opened", date(2021, 7, 2)),
    }
    # No decision date in the record: dated here by its entry (the undated-event rule, step F).
    events = _events(taxonomy, "0000507997")
    assert ("bankruptcy_declared", date(2025, 8, 13)) in events
    assert {e for e, _ in events} == {"bankruptcy_declared", "arrears_enforcement_started"}
    # The production redactor keeps entry descriptions, the only deregistration signal.
    events = _events(taxonomy, "0000440028")
    assert {e for e, _ in events} == {
        "liquidation_opened",
        "liquidation_closed",
        "dissolution_recorded",
        "deregistered",
    }
    assert ("deregistered", date(2025, 9, 10)) in events


# --- rejected configs ----------------------------------------------------------------------------


def _invalid(mutate: Any, match: str) -> None:
    raw = copy.deepcopy(_raw())
    mutate(raw)
    with pytest.raises(ValidationError, match=match):
        ProcedureTaxonomy.model_validate(raw)


def test_rejects_an_event_type_with_two_classes() -> None:
    def twice(raw: dict[str, Any]) -> None:
        raw["event_types"].append(
            {
                "event_type": "liquidation_opened",
                "stage": "opening",
                "outcome_class": "bankruptcy",
                "precludes_silent_exit": True,
            }
        )

    _invalid(twice, "duplicate event types")


def test_rejects_a_class_outside_the_taxonomy() -> None:
    for bad in ("alive", "default"):

        def outside(raw: dict[str, Any], bad: str = bad) -> None:
            raw["event_types"][0]["outcome_class"] = bad

        _invalid(outside, "outcome_class")


def test_rejects_overlapping_effective_ranges() -> None:
    def overlap(raw: dict[str, Any]) -> None:
        declared = next(m for m in raw["mappings"] if m["statute"] == "pu_2003")
        raw["mappings"].append({**declared, "effective_from": date(2010, 1, 1)})

    _invalid(overlap, "overlapping")


def test_rejects_a_mapping_outside_its_statute() -> None:
    def outside(raw: dict[str, Any]) -> None:
        mapping = next(m for m in raw["mappings"] if m["statute"] == "pr_2015")
        mapping["effective_from"] = date(2015, 6, 1)

    _invalid(outside, "not inside statute")


def test_rejects_unknown_references_and_mixed_when() -> None:
    _invalid(lambda raw: raw["mappings"][0].update(event_type="nope"), "unknown event type")  # pyright: ignore[reportUnknownLambdaType, reportUnknownMemberType]
    _invalid(lambda raw: raw["mappings"][0].update(statute="nope"), "unknown statute")  # pyright: ignore[reportUnknownLambdaType, reportUnknownMemberType]

    def mixed(raw: dict[str, Any]) -> None:
        bare = next(m for m in raw["mappings"] if m["locator"] == "naglowekP.wpis")
        raw["mappings"].append({k: v for k, v in bare.items() if k != "when"})

    _invalid(mixed, "both with and without")


def test_rejects_inconsistent_event_types() -> None:
    def class_on_closing(raw: dict[str, Any]) -> None:
        ended = next(e for e in raw["event_types"] if e["event_type"] == "liquidation_closed")
        ended.update(outcome_class="liquidation", precludes_silent_exit=True)

    _invalid(class_on_closing, "cannot start a class")

    def not_precluding(raw: dict[str, Any]) -> None:
        opened = next(e for e in raw["event_types"] if e["event_type"] == "liquidation_opened")
        opened["precludes_silent_exit"] = False

    _invalid(not_precluding, "must preclude silent_exit")

    def unreachable(raw: dict[str, Any]) -> None:
        raw["event_types"] = [e for e in raw["event_types"] if e.get("outcome_class") != "liquidation"]
        raw["mappings"] = [m for m in raw["mappings"] if m["event_type"] != "liquidation_opened"]

    _invalid(unreachable, "no event type starts")
