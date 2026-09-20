"""C1 structure version detection (plan 0004 step D).

XSD validation lives in `test_xsd_validation.py`.
"""

from pathlib import Path

import pytest
from lxml import etree

from distress_radar.parsing.canonical_schema import MappingConfig
from distress_radar.parsing.containers import safe_parser
from distress_radar.parsing.version_detection import DetectionError, detect

STATEMENTS_DIR = Path(__file__).parent.parent / "fixtures" / "statements"
JIN_2018 = "http://www.mf.gov.pl/schematy/SF/DefinicjeTypySprawozdaniaFinansowe/2018/07/09/JednostkaInnaStruktury"


def load(name: str) -> etree._Element:
    return etree.fromstring((STATEMENTS_DIR / name).read_bytes(), safe_parser())


@pytest.mark.parametrize(
    ("name", "version"),
    [
        ("full_2018_v1_0_por_2018.xml", "full-2018-v1-0"),
        ("full_2018_v1_2_por_2022.xml", "full-2018-v1-2"),
        ("full_2025_w2_kalk_2025.xml", "full-2025-w2-v1-0"),
    ],
)
def test_detects_by_content(name: str, version: str, mapping_config: MappingConfig) -> None:
    detection = detect(load(name), mapping_config)
    assert detection.status == "mapped"
    assert detection.spec is not None and detection.spec.structure_version == version


def _with_schema_version(kod: str, wersja: str) -> etree._Element:
    root = load("full_2018_v1_2_por_2022.xml")
    header = root.find(f".//{{{JIN_2018}}}KodSprawozdania")
    assert header is not None
    header.set("kodSystemowy", kod)
    header.set("wersjaSchemy", wersja)
    return root


def test_same_namespace_different_schema_version_is_told_apart(
    mapping_config: MappingConfig,
) -> None:
    detection = detect(_with_schema_version("SFJINZ (1)", "1-0E"), mapping_config)
    assert detection.spec is not None and detection.spec.structure_version == "full-2018-v1-0"


def test_catalogued_but_unmapped_version(mapping_config: MappingConfig) -> None:
    root = etree.fromstring(
        b'<JednostkaMala xmlns="http://www.mf.gov.pl/schematy/SF/DefinicjeTypySprawozdaniaFinansowe/2018/07/09/JednostkaMalaWZlotych">'
        b'<Naglowek><j:KodSprawozdania xmlns:j="urn:x" kodSystemowy="SFJMAZ (1)" wersjaSchemy="1-2">'
        b"SprFinJednostkaMalaWZlotych</j:KodSprawozdania></Naglowek></JednostkaMala>"
    )
    detection = detect(root, mapping_config)
    assert (detection.status, detection.spec) == ("not_yet_mapped", None)


def test_unknown_version(mapping_config: MappingConfig) -> None:
    detection = detect(_with_schema_version("SFJINZ (1)", "9-9"), mapping_config)
    assert (detection.status, detection.spec) == ("unknown", None)


def test_missing_header_raises(mapping_config: MappingConfig) -> None:
    with pytest.raises(DetectionError):
        detect(etree.fromstring(b"<JednostkaInna xmlns='urn:x'/>"), mapping_config)
