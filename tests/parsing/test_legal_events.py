"""`legal_events` normalisation (plan 0008 step F), on the redacted KRS and reduced MSiG fixtures."""

from __future__ import annotations

import copy
import hashlib
import json
import random
from datetime import date
from pathlib import Path
from typing import Any

import pytest

from distress_radar.parsing.contracts import LEGAL_EVENTS
from distress_radar.parsing.legal_events import (
    LegalEvent,
    Normalised,
    finalise,
    from_krs_extract,
    from_msig_notice,
    normalise_signature,
    to_frame,
)
from distress_radar.parsing.legal_taxonomy import ProcedureTaxonomy, load_procedure_taxonomy
from distress_radar.parsing.msig_notice_kinds import NoticeKinds, load_notice_kinds

LEGAL = Path(__file__).resolve().parents[1] / "fixtures" / "legal"


def _sha(name: str) -> str:
    return hashlib.sha256(name.encode()).hexdigest()  # one stand-in object per fixture


@pytest.fixture(scope="module")
def taxonomy() -> ProcedureTaxonomy:
    return load_procedure_taxonomy()


@pytest.fixture(scope="module")
def kinds() -> NoticeKinds:
    return load_notice_kinds()


def _extract(krs: str) -> dict[str, Any]:
    return json.loads((LEGAL / "krs" / f"odpis_pelny_{krs}.json").read_text("utf-8"))


def _notice(name: str) -> dict[str, Any]:
    return json.loads((LEGAL / "msig" / f"notice_{name}.json").read_text("utf-8"))


def _krs(
    taxonomy: ProcedureTaxonomy, krs: str, extract: dict[str, Any] | None = None
) -> Normalised:
    return from_krs_extract(
        extract or _extract(krs),
        krs=krs,
        taxonomy=taxonomy,
        source_document_hash=_sha(krs),
        ingestion_run_id="run-1",
    )


def _msig(
    taxonomy: ProcedureTaxonomy, kinds: NoticeKinds, name: str, record: dict[str, Any] | None = None
) -> Normalised:
    return from_msig_notice(
        record or _notice(name),
        taxonomy=taxonomy,
        kinds=kinds,
        source_document_hash=_sha(name),
        ingestion_run_id="run-1",
    )


def _by_type(events: list[LegalEvent], event_type: str) -> list[LegalEvent]:
    return [e for e in events if e.event_type == event_type]


# --- KRS -----------------------------------------------------------------------------------------


def test_known_from_is_the_entry_date_not_the_decision_date(taxonomy: ProcedureTaxonomy) -> None:
    [declared] = _by_type(_krs(taxonomy, "0000181328").events, "bankruptcy_declared")
    assert declared.event_date == date(2025, 5, 20)
    assert declared.known_from == date(2025, 8, 8)  # entry 44, 80 days later
    assert declared.case_signature == "KI1L/GU/43/2025"
    assert declared.source_element_path == (
        "dane.dzial6.postepowanieUpadlosciowe[0].informacjaOOgloszeniuUpadlosci[0]"
    )
    assert (declared.outcome_class, declared.stage, declared.statute) == (
        "bankruptcy",
        "opening",
        "pu_2003",
    )


def test_a_declaration_without_a_decision_date_stays_undated(taxonomy: ProcedureTaxonomy) -> None:
    [declared] = _by_type(_krs(taxonomy, "0000507997").events, "bankruptcy_declared")
    assert declared.event_date is None and declared.known_from == date(2025, 8, 13)


def test_removed_records_keep_the_date_they_were_removed(taxonomy: ProcedureTaxonomy) -> None:
    events = _krs(taxonomy, "0000440028").events
    [opened] = _by_type(events, "liquidation_opened")
    assert opened.removed_on == date(2025, 9, 10)
    [gone] = _by_type(events, "deregistered")
    assert (gone.event_date, gone.known_from) == (date(2025, 9, 10), date(2025, 9, 10))


def test_an_empty_ending_record_is_not_an_event(taxonomy: ProcedureTaxonomy) -> None:
    out = _krs(taxonomy, "0000181328")
    assert _by_type(out.events, "bankruptcy_proceeding_ended") == []
    assert out.rejects == []


def test_an_entry_without_a_date_is_rejected_not_guessed(taxonomy: ProcedureTaxonomy) -> None:
    extract = copy.deepcopy(_extract("0000181328"))
    extract["odpis"]["naglowekP"]["wpis"] = [
        e for e in extract["odpis"]["naglowekP"]["wpis"] if e["numerWpisu"] != 44
    ]
    out = _krs(taxonomy, "0000181328", extract)
    assert _by_type(out.events, "bankruptcy_declared") == []
    assert {r.reason_code for r in out.rejects} == {"krs_entry_date_missing"}


def test_unknown_sections_and_values_are_rejected(taxonomy: ProcedureTaxonomy) -> None:
    extract = copy.deepcopy(_extract("0000277937"))
    dzial6 = extract["odpis"]["dane"]["dzial6"]
    dzial6["postepowanieUkladowe"] = [{"nrWpisuWprow": "37", "data": "01.01.2020"}]
    opening = dzial6[
        "postepowanieRestrukturyzacyjneNaprawczePrzymusowaRestrukturyzacjaUporzadkowanaLikwidacja"
    ][0][
        "otwarciePostepowaniaRestrukturyzacyjnegoNaprawczegoPrzymusowejRestrukturyzacjiUporzadkowanejLikwidacji"
    ][0]
    opening["rodzajPostepowania"] = "PRZYMUSOWA RESTRUKTURYZACJA"
    rejects = _krs(taxonomy, "0000277937", extract).rejects
    assert sorted((r.reason_code, r.source_element_path.split("[")[0]) for r in rejects) == [
        (
            "legal_event_type_unmapped",
            "dane.dzial6.postepowanieRestrukturyzacyjneNaprawczePrzymusowaRestrukturyzacjaUporzadkowanaLikwidacja",
        ),
        ("legal_event_type_unmapped", "dane.dzial6.postepowanieUkladowe"),
    ]


# --- MSiG ----------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "event_type", "event_date", "known_from"),
    [
        (
            "0000070294_5420042",
            "bankruptcy_petition_asset_security",
            date(2017, 2, 9),
            date(2017, 2, 23),
        ),
        ("0000070294_5448048", "bankruptcy_declared", date(2017, 3, 8), date(2017, 3, 17)),
        ("0000070294_335644", "bankruptcy_proceeding_ended", date(2021, 7, 27), date(2021, 8, 5)),
        ("0000188883_6703250", "bankruptcy_declared", date(2014, 1, 21), date(2014, 2, 11)),
        (
            "0000277937_2155799",
            "simplified_restructuring_announced",
            date(2020, 10, 29),
            date(2020, 10, 29),
        ),
        (
            "0000277937_1274439",
            "restructuring_petition_asset_security",
            date(2021, 4, 1),
            date(2021, 5, 10),
        ),
        ("0000277937_300337", "remedial_proceedings_opened", date(2021, 7, 2), date(2021, 7, 15)),
        (
            "0000386777_4089653",
            "restructuring_petition_asset_security",
            date(2018, 8, 24),
            date(2018, 9, 7),
        ),
        (
            "0000386777_3376311",
            "bankruptcy_petition_asset_security",
            date(2019, 10, 10),
            date(2019, 10, 29),
        ),
        ("0000386777_2109633", "remedial_proceedings_opened", date(2020, 4, 17), date(2020, 5, 6)),
        ("0000397658_935723", "liquidation_opened", date(2021, 10, 1), date(2021, 11, 25)),
        ("0000440028_13039478", "liquidation_opened", date(2023, 7, 21), date(2023, 12, 21)),
        (
            "0000225506_8260486",
            "merger_division_transformation",
            date(2008, 11, 10),
            date(2008, 11, 27),
        ),
    ],
)
def test_each_fixture_notice_is_typed_and_dated(
    taxonomy: ProcedureTaxonomy,
    kinds: NoticeKinds,
    name: str,
    event_type: str,
    event_date: date,
    known_from: date,
) -> None:
    out = _msig(taxonomy, kinds, name)
    assert out.rejects == []
    [event] = out.events
    assert (event.event_type, event.event_date, event.known_from, event.source) == (
        event_type,
        event_date,
        known_from,
        "MSiG",
    )


def test_a_procedural_notice_is_neither_an_event_nor_a_reject(
    taxonomy: ProcedureTaxonomy, kinds: NoticeKinds
) -> None:
    record = _notice("0000070294_5448048")
    record["extracted"]["chapter_code"] = "III/3"  # a claims list
    out = _msig(taxonomy, kinds, "", record)
    assert (out.events, out.rejects) == ([], [])


def test_an_unrecognised_notice_is_quarantined(
    taxonomy: ProcedureTaxonomy, kinds: NoticeKinds
) -> None:
    record = _notice("0000277937_300337")
    record["extracted"]["terms"] = ["obwieszcz", "restrukturyzac"]
    [reject] = _msig(taxonomy, kinds, "", record).rejects
    assert reject.reason_code == "msig_notice_unclassified"


def test_a_simplified_restructuring_outside_its_window_is_unmapped(
    taxonomy: ProcedureTaxonomy, kinds: NoticeKinds
) -> None:
    record = _notice("0000277937_2155799")
    record["notice"]["dateOfPublication"] = "2022-03-01T00:00:00"  # the procedure ended 2021-11-30
    [reject] = _msig(taxonomy, kinds, "", record).rejects
    assert reject.reason_code == "legal_event_type_unmapped"


def test_notice_signatures_become_aliases(taxonomy: ProcedureTaxonomy, kinds: NoticeKinds) -> None:
    out = _msig(taxonomy, kinds, "0000277937_300337")
    assert out.aliases == [("0000277937", ("VIII/GR/7/21", "VIII/GRS/1/21"))]


# --- signatures, linking, grouping ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("IX GU 103/13.", "IX/GU/103/13"),
        ("VIII GUp 41/17", "VIII/GUP/41/17"),
        ("VI GU 751/19/W", "VI/GU/751/19"),
        ("KI1L/GU/43/2025", "KI1L/GU/43/2025"),
        ("RZ1Z/GU/5/2022/20", "RZ1Z/GU/5/2022"),  # a stray suffix in the registry
        (None, None),
    ],
)
def test_normalise_signature(raw: str | None, expected: str | None) -> None:
    assert normalise_signature(raw) == expected


def _seed_pair(
    taxonomy: ProcedureTaxonomy, kinds: NoticeKinds, krs: str, notices: list[str]
) -> list[LegalEvent]:
    out = _krs(taxonomy, krs)
    for name in notices:
        out.extend(_msig(taxonomy, kinds, name))
    return finalise(out)


def test_one_event_seen_in_both_sources_is_one_group_with_both_rows(
    taxonomy: ProcedureTaxonomy, kinds: NoticeKinds
) -> None:
    events = _seed_pair(
        taxonomy,
        kinds,
        "0000277937",
        ["0000277937_1274439", "0000277937_300337", "0000277937_2155799"],
    )
    security = _by_type(events, "restructuring_petition_asset_security")
    assert {e.source for e in security} == {"KRS", "MSiG"}
    assert len({e.dedup_group_id for e in security}) == 1
    # The earliest publication is MSiG's, 15 days before the registry entry.
    assert min(e.known_from for e in security) == date(2021, 5, 10)
    # The registry's generic opening and MSiG's sanacja opening are one opening.
    openings = [e for e in events if e.stage == "opening" and e.outcome_class == "restructuring"]
    assert len({e.dedup_group_id for e in openings if e.event_date == date(2021, 7, 2)}) == 1
    # The 2020 simplified restructuring is a different proceeding, unsigned: its own group.
    [upr] = _by_type(events, "simplified_restructuring_announced")
    assert upr.dedup_group_id not in {e.dedup_group_id for e in openings if e is not upr}


def test_linked_case_files_share_one_proceeding_id(
    taxonomy: ProcedureTaxonomy, kinds: NoticeKinds
) -> None:
    events = _seed_pair(taxonomy, kinds, "0000277937", ["0000277937_1274439", "0000277937_300337"])
    assert {e.proceeding_id for e in events if e.case_signature} == {"VIII/GR/7/21"}


def test_an_undated_registry_row_joins_the_dated_notice(
    taxonomy: ProcedureTaxonomy, kinds: NoticeKinds
) -> None:
    events = _seed_pair(taxonomy, kinds, "0000440028", ["0000440028_13039478"])
    opened = _by_type(events, "liquidation_opened")
    assert {(e.source, e.event_date) for e in opened} == {
        ("KRS", None),
        ("MSiG", date(2023, 7, 21)),
    }
    assert len({e.dedup_group_id for e in opened}) == 1
    # The closing has no counterpart: its own group, even undated.
    [closed] = _by_type(events, "liquidation_closed")
    assert closed.event_date is None and closed.dedup_group_id not in {
        e.dedup_group_id for e in opened
    }


def test_frame_passes_the_contract_and_ignores_input_order(
    taxonomy: ProcedureTaxonomy, kinds: NoticeKinds
) -> None:
    out = Normalised()
    for krs in ("0000181328", "0000277937", "0000440028", "0000507997"):
        out.extend(_krs(taxonomy, krs))
    for path in sorted((LEGAL / "msig").glob("notice_*.json")):
        out.extend(_msig(taxonomy, kinds, path.stem.removeprefix("notice_")))
    frame = LEGAL_EVENTS.validate(to_frame(finalise(out)))
    assert frame.height == len(out.events)

    shuffled = Normalised(events=list(out.events), aliases=list(out.aliases))
    random.Random(7).shuffle(shuffled.events)
    random.Random(7).shuffle(shuffled.aliases)
    assert to_frame(finalise(shuffled)).equals(frame)
