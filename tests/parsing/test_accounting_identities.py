"""E2 accounting identities and grading (plan 0004 step F; AGENT_SPEC §4.3, §9.2)."""

import hashlib
from datetime import date
from decimal import Decimal
from pathlib import Path

import polars as pl
import pytest
from lxml import etree

from distress_radar.parsing.accounting_identities import (
    balance_sheet_balances,
    cashflow_ties,
    grade,
    prior_year_consistency,
    profit_ties,
    run_identity_checks,
    subtotals_consistent,
)
from distress_radar.parsing.canonical_schema import MappingConfig
from distress_radar.parsing.containers import safe_parser
from distress_radar.parsing.mapping_engine import (
    DocumentContext,
    Fact,
    ParsedStatement,
    parse_statement,
    to_frame,
)
from distress_radar.parsing.version_detection import detect

FIXTURES_DIR = Path(__file__).parent.parent / "fixtures"
STATEMENTS_DIR = FIXTURES_DIR / "statements"
GOLDEN = sorted(STATEMENTS_DIR.glob("full_*.xml")) + [FIXTURES_DIR / "neobis_001.xml"]
TOLERANCE = Decimal("1.00")
DTSF_2018 = "http://www.mf.gov.pl/schematy/SF/DefinicjeTypySprawozdaniaFinansowe/2018/07/09/DefinicjeTypySprawozdaniaFinansowe/"
JIN_2018 = "http://www.mf.gov.pl/schematy/SF/DefinicjeTypySprawozdaniaFinansowe/2018/07/09/JednostkaInnaStruktury"


def _ctx(ref: str, known_from: date) -> DocumentContext:
    sha = hashlib.sha256(ref.encode()).hexdigest()
    return DocumentContext(
        krs="0000000001",
        nip=None,
        regon=None,
        document_ref=ref,
        source_document_hash=sha,
        source_member=f"zip:{ref}.xml",
        known_from=known_from,
        ingestion_run_id="run-1",
    )


def _frame(
    values: dict[tuple[str, str], str],
    config: MappingConfig,
    ref: str = "doc",
    period: tuple[date, date] = (date(2022, 1, 1), date(2022, 12, 31)),
    known_from: date = date(2023, 6, 30),
) -> pl.DataFrame:
    chart = config.chart.by_code()
    facts = tuple(
        Fact(
            code,
            chart[code].statement_type,
            chart[code].variant,
            column,
            Decimal(v),
            f"/x/{code}/{column}",
        )  # type: ignore[arg-type]
        for (code, column), v in values.items()
    )
    parsed = ParsedStatement("full-2018-v1-2", period[0], period[1], 1, facts)
    return to_frame(parsed, _ctx(ref, known_from))


BALANCED = {
    ("BS.ASSETS", "current_year"): "100.00",
    ("BS.ASSETS.A", "current_year"): "60.00",
    ("BS.ASSETS.B", "current_year"): "40.00",
    ("BS.EQUITY_LIABILITIES", "current_year"): "100.00",
    ("BS.EQUITY_LIABILITIES.A", "current_year"): "30.00",
    ("BS.EQUITY_LIABILITIES.A.I", "current_year"): "25.00",
    ("BS.EQUITY_LIABILITIES.A.VI", "current_year"): "5.00",
    ("BS.EQUITY_LIABILITIES.B", "current_year"): "70.00",
    ("IS.COMP.A", "current_year"): "50.00",
    ("IS.COMP.B", "current_year"): "45.00",
    ("IS.COMP.C", "current_year"): "5.00",
    ("IS.COMP.L", "current_year"): "5.00",
}


def _statuses(result: pl.DataFrame) -> dict[str, str]:
    return dict(zip(result["line_item"].to_list(), result["status"].to_list(), strict=True))


def test_balanced_statement_passes_every_check(mapping_config: MappingConfig) -> None:
    frame = _frame(BALANCED, mapping_config)
    results = run_identity_checks(frame, mapping_config, TOLERANCE)
    assert set(results["status"].to_list()) == {"pass"}
    assert grade(frame, results, mapping_config)["quality_grade"].unique().to_list() == ["pass"]


def test_tolerance_boundary_is_inclusive(mapping_config: MappingConfig) -> None:
    within = _frame(
        {**BALANCED, ("BS.EQUITY_LIABILITIES", "current_year"): "101.00"}, mapping_config
    )
    beyond = _frame(
        {**BALANCED, ("BS.EQUITY_LIABILITIES", "current_year"): "101.01"}, mapping_config
    )
    assert balance_sheet_balances(within, mapping_config, TOLERANCE)["status"].to_list() == ["pass"]
    assert balance_sheet_balances(beyond, mapping_config, TOLERANCE)["status"].to_list() == ["fail"]


def test_profit_must_tie(mapping_config: MappingConfig) -> None:
    frame = _frame(
        {**BALANCED, ("BS.EQUITY_LIABILITIES.A.VI", "current_year"): "7.00"}, mapping_config
    )
    [row] = profit_ties(frame, mapping_config, TOLERANCE).iter_rows(named=True)
    assert (row["status"], row["expected"], row["actual"]) == (
        "fail",
        Decimal("5.00"),
        Decimal("7.00"),
    )


def test_missing_side_of_a_tie_is_skipped_not_imputed(mapping_config: MappingConfig) -> None:
    values = {k: v for k, v in BALANCED.items() if k[0] != "BS.EQUITY_LIABILITIES.A.VI"}
    [row] = profit_ties(_frame(values, mapping_config), mapping_config, TOLERANCE).iter_rows(
        named=True
    )
    assert row["status"] == "skipped"


def test_subtotals_sum_reported_children_and_follow_formulas(mapping_config: MappingConfig) -> None:
    bad = _frame(
        {
            **BALANCED,
            ("BS.ASSETS.B", "current_year"): "39.00",
            ("IS.COMP.C", "current_year"): "6.00",
        },
        mapping_config,
    )
    statuses = _statuses(subtotals_consistent(bad, mapping_config, Decimal("0.00")))
    assert statuses["BS.ASSETS"] == "fail"  # 60 + 39 != 100
    assert statuses["IS.COMP.C"] == "fail"  # C = A - B = 5
    # A parent whose children were not reported is not checked at all.
    assert "BS.ASSETS.A" not in statuses


def test_of_which_lines_are_not_summed(mapping_config: MappingConfig) -> None:
    values = {
        **BALANCED,
        ("IS.COMP.A.I", "current_year"): "50.00",
        ("IS.COMP.A.J", "current_year"): "20.00",  # of which: related parties
    }
    statuses = _statuses(
        subtotals_consistent(_frame(values, mapping_config), mapping_config, TOLERANCE)
    )
    assert statuses["IS.COMP.A"] == "pass"


def test_cashflow_tie_only_when_a_cash_flow_exists(mapping_config: MappingConfig) -> None:
    assert cashflow_ties(_frame(BALANCED, mapping_config), mapping_config, TOLERANCE).is_empty()
    with_cf = {
        **BALANCED,
        ("CF.IND.D", "current_year"): "10.00",
        ("CF.IND.F", "current_year"): "5.00",
        ("CF.IND.G", "current_year"): "17.00",
    }
    [row] = cashflow_ties(_frame(with_cf, mapping_config), mapping_config, TOLERANCE).iter_rows(
        named=True
    )
    assert (row["status"], row["expected"], row["actual"]) == (
        "fail",
        Decimal("12.00"),
        Decimal("10.00"),
    )


def test_immaterial_subtotal_difference_warns_material_one_quarantines(
    mapping_config: MappingConfig,
) -> None:
    # Total assets are 100.00, so up to 1.00 is immaterial.
    small = _frame({**BALANCED, ("IS.COMP.C", "current_year"): "5.50"}, mapping_config)
    large = _frame({**BALANCED, ("IS.COMP.C", "current_year"): "7.00"}, mapping_config)
    for frame, expected in ((small, "warn"), (large, "quarantined")):
        results = run_identity_checks(frame, mapping_config, Decimal("0.00"))
        assert set(results.filter(pl.col("status") == "fail")["check"].to_list()) == {
            "subtotals_consistent"
        }
        assert grade(frame, results, mapping_config)["quality_grade"].unique().to_list() == [
            expected
        ]


def test_subtotal_failure_without_total_assets_quarantines(mapping_config: MappingConfig) -> None:
    frame = _frame(
        {
            ("IS.COMP.A", "current_year"): "50.00",
            ("IS.COMP.B", "current_year"): "45.00",
            ("IS.COMP.C", "current_year"): "5.50",
        },
        mapping_config,
    )
    results = run_identity_checks(frame, mapping_config, Decimal("0.00"))
    assert grade(frame, results, mapping_config)["quality_grade"].unique().to_list() == [
        "quarantined"
    ]


def test_altered_micro_total_assets_quarantines(mapping_config: MappingConfig) -> None:
    """A micro filing whose balance sheet no longer balances must quarantine.

    Built from the committed fixture rather than a synthetic frame, so it
    exercises the real micro body and the real chart codes. A micro balance
    sheet has 13 lines, which leaves the identity checks very little to catch a
    defect with — this pins that they still catch the one that matters.
    """
    xml = STATEMENTS_DIR / "micro_2018_v1_2_2019.xml"
    root = etree.fromstring(xml.read_bytes(), safe_parser())
    total = root.find(".//{*}BilansJednostkaMikro/{*}Aktywa/{*}KwotaA")
    assert total is not None
    total.text = str(Decimal(total.text or "0") + Decimal("1000.00"))

    detection = detect(root, mapping_config)
    assert detection.spec is not None
    parsed = parse_statement(root, detection.spec, mapping_config)
    sha = hashlib.sha256(b"altered-micro").hexdigest()
    ctx = DocumentContext(
        "0000041651", None, None, "altered", sha, "zip:altered.xml", date(2020, 6, 30), "run-1"
    )
    frame = to_frame(parsed, ctx)
    results = run_identity_checks(frame, mapping_config, TOLERANCE)
    failed = results.filter(pl.col("status") == "fail")
    assert "balance_sheet_balances" in failed["check"].to_list()
    assert grade(frame, results, mapping_config)["quality_grade"].unique().to_list() == [
        "quarantined"
    ]


def _golden_frame(
    xml: Path, config: MappingConfig, known_from: date = date(2024, 1, 1)
) -> pl.DataFrame:
    root = etree.fromstring(xml.read_bytes(), safe_parser())
    detection = detect(root, config)
    assert detection.spec is not None
    parsed = parse_statement(root, detection.spec, config)
    sha = hashlib.sha256(xml.stem.encode()).hexdigest()
    ctx = DocumentContext(
        "0000498679", None, None, xml.stem, sha, f"zip:{xml.name}", known_from, "run-1"
    )
    return to_frame(parsed, ctx)


# The HENPOL 2025 filing reports prior-year trade receivables of 158.32 with a
# 0.00 + 0.00 maturity split: a real, immaterial inconsistency, kept as the
# golden example of a `warn` grade.
EXPECTED_FAILURES = {
    "full_2025_w2_kalk_2025": {("subtotals_consistent", "BS.ASSETS.B.II.3.A", "prior_year")},
}


@pytest.mark.parametrize("xml", GOLDEN, ids=lambda p: p.stem)
def test_golden_statements_pass(xml: Path, mapping_config: MappingConfig) -> None:
    frame = _golden_frame(xml, mapping_config)
    results = run_identity_checks(frame, mapping_config, TOLERANCE)
    failures = results.filter(pl.col("status") == "fail")
    found = {(r["check"], r["line_item"], r["column"]) for r in failures.iter_rows(named=True)}
    expected = EXPECTED_FAILURES.get(xml.stem, set())
    assert found == expected, failures.select(
        "check", "line_item", "column", "expected", "actual"
    ).to_dicts()
    assert results.filter(pl.col("check") == "balance_sheet_balances")[
        "status"
    ].unique().to_list() == ["pass"]
    grades = grade(frame, results, mapping_config)["quality_grade"].unique().to_list()
    assert grades == (["warn"] if expected else ["pass"])


def test_known_bad_statement_is_quarantined(mapping_config: MappingConfig) -> None:
    xml = STATEMENTS_DIR / "full_2018_v1_2_por_2022.xml"
    root = etree.fromstring(xml.read_bytes(), safe_parser())
    total = root.find(f".//{{{JIN_2018}}}Aktywa/{{{DTSF_2018}}}KwotaA")
    assert total is not None and total.text is not None
    total.text = str(Decimal(total.text) + 100)
    detection = detect(root, mapping_config)
    assert detection.spec is not None
    parsed = parse_statement(root, detection.spec, mapping_config)
    frame = to_frame(parsed, _ctx("bad", date(2023, 6, 30)))
    results = run_identity_checks(frame, mapping_config, TOLERANCE)
    failed = results.filter(pl.col("status") == "fail")
    assert set(failed["check"].to_list()) == {"balance_sheet_balances", "subtotals_consistent"}
    assert grade(frame, results, mapping_config)["quality_grade"].unique().to_list() == [
        "quarantined"
    ]


def test_real_consecutive_years_restatements(mapping_config: MappingConfig) -> None:
    earlier = _golden_frame(
        STATEMENTS_DIR / "full_2018_v1_2_por_2022.xml", mapping_config, date(2023, 6, 1)
    )
    later = _golden_frame(
        STATEMENTS_DIR / "full_2018_v1_2_por_2023.xml", mapping_config, date(2024, 6, 1)
    )
    events = prior_year_consistency(pl.concat([earlier, later]), TOLERANCE)
    # Every event points from the 2023 filing back to the 2022 one.
    assert set(events["restating_document_ref"].to_list()) <= {"full_2018_v1_2_por_2023"}
    assert set(events["original_document_ref"].to_list()) <= {"full_2018_v1_2_por_2022"}
    assert set(events["fiscal_year"].to_list()) <= {2022}


def test_restatement_uses_adjacent_period_known_earlier(mapping_config: MappingConfig) -> None:
    prior = {("BS.ASSETS", "current_year"): "100.00"}
    current = {
        ("BS.ASSETS", "current_year"): "130.00",
        ("BS.ASSETS", "prior_year"): "110.00",
        ("BS.ASSETS", "prior_year_restated"): "100.00",
    }
    y2022 = _frame(prior, mapping_config, ref="p", known_from=date(2023, 6, 1))
    y2023 = _frame(
        current,
        mapping_config,
        ref="c",
        known_from=date(2024, 6, 1),
        period=(date(2023, 1, 1), date(2023, 12, 31)),
    )
    events = prior_year_consistency(pl.concat([y2022, y2023]), TOLERANCE)
    [row] = events.iter_rows(named=True)
    assert row["restated_column"] == "prior_year"
    assert (row["originally_reported_value"], row["restated_value"]) == (
        Decimal("100.00"),
        Decimal("110.00"),
    )
    assert (row["original_document_ref"], row["restating_document_ref"]) == ("p", "c")
    assert row["known_from"] == date(2024, 6, 1)

    # A prior filing that became public only later is not what the filer restated.
    late = _frame(prior, mapping_config, ref="p", known_from=date(2024, 7, 1))
    assert prior_year_consistency(pl.concat([late, y2023]), TOLERANCE).is_empty()


def test_short_periods_pair_by_date_not_fiscal_year(mapping_config: MappingConfig) -> None:
    first_half = _frame(
        {("BS.ASSETS", "current_year"): "100.00"},
        mapping_config,
        ref="h",
        period=(date(2022, 1, 1), date(2022, 6, 12)),
        known_from=date(2022, 9, 1),
    )
    second_half = _frame(
        {("BS.ASSETS", "current_year"): "90.00", ("BS.ASSETS", "prior_year"): "95.00"},
        mapping_config,
        ref="s",
        period=(date(2022, 6, 13), date(2022, 12, 31)),
        known_from=date(2023, 6, 1),
    )
    [row] = prior_year_consistency(pl.concat([first_half, second_half]), TOLERANCE).iter_rows(
        named=True
    )
    assert row["period_end"] == date(2022, 6, 12)
    assert row["fiscal_year"] == 2022


def test_user_lines_count_towards_their_parent(mapping_config: MappingConfig) -> None:
    values = {
        **BALANCED,
        ("BS.ASSETS.B", "current_year"): "35.00",
        ("BS.ASSETS.USER", "current_year"): "5.00",
    }
    statuses = _statuses(
        subtotals_consistent(_frame(values, mapping_config), mapping_config, TOLERANCE)
    )
    assert statuses["BS.ASSETS"] == "pass"  # 60 + 35 + 5


def test_user_lines_under_an_of_which_line_are_not_summed(mapping_config: MappingConfig) -> None:
    values = {
        **BALANCED,
        ("BS.EQUITY_LIABILITIES.A.II", "current_year"): "10.00",
        ("BS.EQUITY_LIABILITIES.A.II.1", "current_year"): "4.00",
        ("BS.EQUITY_LIABILITIES.A.II.USER", "current_year"): "3.00",
    }
    statuses = _statuses(
        subtotals_consistent(_frame(values, mapping_config), mapping_config, TOLERANCE)
    )
    assert "BS.EQUITY_LIABILITIES.A.II" not in statuses  # only "w tym" lines under it


def test_cash_difference_explained_by_fx_passes(mapping_config: MappingConfig) -> None:
    # Filed by KRS 0000209396 for 2020: E = G - F, D = E - FX.
    values = {
        **BALANCED,
        ("CF.IND.D", "current_year"): "613991.74",
        ("CF.IND.E.1", "current_year"): "-3065.32",
        ("CF.IND.F", "current_year"): "466063.47",
        ("CF.IND.G", "current_year"): "1083120.53",
    }
    [row] = cashflow_ties(_frame(values, mapping_config), mapping_config, TOLERANCE).iter_rows(
        named=True
    )
    assert row["status"] == "pass"
    values[("CF.IND.E.1", "current_year")] = "-1000.00"
    [row] = cashflow_ties(_frame(values, mapping_config), mapping_config, TOLERANCE).iter_rows(
        named=True
    )
    assert row["status"] == "fail"


def test_prior_year_only_failures_warn(mapping_config: MappingConfig) -> None:
    values = {
        **BALANCED,
        ("BS.EQUITY_LIABILITIES.A.VI", "prior_year"): "9.00",
        ("IS.COMP.L", "prior_year"): "5.00",
    }
    frame = _frame(values, mapping_config)
    results = run_identity_checks(frame, mapping_config, TOLERANCE)
    assert results.filter(pl.col("status") == "fail")["column"].unique().to_list() == ["prior_year"]
    assert grade(frame, results, mapping_config)["quality_grade"].unique().to_list() == ["warn"]
