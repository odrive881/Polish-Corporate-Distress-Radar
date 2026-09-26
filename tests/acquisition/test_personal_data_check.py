"""The standing personal-data check (plan 0011 step F; invariant 6, ADR 0009).

Every committed fixture passes the same scan the pipeline runs on stored objects, and both A3
assets carry the scan as a blocking asset check.
"""

from pathlib import Path

import pytest
from dagster_defs.assets.acquisition import PERSONAL_DATA_CHECK
from dagster_defs.definitions import defs

from distress_radar.acquisition.redaction import personal_data_markers

FIXTURES = Path(__file__).parent.parent / "fixtures"
SCANNED = sorted(
    p
    for p in FIXTURES.rglob("*")
    if p.is_file() and p.suffix in {".xml", ".json", ".zip", ".pdf", ".xades"}
)


def test_the_scan_covers_the_fixtures() -> None:
    assert len(SCANNED) > 30  # statements, RDF responses, legal records, BIR1


@pytest.mark.parametrize("path", SCANNED, ids=lambda p: str(p.relative_to(FIXTURES)))
def test_no_fixture_holds_personal_data(path: Path) -> None:
    """Signatures, file names, attachment names and PDF metadata, as in the raw store."""
    assert personal_data_markers(path.read_bytes()) == []


def test_both_a3_assets_block_on_the_scan() -> None:
    graph = defs.resolve_asset_graph()
    checks = {
        key.asset_key.to_user_string(): graph.get_check_spec(key)
        for key in graph.asset_check_keys
        if key.name == PERSONAL_DATA_CHECK
    }
    assert set(checks) == {"raw_filing_documents", "rdf_manual_import"}
    assert all(spec.blocking for spec in checks.values())
