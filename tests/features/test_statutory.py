"""The statutory configs features read (plan 0010 step B; AGENT_SPEC §4.5, §9.2)."""

from __future__ import annotations

import copy
from datetime import date
from fractions import Fraction
from typing import Any

import pytest
import yaml
from pydantic import ValidationError

from distress_radar.features.statutory import (
    FilingDeadlines,
    KshTripwires,
    add_months,
    load_filing_deadlines,
    load_ksh_tripwires,
)
from distress_radar.parsing.canonical_schema import CONFIG_DIR


@pytest.fixture(scope="module")
def tripwires() -> KshTripwires:
    return load_ksh_tripwires()


@pytest.fixture(scope="module")
def deadlines() -> FilingDeadlines:
    return load_filing_deadlines()


def _raw(name: str) -> dict[str, Any]:
    return yaml.safe_load((CONFIG_DIR / "statutory" / name).read_text("utf-8"))


# --- KSH tripwires -------------------------------------------------------------------------------


def test_art_233_is_half_the_share_capital_for_sp_z_oo(tripwires: KshTripwires) -> None:
    rule = tripwires.resolve("art_233", "sp_z_oo", date(2023, 12, 31))
    assert rule is not None
    assert rule.coefficients() == {
        "supplementary_capital": Fraction(1),
        "reserve_capital": Fraction(1),
        "share_capital": Fraction(1, 2),
    }
    assert rule.losses == ("retained_result", "net_result_balance_sheet")


def test_art_397_is_a_third_and_only_for_sa(tripwires: KshTripwires) -> None:
    rule = tripwires.resolve("art_397", "sa", date(2023, 12, 31))
    assert rule is not None and rule.coefficients()["share_capital"] == Fraction(1, 3)
    assert tripwires.resolve("art_397", "sp_z_oo", date(2023, 12, 31)) is None
    assert tripwires.resolve("art_233", "sa", date(2023, 12, 31)) is None


def test_a_rule_resolves_only_from_the_code_s_entry_into_force(tripwires: KshTripwires) -> None:
    assert tripwires.resolve("art_233", "sp_z_oo", date(2000, 12, 31)) is None
    assert tripwires.resolve("art_233", "sp_z_oo", date(2001, 1, 1)) is not None


def _invalid_tripwires(mutate: Any, match: str) -> None:
    raw = copy.deepcopy(_raw("ksh_tripwires.yaml"))
    mutate(raw)
    with pytest.raises(ValidationError, match=match):
        KshTripwires.model_validate(raw)


def test_rejects_bad_tripwire_configs() -> None:
    def overlapping(raw: dict[str, Any]) -> None:
        raw["rules"].append({**raw["rules"][0], "rule": "art_233_bis"})

    _invalid_tripwires(overlapping, "both apply to sp_z_oo")
    _invalid_tripwires(lambda raw: raw["rules"][0].update(statute="nope"), "unknown statute")  # pyright: ignore[reportUnknownLambdaType, reportUnknownMemberType]
    _invalid_tripwires(
        lambda raw: raw["rules"][0].update(effective_from=date(1999, 1, 1)),  # pyright: ignore[reportUnknownLambdaType, reportUnknownMemberType]
        "not inside statute",
    )
    _invalid_tripwires(
        lambda raw: raw["rules"][0]["threshold"].update(share_capital=0.5),  # pyright: ignore[reportUnknownLambdaType, reportUnknownMemberType]
        "valid string",
    )
    _invalid_tripwires(
        lambda raw: raw["rules"][0]["threshold"].update(share_capital="0/2"),  # pyright: ignore[reportUnknownLambdaType, reportUnknownMemberType]
        "must be positive",
    )
    _invalid_tripwires(
        lambda raw: raw["rules"][0]["threshold"].update(share_capital="-1/2"),  # pyright: ignore[reportUnknownLambdaType, reportUnknownMemberType]
        "should match pattern",
    )


# --- filing deadlines ----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("period_end", "deadline"),
    [
        (date(2018, 12, 31), date(2019, 7, 15)),  # 6 months to approve, 15 days to file
        (date(2019, 6, 30), date(2020, 1, 15)),  # a non-calendar year: its own period end
        (date(2019, 9, 29), date(2020, 4, 13)),  # the day before the first COVID window
        (date(2019, 9, 30), date(2020, 7, 15)),  # its first day: approval + 3 months
        (date(2019, 12, 31), date(2020, 10, 15)),
        (date(2020, 4, 30), date(2021, 2, 15)),  # its last day: 31 Jan + 15 days
        (date(2020, 5, 31), date(2020, 12, 15)),  # after it: back to 6 months
        (date(2020, 12, 31), date(2021, 10, 15)),
        (date(2021, 12, 31), date(2022, 10, 15)),
        (date(2022, 4, 30), date(2023, 2, 15)),
        (date(2022, 12, 31), date(2023, 7, 15)),  # no extension after 2022
    ],
)
def test_the_deadline_follows_the_period_end(
    deadlines: FilingDeadlines, period_end: date, deadline: date
) -> None:
    assert deadlines.deadline(period_end) == deadline


def test_month_arithmetic_keeps_month_ends() -> None:
    assert add_months(date(2019, 12, 31), 6) == date(2020, 6, 30)
    assert add_months(date(2019, 8, 31), 6) == date(2020, 2, 29)
    assert add_months(date(2020, 2, 29), 12) == date(2021, 2, 28)
    assert add_months(date(2019, 1, 15), 13) == date(2020, 2, 15)


def test_rejects_overlapping_extensions() -> None:
    raw = copy.deepcopy(_raw("filing_deadlines.yaml"))
    raw["extensions"][1]["period_end_from"] = date(2020, 4, 30)
    with pytest.raises(ValidationError, match="both cover"):
        FilingDeadlines.model_validate(raw)
    raw = copy.deepcopy(_raw("filing_deadlines.yaml"))
    raw["extensions"][0]["statute"] = "nope"
    with pytest.raises(ValidationError, match="unknown statute"):
        FilingDeadlines.model_validate(raw)
