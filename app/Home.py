"""The internal explorer's home (plan 0016 step E, decision 8): what serves, and the latest
month-end at a glance. Local only: every page names companies (AGENT_SPEC §6K). `make app`."""

import polars as pl
import streamlit as st

from distress_radar.api.schemas import DISCLAIMER
from distress_radar.explorer import data

st.set_page_config(page_title="Distress radar (local)", layout="wide")
st.title("Distress radar: internal explorer")
st.warning(DISCLAIMER)
sources = st.cache_resource(show_spinner="Reading the stores…")(data.load_sources)()

st.subheader("Served models")
st.dataframe(
    pl.DataFrame(
        [
            {
                "horizon (months)": horizon,
                "model": f"{v.name} v{v.version} @{v.alias}",
                "kind": v.model,
                "training eve": v.training_eve,
                "backtest": v.tags.get("backtest"),
                "label set": v.tags.get("label_set_hash", "")[:12],
                "feature set": v.tags.get("feature_set_version"),
            }
            for horizon, v in sorted(sources.served.items())
        ]
    ),
    hide_index=True,
)

day = data.latest_month_end(sources)
if day is None:
    st.info("No scores from the served models yet: run the `scoring` job.")
else:
    st.subheader(f"Latest month-end scored: {day}")
    summary = data.month_end_summary(sources).pivot(
        on="horizon_months", index="status", values="entities"
    )
    st.dataframe(summary, hide_index=True)
    st.caption(
        "Scored: a probability. Inputs incomplete: the served model lacks an input it needs, "
        "never imputed. The other rows were not scored at all: acquisition incomplete (the RDF "
        "search, the statements) or not at risk (already in a proceeding or deregistered)."
    )

st.subheader("Input drift of the served models")
reports = data.drift_reports(sources)
if not reports:
    st.info("No drift report yet: run the `monitoring` job after a scoring run.")
else:
    st.dataframe(
        pl.DataFrame(
            [
                {
                    "month-end": summary["as_of_date"],
                    "horizon (months)": summary["horizon_months"],
                    "model": summary["model"],
                    "rows": f"{summary['current_rows']} against {summary['reference_rows']}",
                    "drifted inputs": ", ".join(
                        c for c, v in sorted(summary["columns"].items()) if v["drifted"]
                    )
                    or "none",
                    "population as pinned": summary["population_matches_pin"],
                }
                for summary, _ in reports
            ]
        ),
        hide_index=True,
    )
    for summary, html in reports[:2]:
        st.download_button(
            f"Drift report, {summary['horizon_months']} months, {summary['as_of_date']}",
            html.read_bytes(),
            file_name=html.name,
            mime="text/html",
        )
    st.caption(
        "The latest scored month-end's inputs against the served version's training rows, "
        "2001 on. A few hundred rows flag little and prove less: read it, never gate on it. "
        "Local files only, never published: the charts can single out a company."
    )

st.caption(
    "The companies come from purposive lists (ADR 0014), chosen partly by distress status: no "
    "share or rate on these pages is the construction sector's. Use the pages on the left for a "
    "company, peers by PKD division, and watchlists."
)
