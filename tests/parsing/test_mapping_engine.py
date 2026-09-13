import json
from pathlib import Path

from distress_radar.parsing.mapping_engine import parse_filing

FIXTURES_DIR = Path(__file__).parent.parent / "fixtures"


def test_parse_filing_matches_expected():
    xml_path = FIXTURES_DIR / "neobis_001.xml"
    expected_path = FIXTURES_DIR / "neobis_001_expected.json"
    expected = json.loads(expected_path.read_text(encoding="utf-8"))

    result = parse_filing(xml_path)

    assert result == expected
