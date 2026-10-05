"""Purged expanding-window folds (plan 0012 step C). The purging test is BLOCKING, like the
leakage test: never skip or weaken it."""

# polars's signatures reference types pyright cannot resolve; scoped to this module.
# pyright: reportUnknownMemberType=false

from __future__ import annotations

import shutil
from dataclasses import replace
from datetime import date
from pathlib import Path

import polars as pl
import pytest
import yaml
from tests.models.frames import TIMING, features, labels, month_ends

from distress_radar.models.dataset import ModellingDataset, modelling_dataset
from distress_radar.models.splits import (
    fold_report,
    label_timing,
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
    for fold in purged_folds(_grid(horizon), YEARS, TIMING):
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
    fold = next(f for f in purged_folds(_grid(12), (2020,), TIMING))
    days = set(fold.train.get_column("as_of_date").to_list())
    assert date(2018, 12, 31) in days and date(2019, 1, 31) not in days


def _late(dataset: ModellingDataset, known: date) -> ModellingDataset:
    """B's event entered in the registry on `known`, long after its decision."""
    frame = dataset.frame.with_columns(
        pl.when(pl.col("distress"))
        .then(pl.lit(known))
        .otherwise(pl.col("event_known_from"))
        .alias("event_known_from")
    )
    return replace(dataset, frame=frame)


@pytest.mark.parametrize("horizon", [12, 24])
def test_no_training_label_was_unknown_on_the_eve_of_its_test_year(horizon: int) -> None:
    """BLOCKING, with the window test above: a training label is one the labels' own rules gave
    on 31 December before the test year. A distress event public only later, or an `alive`
    inside the lag allowance then, would train on the future."""
    dataset = _late(_grid(horizon), date(2023, 3, 31))
    for fold in purged_folds(dataset, YEARS, TIMING):
        eve = date(fold.test_year - 1, 12, 31)
        train = fold.train.with_columns(window_end(horizon).alias("closes"))
        known = train.filter(pl.col("distress")).get_column("event_known_from")
        assert known.is_empty() or known.max() <= eve  # type: ignore[operator]
        alive = train.filter(~pl.col("distress") & (pl.col("closes") >= TIMING.krz_launch))
        limit = date(eve.year - TIMING.alive_lag_months // 12, 12, 31)
        assert alive.is_empty() or alive.get_column("closes").max() <= limit  # type: ignore[operator]


def test_an_event_entered_late_trains_only_once_it_is_public() -> None:
    """B's 2020 bankruptcy, entered on 2023-03-31: its rows closed by 2021, but the 2022 and 2023
    folds could not have known it. They leave the rows out and count them; 2024 trains on them."""
    folds = purged_folds(_late(_grid(12), date(2023, 3, 31)), (2022, 2023, 2024), TIMING)
    by_year = {f.test_year: f for f in folds}
    for year in (2022, 2023):
        assert by_year[year].train.filter(pl.col("distress")).is_empty()
    assert by_year[2024].train.filter(pl.col("distress")).height == 12
    report = fold_report(folds, DISTRESS, min_events=1)
    unsettled = dict(zip(report["test_year"], report["train_rows_unsettled"], strict=True))
    assert unsettled[2022] >= 12 and unsettled[2023] >= 12


def test_alive_after_krz_settles_only_after_the_lag_allowance() -> None:
    """At 12 months, 2021-06-30 closes on 2022-06-30, after KRZ's launch: the labels call it
    `alive` only 12 months on, so it trains the 2024 fold, not 2023. 2020-06-30 closes before the
    launch, when MSiG published within weeks, and trains 2022."""
    folds = {f.test_year: f for f in purged_folds(_grid(12), (2022, 2023, 2024), TIMING)}

    def trains(year: int, day: date) -> bool:
        rows = folds[year].train.filter(pl.col("krs") == A)
        return day in rows.get_column("as_of_date").to_list()

    assert trains(2022, date(2020, 6, 30))
    assert not trains(2023, date(2021, 6, 30)) and trains(2024, date(2021, 6, 30))
    # On the 2022 fold's eve, 2021-12-31: a window closing that day ends after the launch and is
    # not settled yet; one closing on 2021-11-30 ends before it and is.
    assert not trains(2022, date(2020, 12, 31)) and trains(2023, date(2020, 12, 31))
    assert trains(2022, date(2020, 11, 30))


def test_the_timing_is_read_from_the_label_version() -> None:
    frame = labels([(A, date(2020, 1, 31), 12, "alive", None, False)]).with_columns(
        pl.lit("outcome_labels_v2").alias("label_version")
    )
    assert label_timing(frame) == TIMING
    v1 = frame.with_columns(pl.lit("outcome_labels_v1").alias("label_version"))
    assert label_timing(v1).alive_lag_months == 0


def test_events_are_counted_once_however_many_rows_they_label() -> None:
    dataset = _grid(12)
    assert dataset.frame.filter(pl.col("distress")).height == 12
    report = fold_report(purged_folds(dataset, (2020, 2022), TIMING), DISTRESS, min_events=1)
    by_year = {r["test_year"]: r for r in report.iter_rows(named=True)}
    assert by_year[2020]["test_events"] == 1 and by_year[2020]["train_events"] == 0
    assert by_year[2022]["train_events"] == 1 and by_year[2022]["train_events_bankruptcy"] == 1
    assert (
        not by_year[2020]["evaluable"] and not by_year[2022]["evaluable"]
    )  # no test event in 2022


def test_a_fold_below_min_events_is_reported_not_evaluable() -> None:
    report = fold_report(purged_folds(_grid(12), (2021,), TIMING), DISTRESS, min_events=3)
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


def test_backtest_v2_adds_only_the_going_concern_flag() -> None:
    """Plan 0013 decision 8, recorded before any run: v1 on feature set v4, plus one flag."""
    v1 = load_backtest_config("backtest_v1")
    v2 = load_backtest_config("backtest_v2")
    assert v2.feature_set_version == "feature_set_v4"
    assert v2.logistic_regression.features == (
        *v1.logistic_regression.features,
        "going_concern_threat",
    )
    unchanged = {"backtest", "feature_set_version", "logistic_regression"}
    assert v2.model_dump(exclude=unchanged) == v1.model_dump(exclude=unchanged)
    assert v2.logistic_regression.model_dump(exclude={"features"}) == (
        v1.logistic_regression.model_dump(exclude={"features"})
    )


def test_backtest_v3_is_v2_on_feature_set_v5() -> None:
    """Plan 0013 decision 0c, recorded before any run: v2 unchanged, on v5's feature store."""
    v2 = load_backtest_config("backtest_v2")
    v3 = load_backtest_config("backtest_v3")
    assert v3.feature_set_version == "feature_set_v5"
    unchanged = {"backtest", "feature_set_version"}
    assert v3.model_dump(exclude=unchanged) == v2.model_dump(exclude=unchanged)


def test_backtest_v4_is_v3_on_feature_set_v6() -> None:
    """Recorded before any run: v3 unchanged, on v6's feature store."""
    v3 = load_backtest_config("backtest_v3")
    v4 = load_backtest_config("backtest_v4")
    assert v4.feature_set_version == "feature_set_v6"
    unchanged = {"backtest", "feature_set_version"}
    assert v4.model_dump(exclude=unchanged) == v3.model_dump(exclude=unchanged)


def test_the_regression_may_only_read_ratio_features_and_flags(tmp_path: Path) -> None:
    config_dir = tmp_path / "config"
    shutil.copytree(CONFIG_DIR, config_dir)
    path = config_dir / "models" / "backtest_v1.yaml"
    raw = yaml.safe_load(path.read_text("utf-8"))
    raw["logistic_regression"]["features"] = ["roa", "late_filings_3y"]
    path.write_text(yaml.safe_dump(raw), "utf-8")
    with pytest.raises(ValueError, match="late_filings_3y"):
        load_backtest_config("backtest_v1", config_dir)
