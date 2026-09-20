"""Mapping coverage (AGENT_SPEC §6C2, §9.2).

- every structure version seen in the seed is mapped or explicitly catalogued;
- every spec's codes exist in the chart and its XSD is vendored;
- every spec's body lists exactly the statutory elements of its XSD;
- every `required: true` item resolves in at least one golden statement.
"""

import hashlib
from pathlib import Path

import pytest
import yaml
from lxml import etree

from distress_radar.parsing.canonical_schema import CONFIG_DIR, MappingConfig
from distress_radar.parsing.containers import safe_parser
from distress_radar.parsing.mapping_engine import parse_statement
from distress_radar.parsing.version_detection import detect
from distress_radar.parsing.xsd_inventory import statement_line_items
from distress_radar.parsing.xsd_validation import XsdValidator, load_xsd_catalog

FIXTURES_DIR = Path(__file__).parent.parent / "fixtures"
GOLDEN = sorted((FIXTURES_DIR / "statements").glob("full_*.xml")) + [
    FIXTURES_DIR / "neobis_001.xml"
]
XSD_DIR = CONFIG_DIR / "xsd"
XSD_NS = "{http://www.w3.org/2001/XMLSchema}"

# Every (kodSystemowy, wersjaSchemy) found in the Phase 1 seed downloads (plan 0004 survey).
SEED_VERSIONS = {
    ("SFJINZ (1)", "1-0E"),
    ("SFJINZ (1)", "1-2"),
    ("SFJINZ (1)", "1-3"),
    ("SFJINZ (2)", "1-0E"),
    ("SFJMAZ (1)", "1-0E"),
    ("SFJMAZ (1)", "1-2"),
    ("SFJMAZ (1)", "1-3"),
    ("SFJMIZ (1)", "1-0E"),
    ("SFJMIZ (1)", "1-2"),
    ("SFJMIZ (1)", "1-3"),
    ("SFJMIZ (2)", "1-0E"),
}


def test_every_seed_version_is_mapped_or_catalogued(mapping_config: MappingConfig) -> None:
    known = {s.detect.key[2:] for s in mapping_config.specs.values()}
    known |= {(e.kod_systemowy, e.wersja_schemy) for e in mapping_config.catalog.versions}
    assert SEED_VERSIONS <= known


def test_every_schema_is_vendored_with_its_imports(mapping_config: MappingConfig) -> None:
    catalog = load_xsd_catalog(XSD_DIR)
    urls = {s.xsd for s in mapping_config.specs.values()} | {
        e.xsd for e in mapping_config.catalog.versions
    }
    assert urls <= set(catalog)
    for path in catalog.values():
        assert path.is_file(), path
        for imported in (
            etree.parse(str(path)).getroot().iter(f"{XSD_NS}import", f"{XSD_NS}include")
        ):
            location = imported.get("schemaLocation")
            assert location is None or location in catalog, (
                f"{path.name} imports unvendored {location}"
            )


def test_vendored_schemas_match_their_recorded_hashes() -> None:
    raw = yaml.safe_load((XSD_DIR / "catalog.yaml").read_text(encoding="utf-8"))
    for url, entry in raw["schemas"].items():
        digest = hashlib.sha256((XSD_DIR / entry["path"]).read_bytes()).hexdigest()
        assert digest == entry["sha256"], url


def _struktury_file(spec_xsd: str, catalog: dict[str, Path]) -> Path:
    """The `…Struktury…` schema a root XSD imports: where the statement types live."""
    root = etree.parse(str(catalog[spec_xsd])).getroot()
    for imported in root.iter(f"{XSD_NS}import"):
        if "JednostkaInnaStruktury" in (imported.get("namespace") or ""):
            return catalog[imported.get("schemaLocation") or ""]
    raise AssertionError(f"{spec_xsd} imports no statement-structure schema")


@pytest.mark.parametrize(
    "version",
    [
        "full-2018-v1-0",
        "full-2018-v1-2",
        "full-2018-v1-2-tys",
        "full-2025-v1-3",
        "full-2025-w2-v1-0",
    ],
)
def test_body_lists_exactly_the_statutory_elements(
    version: str, mapping_config: MappingConfig
) -> None:
    spec = mapping_config.specs[version]
    body = mapping_config.bodies[spec.body]
    struktury = _struktury_file(spec.xsd, load_xsd_catalog(XSD_DIR))
    suffix = "WTys" if 1000 in spec.unit.values() else ""
    for statement, items in body.statements.items():
        declared = statement_line_items(struktury, f"{statement}JednostkaInna{suffix}")
        assert [i.path for i in items] == ["/".join(d.path) for d in declared], statement
        for item, element in zip(items, declared, strict=True):
            if not item.header:
                assert item.section == (not element.has_amounts), item.path
            assert (item.user_code is not None) == element.user_slots, item.path
            if item.user_code is not None:
                assert item.user_of_which == element.label.rstrip().endswith("w tym:"), item.path


@pytest.mark.parametrize(
    "version",
    [
        "full-2018-v1-0",
        "full-2018-v1-2",
        "full-2018-v1-2-tys",
        "full-2025-v1-3",
        "full-2025-w2-v1-0",
    ],
)
def test_every_code_label_matches_its_xsd_label(
    version: str, mapping_config: MappingConfig
) -> None:
    """The chart label of the code each element maps to must be the XSD's own label.

    Two schema versions can declare the same element tree and still mean
    different things: 1-3 and wariant 2 narrowed six income-statement lines
    from goods *and materials* to goods only, changing nothing but the XSD
    documentation. Only a label comparison catches a spec that is missing the
    `.R2025` overrides, which is how plan 0005 step A shipped `full-2025-v1-3`
    wrong. `test_body_lists_exactly_the_statutory_elements` cannot see it.
    """
    spec = mapping_config.specs[version]
    body = mapping_config.bodies[spec.body]
    struktury = _struktury_file(spec.xsd, load_xsd_catalog(XSD_DIR))
    suffix = "WTys" if 1000 in spec.unit.values() else ""
    chart = {item.code: item for item in mapping_config.chart.items}
    for statement, items in body.statements.items():
        declared = statement_line_items(struktury, f"{statement}JednostkaInna{suffix}")
        for item, element in zip(items, declared, strict=True):
            if not item.code or not element.label:
                continue
            if item.header:
                continue  # the six CF headings carry a note of ours, not the XSD's
            code = spec.code_overrides.get(item.code, item.code)
            assert chart[code].label_pl == element.label, (
                f"{version} {item.path}: code {code} is labelled "
                f"{chart[code].label_pl!r} but the XSD says {element.label!r}"
            )


@pytest.mark.parametrize(
    "version", ["full-2018-v1-0", "full-2018-v1-2", "full-2025-v1-3", "full-2025-w2-v1-0"]
)
def test_required_items_resolve_in_a_golden_statement(
    version: str, mapping_config: MappingConfig
) -> None:
    spec = mapping_config.specs[version]
    required = {
        spec.code_overrides.get(i.code, i.code)
        for items in mapping_config.bodies[spec.body].statements.values()
        for i in items
        if i.required and i.code
    }
    assert required
    matching = 0
    for xml in GOLDEN:
        root = etree.fromstring(xml.read_bytes(), safe_parser())
        if detect(root, mapping_config).spec != spec:
            continue
        matching += 1
        codes = {f.line_item for f in parse_statement(root, spec, mapping_config).facts}
        assert required <= codes, (xml.name, required - codes)
    assert matching, f"no golden statement for {version}"


def test_golden_statements_are_schema_valid(
    mapping_config: MappingConfig, validator: XsdValidator
) -> None:
    for xml in GOLDEN:
        root = etree.fromstring(xml.read_bytes(), safe_parser())
        detection = detect(root, mapping_config)
        assert detection.spec is not None, xml.name
        assert validator.validate(root, detection.spec) == [], xml.name
