"""The sign check (2026-10-23): a balance-sheet line below zero that the forms allow none on."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import polars as pl
import pytest
from pydantic import ValidationError

from distress_radar.parsing.canonical_schema import MappingConfig, SignCheck
from distress_radar.parsing.sign_plausibility import suspects
from distress_radar.parsing.unit_plausibility import SHARE_CAPITAL

CHECK = SignCheck(
    version="sign_check_v1",
    may_be_negative=("BS.EQUITY_LIABILITIES.A", "BS.EQUITY_LIABILITIES.A.VI"),
)


def _filing(ref: str, lines: dict[str, int], prior: dict[str, int] | None = None) -> list[dict]:
    rows: list[dict[str, object]] = []
    for column, amounts in (("current_year", lines), ("prior_year", prior or {})):
        for line, amount in amounts.items():
            rows.append(
                {
                    "krs": "0000000001",
                    "document_ref": ref,
                    "source_document_hash": ref.ljust(64, "0"),
                    "source_member": f"zip:{ref}.xml",
                    "statement_type": "income_statement"
                    if line.startswith("IS.")
                    else "balance_sheet",
                    "line_item": line,
                    "column": column,
                    "value": Decimal(amount),
                    "fiscal_year": 2024,
                    "period_end": date(2024, 12, 31),
                }
            )
    return rows


def _facts(*filings: list[dict]) -> pl.DataFrame:
    return pl.DataFrame([r for f in filings for r in f]).with_columns(
        pl.col("value").cast(pl.Decimal(20, 2))
    )


def test_a_negative_share_capital_is_suspected_and_an_allowed_line_is_not() -> None:
    facts = _facts(
        _filing(
            "a",
            {SHARE_CAPITAL: -9_000, "BS.EQUITY_LIABILITIES.A": -8_000, "BS.ASSETS": 100},
            prior={SHARE_CAPITAL: -8_500},
        ),
        _filing("b", {SHARE_CAPITAL: 5_000, "BS.EQUITY_LIABILITIES.A.VI": -300}),
        _filing("c", {"IS.COMP.A.II": -40}),  # not a balance-sheet line
    )
    found = suspects(facts, CHECK)
    assert found.get_column("document_ref").to_list() == ["a"]
    assert found.get_column("detail").item(0) == (
        f"below zero where the form allows none: {SHARE_CAPITAL} (current_year, prior_year)"
    )


def test_the_detail_names_three_lines_and_counts_the_rest() -> None:
    lines = {f"BS.ASSETS.B.{n}": -1 for n in ("I", "II", "III", "IV", "V")}
    [detail] = suspects(_facts(_filing("a", lines)), CHECK).get_column("detail").to_list()
    assert detail.count("BS.ASSETS.B.") == 3
    assert detail.endswith("; and 2 more")


def test_no_negative_line_gives_an_empty_frame_with_the_schema() -> None:
    found = suspects(_facts(_filing("a", {SHARE_CAPITAL: 5_000})), CHECK)
    assert found.is_empty()
    assert "detail" in found.columns


def test_the_committed_check_names_balance_sheet_codes_only(
    mapping_config: MappingConfig,
) -> None:
    assert mapping_config.sign_check.version == "sign_check_v1"
    assert SHARE_CAPITAL not in mapping_config.sign_check.may_be_negative
    with pytest.raises(ValidationError, match="not balance-sheet codes"):
        mapping_config.model_validate(
            {
                **dict(mapping_config),
                "sign_check": SignCheck(version="sign_check_v2", may_be_negative=("IS.COMP.A",)),
            }
        )


def test_a_line_listed_twice_is_refused() -> None:
    with pytest.raises(ValidationError, match="listed twice"):
        SignCheck(version="sign_check_v1", may_be_negative=(SHARE_CAPITAL, SHARE_CAPITAL))
