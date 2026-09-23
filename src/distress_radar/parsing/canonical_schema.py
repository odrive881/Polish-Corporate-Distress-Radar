"""Typed view of the parsing configuration (C2): chart, statement bodies, structure specs.

- `config/mappings/canonical_chart.yaml`: the vocabulary of `line_item` codes.
- `config/mappings/structures/bodies/*.yaml`: statutory element paths per
  statement, with the hierarchy the identity checks walk.
- `config/mappings/structures/*.yaml`: one spec per structure version, binding
  a detected document version to a body.
- `config/mappings/structure_catalog.yaml`: versions recognised but not mapped.

`load_mapping_config()` loads all four and checks the references between them,
so a bad edit fails at load time rather than as missing facts.
"""

from __future__ import annotations

import hashlib
from collections import Counter
from datetime import date
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

CONFIG_DIR = Path(__file__).resolve().parents[3] / "config"

StatementType = Literal["balance_sheet", "income_statement", "cash_flow", "equity_changes"]
Variant = Literal["comparative", "calculation", "direct", "indirect", "n/a"]
Column = Literal["current_year", "prior_year", "prior_year_restated"]
StatementName = Literal["Bilans", "RZiS", "ZestZmianWKapitale", "RachPrzeplywow"]
Role = Literal[
    "total_assets",
    "total_equity_and_liabilities",
    "total_equity",
    "total_liabilities_and_provisions",
    "net_result_balance_sheet",
    "net_result",
    "net_cash_flow",
    "cash_opening",
    "cash_closing",
    "cash_fx_effect",
]

STATEMENT_TYPES: dict[StatementName, StatementType] = {
    "Bilans": "balance_sheet",
    "RZiS": "income_statement",
    "ZestZmianWKapitale": "equity_changes",
    "RachPrzeplywow": "cash_flow",
}


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


# --- canonical_chart.yaml ----------------------------------------------------------------------


class ChartItem(_Frozen):
    code: str = Field(pattern=r"^[A-Z]+(\.[A-Za-z0-9_]+)+$")
    statement_type: StatementType
    variant: Variant
    label_pl: str
    role: Role | None = None
    replaces: str | None = None  # an earlier code this one narrows or redefines


class CanonicalChart(_Frozen):
    version: int
    items: tuple[ChartItem, ...]

    @model_validator(mode="after")
    def _consistent(self) -> CanonicalChart:
        dupes = [c for c, n in Counter(i.code for i in self.items).items() if n > 1]
        if dupes:
            raise ValueError(f"duplicate chart codes: {dupes}")
        codes = {i.code for i in self.items}
        unknown = [i.code for i in self.items if i.replaces is not None and i.replaces not in codes]
        if unknown:
            raise ValueError(f"`replaces` names an unknown code: {unknown}")
        return self

    def by_code(self) -> dict[str, ChartItem]:
        return {i.code: i for i in self.items}

    def codes_with_role(self, role: Role) -> set[str]:
        return {i.code for i in self.items if i.role == role}


# --- structures/bodies/*.yaml --------------------------------------------------------------------


class BodyItem(_Frozen):
    path: str  # local names below the statement element, "/"-separated
    code: str | None = None
    section: bool = False  # header without amounts
    header: bool = False  # non-statutory heading amounts (schema 1-0): kept, never checked
    of_which: bool = False
    required: bool = False
    # Sum of the element's interleaved user-defined lines (PozycjaUszczegolawiajaca_N).
    user_code: str | None = None
    user_of_which: bool = False  # those lines are "w tym" breakdowns, not components

    @model_validator(mode="after")
    def _code_or_section(self) -> BodyItem:
        if (self.code is None) != self.section:
            raise ValueError(f"{self.path}: give exactly one of `code` or `section: true`")
        if self.header and self.code is None:
            raise ValueError(f"{self.path}: `header: true` needs a `code`")
        if self.user_of_which and self.user_code is None:
            raise ValueError(f"{self.path}: `user_of_which` needs a `user_code`")
        return self

    @property
    def parts(self) -> tuple[str, ...]:
        return tuple(self.path.split("/"))


class StatementBody(_Frozen):
    body: str
    no_subtotal_check: tuple[StatementName, ...] = ()
    # computed line -> [(sign, code), ...]
    formulas: dict[str, tuple[tuple[Literal[1, -1], str], ...]] = {}
    statements: dict[StatementName, tuple[BodyItem, ...]]

    @model_validator(mode="after")
    def _consistent(self) -> StatementBody:
        for name, items in self.statements.items():
            paths = [i.path for i in items]
            dupes = [p for p, n in Counter(paths).items() if n > 1]
            if dupes:
                raise ValueError(f"{self.body}/{name}: duplicate paths {dupes}")
            known = set(paths)
            orphans = [p for p in paths if "/" in p and p.rsplit("/", 1)[0] not in known]
            if orphans:
                raise ValueError(f"{self.body}/{name}: paths without a parent entry {orphans}")
        codes = [
            c
            for items in self.statements.values()
            for i in items
            for c in (i.code, i.user_code)
            if c
        ]
        dupes = [c for c, n in Counter(codes).items() if n > 1]
        if dupes:
            raise ValueError(f"{self.body}: codes mapped twice {dupes}")
        missing = {c for f in self.formulas.values() for _, c in f} | set(self.formulas)
        missing -= set(codes)
        if missing:
            raise ValueError(f"{self.body}: formulas use codes not in the body {sorted(missing)}")
        return self

    def codes(self) -> set[str]:
        return {
            c
            for items in self.statements.values()
            for i in items
            for c in (i.code, i.user_code)
            if c
        }


# --- structures/*.yaml ---------------------------------------------------------------------------


class Detect(_Frozen):
    root_namespace: str
    root_name: str
    kod_systemowy: str
    wersja_schemy: str

    @property
    def key(self) -> tuple[str, str, str, str]:
        return (self.root_namespace, self.root_name, self.kod_systemowy, self.wersja_schemy)


class HeaderPaths(_Frozen):
    kod_sprawozdania: str
    period_start: str
    period_end: str


class StatementAlternative(_Frozen):
    """One shape a statement may take in a document, with the body it implies.

    The small form accepts either the small statements (`BilansJednostkaMala`,
    items in `JednostkaMalaStruktury`) or the full ones (`BilansJednostkaInna`,
    items in `JednostkaInnaStruktury`), chosen per statement and invisible to
    header-based detection (plan 0005 step D). A spec lists the alternatives it
    accepts; the engine uses whichever the document actually contains.
    """

    xpath: str
    body: str
    item_namespace: str


class StructureSpec(_Frozen):
    structure_version: str = Field(pattern=r"^[a-z0-9-]+$")
    form: Literal["full", "small", "micro"]
    body: str  # the default body; a statement alternative may name another
    detect: Detect
    xsd: str
    effective_from: date | None
    effective_to: date | None
    namespaces: dict[str, str]
    statement_root: str
    header: HeaderPaths
    unit: dict[str, int]  # KodSprawozdania text -> multiplier to złoty
    statements: dict[StatementName, str | tuple[StatementAlternative, ...]]
    item_namespace: str
    amount_namespace: str
    columns: dict[str, Column]
    code_overrides: dict[str, str] = {}

    def alternatives(self, name: StatementName) -> tuple[StatementAlternative, ...]:
        """The shapes this spec accepts for a statement, always as alternatives."""
        entry = self.statements[name]
        if isinstance(entry, str):
            return (
                StatementAlternative(
                    xpath=entry, body=self.body, item_namespace=self.item_namespace
                ),
            )
        return entry

    @property
    def bodies_used(self) -> set[str]:
        return {self.body} | {
            alt.body for name in self.statements for alt in self.alternatives(name)
        }

    @model_validator(mode="after")
    def _consistent(self) -> StructureSpec:
        prefixes = [self.item_namespace, self.amount_namespace]
        prefixes += [a.item_namespace for n in self.statements for a in self.alternatives(n)]
        for prefix in prefixes:
            if prefix not in self.namespaces:
                raise ValueError(
                    f"{self.structure_version}: namespace prefix {prefix!r} is not bound"
                )
        for name in self.statements:
            alts = self.alternatives(name)
            if len({a.xpath for a in alts}) != len(alts):
                raise ValueError(f"{self.structure_version}/{name}: duplicate alternative xpath")
        if not self.unit or any(m not in (1, 1000) for m in self.unit.values()):
            raise ValueError(f"{self.structure_version}: unit multipliers must be 1 or 1000")
        return self


class CatalogEntry(_Frozen):
    root_namespace: str
    root_name: str
    kod_systemowy: str
    wersja_schemy: str
    xsd: str

    @property
    def key(self) -> tuple[str, str, str, str]:
        return (self.root_namespace, self.root_name, self.kod_systemowy, self.wersja_schemy)


class StructureCatalog(_Frozen):
    versions: tuple[CatalogEntry, ...]


# --- the bundle ------------------------------------------------------------------------------------


class MappingConfig(_Frozen):
    chart: CanonicalChart
    bodies: dict[str, StatementBody]
    specs: dict[str, StructureSpec]
    catalog: StructureCatalog
    # structure_version -> SHA-256 over the files that shape its output
    spec_hashes: dict[str, str]

    @model_validator(mode="after")
    def _references(self) -> MappingConfig:
        chart_codes = set(self.chart.by_code())
        for body in self.bodies.values():
            unknown = body.codes() - chart_codes
            if unknown:
                raise ValueError(f"body {body.body}: codes not in the chart {sorted(unknown)}")
        keys: dict[tuple[str, str, str, str], str] = {}
        for spec in self.specs.values():
            unknown_bodies = spec.bodies_used - set(self.bodies)
            if unknown_bodies:
                raise ValueError(f"{spec.structure_version}: unknown body {sorted(unknown_bodies)}")
            # An override may target any body the spec can reach, since which one
            # applies is decided per statement from the document (plan 0005 step D).
            body_codes = {c for b in spec.bodies_used for c in self.bodies[b].codes()}
            for old, new in spec.code_overrides.items():
                if old not in body_codes or new not in chart_codes:
                    raise ValueError(f"{spec.structure_version}: bad code override {old} -> {new}")
            if spec.detect.key in keys:
                raise ValueError(
                    f"{spec.structure_version} and {keys[spec.detect.key]} detect the same key"
                )
            keys[spec.detect.key] = spec.structure_version
        overlap = [e.key for e in self.catalog.versions if e.key in keys]
        if overlap:
            raise ValueError(f"catalog lists mapped versions: {overlap}")
        return self

    def spec_for(self, key: tuple[str, str, str, str]) -> StructureSpec | None:
        for spec in self.specs.values():
            if spec.detect.key == key:
                return spec
        return None

    def is_catalogued(self, key: tuple[str, str, str, str]) -> bool:
        return any(e.key == key for e in self.catalog.versions)


def _yaml(path: Path) -> object:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def load_mapping_config(config_dir: Path = CONFIG_DIR) -> MappingConfig:
    mappings = config_dir / "mappings"
    chart_path = mappings / "canonical_chart.yaml"
    chart = CanonicalChart.model_validate(_yaml(chart_path))
    body_paths = {p.stem: p for p in sorted((mappings / "structures" / "bodies").glob("*.yaml"))}
    bodies = {name: StatementBody.model_validate(_yaml(p)) for name, p in body_paths.items()}
    for name, body in bodies.items():
        if body.body != name:
            raise ValueError(f"{body_paths[name]}: `body: {body.body}` must match the file name")
    specs: dict[str, StructureSpec] = {}
    hashes: dict[str, str] = {}
    for path in sorted((mappings / "structures").glob("*.yaml")):
        spec = StructureSpec.model_validate(_yaml(path))
        if spec.structure_version != path.stem:
            raise ValueError(f"{path}: structure_version must match the file name")
        specs[spec.structure_version] = spec
        digest = hashlib.sha256()
        # Every body the spec can reach, not just its default: a small filing
        # may carry the full-form statements (plan 0005 step D), so editing
        # `jednostka_inna` changes how those files are mapped and must give
        # them a new `spec_hash`.
        used = [body_paths[b] for b in sorted(spec.bodies_used) if b in body_paths]
        for part in (path, *used, chart_path):
            digest.update(part.read_bytes())
        hashes[spec.structure_version] = digest.hexdigest()
    catalog = StructureCatalog.model_validate(_yaml(mappings / "structure_catalog.yaml"))
    return MappingConfig(
        chart=chart, bodies=bodies, specs=specs, catalog=catalog, spec_hashes=hashes
    )
