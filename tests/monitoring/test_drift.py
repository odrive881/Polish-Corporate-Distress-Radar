"""Drift of the served model's inputs (plan 0018 step F, decision 6)."""

from __future__ import annotations

import os
from datetime import date
from pathlib import Path

import numpy as np
import polars as pl
import pytest
from tests.models.frames import TIMING, panel

from distress_radar.models.dataset import modelling_dataset
from distress_radar.models.splits import split
from distress_radar.monitoring import drift as d

AS_OF = date(2026, 9, 30)


def _frame(n: int, shift: float, seed: int) -> pl.DataFrame:
    rng = np.random.default_rng(seed)
    a = rng.normal(0.3 + shift, 0.2, n)
    b = rng.normal(0.05, 0.1, n)
    b[::7] = np.nan  # nulls are kept, never imputed
    return pl.DataFrame({"krs": [f"{i:010d}" for i in range(n)], "equity_to_assets": a, "roa": b})


def _report(reference: pl.DataFrame, current: pl.DataFrame) -> d.DriftResult:
    return d.drift_report(
        reference,
        current,
        ["equity_to_assets", "roa"],
        horizon_months=12,
        model="distress_radar_12m v5",
        as_of_date=AS_OF,
        population_matches_pin=True,
    )


def test_a_shifted_input_drifts_and_an_unshifted_one_does_not():
    result = _report(_frame(400, 0.0, 1), _frame(240, 0.2, 2))
    assert result.columns["equity_to_assets"]["drifted"] is True
    assert result.columns["roa"]["drifted"] is False
    assert result.columns["roa"]["method"].startswith("K-S")
    assert (result.reference_rows, result.current_rows) == (400, 240)


def test_the_same_inputs_give_the_same_bytes(tmp_path: Path):
    first = _report(_frame(400, 0.0, 1), _frame(240, 0.2, 2))
    second = _report(_frame(400, 0.0, 1), _frame(240, 0.2, 2))
    assert first.html == second.html
    html, summary = d.write_drift(first, tmp_path)
    assert html.name == "2026-09-30_12m.html" and summary.name == "2026-09-30_12m.json"
    again = d.write_drift(second, tmp_path)
    assert [p.read_bytes() for p in again] == [html.read_bytes(), summary.read_bytes()]


def test_evidently_runs_with_its_telemetry_switched_off():
    _report(_frame(50, 0.0, 1), _frame(50, 0.0, 2))
    assert {name: os.environ.get(name) for name in d.TELEMETRY_OFF} == d.TELEMETRY_OFF


def test_ids_are_renumbered_by_first_appearance_keeping_their_length():
    a, b = "f" * 32, "0123abcd-0000-4000-8000-0123456789ab"
    html = f"var metric_{a} = 1; id='{b}'; ref={a}"
    assert (
        d.normalise_ids(html)
        == "var metric_" + "0" * 32 + " = 1; id='" + "1".zfill(36) + "'; ref=" + "0" * 32
    )


def test_nothing_to_compare_is_refused():
    with pytest.raises(ValueError, match="nothing to compare"):
        _report(_frame(10, 0.0, 1), _frame(0, 0.0, 2))


def test_the_current_rows_are_the_scored_entities_at_the_month_end():
    features = pl.DataFrame(
        {
            "krs": ["0000000001", "0000000002", "0000000001"],
            "as_of_date": [AS_OF, AS_OF, date(2026, 8, 31)],
        }
    )
    current = d.current_frame(features, frozenset({"0000000001"}), AS_OF)
    assert current.rows() == [("0000000001", AS_OF)]


def test_the_reference_is_the_served_folds_training_rows_of_the_population():
    features, labels = panel(entities=40, seed=3)
    distress = ("bankruptcy", "restructuring", "liquidation", "silent_exit")
    kept = frozenset(sorted(set(labels.get_column("krs").to_list()))[:30])
    reference = d.reference_frame(
        features,
        labels,
        horizon_months=12,
        test_year=2024,
        distress_classes=distress,
        entities=kept,
        timing=TIMING,
    )
    expected = split(
        modelling_dataset(
            features, labels.filter(pl.col("krs").is_in(sorted(kept))), 12, distress
        ).frame,
        12,
        2024,
        TIMING,
    ).train
    assert reference.equals(expected) and not reference.is_empty()
    assert set(reference.get_column("krs").to_list()) <= kept
