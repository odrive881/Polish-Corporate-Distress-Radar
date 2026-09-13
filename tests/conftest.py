# Shared fixtures. The package is imported from its installed location (src layout).
from pathlib import Path

import pytest

from distress_radar.parsing.mapping_engine import parse_filing

FIXTURES_DIR = Path(__file__).parent / "fixtures"

def _all_fixture_stems():
    return sorted(p.stem for p in FIXTURES_DIR.glob("*.xml"))

#Supplies parsed filings for all fixtures, not jsut one
@pytest.fixture(params=_all_fixture_stems())
def parsed_filing(request):
    stem = request.param
    return parse_filing(FIXTURES_DIR / f"{stem}.xml")