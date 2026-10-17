"""Alerts: what changed since the previous month-end, score or search (plan 0018 step E, decision 5).
Local only, like every page here: each alert names a company. Nothing is sent anywhere."""

import polars as pl
import streamlit as st

from distress_radar.api.schemas import DISCLAIMER
from distress_radar.explorer import data

st.set_page_config(page_title="Alerts", layout="wide")
st.warning(DISCLAIMER)
st.caption(
    "Thresholds rank companies on a purposive list (ADR 0014); they are not population risks. "
    "A filing is judged overdue only as of the company's last complete RDF search."
)
load = st.cache_resource(show_spinner="Reading the stores…")(data.load_sources)
sources = load()

show_acknowledged = st.toggle("Show acknowledged alerts", value=False)
alerts = data.load_alerts(sources, acknowledged=show_acknowledged)
if alerts.is_empty():
    st.info("No acknowledged alerts." if show_acknowledged else "No open alerts.")
    st.stop()

counts = alerts.group_by("severity").len().sort("severity")
st.write(", ".join(f"{n} {s}" for s, n in counts.iter_rows()) + f" — {alerts.height} in all")

shown = alerts.with_columns(
    pl.when(pl.col("on_watchlist")).then(pl.lit("★")).otherwise(pl.lit("")).alias("watch"),
    pl.lit(False).alias("acknowledge"),
)
columns = ["acknowledge", "watch", "severity", "as_of_date", "krs", "company", "alert_type", "what"]
if show_acknowledged:
    columns = [c for c in columns if c != "acknowledge"] + ["acknowledged_at"]
edited = st.data_editor(
    shown.select(columns),
    hide_index=True,
    disabled=[c for c in columns if c != "acknowledge"],
    use_container_width=True,
)
if not show_acknowledged:
    chosen = [
        alert_id
        for alert_id, ticked in zip(
            shown.get_column("alert_id").to_list(),
            edited["acknowledge"].to_list(),  # type: ignore[index]
            strict=True,
        )
        if ticked
    ]
    if st.button(f"Acknowledge {len(chosen)} selected", disabled=not chosen):
        data.acknowledge_alerts(sources, chosen)
        st.rerun()
