"""Synthetic `feature_store` and label-set frames for the modelling tests (plan 0012 step C)."""

# polars's signatures reference types pyright cannot resolve; scoped to this module.
# pyright: reportUnknownMemberType=false

from __future__ import annotations

import calendar
from datetime import date

import polars as pl

from distress_radar.labels import OUTCOME_LABELS_SCHEMA, label_set_hash

VERSION, HASH = "feature_set_vt", "f" * 64


def month_ends(first: date, last: date) -> list[date]:
    out: list[date] = []
    y, m = first.year, first.month
    while (y, m) <= (last.year, last.month):
        out.append(date(y, m, calendar.monthrange(y, m)[1]))
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out


def features(rows: list[tuple[str, date, float | None]]) -> pl.DataFrame:
    """`feature_store` rows with one feature, `roa`, and its companion."""
    return pl.DataFrame(
        [
            {
                "krs": krs,
                "as_of_date": day,
                "as_of_year": day.year,
                "feature_set_version": VERSION,
                "feature_set_hash": HASH,
                "roa": roa,
                "roa__known_from": None if roa is None else date(day.year, 1, 1),
            }
            for krs, day, roa in rows
        ],
        schema={
            "krs": pl.String,
            "as_of_date": pl.Date,
            "as_of_year": pl.Int32,
            "feature_set_version": pl.String,
            "feature_set_hash": pl.String,
            "roa": pl.Float64,
            "roa__known_from": pl.Date,
        },
    )


Label = tuple[str, date, int, str | None, date | None, bool]  # krs, as_of, h, class, event, regime


def labels(rows: list[Label]) -> pl.DataFrame:
    """A label set: `outcome_class` None means censored."""
    frame = pl.DataFrame(
        [
            {
                "krs": krs,
                "as_of_date": day,
                "horizon_months": h,
                "outcome_class": cls,
                "censored": cls is None,
                "event_date": event,
                "event_known_from": event,
                "trigger_event_type": None,
                "proceeding_id": None,
                "proceeding_id_note": None,
                "regime_flag": regime,
                "source_era": "krs_msig",
                "cutoff_date": date(2026, 9, 23),
                "label_version": "outcome_labels_vt",
            }
            for krs, day, h, cls, event, regime in rows
        ],
        schema=OUTCOME_LABELS_SCHEMA,
    )
    return frame.with_columns(pl.lit(label_set_hash(frame)).alias("label_set_hash"))
