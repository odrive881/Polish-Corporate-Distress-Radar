"""The statement's introduction as disclosures (plan 0013 step B)."""

# xmlschema's and polars's signatures reference types pyright cannot resolve.
# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false
# pyright: reportUnknownArgumentType=false, reportMissingTypeStubs=false

from __future__ import annotations

import copy
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
import xmlschema
from lxml import etree

from distress_radar.parsing.canonical_schema import MappingConfig, load_mapping_config
from distress_radar.parsing.containers import safe_parser
from distress_radar.parsing.contracts import STATEMENT_DISCLOSURES
from distress_radar.parsing.mapping_engine import MappingError
from distress_radar.parsing.statement_introduction import (
    DisclosureContext,
    IntroductionConfig,
    load_introduction_config,
    read_introduction,
    to_frame,
)
from distress_radar.parsing.version_detection import detect
from distress_radar.parsing.xsd_validation import XsdValidator

FIXTURES = Path(__file__).parent.parent / "fixtures" / "statements"


@pytest.fixture(scope="module")
def mapping() -> MappingConfig:
    return load_mapping_config()


@pytest.fixture(scope="module")
def config(mapping: MappingConfig) -> IntroductionConfig:
    return load_introduction_config(set(mapping.specs))


def _read(name: str, mapping: MappingConfig, config: IntroductionConfig) -> dict[str, object]:
    root = etree.parse(FIXTURES / name, safe_parser()).getroot()
    spec = detect(root, mapping).spec
    assert spec is not None
    return {
        d.item: d.value_bool if d.value_bool is not None else d.value_number
        for d in read_introduction(root, spec.structure_version, config)
    }


# --- the config against the XSDs ------------------------------------------------------------------


def test_every_mapped_structure_version_is_in_one_generation(mapping: MappingConfig) -> None:
    config = load_introduction_config(set(mapping.specs))
    assert config.generation_of("full-2025-w2-v1-0") == "wariant_2"
    with pytest.raises(ValueError, match="missing"):
        load_introduction_config({*mapping.specs, "full-2099-v9-9"})


def _xsd_child(parent: xmlschema.XsdElement, relative: str) -> xmlschema.XsdElement | None:
    node: xmlschema.XsdElement | None = parent
    for name in relative.split("/"):
        assert node is not None
        node = next(
            (
                c
                for c in node.iterchildren()
                if isinstance(c, xmlschema.XsdElement) and c.local_name == name
            ),
            None,
        )
        if node is None:
            return None
    return node


def test_every_item_matches_its_xsd_documentation(
    mapping: MappingConfig, config: IntroductionConfig
) -> None:
    """Each configured element, in every introduction block of every mapped version's XSD, is
    documented as the item it is read as, and its flag encoding is the XSD's own: xs:boolean
    before wariant 2, the codes 1 and 2 in it."""
    validator = XsdValidator()
    checked = 0
    for version, spec in sorted(mapping.specs.items()):
        generation = config.generation_of(version)
        encoding = config.generations[generation].encoding
        schema = validator.schema(spec.xsd)
        blocks = [
            el
            for el in schema.maps.iter_components(xsd_classes=xmlschema.XsdElement)
            if el.local_name in config.blocks
        ]
        assert blocks, f"{version}: no introduction block in its XSD"
        for block in blocks:
            layout = config.blocks[block.local_name]
            for item in config.items.values():
                el = _xsd_child(block, item.path[layout])
                if generation not in item.generations:
                    continue
                where = f"{version} {block.local_name}/{item.path[layout]}"
                assert el is not None, f"{where}: not in the XSD"
                assert item.xsd_phrase in str(el.annotation), f"{where}: documented otherwise"
                if item.kind == "flag":
                    simple = el.type
                    if encoding == "xsd_boolean":
                        assert simple.primitive_type.local_name == "boolean", where
                    else:
                        codes = {str(v) for v in simple.enumeration or []}
                        assert codes == set(config.encodings[encoding]), where
                checked += 1
    assert checked > 40  # 12 versions, their blocks and items: the pin is not vacuous


# --- reading statements --------------------------------------------------------------------------


def test_a_boolean_threat_is_read_as_the_warning(
    mapping: MappingConfig, config: IntroductionConfig
) -> None:
    """P_5B false ("there are circumstances") is a threat; P_5C absent is no row, not false."""
    got = _read("full_2018_v1_0_kalk_b1_2018.xml", mapping, config)
    assert got == {"going_concern_basis": True, "going_concern_threat": True}


def test_a_statement_not_prepared_as_a_going_concern(
    mapping: MappingConfig, config: IntroductionConfig
) -> None:
    got = _read("full_2018_v1_2_kalk_2021.xml", mapping, config)
    assert got == {
        "going_concern_basis": False,
        "going_concern_threat": True,
        "going_concern_threat_described": True,
    }


def test_a_sound_statement(mapping: MappingConfig, config: IntroductionConfig) -> None:
    got = _read("full_2018_v1_2_por_2022.xml", mapping, config)
    assert got == {"going_concern_basis": True, "going_concern_threat": False}


def test_wariant_2_codes_employment_and_audit_in_the_full_layout(
    mapping: MappingConfig, config: IntroductionConfig
) -> None:
    """P_5A 2 (not a going concern), P_5B 2 (threats), P_8 employment, P_9 2 (no audit)."""
    got = _read("full_2025_w2_kalk_2025.xml", mapping, config)
    assert got == {
        "going_concern_basis": False,
        "going_concern_threat": True,
        "going_concern_threat_described": True,
        "average_employment": Decimal(0),
        "audit_required": False,
    }


def test_wariant_2_micro_numbers_employment_one_place_earlier(
    mapping: MappingConfig, config: IntroductionConfig
) -> None:
    """Micro: employment at P_7 and the audit flag at P_8; its P_9 is free text, not read."""
    got = _read("micro_2025_w2_2025.xml", mapping, config)
    assert got["average_employment"] == Decimal(0) and got["audit_required"] is False
    assert got["going_concern_basis"] is True and got["going_concern_threat"] is True


def test_before_wariant_2_no_employment_is_read(
    mapping: MappingConfig, config: IntroductionConfig
) -> None:
    """P_7 and P_8 exist in earlier structures as accounting-policy text: never employment."""
    for name in ("full_2025_v1_3_por_2024.xml", "small_2018_v1_2_mala_por_2021.xml"):
        got = _read(name, mapping, config)
        assert "average_employment" not in got and "audit_required" not in got


def test_every_fixture_reports_the_going_concern_flags(
    mapping: MappingConfig, config: IntroductionConfig
) -> None:
    for path in sorted(FIXTURES.glob("*.xml")):
        got = _read(path.name, mapping, config)
        assert {"going_concern_basis", "going_concern_threat"} <= set(got), path.name


def test_an_unreadable_flag_is_an_error_not_a_guess(
    mapping: MappingConfig, config: IntroductionConfig
) -> None:
    root = etree.parse(FIXTURES / "full_2018_v1_2_por_2022.xml", safe_parser()).getroot()
    broken = copy.deepcopy(root)
    flag = next(el for el in broken.iter("{*}P_5B"))
    flag.text = "maybe"
    with pytest.raises(MappingError, match="introduction_value_unknown"):
        read_introduction(broken, "full-2018-v1-2", config)


def test_the_description_text_is_never_kept(
    mapping: MappingConfig, config: IntroductionConfig
) -> None:
    root = etree.parse(FIXTURES / "full_2025_w2_kalk_2025.xml", safe_parser()).getroot()
    described = next(
        d
        for d in read_introduction(root, "full-2025-w2-v1-0", config)
        if d.item == "going_concern_threat_described"
    )
    assert described.raw_value is None and described.value_bool is True


# --- the frame -----------------------------------------------------------------------------------


def test_the_frame_meets_its_contract_with_lineage(
    mapping: MappingConfig, config: IntroductionConfig
) -> None:
    root = etree.parse(FIXTURES / "full_2025_w2_kalk_2025.xml", safe_parser()).getroot()
    disclosures = read_introduction(root, "full-2025-w2-v1-0", config)
    ctx = DisclosureContext(
        krs="0000000001",
        document_ref="ref",
        period_start=date(2025, 1, 1),
        period_end=date(2025, 12, 31),
        structure_version="full-2025-w2-v1-0",
        source_document_hash="a" * 64,
        source_member="zip:x.xml",
        known_from=date(2026, 6, 30),
        ingestion_run_id="run",
        config_version=config.version,
    )
    frame = STATEMENT_DISCLOSURES.validate(to_frame(disclosures, ctx))
    assert frame.height == 5
    assert frame.get_column("fiscal_year").unique().to_list() == [2025]
    paths = frame.get_column("source_element_path").to_list()
    assert all(p.startswith("/Dokument/TrescDokumentu/JednostkaInna/") for p in paths)
    assert to_frame(disclosures, ctx).equals(to_frame(disclosures, ctx))
