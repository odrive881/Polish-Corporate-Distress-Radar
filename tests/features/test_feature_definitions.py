"""Feature families (plan 0010 step D), on hand-computed synthetic entities."""

# polars's signatures reference types pyright cannot resolve; scoped to this module.
# pyright: reportUnknownMemberType=false

from __future__ import annotations

import calendar
from datetime import date
from decimal import Decimal

import polars as pl
import pytest

from distress_radar.features import feature_definitions as fd
from distress_radar.features.config import FeatureConfig, load_feature_set
from distress_radar.features.panel import PANEL_SCHEMA
from distress_radar.parsing.accounting_identities import RESTATEMENT_SCHEMA
from distress_radar.parsing.legal_events import LEGAL_EVENTS_SCHEMA

KRS = "0000000001"

FULL_2021 = {
    "total_assets": "1000",
    "current_assets": "600",
    "inventories": "100",
    "short_term_receivables": "300",
    "short_term_prepayments": "50",
    "equity": "400",
    "share_capital": "100",
    "supplementary_capital": "10",
    "reserve_capital": "5",
    "retained_result": "-40",
    "net_result_balance_sheet": "-25",
    "liabilities_and_provisions": "600",
    "long_term_liabilities": "200",
    "short_term_liabilities": "300",
    "revenue": "2000",
    "operating_result": "100",
    "financial_costs": "20",
    "net_result": "-25",
}
# Micro: no equity breakdown, no liability split, no operating result.
MICRO_2021 = {
    k: v
    for k, v in FULL_2021.items()
    if k
    not in {
        "supplementary_capital",
        "reserve_capital",
        "retained_result",
        "net_result_balance_sheet",
        "long_term_liabilities",
        "short_term_liabilities",
        "short_term_prepayments",
        "operating_result",
        "financial_costs",
    }
}


@pytest.fixture(scope="module")
def config() -> FeatureConfig:
    return load_feature_set("feature_set_v1")


def _version(
    config: FeatureConfig,
    period_end: date,
    known_from: date,
    values: dict[str, str],
    *,
    period_start: date | None = None,
    filled: bool = False,
    krs: str = KRS,
) -> list[dict[str, object]]:
    start = None if filled else (period_start or date(period_end.year, 1, 1))
    return [
        {
            "krs": krs,
            "period_end": period_end,
            "period_start": start,
            "fiscal_year": period_end.year,
            "input": name,
            "value": Decimal(values[name]) if name in values else None,
            "line_item": None,
            "known_from": known_from,
            "source_kind": "comparative" if filled else "filed",
            "document_ref": f"{period_end}-{known_from}",
            "source_member": "m",
            "source_document_hash": "0" * 64,
            "quality_grade": "pass",
        }
        for name in config.line_items.inputs
    ]


def _panel(*versions: list[dict[str, object]]) -> pl.DataFrame:
    return pl.DataFrame([r for v in versions for r in v], schema=PANEL_SCHEMA, orient="row")


def _month_ends(start: date, end: date) -> list[date]:
    out: list[date] = []
    y, m = start.year, start.month
    while (y, m) <= (end.year, end.month):
        out.append(date(y, m, calendar.monthrange(y, m)[1]))
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out


def _grid(*days: date, krs: str = KRS) -> pl.DataFrame:
    return pl.DataFrame([(krs, d) for d in days], schema=fd.GRID_SCHEMA, orient="row")


def _empty(schema: dict[str, pl.DataType | type[pl.DataType]]) -> pl.DataFrame:
    return pl.DataFrame(schema=schema)


def _inputs(
    panel: pl.DataFrame | None = None,
    filings: pl.DataFrame | None = None,
    restatements: pl.DataFrame | None = None,
    events: pl.DataFrame | None = None,
) -> fd.FeatureInputs:
    return fd.FeatureInputs(
        panel=panel if panel is not None else _empty(PANEL_SCHEMA),
        filings=filings if filings is not None else _empty(fd.STATEMENT_FILINGS_SCHEMA),
        restatements=restatements if restatements is not None else _empty(RESTATEMENT_SCHEMA),
        legal_events=events if events is not None else _empty(LEGAL_EVENTS_SCHEMA),
    )


def _values(frame: pl.DataFrame, day: date) -> dict[str, tuple[float, date]]:
    rows = frame.filter(pl.col("as_of_date") == day)
    return {r["feature"]: (r["value"], r["known_from"]) for r in rows.iter_rows(named=True)}


Y2021 = date(2021, 12, 31)
FILED = date(2022, 6, 30)


# --- financial, construction, tripwire ------------------------------------------------------------


def test_ratios_by_hand_for_a_full_form(config: FeatureConfig) -> None:
    panel = _panel(_version(config, Y2021, FILED, FULL_2021))
    grid = _grid(date(2022, 5, 31), date(2022, 7, 31))
    inputs = _inputs(panel)
    got = {
        **_values(fd.financial(grid, inputs, config), date(2022, 7, 31)),
        **_values(fd.construction(grid, inputs, config), date(2022, 7, 31)),
        **_values(fd.tripwire(grid, inputs, config), date(2022, 7, 31)),
    }
    expected = {
        "current_ratio": 2.0,
        "quick_ratio": 500 / 300,
        "debt_to_assets": 0.6,
        "equity_to_assets": 0.4,
        "roa": -0.025,
        "roe": -0.0625,
        "net_margin": -0.0125,
        "operating_margin": 0.05,
        "asset_turnover": 2.0,
        "working_capital_to_assets": 0.3,
        "operating_result_to_financial_costs": 5.0,
        "receivable_days": 54.75,
        "short_term_liability_days": 54.75,
        "prepayments_to_assets": 0.05,
        "art233_triggered": 0.0,  # a loss of 65 against 10 + 5 + 100/2 = 65: not greater
        "negative_equity": 0.0,
    }
    assert {k: v for k, (v, _) in got.items()} == pytest.approx(expected)
    assert {kf for _, kf in got.values()} == {FILED}
    # Nothing before the statement is known; growth and volatility need earlier years.
    assert _values(fd.financial(grid, inputs, config), date(2022, 5, 31)) == {}


def test_a_micro_filer_has_no_liquidity_and_no_art_233(config: FeatureConfig) -> None:
    panel = _panel(_version(config, Y2021, FILED, MICRO_2021))
    grid = _grid(date(2022, 7, 31))
    inputs = _inputs(panel)
    got = {
        **_values(fd.financial(grid, inputs, config), date(2022, 7, 31)),
        **_values(fd.tripwire(grid, inputs, config), date(2022, 7, 31)),
    }
    for absent in ("current_ratio", "quick_ratio", "art233_triggered", "operating_margin"):
        assert absent not in got
    assert got["roa"][0] == pytest.approx(-0.025)
    assert got["negative_equity"][0] == 0.0


def test_a_zero_denominator_gives_null(config: FeatureConfig) -> None:
    panel = _panel(_version(config, Y2021, FILED, {**FULL_2021, "short_term_liabilities": "0"}))
    got = _values(fd.financial(_grid(date(2022, 7, 31)), _inputs(panel), config), date(2022, 7, 31))
    assert "current_ratio" not in got and "quick_ratio" not in got
    assert got["debt_to_assets"][0] == pytest.approx(0.6)


@pytest.mark.parametrize(("net", "triggered"), [("-25", 0.0), ("-25.01", 1.0)])
def test_art_233_at_its_boundary(config: FeatureConfig, net: str, triggered: float) -> None:
    values = {**FULL_2021, "net_result_balance_sheet": net}
    panel = _panel(_version(config, Y2021, FILED, values))
    got = _values(fd.tripwire(_grid(date(2022, 7, 31)), _inputs(panel), config), date(2022, 7, 31))
    assert got["art233_triggered"][0] == triggered


def test_a_short_period_nulls_only_length_dependent_features(config: FeatureConfig) -> None:
    # A liquidation opening on 13 June: 163 days, outside the 335–396 band.
    panel = _panel(_version(config, date(2022, 6, 12), date(2022, 9, 1), FULL_2021))
    got = _values(fd.financial(_grid(date(2022, 9, 30)), _inputs(panel), config), date(2022, 9, 30))
    for dependent in ("roa", "roe", "asset_turnover"):
        assert dependent not in got
    assert got["net_margin"][0] == pytest.approx(-0.0125)  # a flow over a flow
    assert got["current_ratio"][0] == pytest.approx(2.0)  # stocks


def test_a_filled_period_has_no_known_length(config: FeatureConfig) -> None:
    panel = _panel(_version(config, Y2021, FILED, FULL_2021, filled=True))
    got = _values(fd.financial(_grid(date(2022, 7, 31)), _inputs(panel), config), date(2022, 7, 31))
    assert "roa" not in got
    assert got["current_ratio"][0] == pytest.approx(2.0)


def test_a_newer_null_never_shows_an_older_value(config: FeatureConfig) -> None:
    without = {k: v for k, v in FULL_2021.items() if k != "short_term_liabilities"}
    panel = _panel(
        _version(config, Y2021, FILED, FULL_2021),
        _version(config, Y2021, date(2022, 11, 3), without),
    )
    frame = fd.financial(_grid(date(2022, 10, 31), date(2022, 11, 30)), _inputs(panel), config)
    assert _values(frame, date(2022, 10, 31))["current_ratio"][0] == pytest.approx(2.0)
    assert "current_ratio" not in _values(frame, date(2022, 11, 30))


def test_growth_looks_back_by_time_not_by_count(config: FeatureConfig) -> None:
    def year(revenue: str, equity: str) -> dict[str, str]:
        return {**FULL_2021, "revenue": revenue, "equity": equity}

    panel = _panel(
        _version(config, date(2020, 12, 31), date(2021, 6, 30), year("1000", "300")),
        _version(config, Y2021, FILED, year("1200", "400")),
        # A split year: two short periods, each known later.
        _version(config, date(2022, 6, 12), date(2023, 3, 1), year("500", "350")),
        _version(
            config,
            date(2022, 12, 31),
            date(2023, 6, 30),
            year("700", "500"),
            period_start=date(2022, 6, 13),
        ),
    )
    frame = fd.financial(_grid(date(2022, 7, 31), date(2023, 7, 31)), _inputs(panel), config)
    before = _values(frame, date(2022, 7, 31))
    assert before["revenue_growth_1y"] == (pytest.approx(0.2), FILED)
    after = _values(frame, date(2023, 7, 31))
    # The latest period is short: a flow's growth is null. Equity is a stock, and "a year back"
    # is 2021-12-31, not the half-year that ended in June.
    assert "revenue_growth_1y" not in after
    assert after["equity_growth_1y"] == (pytest.approx(0.25), date(2023, 6, 30))


def test_volatility_is_the_sample_deviation_over_three_years(config: FeatureConfig) -> None:
    margins = {2019: "-100", 2020: "0", 2021: "100"}  # on revenue 2000: -0.05, 0, 0.05
    panel = _panel(
        *(
            _version(config, date(y, 12, 31), date(y + 1, 6, 30), {**FULL_2021, "net_result": n})
            for y, n in margins.items()
        )
    )
    got = _values(fd.financial(_grid(date(2022, 7, 31)), _inputs(panel), config), date(2022, 7, 31))
    assert got["net_margin_volatility_3y"] == (pytest.approx(0.05), FILED)


# --- filing behaviour ----------------------------------------------------------------------------


def _filings(*rows: tuple[date, date, bool, bool, bool, date | None]) -> pl.DataFrame:
    """(period_end, known_from, is_correction, is_pdf, is_quarantined, deleted_on)."""
    return pl.DataFrame(
        [
            {
                "krs": KRS,
                "period_start": date(end.year, 1, 1),
                "period_end": end,
                "known_from": known,
                "deleted_on": deleted,
                "is_correction": corr,
                "is_pdf": pdf,
                "is_quarantined": quarantined,
            }
            for end, known, corr, pdf, quarantined, deleted in rows
        ],
        schema=fd.STATEMENT_FILINGS_SCHEMA,
        orient="row",
    )


def test_missing_and_late_years_follow_the_statutory_deadlines(config: FeatureConfig) -> None:
    filings = _filings(
        (date(2018, 12, 31), date(2019, 7, 10), False, False, False, None),  # due 2019-07-15
        (date(2019, 12, 31), date(2020, 10, 20), False, False, False, None),  # due 2020-10-15
    )
    grid = _grid(date(2021, 9, 30), date(2021, 10, 31))
    frame = fd.filing(grid, _inputs(filings=filings), config)
    # 2020's deadline (2021-10-15, COVID-extended) has not passed on 30 September.
    september = _values(frame, date(2021, 9, 30))
    assert september["missing_years_3y"][0] == 0.0
    assert september["late_filings_3y"][0] == 1.0  # 2019, five days late
    october = _values(frame, date(2021, 10, 31))
    assert october["missing_years_3y"] == (1.0, date(2020, 10, 20))
    assert october["days_to_file_latest"] == (294.0, date(2020, 10, 20))


def test_filing_counts_and_flags(config: FeatureConfig) -> None:
    filings = _filings(
        (date(2019, 12, 31), date(2020, 6, 30), False, False, True, None),
        (date(2019, 12, 31), date(2020, 9, 1), True, False, False, None),
        (date(2020, 12, 31), date(2021, 6, 30), False, True, False, date(2021, 8, 1)),
    )
    frame = fd.filing(_grid(date(2021, 7, 31), date(2021, 8, 31)), _inputs(filings=filings), config)
    july = _values(frame, date(2021, 7, 31))
    assert july["corrections"][0] == 1.0
    assert july["statements_quarantined"][0] == 1.0
    assert july["latest_filed_as_pdf"] == (1.0, date(2021, 6, 30))
    # Deleted on 1 August: the PDF filing is no longer known.
    assert _values(frame, date(2021, 8, 31))["latest_filed_as_pdf"][0] == 0.0


def test_restatements_count_and_their_largest_line(config: FeatureConfig) -> None:
    panel = _panel(_version(config, date(2020, 12, 31), date(2021, 6, 30), FULL_2021))
    filings = _filings((date(2020, 12, 31), date(2021, 6, 30), False, False, False, None))
    base: dict[str, object] = {
        "krs": KRS,
        "fiscal_year": 2020,
        "period_start": date(2020, 1, 1),
        "period_end": date(2020, 12, 31),
        "restated_column": "prior_year",
        "original_document_hash": "0" * 64,
        "original_source_member": "m",
        "original_document_ref": "a",
        "restating_document_hash": "1" * 64,
        "restating_source_member": "m",
        "restating_document_ref": "b",
        "known_from": date(2022, 6, 30),
    }
    restatements = pl.DataFrame(
        [
            {
                **base,
                "line_item": "IS.COMP.B",
                "originally_reported_value": Decimal(500),
                "restated_value": Decimal(400),
            },
            {
                **base,
                "line_item": "IS.COMP.C",
                "originally_reported_value": Decimal(50),
                "restated_value": Decimal(80),
            },
        ],
        schema=RESTATEMENT_SCHEMA,
        orient="row",
    )
    frame = fd.filing(
        _grid(date(2022, 5, 31), date(2022, 7, 31)),
        _inputs(panel=panel, filings=filings, restatements=restatements),
        config,
    )
    assert _values(frame, date(2022, 5, 31))["restatement_max_to_assets"][0] == 0.0
    july = _values(frame, date(2022, 7, 31))
    assert july["restating_filings"][0] == 1.0
    assert july["restatement_max_to_assets"] == (pytest.approx(0.1), date(2022, 6, 30))


def test_no_filing_known_means_no_filing_features(config: FeatureConfig) -> None:
    filings = _filings((date(2021, 12, 31), FILED, False, False, False, None))
    frame = fd.filing(_grid(date(2022, 5, 31)), _inputs(filings=filings), config)
    assert frame.is_empty()


def test_statement_filings_join_parts_and_flags() -> None:
    filing_index = pl.DataFrame(
        [
            (
                KRS,
                "p1",
                "1",
                date(2017, 1, 1),
                date(2017, 12, 31),
                date(2020, 8, 10),
                None,
                False,
                "Bilans.pdf",
            ),
            (
                KRS,
                "p2",
                "1",
                date(2017, 1, 1),
                date(2017, 12, 31),
                date(2020, 8, 10),
                None,
                False,
                "RZiS.pdf",
            ),
            (
                KRS,
                "x1",
                "18",
                date(2018, 1, 1),
                date(2018, 12, 31),
                date(2020, 8, 14),
                None,
                False,
                "sf.xml",
            ),
            (KRS, "u1", "1", date(2016, 1, 1), date(2016, 12, 31), None, None, None, None),
            (
                KRS,
                "o1",
                "20",
                date(2018, 1, 1),
                date(2018, 12, 31),
                date(2020, 8, 14),
                None,
                False,
                "report.pdf",
            ),
        ],
        schema={
            "krs": pl.String,
            "document_ref": pl.String,
            "rdf_type_code": pl.String,
            "period_start": pl.Date,
            "period_end": pl.Date,
            "submission_date": pl.Date,
            "deleted_on": pl.Date,
            "is_correction": pl.Boolean,
            "file_name": pl.String,
        },
        orient="row",
    )
    status = pl.DataFrame(
        [(KRS, "x1", "quarantined")], schema=fd.PARSE_STATUS_COLUMNS, orient="row"
    )
    canonical = pl.DataFrame(
        schema={"krs": pl.String, "document_ref": pl.String, "quality_grade": pl.String}
    )
    out = fd.statement_filings(filing_index, status, canonical, ["18", "1"])
    assert out.select("period_end", "is_pdf", "is_quarantined").rows() == [
        (date(2017, 12, 31), True, False),  # two PDF parts, one filing; the undated 2016 is out
        (date(2018, 12, 31), False, True),
    ]


# --- registry dynamics and legal history ---------------------------------------------------------


def _events(*rows: tuple[str, str | None, date | None, date]) -> pl.DataFrame:
    """(event_type, source, event_date, known_from)."""
    return pl.DataFrame(
        [
            {
                "krs": KRS,
                "event_type": event_type,
                "source": source,
                "event_date": event_date,
                "known_from": known_from,
                "ends": [],
                "precludes_silent_exit": False,
            }
            for event_type, source, event_date, known_from in rows
        ],
        schema=LEGAL_EVENTS_SCHEMA,
        orient="row",
    )


def test_event_counts_by_window_and_key(config: FeatureConfig) -> None:
    events = _events(
        ("registered", "KRS", date(2015, 3, 1), date(2015, 3, 1)),
        ("board_changed", "KRS", date(2020, 5, 4), date(2020, 5, 4)),
        ("board_changed", "KRS", date(2021, 2, 1), date(2021, 2, 1)),
        # One order seen twice: in MSiG first, in the register later. One event.
        ("bankruptcy_petition_asset_security", "MSiG", date(2021, 3, 10), date(2021, 3, 20)),
        ("bankruptcy_petition_asset_security", "KRS", date(2021, 3, 10), date(2021, 9, 1)),
    )
    grid = _grid(date(2014, 12, 31), date(2019, 12, 31), date(2021, 4, 30), date(2022, 2, 28))
    inputs = _inputs(events=events)
    registry = fd.registry(grid, inputs, config)
    history = fd.legal_history(grid, inputs, config)
    assert registry.filter(pl.col("as_of_date") == date(2014, 12, 31)).is_empty()
    # Zero is known from the registration on.
    assert _values(registry, date(2019, 12, 31))["board_changes_12m"] == (0.0, date(2015, 3, 1))
    april = _values(registry, date(2021, 4, 30))
    assert april["board_changes_12m"] == (2.0, date(2021, 2, 1))
    # 2022-02-28: the May 2020 change has left the 12-month window, not the 36-month one.
    february = _values(registry, date(2022, 2, 28))
    assert february["board_changes_12m"][0] == 0.0
    assert february["board_changes_36m"] == (2.0, date(2021, 2, 1))
    assert _values(history, date(2021, 4, 30))["bankruptcy_petitions_ever"] == (
        1.0,
        date(2021, 3, 20),
    )
    assert _values(history, date(2022, 2, 28))["bankruptcy_petitions_ever"][0] == 1.0


def test_an_event_counts_from_its_publication_not_its_decision(config: FeatureConfig) -> None:
    events = _events(
        ("registered", "KRS", date(2015, 3, 1), date(2015, 3, 1)),
        ("curator_appointed", "KRS", date(2021, 1, 5), date(2022, 10, 1)),
    )
    frame = fd.registry(
        _grid(date(2022, 9, 30), date(2022, 10, 31)), _inputs(events=events), config
    )
    assert _values(frame, date(2022, 9, 30))["curators_ever"][0] == 0.0
    assert _values(frame, date(2022, 10, 31))["curators_ever"] == (1.0, date(2022, 10, 1))


# --- all families --------------------------------------------------------------------------------


def test_no_value_is_known_after_its_row(config: FeatureConfig) -> None:
    panel = _panel(
        _version(config, date(2020, 12, 31), date(2021, 6, 30), FULL_2021),
        _version(config, Y2021, FILED, FULL_2021),
    )
    filings = _filings(
        (date(2020, 12, 31), date(2021, 6, 30), False, False, False, None),
        (Y2021, FILED, False, False, False, None),
    )
    events = _events(("registered", "KRS", date(2015, 3, 1), date(2015, 3, 1)))
    grid = _grid(*_month_ends(date(2015, 1, 1), date(2023, 12, 31)))
    frame = fd.compute_features(grid, _inputs(panel, filings, events=events), config)
    assert frame.height > 0
    assert frame.filter(pl.col("known_from") > pl.col("as_of_date")).is_empty()
    assert set(frame.get_column("feature").unique()) <= {
        f.name for f in config.feature_set.features
    }
