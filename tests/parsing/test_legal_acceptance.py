"""Seed acceptance for `legal_events` (plan 0008, definition of done)."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import polars as pl
import pytest

from distress_radar.parsing.legal_acceptance import expected_class, load_seed_hints, seed_acceptance
from distress_radar.parsing.legal_events import LEGAL_EVENTS_SCHEMA

SEED = Path(__file__).resolve().parents[2] / "config" / "segments" / "construction_sme_v1_seed.yaml"


def _event(
    krs: str, event_type: str, outcome_class: str | None, on: date | None, known: date
) -> dict[str, object]:
    row: dict[str, object] = dict.fromkeys(LEGAL_EVENTS_SCHEMA)
    row.update(
        krs=krs,
        event_type=event_type,
        outcome_class=outcome_class,
        event_date=on,
        known_from=known,
        source="KRS",
    )
    return row


def _frame(*rows: dict[str, object]) -> pl.DataFrame:
    return pl.DataFrame(list(rows), schema=LEGAL_EVENTS_SCHEMA, orient="row")


@pytest.mark.parametrize(
    ("hint", "expected"),
    [
        ("w upadłości (declared 2025-08-05)", "bankruptcy"),
        ("w upadłości likwidacyjnej", "bankruptcy"),
        ("w restrukturyzacji", "restructuring"),
        ("w likwidacji", "liquidation"),
        (None, None),
    ],
)
def test_expected_class(hint: str | None, expected: str | None) -> None:
    assert expected_class(hint) == expected


def test_the_repository_seed_has_nine_hints() -> None:
    hints = load_seed_hints(SEED)
    assert len(hints) == 17 and sum(h is not None for h in hints.values()) == 9


def test_verdicts() -> None:
    events = _frame(
        # A: hinted bankrupt, an earlier liquidation too: matched on the bankruptcy.
        _event("A", "liquidation_opened", "liquidation", date(2010, 2, 1), date(2010, 3, 15)),
        _event("A", "bankruptcy_declared", "bankruptcy", None, date(2025, 8, 13)),
        # B: hinted in restructuring, only a liquidation found: missing.
        _event("B", "liquidation_opened", "liquidation", date(2021, 10, 1), date(2021, 11, 25)),
        # C: no hint, a deregistration: unexpected. D: no hint, only signals: clear.
        _event("C", "deregistered", "silent_exit", date(2022, 10, 31), date(2022, 10, 31)),
        _event("D", "curator_appointed", None, date(2025, 2, 21), date(2025, 3, 1)),
    )
    hints = {"A": "w upadłości", "B": "w restrukturyzacji", "C": None, "D": None}
    verdicts = {v.krs: v for v in seed_acceptance(events, hints)}
    assert verdicts["A"].status == "matched"
    assert (
        verdicts["A"].first_event == "bankruptcy_declared 2025-08-13 (KRS)"
    )  # undated: known_from
    assert verdicts["A"].found_classes == ("bankruptcy", "liquidation")
    assert (verdicts["B"].status, verdicts["B"].passed) == ("missing", False)
    assert (verdicts["C"].status, verdicts["C"].passed) == ("unexpected", False)
    assert (verdicts["D"].status, verdicts["D"].passed) == ("clear", True)
