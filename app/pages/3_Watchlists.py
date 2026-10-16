"""Watchlists: named lists of companies with their current scores (plan 0016 decision 8). Alerts
on them are Phase 10."""

import polars as pl
import streamlit as st

from distress_radar.api.schemas import DISCLAIMER
from distress_radar.explorer import data

st.set_page_config(page_title="Watchlists", layout="wide")
st.warning(DISCLAIMER)
load = st.cache_resource(show_spinner="Reading the stores…")(data.load_sources)
sources = load()

names = sorted(set(sources.watchlists.get_column("name").to_list()))
choice = st.selectbox("Watchlist", [*names, "(new list)"])
name = st.text_input("Name of the new list") if choice == "(new list)" else choice
if not name:
    st.stop()

members = sources.watchlists.filter(pl.col("name") == name).get_column("krs").to_list()
labels = {
    krs: f"{krs} — {n or '(no name)'}"
    for krs, n in sources.entities.sort("krs").select("krs", "name").iter_rows()
}
chosen = st.multiselect("Companies", list(labels), default=members, format_func=lambda k: labels[k])
if st.button("Save the list"):
    data.save_watchlist(sources, name, chosen)
    load.clear()
    st.rerun()

rows: list[dict[str, object]] = []
for krs in members:
    row: dict[str, object] = {"company": labels.get(krs, krs)}
    for status in data.current_status(sources, krs).iter_rows(named=True):
        probability = status["probability"]
        row[f"{status['horizon_months']} months"] = (
            f"{100 * probability:.2f}%"
            if probability is not None
            else f"not scored: {status['reason']}"
        )
    rows.append(row)
if rows:
    st.dataframe(pl.DataFrame(rows), hide_index=True)
else:
    st.caption("No company on this list yet.")
