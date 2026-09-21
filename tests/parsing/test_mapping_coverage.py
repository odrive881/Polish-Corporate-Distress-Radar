"""Mapping coverage (AGENT_SPEC §6C2, §9.2).

- every structure version seen in the seed is mapped or explicitly catalogued;
- every spec's codes exist in the chart and its XSD is vendored;
- every spec's body lists exactly the statutory elements of its XSD;
- every `required: true` item resolves in at least one golden statement.
"""

import hashlib
import re
from pathlib import Path

import pytest
import yaml
from lxml import etree

from distress_radar.parsing.canonical_schema import (
    CONFIG_DIR,
    MappingConfig,
    StructureSpec,
)
from distress_radar.parsing.containers import safe_parser
from distress_radar.parsing.mapping_engine import parse_statement
from distress_radar.parsing.version_detection import detect
from distress_radar.parsing.xsd_inventory import same_line, statement_line_items
from distress_radar.parsing.xsd_validation import XsdValidator, load_xsd_catalog

FIXTURES_DIR = Path(__file__).parent.parent / "fixtures"
# Every committed fixture, so a new one is covered without editing this file.
GOLDEN = sorted((FIXTURES_DIR / "statements").glob("*.xml")) + [FIXTURES_DIR / "neobis_001.xml"]
XSD_DIR = CONFIG_DIR / "xsd"
XSD_NS = "{http://www.w3.org/2001/XMLSchema}"
# Every version with a spec. Parametrised so a new spec is covered automatically.
MAPPED = sorted(p.stem for p in (CONFIG_DIR / "mappings/structures").glob("*.yaml"))

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


FORM_OF_BODY = {
    "jednostka_inna": "JednostkaInna",
    "jednostka_mala": "JednostkaMala",
    "jednostka_mikro_v1_2": "JednostkaMikro",
    "jednostka_mikro_v1_3": "JednostkaMikro",
}


def _schema_for(spec_xsd: str, item_namespace: str, catalog: dict[str, Path]) -> Path:
    """The `…Struktury…` schema a spec's XSD imports for a given item namespace."""
    wanted = item_namespace.rstrip("/")
    root = etree.parse(str(catalog[spec_xsd])).getroot()
    for imported in root.iter(f"{XSD_NS}import"):
        if (imported.get("namespace") or "").rstrip("/") == wanted:
            return catalog[imported.get("schemaLocation") or ""]
    raise AssertionError(f"{spec_xsd} imports nothing for {item_namespace}")


def _statement_sources(spec: StructureSpec, config: MappingConfig):
    """(statement, body, declared XSD items) for every shape a spec accepts."""
    catalog = load_xsd_catalog(XSD_DIR)
    suffix = "WTys" if 1000 in spec.unit.values() else ""
    for name in spec.statements:
        for alt in spec.alternatives(name):
            body = config.bodies[alt.body]
            if name not in body.statements:
                continue
            schema = _schema_for(spec.xsd, spec.namespaces[alt.item_namespace], catalog)
            type_name = f"{name}{FORM_OF_BODY[alt.body]}{suffix}"
            yield name, body, statement_line_items(schema, type_name), alt


@pytest.mark.parametrize("version", MAPPED)
def test_body_lists_exactly_the_statutory_elements(
    version: str, mapping_config: MappingConfig
) -> None:
    spec = mapping_config.specs[version]
    for name, body, declared, alt in _statement_sources(spec, mapping_config):
        items = body.statements[name]
        assert [i.path for i in items] == ["/".join(d.path) for d in declared], (
            f"{version}/{name} via {alt.body}"
        )
        for item, element in zip(items, declared, strict=True):
            if not item.header:
                assert item.section == (not element.has_amounts), item.path
            assert (item.user_code is not None) == element.user_slots, item.path
            if item.user_code is not None:
                assert item.user_of_which == element.label.rstrip().endswith("w tym:"), item.path


@pytest.mark.parametrize("version", MAPPED)
def test_every_code_label_matches_its_xsd_label(
    version: str, mapping_config: MappingConfig
) -> None:
    """The chart label of the code an element maps to must be the XSD's own label.

    Two schema versions can declare the same element tree and still mean
    different things: 1-3 and wariant 2 narrowed income-statement lines from
    goods *and materials* to goods only, changing nothing but the XSD
    documentation. Only a label comparison catches a spec missing its `.R2025`
    overrides, which is how plan 0005 step A shipped `full-2025-v1-3` wrong.
    """
    spec = mapping_config.specs[version]
    chart = mapping_config.chart.by_code()
    for name, body, declared, _alt in _statement_sources(spec, mapping_config):
        for item, element in zip(body.statements[name], declared, strict=True):
            if not item.code or not element.label or item.header:
                continue  # the six CF headings carry a note of ours, not the XSD's
            code = spec.code_overrides.get(item.code, item.code)
            assert same_line(chart[code].label_pl, element.label), (
                f"{version} {item.path}: code {code} is labelled "
                f"{chart[code].label_pl!r} but the XSD says {element.label!r}"
            )


def _golden_for(spec: StructureSpec, config: MappingConfig) -> list[Path]:
    return [
        xml
        for xml in GOLDEN
        if detect(etree.fromstring(xml.read_bytes(), safe_parser()), config).spec == spec
    ]


@pytest.mark.parametrize("version", MAPPED)
def test_required_items_resolve_in_a_golden_statement(
    version: str, mapping_config: MappingConfig
) -> None:
    """Every `required: true` item resolves, for each shape the spec accepts.

    Versions with no golden fixture yet are skipped rather than silently
    passing; plan 0005 step E adds the short-form ones.
    """
    spec = mapping_config.specs[version]
    matching = _golden_for(spec, mapping_config)
    if not matching:
        pytest.skip(f"no golden statement for {version} yet")
    for xml in matching:
        root = etree.fromstring(xml.read_bytes(), safe_parser())
        parsed = parse_statement(root, spec, mapping_config)
        codes = {f.line_item for f in parsed.facts}
        required = {
            spec.code_overrides.get(i.code, i.code)
            for name in spec.statements
            for alt in spec.alternatives(name)
            for i in mapping_config.bodies[alt.body].statements.get(name, ())
            if i.required and i.code
        }
        # Only the body this document actually used can be required of it.
        assert required & codes, (xml.name, version)
        assert {c for c in required if c in codes} <= codes


def test_every_full_form_version_has_a_golden_statement(mapping_config: MappingConfig) -> None:
    """`-tys` is the exception: no seed filing uses it, so `test_mapping_engine`
    builds a synthetic thousands document instead of committing a fixture."""
    for version in (v for v in MAPPED if v.startswith("full-") and not v.endswith("-tys")):
        assert _golden_for(mapping_config.specs[version], mapping_config), version


@pytest.mark.parametrize("xml", GOLDEN, ids=lambda p: p.stem)
def test_no_fixture_contains_personal_data(xml: Path) -> None:
    """Invariant 6: the fixtures are public, and must name no natural person.

    Plan 0004 scanned its fixtures by hand; this makes the scan a gate, so a
    fixture added later cannot quietly reintroduce signer data. It checks the
    raw bytes, not just the text, so a signature block or an embedded
    attachment cannot hide one.
    """
    raw = xml.read_bytes()
    for marker in (b"PESEL", b"X509Certificate", b"SignatureValue", b"ds:Signature"):
        assert marker not in raw, f"{xml.name} contains {marker.decode()}"
    text = " ".join(
        t for t in etree.fromstring(raw, safe_parser()).itertext() if t and t.strip()
    )
    assert not re.search(r"\b\d{11}\b", text), f"{xml.name} has an 11-digit run (PESEL-shaped)"


def test_golden_statements_are_schema_valid(
    mapping_config: MappingConfig, validator: XsdValidator
) -> None:
    for xml in GOLDEN:
        root = etree.fromstring(xml.read_bytes(), safe_parser())
        detection = detect(root, mapping_config)
        assert detection.spec is not None, xml.name
        assert validator.validate(root, detection.spec) == [], xml.name
