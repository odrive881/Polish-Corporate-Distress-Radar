"""C1 + C2 for whole stored downloads, and C2-through-E2 idempotence (plan 0004)."""

import io
import zipfile
from datetime import date
from decimal import Decimal
from pathlib import Path

import polars as pl
import pytest

from distress_radar.parsing.accounting_identities import (
    grade,
    identity_check_results,
    run_identity_checks,
)
from distress_radar.parsing.canonical_schema import MappingConfig
from distress_radar.parsing.contracts import (
    FINANCIAL_STATEMENTS_CANONICAL,
    IDENTITY_CHECK_RESULTS,
)
from distress_radar.parsing.manifest import StatementSource
from distress_radar.parsing.mapping_engine import SORT_KEY, MappingError
from distress_radar.parsing.statements import classify_download, map_file
from distress_radar.parsing.xsd_validation import XsdValidator
from distress_radar.warehouse import write_dataset

STATEMENTS_DIR = Path(__file__).parent.parent / "fixtures" / "statements"
Y2022 = (STATEMENTS_DIR / "full_2018_v1_2_por_2022.xml").read_bytes()
Y2023 = (STATEMENTS_DIR / "full_2018_v1_2_por_2023.xml").read_bytes()
SHA = "0" * 64
PERIOD_2022 = (date(2022, 1, 1), date(2022, 12, 31))


def _zip(members: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, data in members.items():
            archive.writestr(name, data)
    return buffer.getvalue()


def _source(
    *rows: tuple[str, str | None, date | None, tuple[date, date]], sha: str = SHA
) -> StatementSource:
    return StatementSource(
        sha256=sha,
        krs="0000498679",
        nip="5892013083",
        regon="192796505",
        rows=tuple(
            (ref, name, submitted, period[0], period[1]) for ref, name, submitted, period in rows
        ),
    )


def test_statement_and_correction_in_one_download(
    mapping_config: MappingConfig, validator: XsdValidator
) -> None:
    source = _source(
        ("orig", "sf.xml", date(2023, 6, 30), PERIOD_2022),
        ("corr", "sf_korekta.xml", date(2023, 9, 1), PERIOD_2022),
    )
    raw = _zip({"sf.xml": Y2022, "sf_korekta.xml": Y2022, "sf.xml.XAdES": b"<Signatures/>"})
    outcomes = classify_download(source, raw, mapping_config, validator)
    assert [(o.source_member, o.status) for o in outcomes] == [
        ("zip:sf.xml", "valid"),
        ("zip:sf_korekta.xml", "valid"),
    ]
    frames = [map_file(o, source, mapping_config, "run-1") for o in outcomes]
    assert [f["document_ref"].unique().to_list() for f in frames] == [["orig"], ["corr"]]
    assert [f["known_from"].unique().to_list() for f in frames] == [
        [date(2023, 6, 30)],
        [date(2023, 9, 1)],
    ]
    assert frames[0]["source_document_hash"].unique().to_list() == [SHA]


def test_non_statement_outcomes(mapping_config: MappingConfig, validator: XsdValidator) -> None:
    source = _source(("pdf", "SF2023.xml", date(2024, 6, 1), PERIOD_2022))
    [pdf] = classify_download(
        source, _zip({"SF2023.xml": b"%PDF-1.4\n"}), mapping_config, validator
    )
    assert pdf.status == "needs_pdf_tier"

    unknown = Y2022.replace(b'wersjaSchemy="1-2"', b'wersjaSchemy="7-7"')
    [bad] = classify_download(source, _zip({"x.xml": unknown}), mapping_config, validator)
    assert (bad.status, bad.stage, bad.reason_code) == (
        "quarantined",
        "C1",
        "unknown_structure_version",
    )

    invalid = Y2022.replace(
        b"<dtsf:KwotaA>39402504.45</dtsf:KwotaA>", b"<dtsf:KwotaA>abc</dtsf:KwotaA>", 1
    )
    [xsd] = classify_download(source, _zip({"x.xml": invalid}), mapping_config, validator)
    assert (xsd.status, xsd.reason_code) == ("quarantined", "xsd_invalid")

    [broken] = classify_download(source, b"PK\x03\x04garbage", mapping_config, validator)
    assert (broken.status, broken.reason_code) == ("quarantined", "unknown_container")

    two_rows = _source(("a", "a.xml", None, PERIOD_2022), ("b", "b.xml", None, PERIOD_2022))
    [stray] = classify_download(two_rows, _zip({"c.xml": Y2022}), mapping_config, validator)
    assert (stray.status, stray.reason_code) == ("quarantined", "member_not_in_filing_index")


@pytest.mark.parametrize(
    ("rows", "reason"),
    [
        (
            (("r", "x.xml", date(2023, 6, 30), (date(2022, 1, 1), date(2022, 6, 30))),),
            "period_mismatch",
        ),
        ((("r", "x.xml", None, PERIOD_2022),), "known_from_missing"),
    ],
)
def test_manifest_disagreements_are_c2_quarantine(
    rows: tuple[tuple[str, str, date | None, tuple[date, date]], ...],
    reason: str,
    mapping_config: MappingConfig,
    validator: XsdValidator,
) -> None:
    source = _source(*rows)
    [outcome] = classify_download(source, _zip({"x.xml": Y2022}), mapping_config, validator)
    with pytest.raises(MappingError) as info:
        map_file(outcome, source, mapping_config, "run-1")
    assert info.value.reason_code == reason


def _pipeline(
    tmp_path: Path, mapping_config: MappingConfig, validator: XsdValidator
) -> dict[str, bytes]:
    sources = [
        (
            _source(("y22", "a.xml", date(2023, 6, 30), PERIOD_2022), sha="1" * 64),
            _zip({"a.xml": Y2022}),
        ),
        (
            _source(
                ("y23", "b.xml", date(2024, 6, 28), (date(2023, 1, 1), date(2023, 12, 31))),
                sha="2" * 64,
            ),
            _zip({"b.xml": Y2023}),
        ),
    ]
    frames = [
        map_file(outcome, source, mapping_config, "run-first")
        for source, raw in sources
        for outcome in classify_download(source, raw, mapping_config, validator)
    ]
    facts = pl.concat(frames)
    results = run_identity_checks(facts, mapping_config, Decimal("1.00"))
    graded = FINANCIAL_STATEMENTS_CANONICAL.validate(grade(facts, results).sort(SORT_KEY))
    checked = IDENTITY_CHECK_RESULTS.validate(identity_check_results(results, graded))
    write_dataset(graded, tmp_path, "financial_statements_canonical", "fiscal_year")
    write_dataset(checked, tmp_path, "identity_check_results", "fiscal_year")
    return {
        str(p.relative_to(tmp_path)): p.read_bytes() for p in sorted(tmp_path.rglob("*.parquet"))
    }


def test_end_to_end_output_is_byte_identical(
    tmp_path: Path, mapping_config: MappingConfig, validator: XsdValidator
) -> None:
    first = _pipeline(tmp_path / "one", mapping_config, validator)
    second = _pipeline(tmp_path / "two", mapping_config, validator)
    assert first == second
    assert len(first) == 4  # fiscal years 2022 and 2023, for both datasets
