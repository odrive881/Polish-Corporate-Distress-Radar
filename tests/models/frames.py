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


# The ratios every configured model reads: the regression's, Z'''s and the Poznań model's.
PANEL_RATIOS = (
    "equity_to_assets",
    "working_capital_to_assets",
    "roa",
    "asset_turnover",
    "retained_earnings_to_assets",
    "ebit_to_assets",
    "equity_to_liabilities",
    "quick_ratio",
    "long_term_capital_to_assets",
    "sales_margin",
)


def _months_later(day: date, months: int) -> date:
    n = day.year * 12 + day.month - 1 + months
    first = date(n // 12, n % 12 + 1, 1)
    return month_ends(first, first)[0]


def panel(entities: int, seed: int) -> tuple[pl.DataFrame, pl.DataFrame]:
    """A synthetic `feature_store` and label set: every third entity fails, on a date spread
    over 2017-2024, and its ratios sag in the two years before. 12- and 24-month labels."""
    import numpy as np  # local: the other helpers do not need it

    rng = np.random.default_rng(seed)
    days = month_ends(date(2016, 1, 31), date(2025, 12, 31))
    feature_rows: list[dict[str, object]] = []
    label_rows: list[Label] = []
    for i in range(entities):
        krs = f"{i + 1:010d}"
        event = date(2017 + (i // 3) % 8, 1 + i % 12, 15) if i % 3 == 0 else None
        for day in days:
            if event is not None and day >= event:
                break
            sag = 0.0
            if event is not None and (event - day).days < 730:
                sag = 0.4 * (1 - (event - day).days / 730)
            row: dict[str, object] = {
                "krs": krs,
                "as_of_date": day,
                "as_of_year": day.year,
                "feature_set_version": VERSION,
                "feature_set_hash": HASH,
            }
            for name in PANEL_RATIOS:
                row[name] = float(0.3 - sag + rng.normal(0, 0.1))
                row[f"{name}__known_from"] = date(day.year, 1, 1)
            feature_rows.append(row)
            for horizon in (12, 24):
                closes = _months_later(day, horizon)
                hit = event is not None and day < event <= closes
                regime = day <= date(2021, 12, 31) and closes >= date(2020, 1, 1)
                label_rows.append(
                    (
                        krs,
                        day,
                        horizon,
                        "bankruptcy" if hit else "alive",
                        event if hit else None,
                        regime,
                    )
                )
    schema: dict[str, pl.DataType | type[pl.DataType]] = {
        "krs": pl.String,
        "as_of_date": pl.Date,
        "as_of_year": pl.Int32,
        "feature_set_version": pl.String,
        "feature_set_hash": pl.String,
    }
    for name in PANEL_RATIOS:
        schema.update({name: pl.Float64, f"{name}__known_from": pl.Date})
    return pl.DataFrame(feature_rows, schema=schema), labels(label_rows)
