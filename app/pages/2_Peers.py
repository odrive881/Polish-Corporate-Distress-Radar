"""Peers: the scored companies' probabilities by PKD division, as distributions, never a ranking
of named companies (plan 0016 decision 8)."""

import altair as alt
import streamlit as st

from distress_radar.api.schemas import DISCLAIMER
from distress_radar.explorer import data

st.set_page_config(page_title="Peers", layout="wide")
st.warning(DISCLAIMER)
sources = st.cache_resource(show_spinner="Reading the stores…")(data.load_sources)()

horizon = st.radio("Horizon (months)", sorted(sources.served), horizontal=True)
day = data.latest_month_end(sources)
if day is None or horizon is None:
    st.info("No scores from the served models yet: run the `scoring` job.")
    st.stop()

st.subheader(f"Probabilities by PKD division at {day}")
distribution = data.peer_distribution(sources, horizon)
st.dataframe(distribution, hide_index=True)
if not distribution.is_empty():
    frame = distribution
    base = alt.Chart(frame).encode(y=alt.Y("division:N", title="PKD division"))
    st.altair_chart(
        base.mark_bar(opacity=0.4).encode(
            x=alt.X("q1:Q", title="probability", axis=alt.Axis(format="%")), x2="q3:Q"
        )
        + base.mark_tick(thickness=3, size=20).encode(
            x="median:Q", tooltip=["division:N", "companies:Q", "median:Q"]
        ),
        width="stretch",
    )
st.caption(
    "Bars: the middle half of each division's probabilities; ticks: the median. Divisions from "
    "GUS's predominant PKD code. The companies are a purposive list: a division's spread here is "
    "not the sector's."
)
