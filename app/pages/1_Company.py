"""A company: profile, current scores, out-of-time score history with filings and legal events,
and its canonical statements linked to the stored documents (plan 0016 decisions 3, 4, 8)."""

import json

import altair as alt
import polars as pl
import streamlit as st

from distress_radar.api.schemas import DISCLAIMER
from distress_radar.explorer import data

MICRO_NOTE = (
    "The served model does not cover micro filers: its input `working_capital_to_assets` needs "
    "short-term liabilities, which the micro balance sheet does not report. This is not missing "
    "data."
)

st.set_page_config(page_title="Company", layout="wide")
st.warning(DISCLAIMER)
sources = st.cache_resource(show_spinner="Reading the stores…")(data.load_sources)()

entities = sources.entities.sort("krs")
labels = {
    krs: f"{krs} — {name or '(no name)'}"
    for krs, name in entities.select("krs", "name").iter_rows()
}
options = list(labels)
wanted = st.query_params.get("krs")
krs = st.selectbox(
    "Company",
    options,
    index=options.index(wanted) if wanted in labels else 0,
    format_func=lambda k: labels[k],
)
if krs is None:
    st.stop()
st.query_params["krs"] = krs

entity = entities.filter(pl.col("krs") == krs).row(0, named=True)
facts = data.statement_facts(sources, krs)
forms = facts.sort("period_end").get_column("structure_version").drop_nulls().to_list()
form_filed = forms[-1].split("-")[0] if forms else None

st.header(entity["name"] or krs)
left, right = st.columns(2)
left.markdown(
    f"**KRS** {krs} · **NIP** {entity['nip'] or '–'} · **REGON** {entity['regon'] or '–'}  \n"
    f"**Status (GUS)** {entity['status'] or '–'} · **PKD** {entity['pkd_predominant'] or '–'}  \n"
    f"**Form of the latest statement filed** {form_filed or 'none parsed'}"
)

st.subheader("Current scores")
status = data.current_status(sources, krs)
if status.is_empty():
    st.info("No served scoring run has seen this company.")
for row in status.iter_rows(named=True):
    horizon = row["horizon_months"]
    box = st.container(border=True)
    if row["probability"] is not None:
        percentile = data.peer_percentile(sources, krs, horizon)
        box.metric(
            f"{horizon}-month probability at {row['as_of_date']} (v{row['model_version']})",
            f"{100 * row['probability']:.2f}%",
        )
        if row["probability_low"] is not None:
            box.caption(
                f"90% range {100 * row['probability_low']:.2f}%–{100 * row['probability_high']:.2f}%: "
                "the model's estimation uncertainty only, on the lists it was trained on."
            )
        if percentile is not None:
            box.caption(f"At or above {percentile:.0f}% of its PKD division's scored companies.")
        moved = data.drivers_since_last(
            sources, krs, horizon, row["as_of_date"], row["model_version"]
        )
        drivers = json.loads(row["top_drivers"]) if row["top_drivers"] else []
        if moved:
            box.caption("Drivers, and how each moved since this version's previous score:")
            box.dataframe(pl.DataFrame(moved), hide_index=True)
        elif drivers:
            box.dataframe(pl.DataFrame(drivers), hide_index=True)
    else:
        box.markdown(f"**{horizon} months, {row['as_of_date']}: not scored ({row['reason']})**")
        missing = row["missing_inputs"] or []
        if missing:
            box.markdown("Inputs missing: " + ", ".join(f"`{m}`" for m in missing))
        if "working_capital_to_assets" in missing and form_filed == "micro":
            box.info(MICRO_NOTE)

st.subheader("Score history")
history = data.score_history(sources, krs)
marks = data.history_marks(sources, krs)
if history.is_empty():
    st.info("No score for this company in the backtest or since.")
else:
    lines = (
        alt.Chart(history)
        .mark_line(point=True)
        .encode(
            x=alt.X("as_of_date:T", title="month-end"),
            y=alt.Y("probability:Q", title="probability", axis=alt.Axis(format="%")),
            color=alt.Color("horizon_months:N", title="horizon (months)"),
            tooltip=[
                "as_of_date:T",
                "horizon_months:N",
                alt.Tooltip("probability:Q", format=".2%"),
                "source:N",
            ],
        )
    )
    eves = pl.DataFrame(
        {"date": sorted({v.training_eve for v in sources.served.values()}), "label": "training eve"}
    )
    layers = [
        lines,
        alt.Chart(eves)
        .mark_rule(strokeDash=[4, 4])
        .encode(x="date:T", tooltip=["label:N", "date:T"]),
    ]
    if not marks.is_empty():
        layers.append(
            alt.Chart(marks)
            .mark_rule(opacity=0.4)
            .encode(
                x="date:T",
                color=alt.Color("kind:N", title="mark"),
                tooltip=["date:T", "kind:N", "label:N"],
            )
        )
    st.altair_chart(alt.layer(*layers).resolve_scale(color="independent"), width="stretch")
    st.caption(
        "Every point is out-of-time: up to the latest backtest year, the prediction of the fold "
        "whose test year holds the month-end, from a model trained only on what was settled before "
        "that year; after it, the served model's own scores. Dashed: the served model's training "
        "eve. Thin lines: filings and legal events."
    )

st.subheader("Legal events")
events = sources.legal_events.filter(pl.col("krs") == krs).sort("event_date")
if events.is_empty():
    st.caption("None recorded.")
else:
    st.dataframe(events.drop("krs"), hide_index=True)

st.subheader("Filings")
filings = sources.filings.filter(pl.col("krs") == krs).sort("period_end", "submission_date")
st.dataframe(filings.drop("krs"), hide_index=True)

st.subheader("Statements, source-linked")
if facts.is_empty():
    st.caption("No canonical facts.")
else:
    periods = sorted(set(facts.get_column("period_end").to_list()), reverse=True)
    period = st.selectbox("Period ending", periods)
    shown = facts.filter(pl.col("period_end") == period)
    st.dataframe(shown, hide_index=True)
    documents = sorted(set(shown.get_column("source_document_hash").to_list()))
    document = st.selectbox("Stored document", documents)
    if document and st.button("Fetch the stored document"):
        content = data.stored_document(sources, document)
        st.download_button(
            "Save it",
            content,
            file_name=f"{document[:16]}{data.extension(content)}",
        )
