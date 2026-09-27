"""Purged expanding-window folds (plan 0012 step C). The purging test is BLOCKING, like the
leakage test: never skip or weaken it."""

# polars's signatures reference types pyright cannot resolve; scoped to this module.
# pyright: reportUnknownMemberType=false

from __future__ import annotations

import shutil
from datetime import date
from pathlib import Path

import polars as pl
import pytest
import yaml
from tests.models.frames import features, labels, month_ends

from distress_radar.models.dataset import ModellingDataset, modelling_dataset
from distress_radar.models.splits import (
    fold_report,
    load_backtest_config,
    purged_folds,
    window_end,
)
from distress_radar.parsing.canonical_schema import CONFIG_DIR

DISTRESS = ("bankruptcy", "restructuring", "liquidation", "silent_exit")
YEARS = tuple(range(2014, 2027))
A, B, C = "0000000001", "0000000002", "0000000003"


def _window_close(day: date, horizon: int) -> date:
    months = day.year * 12 + day.month - 1 + horizon
    return month_ends(
        date(months // 12, months % 12 + 1, 1), date(months // 12, months % 12 + 1, 1)
    )[0]


EVENT = date(2020, 6, 15)


def _grid(horizon: int) -> ModellingDataset:
    """Three entities over 2012-2026. B's bankruptcy on 2020-06-15 labels the month-ends whose
    window holds it; B has no rows after it."""
    rows: list[tuple[str, date, int, str | None, date | None, bool]] = []
    for krs in (A, B, C):
        for day in month_ends(date(2012, 1, 31), date(2026, 8, 31)):
            if krs == B and day >= EVENT:
                break
            if krs == B and EVENT <= _window_close(day, horizon):
                rows.append((krs, day, horizon, "bankruptcy", EVENT, False))
            else:
                rows.append((krs, day, horizon, "alive", None, False))
    feature_rows = [(krs, day, 0.1) for krs, day, *_ in rows]
    return modelling_dataset(features(feature_rows), labels(rows), horizon, DISTRESS)


@pytest.mark.parametrize("horizon", [12, 24])
def test_no_training_label_window_reaches_its_test_period(horizon: int) -> None:
    """Owner decision 2: every training row's window closed before 1 January of the test year."""
    for fold in purged_folds(_grid(horizon), YEARS):
        boundary = date(fold.test_year, 1, 1)
        closes = fold.train.select(window_end(horizon).alias("closes")).get_column("closes")
        assert closes.is_empty() or closes.max() < boundary  # type: ignore[operator]
        assert fold.test.get_column("as_of_date").dt.year().unique().to_list() == [fold.test_year]
        # Purged, not just split: the rows between the boundary and the test year are left out.
        assert fold.train.get_column("as_of_date").max() is None or (
            fold.train.get_column("as_of_date").max() < date(fold.test_year - horizon // 12, 1, 1)  # type: ignore[operator]
        )


def test_the_window_boundary_is_exact() -> None:
    """At 12 months, 2018-12-31 closes on 2019-12-31 and trains the 2020 fold; 2019-01-31 does not."""
    fold = next(f for f in purged_folds(_grid(12), (2020,)))
    days = set(fold.train.get_column("as_of_date").to_list())
    assert date(2018, 12, 31) in days and date(2019, 1, 31) not in days


def test_events_are_counted_once_however_many_rows_they_label() -> None:
    dataset = _grid(12)
    assert dataset.frame.filter(pl.col("distress")).height == 12
    report = fold_report(purged_folds(dataset, (2020, 2022)), DISTRESS, min_events=1)
    by_year = {r["test_year"]: r for r in report.iter_rows(named=True)}
    assert by_year[2020]["test_events"] == 1 and by_year[2020]["train_events"] == 0
    assert by_year[2022]["train_events"] == 1 and by_year[2022]["train_events_bankruptcy"] == 1
    assert (
        not by_year[2020]["evaluable"] and not by_year[2022]["evaluable"]
    )  # no test event in 2022


def test_a_fold_below_min_events_is_reported_not_evaluable() -> None:
    report = fold_report(purged_folds(_grid(12), (2021,)), DISTRESS, min_events=3)
    assert report.row(0, named=True)["evaluable"] is False
    assert report.height == 1  # kept and reported, never dropped


def test_the_repository_backtest_config_loads() -> None:
    config = load_backtest_config("backtest_v1")
    assert config.feature_set_version == "feature_set_v3"
    assert config.label_set_hash.startswith("066d18bbd4cd")  # plan 0009's frozen v2 set
    assert config.test_years == (2020, 2021, 2022, 2023, 2024, 2025)
    assert config.min_events == 3 and config.horizons == (12, 24)
    # Owner decision 5, approved 2026-09-27: fixed before the first run.
    lr = config.logistic_regression
    assert lr.features == ("equity_to_assets", "working_capital_to_assets", "roa", "asset_turnover")
    assert (lr.penalty, lr.c, lr.class_weight) == ("l2", 1.0, "none")


def test_the_regression_may_only_read_ratio_features(tmp_path: Path) -> None:
    config_dir = tmp_path / "config"
    shutil.copytree(CONFIG_DIR, config_dir)
    path = config_dir / "models" / "backtest_v1.yaml"
    raw = yaml.safe_load(path.read_text("utf-8"))
    raw["logistic_regression"]["features"] = ["roa", "late_filings_3y"]
    path.write_text(yaml.safe_dump(raw), "utf-8")
    with pytest.raises(ValueError, match="late_filings_3y"):
        load_backtest_config("backtest_v1", config_dir)
