"""The methodology report's figures (plan 0016 step G): read only from the publish database, printed
as the backtest report prints them, every include of the report backed by a figure."""

from __future__ import annotations

import re
from dataclasses import replace
from pathlib import Path

import pytest
from tests.test_publish import _synthetic_publish_db  # pyright: ignore[reportPrivateUsage]

from distress_radar import methodology as m
from distress_radar.labels import load_label_config
from distress_radar.models.report import (
    _metric as backtest_metric,  # pyright: ignore[reportPrivateUsage]
)
from distress_radar.models.serving import ServedVersion
from distress_radar.models.splits import load_backtest_config

REPORT = Path(__file__).parents[1] / "report" / "methodology.qmd"


def _served(backtest: str = "bt", alias: str = "incumbent") -> dict[int, ServedVersion]:
    return {
        h: ServedVersion(
            name=f"distress_radar_{h}m",
            version="5",
            alias=alias,
            run_id="f" * 32,
            model="logistic_regression",
            horizon_months=h,
            test_year=2025,
            tags={"backtest": backtest},
        )
        for h in (12, 24)
    }


@pytest.mark.parametrize(
    "row",
    [
        {"evaluable": True, "brier": 0.0336, "brier_low": 0.0273, "brier_high": 0.0401},
        {"evaluable": True, "brier": 0.5, "brier_low": None, "brier_high": None},
        {"evaluable": True, "brier": None, "brier_low": None, "brier_high": None},
        {"evaluable": False, "brier": None, "brier_low": None, "brier_high": None},
    ],
)
def test_a_metric_prints_as_the_backtest_report_prints_it(row: dict[str, object]) -> None:
    assert m.metric(row, "brier") == backtest_metric(row, "brier")


def test_a_suppressed_cell_prints_no_figure() -> None:
    row = {"evaluable": True, "suppressed": True, "brier": None}
    assert m.metric(row, "brier") == "suppressed"


@pytest.fixture
def figures(tmp_path: Path) -> dict[str, str]:
    tables = m.read_publish(_synthetic_publish_db(tmp_path / "publish.duckdb"))
    config = load_backtest_config("backtest_v8")
    return m.figures(tables, _served(), config, load_label_config("outcome_labels_v2"))


def test_every_include_of_the_report_is_a_figure_and_every_figure_included(
    figures: dict[str, str],
) -> None:
    included = set(re.findall(r"\{\{< include _figures/(\w+)\.md >\}\}", REPORT.read_text("utf-8")))
    assert included == set(figures)


def test_the_figures_come_from_the_publish_database(figures: dict[str, str]) -> None:
    assert figures["coverage"].splitlines()[0] == "| stage | seed |"  # lists as columns
    assert "| listed | 1 |" in figures["coverage"]
    assert "`code_commit`" in figures["provenance"]
    assert "| m |" in figures["metrics_12"] and "0.100 [0.100–0.100]" in figures["metrics_12"]
    assert "No pooled cell" in figures["reliability_24"]
    assert (
        "| 12 months | logistic regression | distress_radar_12m v5 | incumbent |"
        in figures["served"]
    )
    assert "nothing promoted" in figures["served"]
    assert "| test years | 2020, 2021, 2022, 2023, 2024, 2025 |" in figures["settings"]


def test_a_verdict_from_another_backtest_is_refused() -> None:
    m.check_served(_served("bt"), "bt")
    with pytest.raises(ValueError, match="come from"):
        m.check_served(_served("other"), "bt")


def test_the_figures_are_replaced_whole(tmp_path: Path) -> None:
    out = tmp_path / "_figures"
    m.write_figures({"a": "x\n", "b": "y\n"}, out)
    m.write_figures({"a": "z\n"}, out)
    assert sorted(p.name for p in out.iterdir()) == ["a.md"]
    assert (out / "a.md").read_text() == "z\n"


def test_no_figure_names_a_company(figures: dict[str, str]) -> None:
    text = "\n".join(figures.values())
    assert not re.search(r"\b\d{10}\b", text)


def test_the_served_figure_says_whether_a_promotion_is_provisional_or_final() -> None:
    provisional = m.served_figure(_served(alias="champion"))
    assert "| promoted, provisional (unweighted) |" in provisional
    final = {
        h: replace(v, tags={**v.tags, "champion_basis": "weighted"})
        for h, v in _served(alias="champion").items()
    }
    assert "| promoted, final (weighted) |" in m.served_figure(final)
