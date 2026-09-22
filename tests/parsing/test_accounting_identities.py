"""E2 accounting identities and grading (plan 0004 step F; AGENT_SPEC §4.3, §9.2)."""

import hashlib
from datetime import date
from decimal import Decimal
from pathlib import Path

import pandera.errors as pa_errors
import polars as pl
import pytest
from lxml import etree

from distress_radar.parsing.accounting_identities import (
    balance_sheet_balances,
    cashflow_ties,
    filed_bodies,
    grade,
    identity_check_results,
    prior_year_consistency,
    profit_ties,
    run_identity_checks,
    subtotals_consistent,
    unresolved_bodies,
)
from distress_radar.parsing.canonical_schema import MappingConfig
from distress_radar.parsing.containers import safe_parser
from distress_radar.parsing.contracts import IDENTITY_CHECK_RESULTS
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
# Every committed fixture, so a new one is covered without editing this file.
GOLDEN = sorted(STATEMENTS_DIR.glob("*.xml")) + [FIXTURES_DIR / "neobis_001.xml"]
# The forms that declare no cash-flow and no equity-changes statement (plan 0005
# step B): their absence is a schema fact, never a filer omission.
SHORT_FORM = sorted(STATEMENTS_DIR.glob("small_*.xml")) + sorted(STATEMENTS_DIR.glob("micro_*.xml"))
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
    version: str = "full-2018-v1-2",
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
    parsed = ParsedStatement(version, period[0], period[1], 1, facts)
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
    # No cash-flow statement in the fixture: that tie is not applicable, not passed.
    by_check = dict(results.group_by("check").agg(pl.col("status").unique()).iter_rows())
    assert {c: sorted(v) for c, v in by_check.items()} == {
        "balance_sheet_balances": ["pass"],
        "subtotals_consistent": ["pass"],
        "profit_ties": ["pass"],
        "cashflow_ties": ["not_applicable"],
    }
    assert grade(frame, results)["quality_grade"].unique().to_list() == ["pass"]


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


def test_missing_side_of_a_tie_is_not_applicable_not_imputed(
    mapping_config: MappingConfig,
) -> None:
    values = {k: v for k, v in BALANCED.items() if k[0] != "BS.EQUITY_LIABILITIES.A.VI"}
    [row] = profit_ties(_frame(values, mapping_config), mapping_config, TOLERANCE).iter_rows(
        named=True
    )
    assert row["status"] == "not_applicable"


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
    [absent] = cashflow_ties(
        _frame(BALANCED, mapping_config), mapping_config, TOLERANCE
    ).iter_rows(named=True)
    assert (absent["status"], absent["line_item"], absent["difference"]) == (
        "not_applicable",
        "CF",
        None,
    )
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
    for frame, expected, severity in (
        (small, "warn", "immaterial"),
        (large, "quarantined", "material"),
    ):
        results = run_identity_checks(frame, mapping_config, Decimal("0.00"))
        failed = results.filter(pl.col("status") == "fail")
        assert set(failed["check"].to_list()) == {"subtotals_consistent"}
        assert set(failed["severity"].to_list()) == {severity}
        assert grade(frame, results)["quality_grade"].unique().to_list() == [
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
    assert grade(frame, results)["quality_grade"].unique().to_list() == [
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
    assert grade(frame, results)["quality_grade"].unique().to_list() == [
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
    grades = grade(frame, results)["quality_grade"].unique().to_list()
    assert grades == (["warn"] if expected else ["pass"])


def test_severity_is_set_on_failures_only(mapping_config: MappingConfig) -> None:
    """Only a `fail` row is judged; `pass` and `not_applicable` rows carry no severity."""
    frame = _frame(
        {
            **BALANCED,
            ("BS.ASSETS", "current_year"): "101.00",  # a current-year tie failure: material
            # A prior-year profit tie off by 4.00: immaterial, whatever its size.
            ("BS.EQUITY_LIABILITIES.A.VI", "prior_year"): "9.00",
            ("IS.COMP.L", "prior_year"): "5.00",
        },
        mapping_config,
    )
    results = run_identity_checks(frame, mapping_config, Decimal("0.00"))
    failed = results.filter(pl.col("status") == "fail")
    judged = {
        (r["check"], r["line_item"], r["column"]): r["severity"]
        for r in failed.iter_rows(named=True)
    }
    assert judged == {
        # A current-year tie: material whatever its size.
        ("balance_sheet_balances", "BS.ASSETS", "current_year"): "material",
        # 60 + 40 against 101: off by 1.00, within 1% of total assets.
        ("subtotals_consistent", "BS.ASSETS", "current_year"): "immaterial",
        # Confined to the prior-year column: immaterial, whatever its size.
        ("profit_ties", "IS.COMP.L", "prior_year"): "immaterial",
    }
    rest = results.filter(pl.col("status") != "fail")
    assert set(rest["status"].to_list()) == {"pass", "not_applicable"}
    assert rest["severity"].null_count() == rest.height


def test_identity_check_results_carry_each_files_lineage(mapping_config: MappingConfig) -> None:
    one = _frame(BALANCED, mapping_config, ref="one")
    two = _frame(
        {**BALANCED, ("IS.COMP.C", "current_year"): "7.00"},
        mapping_config,
        ref="two",
        period=(date(2023, 1, 1), date(2023, 12, 31)),
        known_from=date(2024, 7, 1),
    )
    facts = pl.concat([one, two])
    results = run_identity_checks(facts, mapping_config, TOLERANCE)
    persisted = IDENTITY_CHECK_RESULTS.validate(identity_check_results(results, facts))
    assert persisted.height == results.height
    by_ref = {
        r["document_ref"]: (r["fiscal_year"], r["known_from"], r["structure_version"])
        for r in persisted.iter_rows(named=True)
    }
    assert by_ref == {
        "one": (2022, date(2023, 6, 30), "full-2018-v1-2"),
        "two": (2023, date(2024, 7, 1), "full-2018-v1-2"),
    }
    assert persisted.filter(pl.col("status") == "fail")["document_ref"].unique().to_list() == [
        "two"
    ]


def test_identity_check_results_contract_rejects_a_pass_with_a_severity(
    mapping_config: MappingConfig,
) -> None:
    facts = _frame(BALANCED, mapping_config)
    persisted = identity_check_results(
        run_identity_checks(facts, mapping_config, TOLERANCE), facts
    )
    IDENTITY_CHECK_RESULTS.validate(persisted)
    with pytest.raises(pa_errors.SchemaError):
        IDENTITY_CHECK_RESULTS.validate(persisted.with_columns(severity=pl.lit("material")))


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
    assert grade(frame, results)["quality_grade"].unique().to_list() == [
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
    assert grade(frame, results)["quality_grade"].unique().to_list() == ["warn"]


# --- Short forms (plan 0005 step F) -----------------------------------------
#
# No new rule: the small and micro forms are checked by the same four identities
# as the full form. What changes is the tree each one walks, and the two
# statements that are simply not there. These pin both.


def _current_year(frame: pl.DataFrame) -> dict[str, Decimal]:
    part = frame.filter(pl.col("column") == "current_year")
    return dict(zip(part["line_item"].to_list(), part["value"].to_list(), strict=True))


@pytest.mark.parametrize("xml", SHORT_FORM, ids=lambda p: p.stem)
def test_short_forms_write_no_cash_flow_or_equity_rows(
    xml: Path, mapping_config: MappingConfig
) -> None:
    """A small or micro filing produces no `cash_flow` or `equity_changes` facts.

    Small and micro entities are exempt from both statements and the schemas do
    not declare them, so there is nothing to report. Invariant 4: absent, not
    zero rows.
    """
    frame = _golden_frame(xml, mapping_config)
    assert set(frame["statement_type"].to_list()) <= {"balance_sheet", "income_statement"}


@pytest.mark.parametrize("xml", SHORT_FORM, ids=lambda p: p.stem)
def test_absent_statements_are_not_applicable_never_failed_or_passed(
    xml: Path, mapping_config: MappingConfig
) -> None:
    """`cashflow_ties` and the equity walk must not fail a form that has neither.

    Nor may they pass it (plan 0007 owner decision 1): `cashflow_ties` records
    one `not_applicable` row per column, so a pass rate built on these rows
    cannot count an exempt filing as a passed tie. The equity walk is a
    subtotal tree that does not exist here, so it produces no row at all.
    """
    frame = _golden_frame(xml, mapping_config)
    cash = cashflow_ties(frame, mapping_config, TOLERANCE)
    assert not cash.is_empty()
    assert set(cash["status"].to_list()) == {"not_applicable"}
    assert set(cash["column"].to_list()) == set(frame["column"].to_list())

    results = run_identity_checks(frame, mapping_config, TOLERANCE)
    cash_rows = results.filter(pl.col("check") == "cashflow_ties")
    assert set(cash_rows["status"].to_list()) == {"not_applicable"}
    assert cash_rows["severity"].null_count() == cash_rows.height
    assert not [c for c in results["line_item"].to_list() if c.startswith(("CF.", "EQ."))]


def test_micro_of_which_lines_are_not_summed_into_current_assets(
    mapping_config: MappingConfig,
) -> None:
    """Finding 3, the trap this plan most needs a regression test for.

    The micro form's `Aktywa/Aktywa_B/Aktywa_B_1` is `– zapasy`, an of-which
    note under `Aktywa obrotowe`. It carries chart code `BS.ASSETS.B.I`, which
    in the full form is `Zapasy`, a summing component of the same parent. Same
    code, opposite role — and no identity check can tell the difference by
    itself, because a wrong flag produces a plausible failure on a correct
    filing rather than an error.
    """
    frame = _golden_frame(STATEMENTS_DIR / "micro_2018_v1_2_2019.xml", mapping_config)
    values = _current_year(frame)
    # The of-which children of this filing do not add up to their parent, so
    # summing them would be visible rather than harmless.
    assert values["BS.ASSETS.B.I"] + values["BS.ASSETS.B.II"] != values["BS.ASSETS.B"]

    results = subtotals_consistent(frame, mapping_config, TOLERANCE)
    # `Aktywa obrotowe` has only of-which children in the micro form, so it has
    # no statutory children to sum and is not checked at all.
    assert "BS.ASSETS.B" not in results["line_item"].to_list()
    assert set(results["status"].to_list()) == {"pass"}


def test_the_full_form_sums_what_the_micro_form_only_notes(
    mapping_config: MappingConfig,
) -> None:
    """The same codes and the same amounts, read against the full-form body.

    The counterpart to the test above: it is the body, not the chart code, that
    decides whether a line is summed. Bound to `jednostka_inna`, these figures
    fail by the 106,169.77 the of-which lines do not account for.
    """
    values = _current_year(
        _golden_frame(STATEMENTS_DIR / "micro_2018_v1_2_2019.xml", mapping_config)
    )
    codes = ("BS.ASSETS.B", "BS.ASSETS.B.I", "BS.ASSETS.B.II")
    as_full_form = _frame(
        {(code, "current_year"): str(values[code]) for code in codes}, mapping_config
    )
    assert (
        _statuses(subtotals_consistent(as_full_form, mapping_config, TOLERANCE))["BS.ASSETS.B"]
        == "fail"
    )


def test_micro_profit_ties_is_not_applicable_with_no_balance_sheet_result(
    mapping_config: MappingConfig,
) -> None:
    """The micro balance sheet declares no net-result line, so the tie has one side.

    `IS.MIKRO.F` is the income statement's result; nothing in the 13-line micro
    balance sheet restates it. That is a `not_applicable` row, not a failure, and not
    an excuse to impute the missing side (invariant 4).
    """
    frame = _golden_frame(STATEMENTS_DIR / "micro_2018_v1_2_2019.xml", mapping_config)
    results = profit_ties(frame, mapping_config, TOLERANCE)
    assert set(results["line_item"].to_list()) == {"IS.MIKRO.F"}
    assert set(results["status"].to_list()) == {"not_applicable"}


def test_small_form_subtotals_walk_its_own_shorter_profit_chain(
    mapping_config: MappingConfig,
) -> None:
    """The small form's section letters do not line up with the full form's.

    It has no operating-result line, so its `F` is the full form's `G` and the
    shift continues to the end of the statement (plan 0005 step C). Its profit
    chain therefore has its own codes and its own formulas, and the full form's
    operating result must not be checked against it.
    """
    frame = _golden_frame(STATEMENTS_DIR / "small_2018_v1_2_mala_por_2021.xml", mapping_config)
    results = subtotals_consistent(frame, mapping_config, TOLERANCE)
    codes = set(results["line_item"].to_list())
    assert {"IS.COMP.MALA.H", "IS.COMP.MALA.J"} <= codes
    assert "IS.COMP.F" not in codes  # "Zysk (strata) z działalności operacyjnej"
    assert set(results["status"].to_list()) == {"pass"}


def test_mixed_filing_is_checked_against_the_body_each_statement_was_filed_in(
    mapping_config: MappingConfig,
) -> None:
    """One document, two trees: a small balance sheet and a full income statement.

    The `JednostkaMala` envelope accepts either body, chosen per statement, and
    the header does not say which (plan 0005 step D). Checking the whole
    document against one of them produced 12 spurious quarantines before the
    checker learned to read the filed shape back off `source_element_path`.
    """
    frame = _golden_frame(STATEMENTS_DIR / "small_2018_v1_0_mixed_por_2018.xml", mapping_config)
    results = subtotals_consistent(frame, mapping_config, TOLERANCE)
    codes = set(results["line_item"].to_list())

    # Balance sheet: the small body, which has lines the full form does not.
    assert {c for c in frame["line_item"].to_list() if c.startswith("BS.ASSETS.MALA.")}
    # Income statement: the full body's chain, not the small form's.
    assert {"IS.COMP.F", "IS.COMP.L"} <= codes
    assert not {c for c in codes if c.startswith("IS.") and ".MALA." in c}
    assert set(results["status"].to_list()) == {"pass"}


def test_small_form_restatement_pair_reports_no_restatement(
    mapping_config: MappingConfig,
) -> None:
    """Adjacent small-form years from one entity: compared, and in agreement.

    KRS 0000225354's FY2022 filing repeats its FY2021 figures exactly, so the
    expected result is no event. Altering one line proves the comparison ran
    rather than passing vacuously.
    """
    earlier = _golden_frame(
        STATEMENTS_DIR / "small_2018_v1_2_mala_por_2021.xml", mapping_config, date(2022, 6, 1)
    )
    later = _golden_frame(
        STATEMENTS_DIR / "small_2018_v1_2_mala_por_2022.xml", mapping_config, date(2023, 6, 1)
    )
    assert prior_year_consistency(pl.concat([earlier, later]), TOLERANCE).is_empty()

    restated = later.with_columns(
        pl.when((pl.col("line_item") == "BS.ASSETS") & (pl.col("column") == "prior_year"))
        .then(pl.col("value") + Decimal("1000.00"))
        .otherwise(pl.col("value"))
        .alias("value")
    )
    [event] = prior_year_consistency(pl.concat([earlier, restated]), TOLERANCE).iter_rows(
        named=True
    )
    assert (event["line_item"], event["fiscal_year"]) == ("BS.ASSETS", 2021)
    assert event["restated_value"] - event["originally_reported_value"] == Decimal("1000.00")


@pytest.mark.parametrize("xml", GOLDEN, ids=lambda p: p.stem)
def test_no_golden_statement_is_checked_against_a_fallback_body(
    xml: Path, mapping_config: MappingConfig
) -> None:
    """Every filed statement resolves to the body it was actually filed in."""
    assert unresolved_bodies(_golden_frame(xml, mapping_config), mapping_config).is_empty()


def test_an_unreadable_element_path_is_reported_as_a_fallback(
    mapping_config: MappingConfig,
) -> None:
    """The fallback in `_bodies_filed` is deliberate, but it must not be silent.

    A renamed body or a changed path format would leave a filing checked
    against another body's rules, and the results themselves cannot show it.
    """
    frame = _golden_frame(STATEMENTS_DIR / "small_2018_v1_2_inna_por_2022.xml", mapping_config)
    assert unresolved_bodies(frame, mapping_config).is_empty()

    renamed = frame.with_columns(
        pl.col("source_element_path").str.replace("BilansJednostkaInna", "BilansJednostkaZmieniona")
    )
    [row] = unresolved_bodies(renamed, mapping_config).iter_rows(named=True)
    assert (row["statement"], row["structure_version"]) == ("Bilans", "small-2018-v1-2")
    assert row["body_used"] == "jednostka_mala"  # the spec's first alternative
    # The income statement still resolves, so only the balance sheet is reported.
    assert unresolved_bodies(renamed, mapping_config).height == 1


def test_an_absent_statement_is_not_a_fallback(mapping_config: MappingConfig) -> None:
    """A small filing declares no cash flow; not filing one is not a resolution failure."""
    frame = _golden_frame(STATEMENTS_DIR / "small_2018_v1_2_mala_por_2021.xml", mapping_config)
    assert "cash_flow" not in set(frame["statement_type"].to_list())
    assert unresolved_bodies(frame, mapping_config).is_empty()


@pytest.mark.parametrize(
    ("xml", "expected"),
    [
        # A small envelope carrying the full-form income statement (plan 0005 step D).
        ("small_2018_v1_0_mixed_por_2018.xml", "jednostka_inna+jednostka_mala"),
        ("small_2018_v1_2_inna_por_2022.xml", "jednostka_inna"),
        ("small_2018_v1_2_mala_por_2021.xml", "jednostka_mala"),
        # A single-body spec reads as that body, so SQL never needs the config.
        ("full_2018_v1_2_por_2022.xml", "jednostka_inna"),
    ],
)
def test_filed_bodies_is_the_files_body_set(
    xml: str, expected: str, mapping_config: MappingConfig
) -> None:
    """The `parsed_documents.filed_bodies` value and `dq_mart`'s body dimension (amendment 6)."""
    [row] = filed_bodies(
        _golden_frame(STATEMENTS_DIR / xml, mapping_config), mapping_config
    ).iter_rows(named=True)
    assert row["filed_bodies"] == expected
