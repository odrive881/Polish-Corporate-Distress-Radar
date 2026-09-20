"""E1 Pandera contracts for the C2 output tables (`parsing/contracts.py`, AGENT_SPEC §6E1).

A contract failure means a bug in this package, so these tests pin what the
schemas actually reject: the §5 column set and order, the enums, the
identifier formats, the lineage columns that may never be null (invariant 3),
and the one-fact-per-line uniqueness key.
"""

from datetime import date
from decimal import Decimal

import pandera.errors as pa_errors
import polars as pl
import pytest

from distress_radar.parsing.accounting_identities import RESTATEMENT_SCHEMA
from distress_radar.parsing.contracts import (
    FINANCIAL_STATEMENTS_CANONICAL,
    RESTATEMENT_EVENTS,
)
from distress_radar.parsing.mapping_engine import CANONICAL_COLUMNS

HASH_A = "a" * 64
HASH_B = "b" * 64

_CANONICAL_ROW: dict[str, object] = {
    "krs": "0000498679",
    "nip": "5252445157",
    "regon": "146066640",
    "fiscal_year": 2022,
    "period_start": date(2022, 1, 1),
    "period_end": date(2022, 12, 31),
    "line_item": "BS.ASSETS.TOTAL",
    "value": Decimal("1234.56"),
    "statement_type": "balance_sheet",
    "variant": "n/a",
    "column": "current_year",
    "structure_version": "full-2018-v1-2",
    "source_document_hash": HASH_A,
    "source_member": "zip:SF.xml",
    "source_element_path": "/tns:JednostkaInna/tns:Bilans/jin:Aktywa/dtsf:KwotaA",
    "document_ref": "abc123",
    "known_from": date(2023, 7, 14),
    "ingestion_run_id": "0db2a464-96a7-458d-844f-a7c225ec9452",
    "quality_grade": "pass",
}

_RESTATEMENT_ROW: dict[str, object] = {
    "krs": "0000498679",
    "fiscal_year": 2022,
    "period_start": date(2022, 1, 1),
    "period_end": date(2022, 12, 31),
    "line_item": "BS.ASSETS.TOTAL",
    "restated_column": "prior_year",
    "originally_reported_value": Decimal("100.00"),
    "restated_value": Decimal("120.00"),
    "original_document_hash": HASH_A,
    "original_source_member": "zip:SF-2021.xml",
    "original_document_ref": "abc123",
    "restating_document_hash": HASH_B,
    "restating_source_member": "zip:SF-2022.xml",
    "restating_document_ref": "def456",
    "known_from": date(2023, 7, 14),
}


def canonical(**overrides: object) -> pl.DataFrame:
    return pl.DataFrame([_CANONICAL_ROW | overrides], schema=CANONICAL_COLUMNS)


def restatements(**overrides: object) -> pl.DataFrame:
    return pl.DataFrame([_RESTATEMENT_ROW | overrides], schema=RESTATEMENT_SCHEMA)


def test_a_well_formed_frame_passes() -> None:
    assert FINANCIAL_STATEMENTS_CANONICAL.validate(canonical()).height == 1
    assert RESTATEMENT_EVENTS.validate(restatements()).height == 1


def test_the_contract_covers_every_spec_column() -> None:
    """§5 is the source of truth for both tables' column sets."""
    assert list(FINANCIAL_STATEMENTS_CANONICAL.columns) == list(CANONICAL_COLUMNS)
    assert list(RESTATEMENT_EVENTS.columns) == list(RESTATEMENT_SCHEMA)


def test_extra_and_reordered_columns_are_rejected() -> None:
    with pytest.raises(pa_errors.SchemaError):
        FINANCIAL_STATEMENTS_CANONICAL.validate(canonical().with_columns(extra=pl.lit(1)))
    shuffled = canonical().select(reversed(list(CANONICAL_COLUMNS)))
    with pytest.raises(pa_errors.SchemaError):
        FINANCIAL_STATEMENTS_CANONICAL.validate(shuffled)


def test_a_missing_column_is_rejected() -> None:
    with pytest.raises(pa_errors.SchemaError):
        FINANCIAL_STATEMENTS_CANONICAL.validate(canonical().drop("known_from"))


@pytest.mark.parametrize(
    ("column", "bad"),
    [
        ("krs", "498679"),  # not zero-padded to 10
        ("krs", "000049867X"),
        ("source_document_hash", "A" * 64),  # upper case
        ("source_document_hash", "abc"),
        ("statement_type", "notes"),
        ("variant", "posredni"),
        ("column", "next_year"),
        ("quality_grade", "failed"),
    ],
)
def test_identifier_and_enum_values_are_checked(column: str, bad: str) -> None:
    with pytest.raises(pa_errors.SchemaError):
        FINANCIAL_STATEMENTS_CANONICAL.validate(canonical(**{column: bad}))


@pytest.mark.parametrize(
    "column",
    [
        "krs",
        "fiscal_year",
        "period_start",
        "period_end",
        "line_item",
        "value",
        "statement_type",
        "variant",
        "column",
        "structure_version",
        "source_document_hash",
        "source_member",
        "source_element_path",
        "document_ref",
        "known_from",
        "ingestion_run_id",
        "quality_grade",
    ],
)
def test_lineage_and_fact_columns_may_not_be_null(column: str) -> None:
    """Invariant 3: every fact carries its full lineage."""
    with pytest.raises(pa_errors.SchemaError):
        FINANCIAL_STATEMENTS_CANONICAL.validate(canonical(**{column: None}))


@pytest.mark.parametrize("column", ["nip", "regon"])
def test_only_the_secondary_identifiers_may_be_null(column: str) -> None:
    assert FINANCIAL_STATEMENTS_CANONICAL.validate(canonical(**{column: None})).height == 1


def test_one_fact_per_line_item_and_column_per_file() -> None:
    duplicated = pl.concat([canonical(), canonical()])
    with pytest.raises(pa_errors.SchemaError):
        FINANCIAL_STATEMENTS_CANONICAL.validate(duplicated)
    # the same line in the other column, and in the correction, are distinct facts
    ok = pl.concat(
        [canonical(), canonical(column="prior_year"), canonical(source_member="zip:SF-corr.xml")]
    )
    assert FINANCIAL_STATEMENTS_CANONICAL.validate(ok).height == 3


def test_restatement_events_reject_bad_columns_and_nulls() -> None:
    with pytest.raises(pa_errors.SchemaError):
        RESTATEMENT_EVENTS.validate(restatements(restated_column="current_year"))
    with pytest.raises(pa_errors.SchemaError):
        RESTATEMENT_EVENTS.validate(restatements(restating_document_hash="nope"))
    with pytest.raises(pa_errors.SchemaError):
        RESTATEMENT_EVENTS.validate(restatements(original_document_ref=None))
