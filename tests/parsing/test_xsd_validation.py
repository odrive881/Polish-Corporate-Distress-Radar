"""C1 offline XSD validation (plan 0004 step D).

Moved out of `test_version_detection.py` so the file mirrors `src/`
(DIRECTORY_STRUCTURE.md §5).
"""

import copy
import socket
from pathlib import Path

import pytest
import xmlschema
from lxml import etree

from distress_radar.parsing.canonical_schema import MappingConfig
from distress_radar.parsing.containers import DS_NS, safe_parser
from distress_radar.parsing.xsd_validation import XsdValidator

STATEMENTS_DIR = Path(__file__).parent.parent / "fixtures" / "statements"
JIN_2018 = "http://www.mf.gov.pl/schematy/SF/DefinicjeTypySprawozdaniaFinansowe/2018/07/09/JednostkaInnaStruktury"
DTSF_2018 = "http://www.mf.gov.pl/schematy/SF/DefinicjeTypySprawozdaniaFinansowe/2018/07/09/DefinicjeTypySprawozdaniaFinansowe/"


def load(name: str) -> etree._Element:
    return etree.fromstring((STATEMENTS_DIR / name).read_bytes(), safe_parser())


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
    validator = XsdValidator()
    assert not validator.has_schema("https://example.invalid/schema.xsd")
    with pytest.raises(KeyError, match="not vendored"):
        validator.schema("https://example.invalid/schema.xsd")
