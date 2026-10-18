"""How a score's drivers moved since the previous score (plan 0021 decision 3).

Computed when a score is read, from two `top_drivers` lists: the score's and the previous score's
of the same entity, horizon and model version. A driver's contribution is its share of the score's
log-odds against the training rows' average (`serving._rank_logistic`), so the change in it is
what moved the score. Two versions' contributions are not on one scale, so a score whose version
has no earlier score has no movement, never one measured against another version's.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Any

Driver = Mapping[str, Any]


def parse_drivers(value: str | Sequence[Driver] | None) -> list[Driver] | None:
    """`top_drivers` as stored (JSON text) or as read (a list), as a list."""
    if value is None:
        return None
    return json.loads(value) if isinstance(value, str) else list(value)


def driver_movement(
    current: str | Sequence[Driver] | None, previous: str | Sequence[Driver] | None
) -> list[dict[str, Any]] | None:
    """Each driver of either score: its contribution and value now and before, and the change
    in contribution, largest change first (ties by name). None when either score has no drivers.
    A driver in only one of the two lists has no figure on the other side, not a zero."""
    now, before = parse_drivers(current), parse_drivers(previous)
    if not now or not before:
        return None
    by_now = {str(d["feature"]): d for d in now}
    by_before = {str(d["feature"]): d for d in before}
    moved: list[dict[str, Any]] = []
    for feature in sorted(set(by_now) | set(by_before)):
        a, b = by_now.get(feature), by_before.get(feature)
        contribution = None if a is None else float(a["contribution"])
        earlier = None if b is None else float(b["contribution"])
        moved.append(
            {
                "feature": feature,
                "contribution": contribution,
                "previous_contribution": earlier,
                "change": None
                if contribution is None or earlier is None
                else contribution - earlier,
                "value": None if a is None else a.get("value"),
                "previous_value": None if b is None else b.get("value"),
            }
        )
    return sorted(
        moved,
        key=lambda m: (-(abs(m["change"]) if m["change"] is not None else -1.0), m["feature"]),
    )
