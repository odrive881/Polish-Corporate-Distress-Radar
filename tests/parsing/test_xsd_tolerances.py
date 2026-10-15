"""C1's XSD tolerances (plan 0017 step E, decision 2).

A file parses when every XSD violation it has is one the tolerance list names: a path no stage
reads and an error kind. Anything else still quarantines it. An entry that reaches what C reads
(the header, a bound statement, an introduction item of its generation) is refused at load.
"""

import shutil
from datetime import date
from pathlib import Path

import pytest
import yaml
from lxml import etree

from distress_radar.parsing import statement_introduction
from distress_radar.parsing.canonical_schema import CONFIG_DIR, MappingConfig, load_mapping_config
from distress_radar.parsing.containers import ContainerMember, safe_parser
from distress_radar.parsing.statements import (  # pyright: ignore[reportPrivateUsage]
    Filing,
    _classify_member,
)
from distress_radar.parsing.xsd_tolerances import (
    ERROR_KINDS,
    below_statement_root,
    check_introduction,
    error_kind,
    split,
)
from distress_radar.parsing.xsd_validation import XsdValidator

STATEMENTS_DIR = Path(__file__).parent.parent / "fixtures" / "statements"
FIXTURE = "full_2018_v1_2_por_2022.xml"
DTSF_2018 = "http://www.mf.gov.pl/schematy/SF/DefinicjeTypySprawozdaniaFinansowe/2018/07/09/DefinicjeTypySprawozdaniaFinansowe/"
JIN_2018 = "http://www.mf.gov.pl/schematy/SF/DefinicjeTypySprawozdaniaFinansowe/2018/07/09/JednostkaInnaStruktury"


def _statement() -> etree._Element:
    return etree.fromstring((STATEMENTS_DIR / FIXTURE).read_bytes(), safe_parser())


def _placeholder_pkd(root: etree._Element) -> etree._Element:
    """The filers' defect: `KodPKD` holding only an XML comment."""
    pkd = root.find(f".//{{{DTSF_2018}}}KodPKD")
    assert pkd is not None
    pkd.text = None
    for child in list(pkd):
        pkd.remove(child)
    pkd.append(etree.Comment(" 1 "))
    return root


def _member(root: etree._Element) -> ContainerMember:
    return ContainerMember("zip:t.xml", "t.xml", "xml_statement", etree.tostring(root))


FILING = Filing("ref", date(2023, 6, 30), date(2022, 1, 1), date(2022, 12, 31))


def test_every_reason_seen_in_list_v1_has_its_kind() -> None:
    reasons = {
        "a simple content element can't have child elements": "child_in_simple_content",
        "value must be one of ['4110Z', '4120Z']": "not_in_enumeration",
        "value length cannot be lesser than 1": "too_short",
        "value doesn't match any pattern of ['\\\\d{10}']": "pattern_mismatch",
        "The content of element 'PodstawaPrawna' is not complete. Tag 'Art' expected.": (
            "content_incomplete"
        ),
        "Unexpected child with tag 'Plik' at position 1. Tag 'Opis' expected.": "unexpected_child",
        "invalid value ' ' for xs:decimal": "invalid_decimal",
    }
    assert {error_kind(r) for r in reasons} == set(ERROR_KINDS)
    assert all(error_kind(r) == k for r, k in reasons.items())
    assert error_kind("something no filer has produced yet") is None


def test_paths_are_read_below_the_statement_root(mapping_config: MappingConfig) -> None:
    spec = mapping_config.specs["full-2018-v1-2"]
    path = "/tns:JednostkaInna/tns:WprowadzenieDoSprawozdaniaFinansowego/tns:P_1/dtsf:KodPKD[1]"
    assert below_statement_root(path, spec) == (
        "WprowadzenieDoSprawozdaniaFinansowego",
        "P_1",
        "KodPKD",
    )
    assert below_statement_root("/tns:JednostkaInna", spec) is None
    assert below_statement_root("/tns:Other/tns:P_1", spec) is None
    assert below_statement_root(None, spec) is None


def test_a_placeholder_pkd_is_tolerated_and_the_file_parses(
    mapping_config: MappingConfig, validator: XsdValidator
) -> None:
    spec = mapping_config.specs["full-2018-v1-2"]
    root = _placeholder_pkd(_statement())
    violations = validator.violations(root, spec)
    assert violations
    refused, tolerated = split(violations, spec, mapping_config.tolerances)
    assert refused == []
    assert {t.tolerance_id for t in tolerated} == {"placeholder_pkd"}
    outcome = _classify_member(_member(root), FILING, mapping_config, validator)
    assert outcome.status == "valid"
    assert outcome.tolerated and outcome.tolerated[0].path.endswith("P_1C/KodPKD")


def test_an_untolerated_violation_still_quarantines_the_file(
    mapping_config: MappingConfig, validator: XsdValidator
) -> None:
    root = _placeholder_pkd(_statement())
    amount = root.find(f".//{{{JIN_2018}}}Aktywa/{{{DTSF_2018}}}KwotaA")
    assert amount is not None
    amount.text = "not a number"
    outcome = _classify_member(_member(root), FILING, mapping_config, validator)
    assert (outcome.status, outcome.reason_code) == ("quarantined", "xsd_invalid")
    assert outcome.tolerated == ()


def test_the_same_defect_in_a_read_element_is_never_tolerated(
    mapping_config: MappingConfig, validator: XsdValidator
) -> None:
    """A comment-only amount in the balance sheet is the same kind of error, in a statement."""
    root = _statement()
    amount = root.find(f".//{{{JIN_2018}}}Aktywa/{{{DTSF_2018}}}KwotaA")
    assert amount is not None
    amount.text = None
    amount.append(etree.Comment(" 1 "))
    outcome = _classify_member(_member(root), FILING, mapping_config, validator)
    assert outcome.status == "quarantined"


def _copy_config(tmp_path: Path) -> Path:
    target = tmp_path / "config"
    shutil.copytree(CONFIG_DIR / "mappings", target / "mappings")
    return target


def _write_version(config_dir: Path, n: int, entries: list[dict[str, object]]) -> None:
    path = config_dir / "mappings" / f"xsd_tolerances_v{n}.yaml"
    path.write_text(
        yaml.safe_dump({"version": f"xsd_tolerances_v{n}", "tolerances": entries}),
        encoding="utf-8",
    )


def test_the_highest_version_is_in_use(tmp_path: Path) -> None:
    config_dir = _copy_config(tmp_path)
    _write_version(config_dir, 2, [])
    assert load_mapping_config(config_dir).tolerances.version == "xsd_tolerances_v2"


@pytest.mark.parametrize(
    "path",
    [
        "Bilans/Aktywa/KwotaA",  # inside a bound statement
        "BilansJednostkaMala",  # a small spec's alternative
        "Naglowek/OkresOd",  # the header
        "Bil*",  # a pattern that can reach a statement
    ],
)
def test_an_entry_reaching_what_c_reads_is_refused_at_load(tmp_path: Path, path: str) -> None:
    config_dir = _copy_config(tmp_path)
    entry = {"id": "too_broad", "paths": [path], "errors": ["too_short"]}
    _write_version(config_dir, 2, [entry])
    with pytest.raises(ValueError, match="reaches"):
        load_mapping_config(config_dir)


def test_an_entry_reaching_an_introduction_item_is_refused(tmp_path: Path) -> None:
    config_dir = _copy_config(tmp_path)
    entry = {"id": "going_concern", "paths": ["Wprowadzenie*/P_5"], "errors": ["too_short"]}
    _write_version(config_dir, 2, [entry])
    config = load_mapping_config(config_dir)
    introduction = statement_introduction.load_introduction_config(set(config.specs))
    with pytest.raises(ValueError, match="P_5"):
        check_introduction(config, introduction)


def test_the_user_item_entry_must_stay_out_of_wariant_2(tmp_path: Path) -> None:
    """P_8 is a user item before wariant 2 and average employment in it: the entry is scoped."""
    config_dir = _copy_config(tmp_path)
    current = yaml.safe_load(
        (config_dir / "mappings" / "xsd_tolerances_v1.yaml").read_text(encoding="utf-8")
    )
    for entry in current["tolerances"]:
        entry.pop("structure_versions", None)
    _write_version(config_dir, 2, current["tolerances"])
    config = load_mapping_config(config_dir)
    introduction = statement_introduction.load_introduction_config(set(config.specs))
    with pytest.raises(ValueError, match="full-2025-w2-v1-0"):
        check_introduction(config, introduction)


def test_the_tolerance_list_in_use_passes_every_check(mapping_config: MappingConfig) -> None:
    introduction = statement_introduction.load_introduction_config(set(mapping_config.specs))
    check_introduction(mapping_config, introduction)


def test_a_spec_hash_moves_with_the_tolerances_that_apply_to_it(tmp_path: Path) -> None:
    """Editing an entry re-parses the versions it applies to, and only those."""
    config_dir = _copy_config(tmp_path)
    before = load_mapping_config(config_dir).spec_hashes
    path = config_dir / "mappings" / "xsd_tolerances_v1.yaml"
    current = yaml.safe_load(path.read_text(encoding="utf-8"))
    scoped = next(e for e in current["tolerances"] if e["id"] == "placeholder_user_item")
    scoped["errors"] = ["too_short"]
    path.write_text(yaml.safe_dump(current), encoding="utf-8")
    after = load_mapping_config(config_dir).spec_hashes
    assert set(scoped["structure_versions"]) == {v for v in after if after[v] != before[v]}
