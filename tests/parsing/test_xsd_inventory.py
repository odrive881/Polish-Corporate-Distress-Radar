"""Statutory line items read out of an MF XSD (`parsing/xsd_inventory.py`, plan 0004).

`test_mapping_coverage.py` uses this module to check each body file against the
official schema, so its own behaviour is pinned here: the shape it reports for
a small hand-built schema, and that it still reads the real MF schema.
"""

from pathlib import Path

import pytest

from distress_radar.parsing.canonical_schema import CONFIG_DIR
from distress_radar.parsing.xsd_inventory import (
    normalise_label,
    same_line,
    statement_line_items,
)

JIN_V1_2 = (
    CONFIG_DIR
    / "xsd/www.gov.pl/documents/2034621/2182793/JednostkaInnaStrukturyDanychSprFin_v1-2.xsd"
)

# A miniature of the MF shape: a named complexType whose anonymous nested
# elements are the statutory items. `Aktywa_A` is a section header (no amount
# type), `Aktywa_A_I` carries amounts and declares user-defined slots.
MINI_XSD = """<?xml version="1.0" encoding="UTF-8"?>
<xsd:schema xmlns:xsd="http://www.w3.org/2001/XMLSchema">
  <xsd:complexType name="BilansMini">
    <xsd:sequence>
      <xsd:element name="Aktywa" type="TKwotyPozycji">
        <xsd:annotation><xsd:documentation>  Aktywa
          razem </xsd:documentation></xsd:annotation>
        <xsd:complexType>
          <xsd:sequence>
            <xsd:element name="Aktywa_A">
              <xsd:annotation><xsd:documentation>A. Trwale</xsd:documentation></xsd:annotation>
              <xsd:complexType>
                <xsd:sequence>
                  <xsd:element name="Aktywa_A_I" type="TKwotyPozycji">
                    <xsd:annotation><xsd:documentation>I. Wartosci, w tym:</xsd:documentation></xsd:annotation>
                    <xsd:complexType>
                      <xsd:sequence>
                        <xsd:element name="PozycjaUszczegolawiajaca_1" type="TKwotyPozycji"/>
                        <xsd:element name="PozycjaUszczegolawiajaca_2" type="TKwotyPozycji"/>
                      </xsd:sequence>
                    </xsd:complexType>
                  </xsd:element>
                  <xsd:element name="Aktywa_A_II">
                    <xsd:complexType>
                      <xsd:complexContent>
                        <xsd:extension base="TPozycjaSprawozdania"/>
                      </xsd:complexContent>
                    </xsd:complexType>
                  </xsd:element>
                </xsd:sequence>
              </xsd:complexType>
            </xsd:element>
          </xsd:sequence>
        </xsd:complexType>
      </xsd:element>
    </xsd:sequence>
  </xsd:complexType>
</xsd:schema>
"""


@pytest.fixture(scope="module")
def mini(tmp_path_factory: pytest.TempPathFactory) -> Path:
    path = tmp_path_factory.mktemp("xsd") / "mini.xsd"
    path.write_text(MINI_XSD, encoding="utf-8")
    return path


def test_paths_are_nested_in_schema_order(mini: Path) -> None:
    items = statement_line_items(mini, "BilansMini")
    assert [i.path for i in items] == [
        ("Aktywa",),
        ("Aktywa", "Aktywa_A"),
        ("Aktywa", "Aktywa_A", "Aktywa_A_I"),
        ("Aktywa", "Aktywa_A", "Aktywa_A_II"),
    ]


def test_user_defined_lines_are_not_statutory_items(mini: Path) -> None:
    """`PozycjaUszczegolawiajaca_N` is reported as a slot, never as an item."""
    items = statement_line_items(mini, "BilansMini")
    assert not any("Pozycja" in name for i in items for name in i.path)
    by_path = {i.path: i for i in items}
    assert by_path[("Aktywa", "Aktywa_A", "Aktywa_A_I")].user_slots
    assert not by_path[("Aktywa", "Aktywa_A")].user_slots


def test_section_headers_carry_no_amounts(mini: Path) -> None:
    by_path = {i.path: i for i in statement_line_items(mini, "BilansMini")}
    assert by_path[("Aktywa",)].has_amounts  # type="TKwotyPozycji"
    assert by_path[("Aktywa", "Aktywa_A", "Aktywa_A_II")].has_amounts  # extension base
    assert not by_path[("Aktywa", "Aktywa_A")].has_amounts


def test_labels_are_whitespace_normalised(mini: Path) -> None:
    items = {i.path: i.label for i in statement_line_items(mini, "BilansMini")}
    assert items[("Aktywa",)] == "Aktywa razem"
    assert items[("Aktywa", "Aktywa_A", "Aktywa_A_II")] == ""


def test_unknown_type_raises(mini: Path) -> None:
    with pytest.raises(ValueError, match="expected one complexType"):
        statement_line_items(mini, "BilansNieistniejacy")


@pytest.mark.parametrize(
    "type_name", ["BilansJednostkaInna", "RZiSJednostkaInna", "RachPrzeplywowJednostkaInna"]
)
def test_reads_the_real_mf_schema(type_name: str) -> None:
    items = statement_line_items(JIN_V1_2, type_name)
    assert len(items) > 20
    assert all(i.path for i in items)
    assert any(i.user_slots for i in items)
    assert any(i.has_amounts for i in items)


def test_normalise_label_only_ignores_how_a_label_is_written() -> None:
    assert normalise_label("Aktywa  trwałe:") == normalise_label("aktywa trwałe")
    assert normalise_label("– zapasy") == normalise_label("— zapasy")
    # A note or a marker is text, so this comparison sees them.
    assert normalise_label("Aktywa trwałe, w tym środki trwałe") != normalise_label("Aktywa trwałe")
    assert normalise_label("- zapasy") != normalise_label("zapasy")


def test_same_line_tolerates_presentation_but_not_a_narrowing() -> None:
    """The rule behind `test_every_code_label_matches_its_xsd_label` (plan 0005 step A)."""
    assert same_line("Aktywa trwałe, w tym środki trwałe", "Aktywa trwałe")
    assert same_line("a) do 12 miesięcy", "do 12 miesięcy")
    assert same_line("Zysk (strata) brutto (A - B)", "Zysk (strata) brutto (A-B)")
    assert same_line(
        "Kapitał podstawowy (dla jednostek innych niż spółki kapitałowe).",
        "Kapitał podstawowy",
    )
    # The 2025 narrowing: the words BEFORE "w tym" change, so it is a different line.
    assert not same_line(
        "Przychody netto ze sprzedaży towarów i materiałów",
        "Przychody netto ze sprzedaży towarów",
    )
    assert not same_line("Przychody finansowe", "Pozostałe koszty operacyjne")
