"""The explorer's pages run on synthetic sources (Streamlit's `AppTest`), and every page that
shows a score carries the disclaimer (plan 0016 step E, AGENT_SPEC §6K)."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
import streamlit as st
from streamlit.testing.v1 import AppTest
from tests.explorer.sources import make_sources

from distress_radar.api.schemas import DISCLAIMER
from distress_radar.explorer import data

APP = Path(__file__).parents[2] / "app"
PAGES = ["Home.py", "pages/1_Company.py", "pages/2_Peers.py", "pages/3_Watchlists.py"]


@pytest.fixture(autouse=True)
def synthetic(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[data.Sources]:
    sources = make_sources(tmp_path)
    monkeypatch.setattr(data, "load_sources", lambda: sources)
    st.cache_resource.clear()
    yield sources
    st.cache_resource.clear()


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
