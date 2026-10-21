"""Unit corrections and the unit plausibility check (plan 0023). Every filing below is invented."""

# polars's signatures reference types pyright cannot resolve; scoped to this module.
# pyright: reportUnknownMemberType=false

from __future__ import annotations

from datetime import date
from decimal import Decimal

import polars as pl
import pytest
from pydantic import ValidationError
from tests.parsing.test_mapping_engine import STATEMENTS_DIR, _retag_to_thousands, load

from distress_radar.parsing.canonical_schema import (
    MappingConfig,
    UnitCheck,
    UnitCorrection,
    UnitCorrections,
)
from distress_radar.parsing.mapping_engine import MappingError, parse_statement
from distress_radar.parsing.unit_plausibility import SHARE_CAPITAL, scaled_pairs, suspects
from distress_radar.parsing.version_detection import detect

CHECK = UnitCheck(tolerance_log10=0.5, min_share_scaled=0.8, min_shared_lines=3)


def _correction(**changes: object) -> UnitCorrection:
    fields: dict[str, object] = {
        "id": "krs_0000000001_fy2021",
        "source_document_hash": "a" * 64,
        "source_member": "zip:id-1.xml",
        "structure_version": "full-2018-v1-2-tys",
        "declared_multiplier": 1000,
        "actual_multiplier": 1,
        "evidence": "share capital 1,000 times its other years'",
        "reviewed_by": "owner",
        "reviewed_on": date(2026, 10, 21),
    }
    return UnitCorrection.model_validate({**fields, **changes})


# --- the config ------------------------------------------------------------------------------------


def test_the_committed_corrections_load_and_name_real_versions(
    mapping_config: MappingConfig,
) -> None:
    corrections = mapping_config.unit_corrections
    assert corrections.version == "unit_corrections_v1"
    assert len(corrections.corrections) == 3
    for c in corrections.corrections:
        assert mapping_config.specs[c.structure_version].unit
        assert (c.declared_multiplier, c.actual_multiplier) == (1000, 1)


def test_a_correction_must_change_the_multiplier() -> None:
    with pytest.raises(ValidationError, match="actual multiplier is the declared one"):
        _correction(actual_multiplier=1000)


def test_a_file_is_corrected_at_most_once() -> None:
    with pytest.raises(ValidationError, match="listed twice"):
        UnitCorrections(
            version="unit_corrections_v9",
            check=CHECK,
            corrections=(_correction(), _correction(id="other")),
        )


def test_a_correction_must_match_what_its_structure_declares(
    mapping_config: MappingConfig,
) -> None:
    wrong = _correction(declared_multiplier=1, actual_multiplier=1000)
    corrections = mapping_config.unit_corrections.model_copy(update={"corrections": (wrong,)})
    with pytest.raises(ValidationError, match="declares no multiplier 1"):
        MappingConfig.model_validate(
            {**mapping_config.model_dump(), "unit_corrections": corrections.model_dump()}
        )


def test_only_a_corrected_file_gets_a_spec_hash_of_its_own(mapping_config: MappingConfig) -> None:
    corrected = mapping_config.unit_corrections.corrections[0]
    version = corrected.structure_version
    base = mapping_config.spec_hashes[version]
    assert mapping_config.file_spec_hash(version, "b" * 64, "zip:other.xml") == base
    own = mapping_config.file_spec_hash(
        version, corrected.source_document_hash, corrected.source_member
    )
    assert own != base and len(own) == 64


# --- the corrected parse ---------------------------------------------------------------------------


def test_a_corrected_file_is_mapped_in_the_reviewed_unit(mapping_config: MappingConfig) -> None:
    doc = _retag_to_thousands(load(STATEMENTS_DIR / "full_2018_v1_2_por_2022.xml"))
    spec = detect(doc, mapping_config).spec
    assert spec is not None
    as_filed = parse_statement(doc, spec, mapping_config)
    corrected = parse_statement(doc, spec, mapping_config, _correction())
    assert (as_filed.unit_multiplier, corrected.unit_multiplier) == (1000, 1)
    pairs = zip(as_filed.facts, corrected.facts, strict=True)
    assert all(a.value == b.value * 1000 for a, b in pairs)


def test_a_correction_for_another_structure_is_refused(mapping_config: MappingConfig) -> None:
    doc = _retag_to_thousands(load(STATEMENTS_DIR / "full_2018_v1_2_por_2022.xml"))
    spec = detect(doc, mapping_config).spec
    assert spec is not None
    with pytest.raises(MappingError, match="names small-2018-v1-2-tys"):
        parse_statement(
            doc, spec, mapping_config, _correction(structure_version="small-2018-v1-2-tys")
        )


# --- the check -------------------------------------------------------------------------------------

LINES = {"BS.ASSETS": 1_000_000, "BS.ASSETS.A": 400_000, "BS.ASSETS.B": 600_000}


def _filing(year: int, *, scale: int = 1, capital: int | None = 50_000) -> list[dict[str, object]]:
    """One filing for FY`year`: its current year and a prior-year column, every amount times
    `scale`; the company grows 10% a year."""
    doc = f"{year:02d}" * 32
    rows: list[dict[str, object]] = []
    for column, of_year in (("current_year", year), ("prior_year", year - 1)):
        growth = 1.1 ** (of_year - 2018)
        lines = {k: v * growth for k, v in LINES.items()}
        if capital is not None:
            lines[SHARE_CAPITAL] = capital
        for line, amount in lines.items():
            rows.append(
                {
                    "krs": "0000000001",
                    "fiscal_year": year,
                    "period_end": date(year, 12, 31),
                    "line_item": line,
                    "value": Decimal(round(amount * scale)),
                    "statement_type": "balance_sheet",
                    "variant": "n/a",
                    "column": column,
                    "source_document_hash": doc,
                    "source_member": f"zip:id-{year}.xml",
                    "document_ref": f"id-{year}",
                    "known_from": date(year + 1, 6, 30),
                }
            )
    return rows


def _facts(*filings: list[dict[str, object]]) -> pl.DataFrame:
    return pl.DataFrame([r for f in filings for r in f]).with_columns(
        pl.col("value").cast(pl.Decimal(20, 2))
    )


def test_a_filing_in_the_wrong_unit_is_the_one_suspected() -> None:
    facts = _facts(_filing(2019), _filing(2020), _filing(2021, scale=1000), _filing(2022))
    pairs = scaled_pairs(facts, CHECK)
    # FY2020 against FY2021's prior column, and FY2021 against FY2022's: both about 1,000 off
    assert pairs.height == 2
    found = suspects(facts, CHECK)
    assert found.get_column("document_ref").to_list() == ["id-2021"]
    assert "about 1,000 apart" in found.get_column("detail").item(0)


def test_consistent_filings_raise_nothing() -> None:
    facts = _facts(_filing(2019), _filing(2020), _filing(2021))
    assert suspects(facts, CHECK).is_empty()


def test_without_share_capital_both_sides_are_suspected() -> None:
    facts = _facts(_filing(2020, capital=None), _filing(2021, scale=1000, capital=None))
    found = suspects(facts, CHECK)
    assert found.get_column("document_ref").to_list() == ["id-2020", "id-2021"]
    assert all("cannot tell which" in d for d in found.get_column("detail").to_list())


def test_too_few_shared_lines_raise_nothing() -> None:
    facts = _facts(_filing(2020), _filing(2021, scale=1000))
    strict = CHECK.model_copy(update={"min_shared_lines": 10})
    assert suspects(facts, strict).is_empty()
