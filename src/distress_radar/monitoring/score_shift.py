"""L — whether the served model's scores have shifted since the previous month-end (plan 0021
decision 4).

The latest scored month-end's probabilities against the previous month-end's, from the same
model version: their rows, mean and quartiles, the share above each alert threshold, the
population stability index (PSI) over ten bins cut at the previous month's deciles, and the
two-sample Kolmogorov–Smirnov statistic (the largest gap between the two empirical
distribution functions; no p-value, since the scores of one company in consecutive months are not
independent draws). A new version's first month-end has nothing of its own
to compare with, so it is "not evaluable", as is a month-end without `MIN_ROWS` scores on either
side.

At about 240 scores a month, a shift is read, never acted on: what to do about one (retrain,
recalibrate) stays a decision (plan 0018, out of scope). The report is local only
(`WAREHOUSE_DIR/reports/score_shift/`): it describes the scores of named companies.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from datetime import date
from pathlib import Path
from typing import Any

import numpy as np
import numpy.typing as npt

SHIFT_DIR = Path("reports") / "score_shift"
MIN_ROWS = 30
BINS = 10
_FLOOR = 1e-4  # a bin's share, floored, so an empty bin does not make the PSI infinite
Array = npt.NDArray[np.float64]


@dataclass(frozen=True)
class ShiftResult:
    as_of_date: date
    previous_as_of_date: date | None
    horizon_months: int
    model: str
    evaluable: bool
    note: str
    stats: dict[str, Any] = field(default_factory=dict[str, Any])

    def summary(self) -> dict[str, Any]:
        return {
            "as_of_date": self.as_of_date.isoformat(),
            "previous_as_of_date": (
                None if self.previous_as_of_date is None else self.previous_as_of_date.isoformat()
            ),
            "horizon_months": self.horizon_months,
            "model": self.model,
            "evaluable": self.evaluable,
            "note": self.note,
            **self.stats,
        }


def _describe(p: Array, thresholds: Sequence[float]) -> dict[str, Any]:
    q1, median, q3 = (float(v) for v in np.quantile(p, [0.25, 0.5, 0.75]))
    return {
        "rows": int(p.size),
        "mean": float(p.mean()),
        "q1": q1,
        "median": median,
        "q3": q3,
        "share_above": {f"{t:g}": float((p > t).mean()) for t in sorted(thresholds)},
    }


def psi(current: Array, previous: Array, bins: int = BINS) -> float:
    """Σ (c − p) · ln(c / p) over bins cut at the previous scores' quantiles."""
    edges = np.unique(np.quantile(previous, np.linspace(0, 1, bins + 1)[1:-1]))
    p_share = np.bincount(np.searchsorted(edges, previous, side="right"), minlength=edges.size + 1)
    c_share = np.bincount(np.searchsorted(edges, current, side="right"), minlength=edges.size + 1)
    p_frac = np.maximum(p_share / previous.size, _FLOOR)
    c_frac = np.maximum(c_share / current.size, _FLOOR)
    return float(np.sum((c_frac - p_frac) * np.log(c_frac / p_frac)))


def ks_statistic(current: Array, previous: Array) -> float:
    """The largest gap between the two samples' empirical distribution functions."""
    points = np.union1d(current, previous)
    a = np.searchsorted(np.sort(current), points, side="right") / current.size
    b = np.searchsorted(np.sort(previous), points, side="right") / previous.size
    return float(np.max(np.abs(a - b)))


def score_shift(
    current: Array,
    previous: Array | None,
    *,
    as_of_date: date,
    previous_as_of_date: date | None,
    horizon_months: int,
    model: str,
    thresholds: Sequence[float],
) -> ShiftResult:
    """One horizon's shift: `previous` None when the version scored no earlier month-end."""
    base = ShiftResult(
        as_of_date=as_of_date,
        previous_as_of_date=previous_as_of_date,
        horizon_months=horizon_months,
        model=model,
        evaluable=False,
        note="",
    )
    if previous is None:
        return replace(base, note="no earlier month-end of this version")
    if current.size < MIN_ROWS or previous.size < MIN_ROWS:
        return replace(base, note=f"fewer than {MIN_ROWS} scores on one side")
    return replace(
        base,
        evaluable=True,
        note="read, never a gate",
        stats={
            "current": _describe(current, thresholds),
            "previous": _describe(previous, thresholds),
            "psi": psi(current, previous),
            "ks_statistic": ks_statistic(current, previous),
        },
    )


def write_shift(result: ShiftResult, warehouse_dir: Path) -> Path:
    """`<as_of>_<h>m.json` under `WAREHOUSE_DIR/reports/score_shift/`."""
    directory = warehouse_dir / SHIFT_DIR
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{result.as_of_date.isoformat()}_{result.horizon_months}m.json"
    path.write_text(json.dumps(result.summary(), indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path
