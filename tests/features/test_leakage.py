"""The leakage tests (AGENT_SPEC §9.1; plan 0010 step F). BLOCKING: never skip or weaken them.

A synthetic warehouse with the classic traps, built in the test and run through the real
assembly code: a statement filed the day after a month-end, a registry change decided before a
month-end but entered after it, a correction filed later, a statement deleted after it was
filed, and a restatement. Both checks of `features.leakage` must find nothing, and deliberately
leaky families must fail them: a check that cannot fail proves nothing.
"""

# polars's signatures reference types pyright cannot resolve; scoped to this module.
# pyright: reportUnknownMemberType=false

from __future__ import annotations

import calendar
from dataclasses import replace
from datetime import date
from decimal import Decimal

import polars as pl
import pytest

from distress_radar.features import feature_definitions as fd
from distress_radar.features.asof_assembly import (
    FILING_INDEX_SCHEMA,
    PARSE_STATUS_SCHEMA,
    FeatureSources,
    assemble,
    feature_inputs,
    widen,
)
from distress_radar.features.config import Family, FeatureConfig, load_feature_set
from distress_radar.features.leakage import (
    FamilyFn,
    known_from_violations,
    known_on,
    truncation_differences,
)
from distress_radar.parsing.accounting_identities import RESTATEMENT_SCHEMA
from distress_radar.parsing.legal_events import LEGAL_EVENTS_SCHEMA
from distress_radar.parsing.mapping_engine import CANONICAL_COLUMNS

A, B = "0000000001", "0000000002"  # A files the full form, B the micro form

FULL = {
    "total_assets": 1000,
    "current_assets": 600,
    "inventories": 100,
    "short_term_receivables": 300,
    "short_term_prepayments": 50,
    "equity": 400,
    "share_capital": 100,
    "supplementary_capital": 10,
    "reserve_capital": 5,
    "retained_result": -40,
    "net_result_balance_sheet": -25,
    "liabilities_and_provisions": 600,
    "long_term_liabilities": 200,
    "short_term_liabilities": 300,
    "revenue": 2000,
    "operating_result": 100,
    "financial_costs": 20,
    "net_result": -25,
}
MICRO = {"total_assets": 500, "current_assets": 300, "equity": 100, "revenue": 800, "net_result": 5}


def _scaled(figures: dict[str, int], factor: str) -> dict[str, Decimal]:
    return {k: Decimal(v) * Decimal(factor) for k, v in figures.items()}


# (krs, document_ref, fiscal year, submitted, correction, deleted, current, prior)
Statement = tuple[str, str, int, date, bool, date | None, dict[str, Decimal], dict[str, Decimal]]
STATEMENTS: list[Statement] = [
    # Trap: filed the day after the 2021-06-30 month-end.
    (A, "a20", 2020, date(2021, 7, 1), False, None, _scaled(FULL, "1"), _scaled(FULL, "0.9")),
    # On a month-end: known that day.
    (A, "a21", 2021, date(2022, 6, 30), False, None, _scaled(FULL, "1.1"), _scaled(FULL, "1")),
    # Trap: a correction filed later.
    (A, "a21c", 2021, date(2022, 11, 3), True, None, _scaled(FULL, "1.2"), _scaled(FULL, "1")),
    # Trap: deleted after it was filed; the 2021 correction speaks for the latest year again.
    (
        A,
        "a22",
        2022,
        date(2023, 7, 10),
        False,
        date(2023, 9, 15),
        _scaled(FULL, "1.3"),
        _scaled(FULL, "1.25"),
    ),
    # Late; its prior-year column fills the 2022 period the deletion emptied.
    (A, "a23", 2023, date(2024, 10, 20), False, None, _scaled(FULL, "1.4"), _scaled(FULL, "1.3")),
    (B, "b21", 2021, date(2022, 3, 15), False, None, _scaled(MICRO, "1"), _scaled(MICRO, "0.8")),
    (B, "b22", 2022, date(2023, 12, 1), False, None, _scaled(MICRO, "0.7"), _scaled(MICRO, "1")),
]

# (krs, event type, stage, decision date, known_from, removed_on)
Event = tuple[str, str, str, date | None, date, date | None]
EVENTS: list[Event] = [
    (A, "registered", "signal", date(2010, 3, 1), date(2010, 3, 15), None),
    # Trap: decided before the 2022-06-30 and 2022-07-31 month-ends, entered after both.
    (A, "board_changed", "signal", date(2022, 5, 10), date(2022, 8, 15), None),
    # Removed from the register later: the removal is a later fact.
    (A, "office_moved", "signal", date(2023, 1, 20), date(2023, 2, 1), date(2024, 5, 1)),
    (A, "arrears_enforcement_started", "signal", None, date(2024, 3, 5), None),
    (B, "registered", "signal", date(2015, 6, 1), date(2015, 6, 10), None),
    # Trap: a petition published long after its date.
    (B, "bankruptcy_petition", "petition", date(2023, 1, 12), date(2023, 10, 2), None),
    (B, "bankruptcy_petition_dismissed", "closing", date(2023, 11, 20), date(2024, 1, 8), None),
]


def _canonical(config: FeatureConfig) -> pl.DataFrame:
    rows: list[dict[str, object]] = []
    for krs, ref, year, filed, _, _, current, prior in STATEMENTS:
        micro = krs == B
        for column, figures in (("current_year", current), ("prior_year", prior)):
            for name, value in figures.items():
                codes = config.line_items.inputs[name]
                code = (
                    next(c for c in codes if "MIKRO" in c)
                    if micro
                    and name
                    in {
                        "revenue",
                        "net_result",
                    }
                    else codes[0]
                )
                rows.append(
                    {
                        "krs": krs,
                        "fiscal_year": year,
                        "period_start": date(year, 1, 1),
                        "period_end": date(year, 12, 31),
                        "line_item": code,
                        "value": value.quantize(Decimal("0.01")),
                        "statement_type": "balance_sheet"
                        if code.startswith("BS")
                        else "income_statement",
                        "variant": "n/a" if code.startswith("BS") else "comparative",
                        "column": column,
                        "structure_version": "micro-2018-v1-2" if micro else "full-2018-v1-2",
                        "source_document_hash": ref.ljust(64, "0"),
                        "source_member": f"zip:{ref}.xml",
                        "source_element_path": code,
                        "document_ref": ref,
                        "known_from": filed,
                        "ingestion_run_id": "run-1",
                        "quality_grade": "pass",
                    }
                )
    return pl.DataFrame(rows, schema=CANONICAL_COLUMNS, orient="row")


def _filing_index() -> pl.DataFrame:
    rows = [
        (krs, ref, "18", date(y, 1, 1), date(y, 12, 31), filed, deleted, corr, f"{ref}.xml")
        for krs, ref, y, filed, corr, deleted, _, _ in STATEMENTS
    ]
    # An auditor's report (not a statement), and a statement row never expanded (no date).
    rows.append((A, "a21-audit", "20", None, None, date(2022, 6, 30), None, False, "r.pdf"))
    rows.append((B, "b23", "18", date(2023, 1, 1), date(2023, 12, 31), None, None, None, None))
    return pl.DataFrame(rows, schema=FILING_INDEX_SCHEMA, orient="row")


def _parse_status() -> pl.DataFrame:
    return pl.DataFrame(
        [(krs, ref, "parsed") for krs, ref, *_ in STATEMENTS],
        schema=PARSE_STATUS_SCHEMA,
        orient="row",
    )


def _restatements() -> pl.DataFrame:
    # a22's prior-year column restates the 2021 correction's revenue.
    row = {
        "krs": A,
        "fiscal_year": 2021,
        "period_start": date(2021, 1, 1),
        "period_end": date(2021, 12, 31),
        "line_item": "IS.COMP.A",
        "restated_column": "prior_year",
        "originally_reported_value": Decimal("2400.00"),
        "restated_value": Decimal("2500.00"),
        "original_document_hash": "a21c".ljust(64, "0"),
        "original_source_member": "zip:a21c.xml",
        "original_document_ref": "a21c",
        "restating_document_hash": "a22".ljust(64, "0"),
        "restating_source_member": "zip:a22.xml",
        "restating_document_ref": "a22",
        "known_from": date(2023, 7, 10),
    }
    return pl.DataFrame([row], schema=RESTATEMENT_SCHEMA, orient="row")


def _legal_events() -> pl.DataFrame:
    rows = [
        {
            "krs": krs,
            "event_type": event_type,
            "stage": stage,
            "ends": [],
            "precludes_silent_exit": False,
            "event_date": decided,
            "known_from": known,
            "removed_on": removed,
            "source": "KRS",
            "source_element_path": f"{event_type}-{known}",
            "event_year": (decided or known).year,
        }
        for krs, event_type, stage, decided, known, removed in EVENTS
    ]
    return pl.DataFrame(rows, schema=LEGAL_EVENTS_SCHEMA, orient="row")


def _month_ends(first: date, last: date) -> list[date]:
    out: list[date] = []
    y, m = first.year, first.month
    while (y, m) <= (last.year, last.month):
        out.append(date(y, m, calendar.monthrange(y, m)[1]))
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out


@pytest.fixture(scope="module")
def config() -> FeatureConfig:
    return load_feature_set("feature_set_v1")


@pytest.fixture(scope="module")
def sources(config: FeatureConfig) -> FeatureSources:
    return FeatureSources(
        canonical=_canonical(config),
        restatements=_restatements(),
        legal_events=_legal_events(),
        filing_index=_filing_index(),
        parse_status=_parse_status(),
    )


@pytest.fixture(scope="module")
def grid() -> pl.DataFrame:
    days = _month_ends(date(2021, 1, 31), date(2025, 6, 30))
    return pl.DataFrame(
        [(krs, d) for krs in (A, B) for d in days], schema=fd.GRID_SCHEMA, orient="row"
    )


@pytest.fixture(scope="module")
def inputs(sources: FeatureSources, config: FeatureConfig) -> fd.FeatureInputs:
    return feature_inputs(sources, config)[0]


@pytest.fixture(scope="module")
def store(grid: pl.DataFrame, inputs: fd.FeatureInputs, config: FeatureConfig) -> pl.DataFrame:
    return assemble(grid, fd.compute_features(grid, inputs, config), config)


def _row(store: pl.DataFrame, krs: str, day: date) -> dict[str, object]:
    return store.filter((pl.col("krs") == krs) & (pl.col("as_of_date") == day)).row(0, named=True)


# --- the two checks ------------------------------------------------------------------------------


def test_every_feature_is_known_by_its_row(store: pl.DataFrame, config: FeatureConfig) -> None:
    """§9.1 as written, on every row and every feature column."""
    assert known_from_violations(store, config).is_empty()


def test_no_family_reads_a_fact_published_after_its_row(
    grid: pl.DataFrame, sources: FeatureSources, config: FeatureConfig
) -> None:
    """Per family: recomputed from only what was public by each date, nothing changes."""
    differences = truncation_differences(grid, sources, config)
    assert differences.is_empty(), differences.head(20)


def test_the_checks_are_not_vacuous(
    grid: pl.DataFrame, inputs: fd.FeatureInputs, config: FeatureConfig
) -> None:
    """Every family computes something on this warehouse, so the checks above see it."""
    for name, compute in fd.FAMILIES.items():
        assert compute(grid, inputs, config).height > 0, name


# --- the traps -----------------------------------------------------------------------------------


def test_a_statement_filed_the_day_after_is_not_known(store: pl.DataFrame) -> None:
    before = _row(store, A, date(2021, 6, 30))
    after = _row(store, A, date(2021, 7, 31))
    assert before["current_ratio"] is None and before["days_to_file_latest"] is None
    assert after["current_ratio"] == 2.0
    assert after["current_ratio__known_from"] == date(2021, 7, 1)


def test_a_statement_filed_on_the_day_is_known(store: pl.DataFrame) -> None:
    row = _row(store, A, date(2022, 6, 30))
    assert row["revenue_growth_1y"] == pytest.approx(0.1)
    assert row["revenue_growth_1y__known_from"] == date(2022, 6, 30)


def test_an_event_entered_later_counts_from_its_entry(store: pl.DataFrame) -> None:
    for day in (date(2022, 6, 30), date(2022, 7, 31)):  # decided before, entered after
        assert _row(store, A, day)["board_changes_12m"] == 0
    entered = _row(store, A, date(2022, 8, 31))
    assert entered["board_changes_12m"] == 1
    assert entered["board_changes_12m__known_from"] == date(2022, 8, 15)
    # B's petition, dated 2023-01-12, is published on 2023-10-02.
    assert _row(store, B, date(2023, 9, 30))["bankruptcy_petitions_ever"] == 0
    assert _row(store, B, date(2023, 10, 31))["bankruptcy_petitions_ever"] == 1


def test_a_correction_replaces_the_original_only_from_its_filing(store: pl.DataFrame) -> None:
    october = _row(store, A, date(2022, 10, 31))
    november = _row(store, A, date(2022, 11, 30))
    assert october["revenue_growth_1y"] == pytest.approx(0.1)
    assert (october["corrections"], october["corrections__known_from"]) == (0, date(2022, 6, 30))
    assert november["revenue_growth_1y"] == pytest.approx(0.2)
    assert november["revenue_growth_1y__known_from"] == date(2022, 11, 3)
    assert november["corrections"] == 1


def test_a_deleted_statement_speaks_only_until_its_deletion(store: pl.DataFrame) -> None:
    filed = _row(store, A, date(2023, 8, 31))
    deleted = _row(store, A, date(2023, 9, 30))
    refilled = _row(store, A, date(2024, 10, 31))
    # 2022 is the latest year while its statement stands.
    assert filed["revenue_growth_1y"] == pytest.approx(1.3 / 1.2 - 1)
    assert filed["revenue_growth_1y__known_from"] == date(2023, 7, 10)
    # Deleted: the 2021 correction is the latest known year again.
    assert deleted["revenue_growth_1y"] == pytest.approx(0.2)
    assert deleted["revenue_growth_1y__known_from"] == date(2022, 11, 3)
    # a23 fills 2022 from its prior-year column and speaks for 2023. Revenue growth needs the
    # filled period's length, which a prior-year column does not give (owner decision 7).
    assert refilled["revenue_growth_1y"] is None
    assert refilled["equity_growth_1y"] == pytest.approx(1.4 / 1.3 - 1)
    assert refilled["equity_growth_1y__known_from"] == date(2024, 10, 20)


def test_a_restatement_counts_from_the_restating_filing(store: pl.DataFrame) -> None:
    assert _row(store, A, date(2023, 6, 30))["restating_filings"] == 0
    assert _row(store, A, date(2023, 7, 31))["restating_filings"] == 1


def test_the_cut_sources_hold_nothing_later(sources: FeatureSources) -> None:
    day = date(2023, 8, 31)
    cut = known_on(sources, day)
    assert cut.canonical.get_column("known_from").max() <= day  # type: ignore[operator]
    assert cut.legal_events.get_column("known_from").max() <= day  # type: ignore[operator]
    assert cut.restatements.get_column("known_from").max() <= day  # type: ignore[operator]
    # a22's deletion (2023-09-15) and the office move's removal (2024-05-01) had not happened.
    assert cut.filing_index.get_column("deleted_on").null_count() == cut.filing_index.height
    assert cut.legal_events.get_column("removed_on").null_count() == cut.legal_events.height
    # A statement row never expanded has no date, so it is never known.
    assert "b23" not in cut.filing_index.get_column("document_ref").to_list()


# --- the checks must bite ------------------------------------------------------------------------


TRAP_DAYS = [date(2021, 6, 30), date(2022, 6, 30), date(2022, 10, 31), date(2023, 9, 30)]


def _registry_by_event_date(
    grid: pl.DataFrame, inputs: fd.FeatureInputs, config: FeatureConfig
) -> pl.DataFrame:
    """The classic bug (§9.1): events counted from their decision date, not their publication."""
    events = inputs.legal_events.with_columns(
        pl.coalesce("event_date", "known_from").alias("known_from")
    )
    return fd.registry(grid, replace(inputs, legal_events=events), config)


def _financial_by_period_end(
    grid: pl.DataFrame, inputs: fd.FeatureInputs, config: FeatureConfig
) -> pl.DataFrame:
    """Statements treated as known at their balance-sheet date, not their filing date."""
    panel = inputs.panel.filter(pl.col("source_kind") != "withdrawn").with_columns(
        pl.col("period_end").alias("known_from")
    )
    panel = panel.unique(["krs", "period_end", "known_from", "input"], keep="last")
    return fd.financial(grid, replace(inputs, panel=panel), config)


def _financial_unbounded(
    grid: pl.DataFrame, inputs: fd.FeatureInputs, config: FeatureConfig
) -> pl.DataFrame:
    """No ASOF bound: every row gets the entity's latest figures, honestly dated."""
    last = grid.group_by("krs").agg(pl.col("as_of_date").max())
    latest = fd.financial(last, inputs, config).drop("as_of_date")
    return grid.join(latest, on="krs").select(fd.FEATURE_VALUES_SCHEMA.keys())


@pytest.mark.parametrize(
    ("family", "leaky"),
    [
        ("registry", _registry_by_event_date),
        ("financial", _financial_by_period_end),
        ("financial", _financial_unbounded),
    ],
)
def test_a_leaky_family_fails_the_truncation_check(
    grid: pl.DataFrame,
    sources: FeatureSources,
    config: FeatureConfig,
    family: Family,
    leaky: FamilyFn,
) -> None:
    # The trap month-ends only: the full grid is the main check's, and this one is slow.
    traps = grid.filter(pl.col("as_of_date").is_in(TRAP_DAYS))
    differences = truncation_differences(traps, sources, config, {family: leaky})
    assert not differences.is_empty()


def test_a_misdated_leak_passes_the_companion_check_so_both_are_needed(
    grid: pl.DataFrame, inputs: fd.FeatureInputs, config: FeatureConfig
) -> None:
    """Dating events by their decision keeps every `__known_from` in bounds: §9.1 alone misses it."""
    values = _registry_by_event_date(grid, inputs, config)
    assert known_from_violations(widen(grid, values, config), config).is_empty()
    leaked = values.filter(
        (pl.col("krs") == A)
        & (pl.col("as_of_date") == date(2022, 6, 30))
        & (pl.col("feature") == "board_changes_12m")
    )
    assert leaked.get_column("value").to_list() == [1.0]


def test_an_unbounded_leak_fails_the_companion_check(
    grid: pl.DataFrame, inputs: fd.FeatureInputs, config: FeatureConfig
) -> None:
    values = _financial_unbounded(grid, inputs, config)
    violations = known_from_violations(widen(grid, values, config), config)
    assert set(violations.get_column("reason").to_list()) == {"known_after_as_of"}
