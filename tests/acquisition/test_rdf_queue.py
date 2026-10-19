"""The re-search queue for the owner's script (plan 0018 step C, decision 3)."""

from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path

import polars as pl

from distress_radar.acquisition.rdf_queue import (
    SEARCHES_SCHEMA,
    STATEMENTS_SCHEMA,
    build_queue,
    write_queue,
)
from distress_radar.features.statutory import load_filing_deadlines

ON = date(2026, 10, 19)


def _searches(*rows: tuple[str, datetime, bool]) -> pl.DataFrame:
    return pl.DataFrame(
        [{"krs": k, "searched_at": t, "complete": c} for k, t, c in rows], schema=SEARCHES_SCHEMA
    )


def _statements(*rows: tuple[str, date]) -> pl.DataFrame:
    return pl.DataFrame([{"krs": k, "period_end": p} for k, p in rows], schema=STATEMENTS_SCHEMA)


def _at(day: str) -> datetime:
    return datetime.fromisoformat(f"{day}T10:00:00").replace(tzinfo=UTC)


def test_each_entity_lands_in_the_first_tier_that_fits_it():
    searches = _searches(
        ("0000000002", _at("2026-09-01"), True),
        ("0000000002", _at("2026-10-01"), False),  # the latest search is incomplete
        ("0000000003", _at("2026-07-01"), True),  # before FY2025's deadline, 2026-07-15
        ("0000000004", _at("2026-08-01"), True),  # after it: nothing new to expect
        ("0000000005", _at("2026-03-01"), True),  # FY2025 listed: FY2026 not due until 2027
    )
    statements = _statements(
        ("0000000003", date(2024, 12, 31)),
        ("0000000004", date(2024, 12, 31)),
        ("0000000005", date(2025, 12, 31)),
        ("0000000006", date(2024, 12, 31)),  # the seed: captured by hand, never searched
    )
    entities = [f"00000000{n:02d}" for n in (1, 2, 3, 4, 5, 6)]

    queue = build_queue(
        entities,
        searches,
        statements,
        captured=["0000000006"],
        deadlines=load_filing_deadlines(),
        on=ON,
    )

    assert [(e.krs, e.tier) for e in queue] == [
        ("0000000001", "not_searched"),
        ("0000000002", "search_incomplete"),
        ("0000000006", "filing_due"),  # never searched comes first in its tier
        ("0000000003", "filing_due"),
        ("0000000005", "oldest_search"),
        ("0000000004", "oldest_search"),
    ]
    assert queue[3].next_deadline == date(2026, 7, 15)


def test_the_file_holds_krs_numbers_only_and_is_the_same_on_a_rerun(tmp_path: Path):
    queue = build_queue(
        ["0000000002", "0000000001"],
        _searches(),
        _statements(),
        captured=[],
        deadlines=load_filing_deadlines(),
        on=ON,
    )
    first = write_queue(queue, tmp_path, ON).read_bytes()
    assert first == b"0000000001\n0000000002\n"
    assert write_queue(queue, tmp_path, ON).read_bytes() == first
    assert [p.name for p in tmp_path.iterdir()] == ["queue_2026-10-19.txt"]


def test_a_draws_never_searched_come_first_distress_group_then_random_in_draw_order():
    searches = _searches(("0000000009", _at("2026-01-01"), True))
    entities = [f"00000000{n:02d}" for n in range(1, 10)]
    draw_ranks = {
        "0000000007": (1, 0),  # A, first in its draw order
        "0000000003": (1, 1),
        "0000000008": (0, 1),  # B
        "0000000005": (0, 0),
        "0000000009": (0, 2),  # drawn, but already searched: its tier decides
    }

    queue = build_queue(
        entities,
        searches,
        _statements(),
        captured=[],
        deadlines=load_filing_deadlines(),
        on=ON,
        draw_ranks=draw_ranks,
    )

    assert [e.krs for e in queue] == [
        "0000000005",
        "0000000008",
        "0000000007",
        "0000000003",
        "0000000001",
        "0000000002",
        "0000000004",
        "0000000006",
        "0000000009",
    ]
