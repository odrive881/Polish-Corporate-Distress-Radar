"""`entity_size_class_history` (plan 0020): the statute's cases, the exactness of a decided class
when a figure is missing, the revenue bounds, and the dataset's point-in-time rows."""

from __future__ import annotations

import hashlib
import random
from datetime import date
from decimal import Decimal
from pathlib import Path

import polars as pl
import pytest
import yaml
from tests.parsing.test_accounting_identities import _frame

from distress_radar.parsing.canonical_schema import MappingConfig
from distress_radar.parsing.legal_events import LEGAL_EVENTS_SCHEMA
from distress_radar.parsing.size_class import (
    CLASSES,
    UNDETERMINED,
    Bounds,
    Regime,
    SizeClass,
    SizeClassInputs,
    SizeThresholds,
    YearFigures,
    classify_year,
    entity_size_class_history,
    load_size_class_inputs,
    load_size_thresholds,
    revenue_bounds,
)
from distress_radar.parsing.statement_introduction import DISCLOSURE_COLUMNS

THRESHOLDS = load_size_thresholds()
OLD = THRESHOLDS.regime_for(date(2022, 1, 1))  # uor_2019: micro 1.5m/3m/10, small 25.5m/51m/50
NEW = THRESHOLDS.regime_for(date(2024, 1, 1))  # uor_2024: micro 2m/4m/10, small 33m/66m/50
NOTHING_CARRIED: frozenset[SizeClass | None] = frozenset({None})
UNKNOWN = Bounds()


def _regime(regime: Regime | None) -> Regime:
    assert regime is not None
    return regime


def _year(assets: str | None, revenue: str | Bounds | None, employment: str | None) -> YearFigures:
    def exact(v: str | None) -> Bounds:
        return UNKNOWN if v is None else Bounds.exact(Decimal(v))

    rev = revenue if isinstance(revenue, Bounds) else exact(revenue)
    return YearFigures(exact(assets), rev, exact(employment))


def _carried(*classes: SizeClass) -> frozenset[SizeClass | None]:
    return frozenset(classes)


# --- the configs ---------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("start", "regime"),
    [
        (date(2016, 12, 31), None),
        (date(2017, 1, 1), "uor_2017"),
        (date(2018, 12, 31), "uor_2017"),
        (date(2019, 1, 1), "uor_2019"),
        (date(2023, 12, 31), "uor_2019"),
        (date(2024, 1, 1), "uor_2024"),
        (date(2026, 7, 1), "uor_2024"),
    ],
)
def test_a_year_takes_the_regime_its_start_date_falls_in(start: date, regime: str | None) -> None:
    found = THRESHOLDS.regime_for(start)
    assert (None if found is None else found.id) == regime


def test_the_thresholds_are_the_statutes() -> None:
    def values(regime: Regime) -> list[tuple[str, str, str]]:
        return [
            (str(b.total_assets), str(b.net_revenue), str(b.average_employment))
            for b in regime.boundaries[:2]
        ]

    assert values(_regime(THRESHOLDS.regime_for(date(2018, 1, 1)))) == [
        ("1500000", "3000000", "10"),
        ("17000000", "34000000", "50"),
    ]
    assert values(_regime(OLD)) == [("1500000", "3000000", "10"), ("25500000", "51000000", "50")]
    assert values(_regime(NEW)) == [("2000000", "4000000", "10"), ("33000000", "66000000", "50")]
    assert _regime(NEW).boundaries[2].basis == "statutory"
    assert _regime(OLD).boundaries[2].basis == "by_analogy"
    assert _regime(NEW).carry_over and not _regime(OLD).carry_over


def test_regimes_with_a_gap_are_refused() -> None:
    document = THRESHOLDS.model_dump(by_alias=True)
    document["regimes"][1]["fiscal_years_starting"]["from"] = date(2019, 2, 1)
    with pytest.raises(ValueError, match="gap or overlap"):
        SizeThresholds.model_validate(document)


def test_thresholds_that_do_not_increase_are_refused() -> None:
    document = THRESHOLDS.model_dump(by_alias=True)
    document["regimes"][0]["boundaries"][1]["total_assets"] = Decimal(1000000)
    with pytest.raises(ValueError, match="must increase"):
        SizeThresholds.model_validate(document)


def test_the_inputs_config_loads_and_every_rule_can_apply(mapping_config: MappingConfig) -> None:
    inputs = load_size_class_inputs(mapping_config)
    assert inputs.total_assets == "BS.ASSETS"


def test_a_rule_whose_lines_no_body_carries_together_is_refused(
    mapping_config: MappingConfig, tmp_path: Path
) -> None:
    root = tmp_path
    (root / "mappings").mkdir()
    inputs = load_size_class_inputs(mapping_config).model_dump(mode="json")
    inputs["revenue_rules"].append(
        {"id": "impossible", "when": ["IS.MIKRO.A", "IS.CALC.A"], "low": [], "high": None}
    )
    (root / "mappings" / "size_class_inputs_v1.yaml").write_text(yaml.safe_dump(inputs))
    with pytest.raises(ValueError, match="impossible"):
        load_size_class_inputs(mapping_config, root)


def test_a_bound_on_a_line_the_rule_does_not_require_is_refused() -> None:
    with pytest.raises(ValueError, match="not in `when`"):
        SizeClassInputs.model_validate(
            {
                "version": "x",
                "total_assets": "BS.ASSETS",
                "revenue_rules": [
                    {"id": "r", "when": ["IS.COMP.A.I"], "low": [[1, "IS.COMP.A.IV"]], "high": None}
                ],
            }
        )


# --- revenue bounds ------------------------------------------------------------------------------


@pytest.fixture(scope="module")
def inputs(mapping_config: MappingConfig) -> SizeClassInputs:
    return load_size_class_inputs(mapping_config)


def _codes(**values: str) -> dict[str, Decimal]:
    return {k.replace("_", "."): Decimal(v) for k, v in values.items()}


@pytest.mark.parametrize(
    ("codes", "expected"),
    [
        # comparative before wariant 2: goods and materials on one line, so an upper bound only
        (_codes(IS_COMP_A_I="100", IS_COMP_A_IV="20"), ("comp", "100", "120")),
        # from wariant 2 the goods line is goods only: exact
        (
            {"IS.COMP.A.I": Decimal(100), "IS.COMP.A.IV.R2025": Decimal(20)},
            ("comp_r2025", "120", "120"),
        ),
        ({"IS.CALC.A.R2025": Decimal(500)}, ("calc_r2025", "500", "500")),
        (_codes(IS_CALC_A="500", IS_CALC_A_I="450"), ("calc_with_products", "450", "500")),
        (_codes(IS_CALC_A="500"), ("calc", "0", "500")),
        # the small form's sales line includes materials: within, never beyond
        ({"IS.COMP.A": Decimal(90), "IS.COMP.MALA.A.I": Decimal(80)}, ("small_sales", "0", "80")),
        # the micro headline less its change in inventories
        ({"IS.MIKRO.A": Decimal(100), "IS.MIKRO.A.1": Decimal(-10)}, ("micro", "0", "110")),
        # products only: a lower bound, no upper one
        (_codes(IS_COMP_A_I="100"), ("comp_products", "100", None)),
    ],
)
def test_revenue_bounds_by_rule(
    inputs: SizeClassInputs,
    codes: dict[str, Decimal],
    expected: tuple[str, str, str | None],
) -> None:
    bounds = revenue_bounds(codes, inputs)
    assert (bounds.rule, str(bounds.low), None if bounds.high is None else str(bounds.high)) == (
        expected[0],
        expected[1],
        expected[2],
    )


def test_a_micro_headline_without_its_change_in_inventories_bounds_nothing(
    inputs: SizeClassInputs,
) -> None:
    assert revenue_bounds({"IS.MIKRO.A": Decimal(100)}, inputs) == Bounds()


def test_the_comparative_headline_alone_bounds_nothing(inputs: SizeClassInputs) -> None:
    assert revenue_bounds({"IS.COMP.A": Decimal(100)}, inputs) == Bounds()


def test_at_the_threshold_is_not_exceeding_it() -> None:
    thresholds = _regime(OLD).thresholds("total_assets")
    assert Bounds.exact(Decimal(1500000)).regions(thresholds) == {0}
    assert Bounds.exact(Decimal("1500000.01")).regions(thresholds) == {1}
    assert Bounds(Decimal(0), Decimal(1600000)).regions(thresholds) == {0, 1}
    assert UNKNOWN.regions(thresholds) == {0, 1, 2, 3}


# --- the statute's cases -------------------------------------------------------------------------


def test_two_years_within_micro_is_micro() -> None:
    year = _year("1000000", "2000000", "5")
    result = classify_year(_regime(OLD), year, year, NOTHING_CARRIED)
    assert (result.size_class, result.basis) == ("micro", "statutory")


def test_two_of_three_is_enough() -> None:
    # assets above micro, revenue and employment within: one criterion exceeded
    year = _year("9000000", "2000000", "5")
    assert classify_year(_regime(OLD), year, year, NOTHING_CARRIED).size_class == "micro"


def test_two_years_beyond_micro_and_within_small_is_small() -> None:
    year = _year("5000000", "9000000", "20")
    assert classify_year(_regime(OLD), year, year, NOTHING_CARRIED).size_class == "small"


def test_before_2024_one_year_on_each_side_is_not_micro() -> None:
    within = _year("1000000", "2000000", "5")
    beyond = _year("5000000", "9000000", "20")
    assert classify_year(_regime(OLD), within, beyond, NOTHING_CARRIED).size_class == "small"
    assert classify_year(_regime(OLD), beyond, within, NOTHING_CARRIED).size_class == "small"


def test_from_2024_a_status_is_kept_until_two_years_beyond_it() -> None:
    within = _year("1000000", "2000000", "5")
    beyond = _year("5000000", "9000000", "20")
    regime = _regime(NEW)
    assert classify_year(regime, beyond, within, _carried("micro")).size_class == "micro"
    assert classify_year(regime, beyond, beyond, _carried("micro")).size_class == "small"
    # and a small entity becomes micro only after two years within micro
    assert classify_year(regime, within, beyond, _carried("small")).size_class == "small"
    assert classify_year(regime, within, within, _carried("small")).size_class == "micro"


def test_from_2024_a_split_with_no_status_to_keep_falls_to_the_class_above_micro() -> None:
    within = _year("1000000", "2000000", "5")
    beyond = _year("5000000", "9000000", "20")
    assert classify_year(_regime(NEW), beyond, within, NOTHING_CARRIED).size_class == "small"


def test_a_split_at_the_top_boundary_with_no_status_to_keep_is_undetermined() -> None:
    medium = _year("50000000", "100000000", "100")
    large = _year("200000000", "300000000", "300")
    result = classify_year(_regime(NEW), large, medium, NOTHING_CARRIED)
    assert (result.size_class, result.reason) == (UNDETERMINED, "no_prior_side")
    assert classify_year(_regime(NEW), large, medium, _carried("medium")).size_class == "medium"
    assert classify_year(_regime(NEW), large, large, _carried("medium")).size_class == "large"


def test_the_first_year_of_activity_is_tested_on_itself() -> None:
    year = _year("1000000", "2000000", "5")
    assert classify_year(_regime(OLD), year, None, NOTHING_CARRIED).size_class == "micro"


def test_before_2024_above_small_is_medium_by_analogy() -> None:
    year = _year("50000000", "100000000", "100")
    result = classify_year(_regime(OLD), year, year, NOTHING_CARRIED)
    assert (result.size_class, result.basis) == ("medium", "by_analogy")
    assert classify_year(_regime(NEW), year, year, NOTHING_CARRIED).basis == "statutory"


def test_2024_tests_2023s_figures_against_the_new_thresholds() -> None:
    # above the old micro thresholds, within the new ones, in both years
    year = _year("1800000", "3500000", "5")
    assert classify_year(_regime(OLD), year, year, NOTHING_CARRIED).size_class == "small"
    assert classify_year(_regime(NEW), year, year, NOTHING_CARRIED).size_class == "micro"


def test_no_regime_is_undetermined() -> None:
    year = _year("1000000", "2000000", "5")
    result = classify_year(None, year, year, NOTHING_CARRIED)
    assert (result.size_class, result.reason) == (UNDETERMINED, "no_regime")


# --- exact where decidable (owner decision 1) ----------------------------------------------------


def test_missing_employment_decides_nothing_when_both_financial_figures_agree() -> None:
    year = _year("1000000", "2000000", None)
    assert classify_year(_regime(OLD), year, year, NOTHING_CARRIED).size_class == "micro"
    big = _year("9000000", "9000000", None)
    assert classify_year(_regime(OLD), big, big, NOTHING_CARRIED).size_class == "small"


def test_missing_employment_where_it_decides_is_undetermined_with_its_reason() -> None:
    year = _year("1000000", "9000000", None)  # assets within micro, revenue beyond
    result = classify_year(_regime(OLD), year, year, NOTHING_CARRIED)
    assert (result.size_class, result.reason) == (UNDETERMINED, "employment_decides")


def test_revenue_bounds_that_straddle_a_threshold_are_reported() -> None:
    year = _year("1000000", Bounds(Decimal(2000000), Decimal(9000000)), "50")
    result = classify_year(_regime(OLD), year, year, NOTHING_CARRIED)
    assert (result.size_class, result.reason) == (UNDETERMINED, "revenue_bounds")


def _fill(bounds: Bounds, points: list[Decimal], rng: random.Random) -> Bounds:
    """A value the bounds allow, often on or next to a threshold."""
    allowed = [p for p in points if p >= bounds.low and (bounds.high is None or p <= bounds.high)]
    if bounds.value is not None:
        return bounds
    return Bounds.exact(rng.choice(allowed or [bounds.low]))


def _draw(points: list[Decimal], rng: random.Random) -> Bounds:
    """Unknown, bounded or exact, on or next to a threshold."""
    value = rng.choice(points)
    kind = rng.random()
    if kind < 0.3:
        return UNKNOWN
    if kind < 0.6:
        other = rng.choice(points)
        return Bounds(min(value, other), max(value, other))
    return Bounds.exact(value)


def test_a_decided_class_is_the_class_of_every_value_the_missing_figures_could_take() -> None:
    """Draw figures with some of them missing or bounded; whenever the year is decided, fill the
    gaps with values on and around every threshold and check the class never changes."""
    rng = random.Random(20261018)
    decided = 0
    for _ in range(1500):
        regime = _regime(rng.choice([OLD, NEW]))
        points: dict[str, list[Decimal]] = {}
        for criterion in ("total_assets", "net_revenue", "average_employment"):
            ts = regime.thresholds(criterion)
            points[criterion] = sorted(
                {Decimal(0), *ts, *(t + Decimal("0.01") for t in ts), ts[2] * 2}
            )

        years = [
            YearFigures(
                _draw(points["total_assets"], rng),
                _draw(points["net_revenue"], rng),
                _draw(points["average_employment"], rng),
            )
            for _ in range(2)
        ]
        priors: frozenset[SizeClass | None] = (
            frozenset({rng.choice(CLASSES)})
            if regime.carry_over and rng.random() < 0.5
            else NOTHING_CARRIED
        )
        result = classify_year(regime, years[0], years[1], priors)
        if result.size_class == UNDETERMINED:
            continue
        decided += 1
        for _ in range(20):
            filled = [
                YearFigures(
                    _fill(y.total_assets, points["total_assets"], rng),
                    _fill(y.revenue, points["net_revenue"], rng),
                    _fill(y.employment, points["average_employment"], rng),
                )
                for y in years
            ]
            again = classify_year(regime, filled[0], filled[1], priors)
            assert again.size_class == result.size_class, (years, filled, priors)
    assert decided > 100


# --- the dataset ---------------------------------------------------------------------------------

KRS = "0000000001"


def _filing(
    config: MappingConfig,
    year: int,
    assets: str,
    revenue: str,
    *,
    prior: tuple[str, str] | None = None,
    ref: str | None = None,
    known_from: date | None = None,
    version: str = "full-2018-v1-2",
    grade: str = "pass",
) -> pl.DataFrame:
    """A calculation-variant statement whose revenue line is exact from 2025's form (R2025)."""
    revenue_code = "IS.CALC.A.R2025"
    values = {("BS.ASSETS", "current_year"): assets, (revenue_code, "current_year"): revenue}
    if prior is not None:
        values[("BS.ASSETS", "prior_year")] = prior[0]
        values[(revenue_code, "prior_year")] = prior[1]
    frame = _frame(
        values,
        config,
        ref=ref or f"por_{year}",
        period=(date(year, 1, 1), date(year, 12, 31)),
        known_from=known_from or date(year + 1, 6, 30),
        version=version,
    )
    return frame.with_columns(pl.lit(grade).alias("quality_grade"))


def _employment(frame: pl.DataFrame, value: str) -> pl.DataFrame:
    head = frame.row(0, named=True)
    return pl.DataFrame(
        [
            {
                "krs": head["krs"],
                "fiscal_year": head["fiscal_year"],
                "period_start": head["period_start"],
                "period_end": head["period_end"],
                "item": "average_employment",
                "value_bool": None,
                "value_number": Decimal(value),
                "raw_value": value,
                "structure_version": head["structure_version"],
                "config_version": "x",
                "source_document_hash": head["source_document_hash"],
                "source_member": head["source_member"],
                "source_element_path": "/P_8",
                "document_ref": head["document_ref"],
                "known_from": head["known_from"],
                "ingestion_run_id": head["ingestion_run_id"],
            }
        ],
        schema=DISCLOSURE_COLUMNS,
    )


NO_DISCLOSURES = pl.DataFrame(schema=DISCLOSURE_COLUMNS)
NO_EVENTS = pl.DataFrame(schema=LEGAL_EVENTS_SCHEMA)


def _history(
    inputs: SizeClassInputs,
    *frames: pl.DataFrame,
    disclosures: pl.DataFrame = NO_DISCLOSURES,
    events: pl.DataFrame = NO_EVENTS,
) -> pl.DataFrame:
    return entity_size_class_history(pl.concat(frames), disclosures, events, THRESHOLDS, inputs)


def test_each_year_gets_a_row_dated_by_its_filing(
    mapping_config: MappingConfig, inputs: SizeClassInputs
) -> None:
    history = _history(
        inputs,
        _filing(mapping_config, 2022, "1000000", "2000000", prior=("1000000", "2000000")),
        _filing(mapping_config, 2023, "1000000", "2000000", prior=("1000000", "2000000")),
    )
    assert history.select("fiscal_year", "size_class", "known_from").rows() == [
        (2022, "micro", date(2023, 6, 30)),
        (2023, "micro", date(2024, 6, 30)),
    ]
    assert history["undetermined_reason"].null_count() == history.height


def test_a_correction_adds_a_row_and_keeps_the_earlier_one(
    mapping_config: MappingConfig, inputs: SizeClassInputs
) -> None:
    first = _filing(mapping_config, 2022, "1000000", "2000000", prior=("1000000", "2000000"))
    corrected = _filing(
        mapping_config,
        2022,
        "9000000",
        "9000000",
        prior=("1000000", "2000000"),
        ref="por_2022_korekta",
        known_from=date(2023, 9, 1),
    )
    history = _history(inputs, first, corrected)
    assert history.select("size_class", "known_from", "document_ref").rows() == [
        ("micro", date(2023, 6, 30), "por_2022"),
        ("small", date(2023, 9, 1), "por_2022_korekta"),
    ]
    # the row known on 2023-06-30 is the same with or without the later correction
    alone = _history(inputs, first)
    assert alone.row(0) == history.row(0)


def test_a_later_filing_of_one_year_can_add_a_row_for_the_next(
    mapping_config: MappingConfig, inputs: SizeClassInputs
) -> None:
    """From 2024 a class carries over, so 2025's class waits on 2024's being known."""
    micro = ("1000000", "2000000")
    small = ("9000000", "9000000")
    y2024 = _filing(mapping_config, 2024, *micro, prior=micro, known_from=date(2025, 9, 1))
    y2025 = _filing(mapping_config, 2025, *small, prior=micro, known_from=date(2025, 8, 1))
    history = _history(inputs, y2024, y2025)
    rows = history.filter(pl.col("fiscal_year") == 2025).select("size_class", "known_from").rows()
    # alone, 2025 has one year on each side of micro and nothing to keep: above micro; once 2024
    # is known to be micro, 2025 keeps it
    assert rows == [("small", date(2025, 8, 1)), ("micro", date(2025, 9, 1))]


def test_a_quarantined_statement_is_not_read(
    mapping_config: MappingConfig, inputs: SizeClassInputs
) -> None:
    history = _history(
        inputs,
        _filing(mapping_config, 2022, "1000000", "2000000", prior=("1000000", "2000000")),
        _filing(
            mapping_config,
            2023,
            "1000000",
            "2000000",
            prior=("1000000", "2000000"),
            grade="quarantined",
        ),
    )
    assert history["fiscal_year"].to_list() == [2022]


def test_the_form_filed_is_never_read(
    mapping_config: MappingConfig, inputs: SizeClassInputs
) -> None:
    def classes(version: str) -> list[tuple[object, ...]]:
        frame = _filing(
            mapping_config,
            2022,
            "1000000",
            "2000000",
            prior=("1000000", "2000000"),
            version=version,
        )
        return _history(inputs, frame).select("size_class", "size_class_basis").rows()

    assert classes("micro-2018-v1-2") == classes("full-2018-v1-2") == [("micro", "statutory")]


def test_employment_is_read_for_its_year_and_the_year_after(
    mapping_config: MappingConfig, inputs: SizeClassInputs
) -> None:
    # assets within micro, revenue beyond: employment decides, in both years
    y2024 = _filing(mapping_config, 2024, "1000000", "9000000", prior=("1000000", "9000000"))
    y2025 = _filing(mapping_config, 2025, "1000000", "9000000", prior=("1000000", "9000000"))
    without = _history(inputs, y2024, y2025)
    assert set(without["size_class"].to_list()) == {UNDETERMINED}
    assert without.filter(pl.col("fiscal_year") == 2024)["undetermined_reason"].to_list() == [
        "employment_decides"
    ]
    disclosures = pl.concat([_employment(y2024, "5"), _employment(y2025, "5")])
    rows = _history(inputs, y2024, y2025, disclosures=disclosures)
    latest = rows.sort("known_from").group_by("fiscal_year").last().sort("fiscal_year")
    # 2024's prior year (2023) has no employment filed, 2025's has 2024's
    assert latest.select("fiscal_year", "size_class", "prior_average_employment").rows() == [
        (2024, UNDETERMINED, None),
        (2025, "micro", Decimal("5.00")),
    ]


def test_the_first_year_of_activity_comes_from_the_registration(
    mapping_config: MappingConfig, inputs: SizeClassInputs
) -> None:
    filing = _filing(mapping_config, 2022, "1000000", "2000000")  # no prior-year figures
    without = _history(inputs, filing)
    assert without["size_class"].to_list() == [UNDETERMINED]
    registered = pl.DataFrame(
        [
            {
                "krs": KRS,
                "event_type": "registered",
                "event_date": date(2022, 3, 1),
                "known_from": date(2022, 3, 1),
                "source": "KRS",
                "source_document_hash": hashlib.sha256(b"x").hexdigest(),
                "event_year": 2022,
            }
        ],
        schema=LEGAL_EVENTS_SCHEMA,
    )
    with_registration = _history(inputs, filing, events=registered)
    assert with_registration.select("size_class", "first_year").rows() == [("micro", True)]


def test_a_rerun_gives_the_same_rows(
    mapping_config: MappingConfig, inputs: SizeClassInputs
) -> None:
    frames = [
        _filing(mapping_config, 2023, "1000000", "9000000", prior=("1000000", "2000000")),
        _filing(mapping_config, 2024, "5000000", "9000000", prior=("1000000", "9000000")),
    ]
    assert _history(inputs, *frames).equals(_history(inputs, *reversed(frames)))
