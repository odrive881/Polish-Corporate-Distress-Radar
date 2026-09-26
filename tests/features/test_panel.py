"""The point-in-time financial panel (plan 0010 step C), on synthetic statements."""

# polars's signatures reference types pyright cannot resolve; scoped to this module.
# pyright: reportUnknownMemberType=false

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

import polars as pl
import pytest

from distress_radar.features.config import LineItems, load_line_items
from distress_radar.features.panel import (
    FILINGS_SCHEMA,
    Panel,
    PanelError,
    build_panel,
)
from distress_radar.parsing.mapping_engine import CANONICAL_COLUMNS

KRS = "0000000001"


@pytest.fixture(scope="module")
def line_items() -> LineItems:
    return load_line_items("line_items_v1")


@dataclass
class Filing:
    """One synthetic statement file: its current-year and prior-year figures."""

    ref: str
    year: int
    known_from: date
    current: dict[str, str]
    prior: dict[str, str] = field(default_factory=dict[str, str])
    grade: str = "pass"
    is_correction: bool = False
    period_start: date | None = None
    period_end: date | None = None
    deleted_on: date | None = None


def _frames(*filings: Filing) -> tuple[pl.DataFrame, pl.DataFrame]:
    rows: list[dict[str, object]] = []
    for f in filings:
        start = f.period_start or date(f.year, 1, 1)
        end = f.period_end or date(f.year, 12, 31)
        for column, figures in (("current_year", f.current), ("prior_year", f.prior)):
            for code, value in figures.items():
                rows.append(
                    {
                        "krs": KRS,
                        "nip": None,
                        "regon": None,
                        "fiscal_year": end.year,
                        "period_start": start,
                        "period_end": end,
                        "line_item": code,
                        "value": Decimal(value),
                        "statement_type": "balance_sheet"
                        if code.startswith("BS")
                        else "income_statement",
                        "variant": "n/a" if code.startswith("BS") else "comparative",
                        "column": column,
                        "structure_version": "full-2018-v1-2",
                        "source_document_hash": f.ref.ljust(64, "0"),
                        "source_member": f"zip:{f.ref}.xml",
                        "source_element_path": code,
                        "document_ref": f.ref,
                        "known_from": f.known_from,
                        "ingestion_run_id": "run-1",
                        "quality_grade": f.grade,
                    }
                )
    canonical = pl.DataFrame(rows, schema=CANONICAL_COLUMNS, orient="row")
    filings_frame = pl.DataFrame(
        [
            {
                "krs": KRS,
                "document_ref": f.ref,
                "is_correction": f.is_correction,
                "deleted_on": f.deleted_on,
            }
            for f in filings
        ],
        schema=FILINGS_SCHEMA,
        orient="row",
    )
    return canonical, filings_frame


def _panel(line_items: LineItems, *filings: Filing, include_quarantined: bool = False) -> Panel:
    canonical, filings_frame = _frames(*filings)
    return build_panel(
        canonical, filings_frame, line_items, include_quarantined=include_quarantined
    )


def _history(
    panel: Panel, period_end: date, input_name: str = "total_assets"
) -> list[tuple[date, str, object]]:
    """(known_from, source_kind, value) of each version of one period's input."""
    rows = panel.frame.filter(
        (pl.col("period_end") == period_end) & (pl.col("input") == input_name)
    ).sort("known_from")
    return [(r["known_from"], r["source_kind"], r["value"]) for r in rows.iter_rows(named=True)]


def _assets(value: str) -> dict[str, str]:
    return {"BS.ASSETS": value}


Y2021 = date(2021, 12, 31)
Y2022 = date(2022, 12, 31)


def test_a_correction_replaces_its_original_from_its_own_date(line_items: LineItems) -> None:
    panel = _panel(
        line_items,
        Filing("orig", 2021, date(2022, 7, 1), _assets("100")),
        Filing("corr", 2021, date(2022, 11, 3), _assets("90"), is_correction=True),
    )
    assert _history(panel, Y2021) == [
        (date(2022, 7, 1), "filed", Decimal("100.00")),
        (date(2022, 11, 3), "correction", Decimal("90.00")),
    ]


def test_a_comparative_fills_a_missing_year_from_the_later_filing(line_items: LineItems) -> None:
    panel = _panel(
        line_items,
        Filing("y22", 2022, date(2023, 7, 1), _assets("120"), prior=_assets("100")),
    )
    assert _history(panel, Y2021) == [(date(2023, 7, 1), "comparative", Decimal("100.00"))]
    [row] = panel.frame.filter(
        (pl.col("period_end") == Y2021) & (pl.col("input") == "total_assets")
    ).iter_rows(named=True)
    assert row["period_start"] is None and row["fiscal_year"] == 2021
    assert row["document_ref"] == "y22" and row["line_item"] == "BS.ASSETS"


def test_a_restated_comparative_never_replaces_a_filed_year(line_items: LineItems) -> None:
    panel = _panel(
        line_items,
        Filing("y21", 2021, date(2022, 7, 1), _assets("100")),
        Filing("y22", 2022, date(2023, 7, 1), _assets("120"), prior=_assets("95")),
    )
    assert _history(panel, Y2021) == [(date(2022, 7, 1), "filed", Decimal("100.00"))]


def test_a_filed_statement_replaces_a_comparative_once_known(line_items: LineItems) -> None:
    # A late filing: the next year's comparative was known first.
    panel = _panel(
        line_items,
        Filing("y22", 2022, date(2023, 7, 1), _assets("120"), prior=_assets("95")),
        Filing("y21", 2021, date(2023, 9, 1), _assets("100")),
    )
    assert _history(panel, Y2021) == [
        (date(2023, 7, 1), "comparative", Decimal("95.00")),
        (date(2023, 9, 1), "filed", Decimal("100.00")),
    ]


def test_a_quarantined_statement_leaves_its_year_empty(line_items: LineItems) -> None:
    panel = _panel(
        line_items, Filing("y21", 2021, date(2022, 7, 1), _assets("100"), grade="quarantined")
    )
    assert panel.frame.is_empty()
    assert panel.excluded.select("document_ref", "role", "reason").rows() == [
        ("y21", "comparative", "quarantined"),
        ("y21", "filed", "quarantined"),
    ]


def test_a_quarantined_year_can_still_be_filled_by_the_next_filing(line_items: LineItems) -> None:
    panel = _panel(
        line_items,
        Filing("y21", 2021, date(2022, 7, 1), _assets("100"), grade="quarantined"),
        Filing("y22", 2022, date(2023, 7, 1), _assets("120"), prior=_assets("98")),
    )
    assert _history(panel, Y2021) == [(date(2023, 7, 1), "comparative", Decimal("98.00"))]


def test_with_the_switch_on_a_quarantined_statement_fills_its_year(line_items: LineItems) -> None:
    filings = (
        Filing("y21", 2021, date(2022, 7, 1), _assets("100"), grade="quarantined"),
        Filing("y22", 2022, date(2023, 7, 1), _assets("120"), prior=_assets("98")),
    )
    panel = _panel(line_items, *filings, include_quarantined=True)
    assert _history(panel, Y2021) == [(date(2022, 7, 1), "filed", Decimal("100.00"))]
    grades = panel.frame.filter(pl.col("period_end") == Y2021).get_column("quality_grade")
    assert set(grades.to_list()) == {"quarantined"}
    assert "quarantined" not in panel.excluded.get_column("reason").to_list()


def test_a_missing_input_is_null_and_hides_the_older_figure(line_items: LineItems) -> None:
    panel = _panel(
        line_items,
        Filing(
            "orig",
            2021,
            date(2022, 7, 1),
            {"BS.ASSETS": "100", "BS.EQUITY_LIABILITIES.B.III": "40"},
        ),
        Filing("corr", 2021, date(2022, 11, 3), _assets("90"), is_correction=True),
    )
    assert _history(panel, Y2021, "short_term_liabilities") == [
        (date(2022, 7, 1), "filed", Decimal("40.00")),
        (date(2022, 11, 3), "correction", None),
    ]
    # Every input has a row in every version, so the ASOF join never reaches past one.
    per_version = panel.frame.group_by("known_from").len().get_column("len").unique().to_list()
    assert per_version == [len(line_items.inputs)]


def test_the_income_statement_input_comes_from_whichever_code_is_filed(
    line_items: LineItems,
) -> None:
    panel = _panel(
        line_items,
        Filing("full", 2021, date(2022, 7, 1), {"BS.ASSETS": "100", "IS.COMP.L": "-5"}),
        Filing("small", 2022, date(2023, 7, 1), {"BS.ASSETS": "90", "IS.COMP.MALA.J": "-7"}),
    )
    net = panel.frame.filter(pl.col("input") == "net_result").sort("period_end")
    assert net.select("line_item", "value").rows() == [
        ("IS.COMP.L", Decimal("-5.00")),
        ("IS.COMP.MALA.J", Decimal("-7.00")),
    ]


def test_a_split_year_is_two_periods(line_items: LineItems) -> None:
    # A liquidation opening on 13 June closes one period and starts the next, in one year.
    first_end = date(2022, 6, 12)
    panel = _panel(
        line_items,
        Filing("h1", 2022, date(2022, 9, 1), _assets("80"), period_end=first_end),
        Filing(
            "h2",
            2022,
            date(2023, 7, 1),
            _assets("60"),
            prior=_assets("81"),
            period_start=date(2022, 6, 13),
        ),
    )
    assert _history(panel, first_end) == [(date(2022, 9, 1), "filed", Decimal("80.00"))]
    assert _history(panel, Y2022) == [(date(2023, 7, 1), "filed", Decimal("60.00"))]


def test_a_comparative_without_assets_fills_nothing(line_items: LineItems) -> None:
    # The first year's prior column describes the time before the entity existed.
    panel = _panel(
        line_items, Filing("first", 2021, date(2022, 7, 1), _assets("50"), prior=_assets("0"))
    )
    assert _history(panel, date(2020, 12, 31)) == []
    assert panel.excluded.select("role", "reason").rows() == [
        ("comparative", "comparative_without_assets")
    ]


def test_an_original_and_its_correction_on_one_day_are_one_version(line_items: LineItems) -> None:
    panel = _panel(
        line_items,
        Filing("orig", 2021, date(2022, 7, 1), _assets("100")),
        Filing("corr", 2021, date(2022, 7, 1), _assets("90"), is_correction=True),
    )
    assert _history(panel, Y2021) == [(date(2022, 7, 1), "correction", Decimal("90.00"))]


def test_two_originals_on_one_day_are_excluded_not_guessed(line_items: LineItems) -> None:
    panel = _panel(
        line_items,
        Filing("a", 2021, date(2022, 7, 1), _assets("100")),
        Filing("b", 2021, date(2022, 7, 1), _assets("90")),
    )
    assert _history(panel, Y2021) == []
    clashes = panel.excluded.filter(pl.col("role") == "filed")
    assert set(clashes.select("document_ref", "reason").rows()) == {
        ("a", "same_day_filings"),
        ("b", "same_day_filings"),
    }


def test_a_statement_without_a_filing_row_is_an_error(line_items: LineItems) -> None:
    canonical, filings = _frames(Filing("y21", 2021, date(2022, 7, 1), _assets("100")))
    with pytest.raises(PanelError, match="no filing_index row"):
        build_panel(canonical, filings.clear(), line_items, include_quarantined=False)


def test_the_panel_ignores_input_order(line_items: LineItems) -> None:
    canonical, filings = _frames(
        Filing("y21", 2021, date(2022, 7, 1), _assets("100")),
        Filing("y22", 2022, date(2023, 7, 1), _assets("120"), prior=_assets("95")),
        Filing("corr", 2022, date(2023, 9, 1), _assets("121"), is_correction=True),
    )
    first = build_panel(canonical, filings, line_items, include_quarantined=False)
    again = build_panel(
        canonical.sample(fraction=1.0, shuffle=True, seed=7),
        filings.reverse(),
        line_items,
        include_quarantined=False,
    )
    assert first.frame.equals(again.frame) and first.excluded.equals(again.excluded)


# --- deletions (plan 0010 owner decision 5 for steps F-H) ------------------------------------------


def test_a_deleted_statement_leaves_its_period_withdrawn(line_items: LineItems) -> None:
    panel = _panel(
        line_items,
        Filing("y21", 2021, date(2022, 7, 1), _assets("100"), deleted_on=date(2022, 9, 1)),
    )
    assert _history(panel, Y2021) == [
        (date(2022, 7, 1), "filed", Decimal("100.00")),
        (date(2022, 9, 1), "withdrawn", None),
    ]


def test_a_deleted_correction_gives_the_period_back_to_the_original(
    line_items: LineItems,
) -> None:
    panel = _panel(
        line_items,
        Filing("orig", 2021, date(2022, 7, 1), _assets("100")),
        Filing(
            "corr",
            2021,
            date(2022, 8, 1),
            _assets("90"),
            is_correction=True,
            deleted_on=date(2022, 10, 1),
        ),
    )
    assert _history(panel, Y2021) == [
        (date(2022, 7, 1), "filed", Decimal("100.00")),
        (date(2022, 8, 1), "correction", Decimal("90.00")),
        (date(2022, 10, 1), "filed", Decimal("100.00")),
    ]


def test_a_deleted_statement_takes_its_prior_year_fill_with_it(line_items: LineItems) -> None:
    panel = _panel(
        line_items,
        Filing(
            "y22",
            2022,
            date(2023, 7, 1),
            _assets("120"),
            prior=_assets("95"),
            deleted_on=date(2023, 8, 1),
        ),
    )
    assert _history(panel, Y2021) == [
        (date(2023, 7, 1), "comparative", Decimal("95.00")),
        (date(2023, 8, 1), "withdrawn", None),
    ]


def test_a_deletion_does_not_withdraw_a_statement_filed_after_it(line_items: LineItems) -> None:
    panel = _panel(
        line_items,
        Filing("a", 2021, date(2022, 7, 1), _assets("100"), deleted_on=date(2022, 9, 1)),
        Filing("b", 2021, date(2022, 9, 1), _assets("101")),  # refiled on the deletion day
    )
    assert _history(panel, Y2021) == [
        (date(2022, 7, 1), "filed", Decimal("100.00")),
        (date(2022, 9, 1), "filed", Decimal("101.00")),
    ]


def test_a_statement_deleted_when_filed_never_speaks(line_items: LineItems) -> None:
    panel = _panel(
        line_items,
        Filing("y21", 2021, date(2022, 7, 1), _assets("100"), deleted_on=date(2022, 7, 1)),
    )
    assert _history(panel, Y2021) == []
    assert ("filed", "deleted_when_filed") in panel.excluded.select("role", "reason").rows()
