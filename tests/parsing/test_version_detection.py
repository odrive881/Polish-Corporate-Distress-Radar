"""C1 structure version detection and offline XSD validation (plan 0004 step D)."""

import copy
import socket
from pathlib import Path

import pytest
import xmlschema
from lxml import etree

from distress_radar.parsing.canonical_schema import MappingConfig
from distress_radar.parsing.containers import DS_NS, safe_parser
from distress_radar.parsing.version_detection import DetectionError, detect
from distress_radar.parsing.xsd_validation import XsdValidator

STATEMENTS_DIR = Path(__file__).parent.parent / "fixtures" / "statements"
JIN_2018 = "http://www.mf.gov.pl/schematy/SF/DefinicjeTypySprawozdaniaFinansowe/2018/07/09/JednostkaInnaStruktury"
DTSF_2018 = "http://www.mf.gov.pl/schematy/SF/DefinicjeTypySprawozdaniaFinansowe/2018/07/09/DefinicjeTypySprawozdaniaFinansowe/"


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


def test_tampered_statement_fails_validation(
    mapping_config: MappingConfig, validator: XsdValidator
) -> None:
    root = load("full_2018_v1_2_por_2022.xml")
    spec = mapping_config.specs["full-2018-v1-2"]
    assert validator.validate(root, spec) == []
    amount = root.find(f".//{{{JIN_2018}}}Aktywa/{{{DTSF_2018}}}KwotaA")
    assert amount is not None
    amount.text = "not a number"
    errors = validator.validate(root, spec)
    assert errors and len(errors) <= 5


def test_enveloped_signature_is_ignored_and_document_untouched(
    mapping_config: MappingConfig, validator: XsdValidator
) -> None:
    root = load("full_2018_v1_2_por_2022.xml")
    etree.SubElement(root, f"{{{DS_NS}}}Signature").text = "signed"
    before = etree.tostring(root)
    assert validator.validate(root, mapping_config.specs["full-2018-v1-2"]) == []
    assert etree.tostring(root) == before


def test_validation_never_uses_the_network(
    mapping_config: MappingConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    def refuse(*args: object, **kwargs: object) -> None:
        raise AssertionError(f"network access attempted: {args!r}")

    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)
    monkeypatch.setattr(socket, "getaddrinfo", refuse)
    fresh = XsdValidator()  # compiles every schema from the vendored copies
    for spec in mapping_config.specs.values():
        assert isinstance(fresh.schema(spec.xsd), xmlschema.XMLSchema)
    assert (
        fresh.validate(
            copy.deepcopy(load("full_2025_w2_kalk_2025.xml")),
            mapping_config.specs["full-2025-w2-v1-0"],
        )
        == []
    )


def test_unvendored_schema_is_refused() -> None:
    with pytest.raises(KeyError, match="not vendored"):
        XsdValidator().schema("https://example.invalid/schema.xsd")
