"""The score-shift report (plan 0021 decision 4)."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import numpy as np

from distress_radar.monitoring.score_shift import MIN_ROWS, psi, score_shift, write_shift

DAY, BEFORE = date(2026, 10, 31), date(2026, 9, 30)
THRESHOLDS = [0.25, 0.10]


def _shift(current: np.ndarray, previous: np.ndarray | None):  # type: ignore[no-untyped-def]
    return score_shift(
        current,
        previous,
        as_of_date=DAY,
        previous_as_of_date=None if previous is None else BEFORE,
        horizon_months=12,
        model="distress_radar_12m v5",
        thresholds=THRESHOLDS,
    )


def test_the_same_distribution_has_no_shift() -> None:
    p = np.random.default_rng(1).beta(1, 40, size=240)
    result = _shift(p, p.copy())
    assert result.evaluable
    assert result.stats["psi"] == 0.0 and result.stats["ks_statistic"] == 0.0


def test_a_moved_distribution_shows_one() -> None:
    rng = np.random.default_rng(2)
    before, after = rng.beta(1, 40, size=240), rng.beta(1, 10, size=240)
    result = _shift(after, before)
    assert result.stats["psi"] > 0.25  # the conventional "significant shift"
    assert (
        result.stats["current"]["share_above"]["0.1"]
        > result.stats["previous"]["share_above"]["0.1"]
    )
    assert result.stats["ks_statistic"] > 0.2


def test_a_versions_first_month_end_is_not_evaluable() -> None:
    result = _shift(np.full(240, 0.02), None)
    assert not result.evaluable and "no earlier month-end" in result.note


def test_too_few_scores_are_not_evaluable() -> None:
    result = _shift(np.full(MIN_ROWS - 1, 0.02), np.full(240, 0.02))
    assert not result.evaluable


def test_an_empty_bin_does_not_make_psi_infinite() -> None:
    previous = np.linspace(0.0, 0.1, 200)
    current = np.full(200, 0.5)  # every score above every previous one
    assert np.isfinite(psi(current, previous))


def test_the_report_is_written_whole_and_stably(tmp_path: Path) -> None:
    p = np.random.default_rng(3).beta(1, 40, size=240)
    first = write_shift(_shift(p, p * 1.1), tmp_path).read_bytes()
    second = write_shift(_shift(p, p * 1.1), tmp_path).read_bytes()
    assert first == second
    body = json.loads(first)
    assert body["as_of_date"] == "2026-10-31" and body["previous_as_of_date"] == "2026-09-30"
