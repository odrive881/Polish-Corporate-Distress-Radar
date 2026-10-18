"""Synthetic explorer sources: three companies, two served horizons, a backtest before the eve."""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

import polars as pl

from distress_radar.acquisition.raw_store import InMemoryObjectStore, raw_key
from distress_radar.explorer import data
from distress_radar.models.serving import ServedVersion

DAY = date(2026, 9, 30)
NOW = datetime(2026, 10, 16, tzinfo=UTC)
DOCUMENT = "a" * 64


def served(horizon: int) -> ServedVersion:
    return ServedVersion(
        name=f"distress_radar_{horizon}m",
        version="4",
        alias="incumbent",
        run_id="f" * 32,
        model="logistic_regression",
        horizon_months=horizon,
        test_year=2025,
        tags={"backtest": "backtest_v7", "label_set_hash": "c" * 64},
    )


def _scores() -> pl.DataFrame:
    rows: list[tuple[object, ...]] = [
        ("0000000001", DAY, 12, 0.02, None, None, '[{"feature": "roa", "contribution": 0.4}]'),
        ("0000000001", DAY, 24, 0.05, None, None, '[{"feature": "roa", "contribution": 0.3}]'),
        ("0000000002", DAY, 12, 0.04, None, None, None),
        ("0000000002", DAY, 24, None, "inputs incomplete", ["working_capital_to_assets"], None),
    ]
    return pl.DataFrame(
        # 0000000001's 12-month score has an interval (plan 0021); the others have none yet.
        [
            (
                *r,
                f"distress_radar_{r[2]}m",
                "4",
                NOW,
                *((0.01, 0.06) if r[:3] == ("0000000001", DAY, 12) else (None, None)),
            )
            for r in rows
        ],
        schema=list(data.SCORE_SCHEMA),
        orient="row",
    ).cast(data.SCORE_SCHEMA)  # type: ignore[arg-type]


def _predictions() -> pl.DataFrame:
    return pl.DataFrame(
        {
            "backtest": ["backtest_v7"] * 4 + ["backtest_v6"],
            "run": ["main", "main", "main", "no_regime", "main"],
            "model": ["logistic_regression"] * 5,
            "horizon_months": [12, 12, 24, 12, 12],
            "test_year": [2024, 2025, 2025, 2025, 2025],
            "krs": ["0000000001"] * 5,
            "as_of_date": [
                date(2024, 6, 30),
                date(2025, 6, 30),
                date(2025, 6, 30),
                date(2025, 6, 30),
                date(2025, 6, 30),
            ],
            "probability": [0.01, 0.015, 0.03, 0.9, 0.9],
        },
        schema_overrides={"horizon_months": pl.Int32, "test_year": pl.Int32},
    )


def make_sources(tmp_path: Path) -> data.Sources:
    canonical = tmp_path / "financial_statements_canonical"
    canonical.mkdir()
    pl.DataFrame(
        {
            "krs": ["0000000001", "0000000002"],
            "fiscal_year": [2025, 2025],
            "period_end": [date(2025, 12, 31)] * 2,
            "statement_type": ["balance_sheet"] * 2,
            "line_item": ["BS.A", "BS.A"],
            "column": ["current_year"] * 2,
            "value": [Decimal("100.00"), Decimal("5.00")],
            "quality_grade": ["pass", "pass"],
            "known_from": [date(2026, 6, 1)] * 2,
            "structure_version": ["small-2025-v1-3", "micro-2025-v1-3"],
            "source_document_hash": [DOCUMENT, "b" * 64],
            "source_member": ["SF.xml"] * 2,
            "source_element_path": ["/Bilans/Aktywa"] * 2,
        }
    ).write_parquet(canonical / "part.parquet")
    store = InMemoryObjectStore()
    store.put_if_absent(raw_key(DOCUMENT), b"PK\x03\x04zip", "application/zip")
    return data.Sources(
        served={12: served(12), 24: served(24)},
        entities=pl.DataFrame(
            {
                "krs": ["0000000001", "0000000002", "0000000003"],
                "name": [
                    "Alfa Budowa sp. z o.o.",
                    "Beta Instalacje sp. z o.o.",
                    "Gamma sp. z o.o.",
                ],
                "nip": ["1111111111", "2222222222", None],
                "regon": ["111111111", "222222222", None],
                "status": ["active"] * 3,
                "pkd_predominant": ["41.20.Z", "41.10.Z", "43.99.Z"],
            }
        ),
        scores=_scores(),
        exclusions=pl.DataFrame(
            {
                "krs": ["0000000003", "0000000003"],
                "as_of_date": [DAY, DAY],
                "horizon_months": [12, 24],
                "reason": ["rdf_not_searched"] * 2,
                "model_name": ["distress_radar_12m", "distress_radar_24m"],
                "model_version": ["4", "4"],
            },
            schema_overrides={"horizon_months": pl.Int32},
        ),
        filings=pl.DataFrame(
            {
                "krs": ["0000000001"],
                "document_ref": ["r1"],
                "rdf_type_name": ["Roczne sprawozdanie finansowe"],
                "period_start": [date(2025, 1, 1)],
                "period_end": [date(2025, 12, 31)],
                "submission_date": [date(2026, 6, 1)],
                "is_correction": [False],
                "sha256": [DOCUMENT],
            }
        ),
        watchlists=pl.DataFrame(
            {"name": ["budowlane"], "krs": ["0000000001"], "added_at": [NOW]},
            schema=data.WATCHLIST_SCHEMA,  # type: ignore[arg-type]
        ),
        predictions=data.served_predictions(_predictions(), {12: served(12), 24: served(24)}),
        legal_events=pl.DataFrame(
            {
                "krs": ["0000000001", "0000000001"],
                "event_type": ["registered", "bankruptcy_petition_filed"],
                "outcome_class": [None, "bankruptcy"],
                "event_date": [date(2015, 1, 1), date(2026, 3, 1)],
                "known_from": [date(2015, 1, 1), date(2026, 3, 5)],
                "source": ["krs", "msig"],
            }
        ),
        canonical_dir=canonical,
        connect=_no_connection,
        store=store,
    )


def _no_connection():  # type: ignore[no-untyped-def]
    raise AssertionError("no Postgres connection expected")
