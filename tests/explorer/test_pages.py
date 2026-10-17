"""The explorer's pages run on synthetic sources (Streamlit's `AppTest`), and every page that
shows a score carries the disclaimer (plan 0016 step E, AGENT_SPEC §6K)."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import date
from pathlib import Path

import polars as pl
import pytest
import streamlit as st
from streamlit.testing.v1 import AppTest
from tests.explorer.sources import make_sources

from distress_radar.api.schemas import DISCLAIMER
from distress_radar.explorer import data

APP = Path(__file__).parents[2] / "app"
PAGES = [
    "Home.py",
    "pages/1_Company.py",
    "pages/2_Peers.py",
    "pages/3_Watchlists.py",
    "pages/4_Alerts.py",
]


@pytest.fixture(autouse=True)
def synthetic(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[data.Sources]:
    sources = make_sources(tmp_path)
    monkeypatch.setattr(data, "load_sources", lambda: sources)
    monkeypatch.setattr(data, "load_alerts", lambda s, *, acknowledged: _alerts(acknowledged))  # type: ignore[reportUnknownLambdaType]
    st.cache_resource.clear()
    yield sources
    st.cache_resource.clear()


def _alerts(acknowledged: bool) -> pl.DataFrame:
    """Plan 0018 step E: one open alert on a watchlist company, none acknowledged."""
    rows = [
        {
            "alert_id": "a" * 64,
            "krs": "0000000001",
            "company": "Alfa sp. z o.o.",
            "on_watchlist": True,
            "as_of_date": date(2026, 9, 30),
            "alert_type": "tripwire_triggered",
            "severity": "high",
            "what": data.describe(
                "tripwire_triggered",
                {"feature": "art233_triggered", "known_from": "2026-07-01"},
            ),
            "acknowledged_at": None,
        }
    ]
    return pl.DataFrame([] if acknowledged else rows, schema=data.ALERT_COLUMNS)


def _run(page: str, **query: str) -> AppTest:
    app = AppTest.from_file(str(APP / page), default_timeout=60)
    for key, value in query.items():
        app.query_params[key] = value
    app.run()
    assert not app.exception, [e.value for e in app.exception]
    return app


@pytest.mark.parametrize("page", PAGES)
def test_every_page_runs_and_carries_the_disclaimer(page: str) -> None:
    app = _run(page)
    assert DISCLAIMER in [w.value for w in app.warning]


def test_the_company_page_shows_a_score_and_its_drivers() -> None:
    app = _run("pages/1_Company.py", krs="0000000001")
    assert [m.value for m in app.metric] == ["2.00%", "5.00%"]
    assert app.dataframe  # drivers, events, filings, facts


def test_a_micro_filer_missing_working_capital_is_told_why() -> None:
    app = _run("pages/1_Company.py", krs="0000000002")
    assert any("does not cover micro filers" in i.value for i in app.info)


def test_an_excluded_company_shows_its_reason() -> None:
    app = _run("pages/1_Company.py", krs="0000000003")
    assert any("rdf_not_searched" in m.value for m in app.markdown)
    assert not app.metric


def test_a_watchlist_shows_its_members() -> None:
    app = _run("pages/3_Watchlists.py")
    assert app.selectbox[0].value == "budowlane"
    assert "Alfa Budowa" in str(app.dataframe[0].value)


def test_the_alerts_page_lists_open_alerts_and_nothing_is_ticked() -> None:
    app = _run("pages/4_Alerts.py")
    assert any("1 high" in m.value for m in app.markdown)
    (button,) = app.button
    assert button.label == "Acknowledge 0 selected" and button.disabled


def test_the_alerts_page_says_when_nothing_was_acknowledged() -> None:
    app = AppTest.from_file(str(APP / "pages/4_Alerts.py"), default_timeout=60)
    app.run()
    app.toggle[0].set_value(True).run()
    assert not app.exception, [e.value for e in app.exception]
    assert [i.value for i in app.info] == ["No acknowledged alerts."]
