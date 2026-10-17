"""Realised-outcome calibration (plan 0018 step G, decision 7), on hand-built frames."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import polars as pl
from tests.models.frames import TIMING  # `alive` settles 12 months late after KRZ's launch

from distress_radar.monitoring.realised import realised, write_realised

DISTRESS = ("bankruptcy", "restructuring", "liquidation", "silent_exit")
ON = date(2026, 10, 17)


def _scores(rows: list[tuple[str, date, float | None]]) -> pl.DataFrame:
    return pl.DataFrame(
        [
            {
                "krs": k,
                "as_of_date": d,
                "horizon_months": 12,
                "probability": p,
                "model_name": "distress_radar_12m",
                "model_version": "5",
            }
            for k, d, p in rows
        ],
        schema_overrides={"probability": pl.Float64},
    )


def _labels(rows: list[tuple[str, date, str | None]]) -> pl.DataFrame:
    return pl.DataFrame(
        [
            {
                "krs": k,
                "as_of_date": d,
                "horizon_months": 12,
                "outcome_class": c,
                "censored": c is None,
            }
            for k, d, c in rows
        ],
        schema_overrides={"outcome_class": pl.String},
    )


def _run(scores: pl.DataFrame, labels: pl.DataFrame) -> dict[str, object]:
    (cell,) = realised(
        scores, labels, distress_classes=DISTRESS, timing=TIMING, on=ON, min_events=3, bins=5
    )
    return cell


def test_todays_scores_are_not_evaluable_and_say_when_they_settle():
    day = date(2026, 9, 30)
    cell = _run(
        _scores([("0000000001", day, 0.4), ("0000000002", day, None)]),
        _labels([("0000000001", day, None)]),
    )
    # Window closes 2027-09-30; an `alive` label is known 12 months later.
    assert cell["evaluable"] is False and cell["scores"] == 1  # the unscored row is left out
    assert cell["first_settles_on"] == "2028-09-30"
    assert "settles on 2028-09-30" in str(cell["reason"])


def test_a_failure_known_mid_window_waits_for_its_cohort():
    """Counting early failures before the survivors beside them would bias the sample."""
    day = date(2025, 12, 31)  # window closes 2026-12-31: not settled on ON
    cell = _run(
        _scores([(f"000000000{i}", day, 0.5) for i in range(1, 5)]),
        _labels([(f"000000000{i}", day, "bankruptcy") for i in range(1, 5)]),
    )
    assert (cell["settled_rows"], cell["events"], cell["evaluable"]) == (0, 0, False)


def test_a_settled_cohort_is_scored_as_the_backtest_scores():
    day = date(2019, 12, 31)  # window closed 2020-12-31, before KRZ: settled then
    scores = _scores(
        [("0000000001", day, 0.5), ("0000000002", day, 0.5), ("0000000003", day, 0.5)]
        + [("0000000004", day, 0.1)]
    )
    labels = _labels(
        [("0000000001", day, "bankruptcy"), ("0000000002", day, "liquidation")]
        + [("0000000003", day, "restructuring"), ("0000000004", day, "alive")]
    )
    cell = _run(scores, labels)
    assert cell["evaluable"] is True and cell["events"] == 3
    assert cell["brier"] == round((3 * 0.25 + 0.01) / 4, 6)
    assert (cell["base_rate"], cell["mean_predicted"]) == (0.75, 0.4)
    assert cell["reliability"]


def test_the_file_is_the_same_on_a_rerun(tmp_path: Path):
    cells = [{"horizon_months": 12, "evaluable": False}]
    first = write_realised(cells, ON, tmp_path).read_bytes()
    assert write_realised(cells, ON, tmp_path).read_bytes() == first
    assert (tmp_path / "reports" / "calibration" / "realised_2026-10-17.json").exists()
