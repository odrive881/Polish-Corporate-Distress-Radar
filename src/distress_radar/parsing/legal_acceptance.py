"""Seed acceptance for `legal_events` (plan 0008, definition of done).

The seed lists a publicly reported status for 9 of its 17 entities (`status_hint` in
`config/segments/<segment>_seed.yaml`: "w upadłości", "w restrukturyzacji", "w likwidacji").
Phase 4 is done when the pipeline finds each of them independently, from the registers, with
a date and a source document, and finds nothing of the kind for the others.

The hints are hints, not ground truth. A mismatch is a finding to investigate and record,
which is why the result lists every entity rather than only a pass/fail.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Literal

import polars as pl
import yaml

# A hint names at most one class; the first stem found wins ("w upadłości likwidacyjnej" is a
# bankruptcy, not a liquidation).
_HINT_STEMS: tuple[tuple[str, str], ...] = (
    ("upadłoś", "bankruptcy"),
    ("restrukturyz", "restructuring"),
    ("likwidac", "liquidation"),
)
# Events that make an entity "distressed" for this check: an outcome class, or a deregistration.
_QUALIFYING = ("bankruptcy", "restructuring", "liquidation", "silent_exit")

Status = Literal["matched", "missing", "unexpected", "clear"]


@dataclass(frozen=True)
class Acceptance:
    krs: str
    hint: str | None
    expected_class: str | None
    status: Status
    found_classes: tuple[str, ...]
    first_event: str | None  # "<event_type> <date> (<source>)" of the matching or first event

    @property
    def passed(self) -> bool:
        return self.status in ("matched", "clear")


def expected_class(hint: str | None) -> str | None:
    low = (hint or "").lower()
    return next((cls for stem, cls in _HINT_STEMS if stem in low), None)


def load_seed_hints(path: Path) -> dict[str, str | None]:
    """KRS → `status_hint` (None where the seed gives none)."""
    seed = yaml.safe_load(path.read_text(encoding="utf-8"))
    return {str(e["krs"]): e.get("status_hint") for e in seed["entities"]}


def seed_acceptance(events: pl.DataFrame, hints: Mapping[str, str | None]) -> list[Acceptance]:
    """One verdict per seed entity, from `legal_events` rows."""
    qualifying = events.filter(pl.col("outcome_class").is_in(_QUALIFYING)).with_columns(
        pl.coalesce("event_date", "known_from").alias("_on")
    )
    out: list[Acceptance] = []
    for krs, hint in sorted(hints.items()):
        rows = qualifying.filter(pl.col("krs") == krs).sort("_on", "event_type", "source")
        classes = tuple(sorted(set(rows["outcome_class"].to_list())))
        expected = expected_class(hint)
        if expected is not None:
            matching = rows.filter(pl.col("outcome_class") == expected)
            status: Status = "matched" if matching.height else "missing"
            shown = matching if matching.height else rows
        else:
            status = "unexpected" if rows.height else "clear"
            shown = rows
        first = None
        if shown.height:
            row = shown.row(0, named=True)
            on: date = row["_on"]
            first = f"{row['event_type']} {on.isoformat()} ({row['source']})"
        out.append(Acceptance(krs, hint, expected, status, classes, first))
    return out
