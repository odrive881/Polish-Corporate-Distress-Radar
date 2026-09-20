"""C2: map a detected statement onto the canonical chart (AGENT_SPEC §6C2).

All mapping knowledge is in `config/mappings/` (see `canonical_schema.py`);
this engine only follows it:

1. find the statement root and read the header (unit, reporting period);
2. for each statement present, walk the body's element paths in the spec's
   item namespace and read each amount column (`KwotaA`, `KwotaB`, `KwotaB1`);
3. convert amounts straight from text to `Decimal`, times the unit multiplier.

A missing optional element yields no fact, never a zero (invariant 4). Anything
that makes the document's numbers untrustworthy raises `MappingError`, which
the caller quarantines with its `reason_code`.
"""

# polars's signatures reference types pyright cannot resolve; scoped to this module.
# pyright: reportUnknownMemberType=false

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation

import polars as pl
from lxml import etree

from distress_radar.parsing.canonical_schema import (
    STATEMENT_TYPES,
    BodyItem,
    ChartItem,
    Column,
    MappingConfig,
    StatementBody,
    StatementName,
    StatementType,
    StructureSpec,
    Variant,
)
from distress_radar.parsing.containers import Element

CENT = Decimal("0.01")
MANDATORY_STATEMENTS: tuple[StatementName, ...] = ("Bilans", "RZiS")
# Statements that MAY start with a choice of variant sections. Whether a given
# body actually does is read from the body itself: the micro income statement
# has no variants, its items hang directly off RZiS (plan 0005 step D).
VARIANT_SECTIONS: dict[StatementName, tuple[str, ...]] = {
    "RZiS": ("RZiSPor", "RZiSKalk"),
    "RachPrzeplywow": ("PrzeplywyPosr", "PrzeplywyBezp"),
}


def _variant_sections(body: StatementBody, name: StatementName) -> tuple[str, ...]:
    """The variant sections this body declares for a statement, if any."""
    tops = {item.parts[0] for item in body.statements.get(name, ())}
    return tuple(s for s in VARIANT_SECTIONS.get(name, ()) if s in tops)


VALUE_DTYPE = pl.Decimal(precision=20, scale=2)
USER_LINE = re.compile(r"PozycjaUszczegolawiajaca_\d+")


class MappingError(Exception):
    def __init__(self, reason_code: str, detail: str) -> None:
        super().__init__(f"{reason_code}: {detail}")
        self.reason_code = reason_code
        self.detail = detail


@dataclass(frozen=True)
class Fact:
    line_item: str
    statement_type: StatementType
    variant: Variant
    column: Column
    value: Decimal
    source_element_path: str


@dataclass(frozen=True)
class ParsedStatement:
    structure_version: str
    period_start: date
    period_end: date
    unit_multiplier: int
    facts: tuple[Fact, ...]


def _elements(context: Element, xpath: str, namespaces: dict[str, str]) -> list[Element]:
    """Element results of a spec XPath; anything else means a broken spec."""
    result = context.xpath(xpath, namespaces=namespaces)
    if not isinstance(result, list):
        raise TypeError(f"spec XPath {xpath!r} does not select nodes")
    return [node for node in result if isinstance(node, Element)]


def _one(nodes: list[Element], what: str, reason: str) -> Element:
    if len(nodes) != 1:
        raise MappingError(reason, f"expected exactly one {what}, found {len(nodes)}")
    return nodes[0]


def _text(el: Element, what: str) -> str:
    text = (el.text or "").strip()
    if not text:
        raise MappingError("header_malformed", f"{what} is empty")
    return text


def _date(el: Element, what: str) -> date:
    try:
        return date.fromisoformat(_text(el, what))
    except ValueError as exc:
        raise MappingError("header_malformed", f"{what}: {exc}") from exc


def _amount(text: str | None, multiplier: int, where: str) -> Decimal:
    raw = (text or "").strip()
    try:
        value = Decimal(raw)
    except InvalidOperation as exc:
        raise MappingError("amount_malformed", f"{where}: {raw!r}") from exc
    if not value.is_finite():
        raise MappingError("amount_malformed", f"{where}: {raw!r}")
    value *= multiplier
    if value != value.quantize(CENT):
        raise MappingError("amount_malformed", f"{where}: {raw!r} has more than 2 decimal places")
    return value.quantize(CENT)


def parse_statement(root: Element, spec: StructureSpec, config: MappingConfig) -> ParsedStatement:
    ns = spec.namespaces
    amount_ns = ns[spec.amount_namespace]
    chart = config.chart.by_code()

    top = _one(
        _elements(root, spec.statement_root, ns), spec.statement_root, "statement_root_missing"
    )
    kod = _text(
        _one(
            _elements(top, spec.header.kod_sprawozdania, ns), "KodSprawozdania", "header_malformed"
        ),
        "KodSprawozdania",
    )
    if kod not in spec.unit:
        raise MappingError(
            "unit_unrecognised", f"KodSprawozdania {kod!r} is not one of {sorted(spec.unit)}"
        )
    multiplier = spec.unit[kod]
    period_start = _date(
        _one(_elements(top, spec.header.period_start, ns), "OkresOd", "header_malformed"), "OkresOd"
    )
    period_end = _date(
        _one(_elements(top, spec.header.period_end, ns), "OkresDo", "header_malformed"), "OkresDo"
    )
    if period_end < period_start:
        raise MappingError(
            "header_malformed", f"period ends {period_end} before it starts {period_start}"
        )

    facts: list[Fact] = []
    for name in spec.statements:
        # A spec may accept the statement in more than one shape, each implying
        # its own body: a small filing carries either the small statements or
        # the full ones, chosen per statement (plan 0005 step D). Exactly one
        # may be present.
        present_alts = [
            (alt, _one(found, name, "statement_duplicated"))
            for alt in spec.alternatives(name)
            if (found := _elements(top, alt.xpath, ns))
        ]
        if not present_alts:
            if name in MANDATORY_STATEMENTS:
                raise MappingError("required_statement_missing", f"no {name}")
            continue
        if len(present_alts) > 1:
            raise MappingError(
                "statement_body_ambiguous",
                f"{name} present as {[a.xpath for a, _ in present_alts]}",
            )
        alt, statement = present_alts[0]
        alt_body = config.bodies[alt.body]
        alt_item_ns = ns[alt.item_namespace]
        sections = _variant_sections(alt_body, name)
        if sections:
            present = [s for s in sections if statement.find(f"{{{alt_item_ns}}}{s}") is not None]
            if len(present) != 1:
                raise MappingError("statement_variant_ambiguous", f"{name} has sections {present}")
        prefix = f"{spec.statement_root}/{alt.xpath}"
        facts.extend(
            _statement_facts(
                statement,
                alt_body.statements.get(name, ()),
                name,
                prefix,
                alt.item_namespace,
                spec,
                chart,
                alt_item_ns,
                amount_ns,
                multiplier,
            )
        )
    return ParsedStatement(
        spec.structure_version, period_start, period_end, multiplier, tuple(facts)
    )


def _statement_facts(
    statement: Element,
    items: tuple[BodyItem, ...],
    name: StatementName,
    prefix: str,
    item_prefix: str,
    spec: StructureSpec,
    chart: dict[str, ChartItem],
    item_ns: str,
    amount_ns: str,
    multiplier: int,
) -> list[Fact]:
    elements: dict[str, Element | None] = {}
    facts: list[Fact] = []
    for item in items:
        parent_path, _, leaf = item.path.rpartition("/")
        parent = statement if not parent_path else elements.get(parent_path)
        el = None if parent is None else parent.find(f"{{{item_ns}}}{leaf}")
        elements[item.path] = el
        if el is None:
            if item.required:
                raise MappingError("required_item_missing", f"{name}/{item.path}")
            continue
        element_path = prefix + "".join(f"/{item_prefix}:{p}" for p in item.parts)
        if item.code is not None:
            code = spec.code_overrides.get(item.code, item.code)
            chart_item = chart[code]
            for column_tag, column in spec.columns.items():
                amount = el.find(f"{{{amount_ns}}}{column_tag}")
                if amount is None:
                    continue
                where = f"{element_path}/{spec.amount_namespace}:{column_tag}"
                value = _amount(amount.text, multiplier, where)
                facts.append(
                    Fact(code, chart_item.statement_type, chart_item.variant, column, value, where)
                )
        if item.user_code is not None:
            facts.extend(
                _user_line_facts(
                    el,
                    item.user_code,
                    element_path,
                    item_prefix,
                    spec,
                    chart,
                    item_ns,
                    amount_ns,
                    multiplier,
                )
            )
    return _check_balance_sheet(name, facts)


def _user_line_facts(
    el: Element,
    user_code: str,
    element_path: str,
    item_prefix: str,
    spec: StructureSpec,
    chart: dict[str, ChartItem],
    item_ns: str,
    amount_ns: str,
    multiplier: int,
) -> list[Fact]:
    """One fact per column: the sum of the element's interleaved user-defined lines."""
    lines = [
        child
        for child in el.iterchildren(f"{{{item_ns}}}*")
        if USER_LINE.fullmatch(etree.QName(child).localname)
    ]
    if not lines:
        return []
    code = spec.code_overrides.get(user_code, user_code)
    chart_item = chart[code]
    facts: list[Fact] = []
    for column_tag, column in spec.columns.items():
        total = Decimal(0)
        paths: list[str] = []
        for line in lines:
            amount = line.find(f"{{{amount_ns}}}KwotyPozycji/{{{amount_ns}}}{column_tag}")
            if amount is None:
                continue
            local = etree.QName(line).localname
            position = 1 + len(list(line.itersiblings(line.tag, preceding=True)))
            where = (
                f"{element_path}/{item_prefix}:{local}[{position}]"
                f"/{spec.amount_namespace}:KwotyPozycji/{spec.amount_namespace}:{column_tag}"
            )
            total += _amount(amount.text, multiplier, where)
            paths.append(where)
        if paths:
            facts.append(
                Fact(
                    code,
                    chart_item.statement_type,
                    chart_item.variant,
                    column,
                    total,
                    " | ".join(paths),
                )
            )
    return facts


def _check_balance_sheet(name: StatementName, facts: list[Fact]) -> list[Fact]:
    if STATEMENT_TYPES[name] == "balance_sheet" and not facts:
        raise MappingError("required_item_missing", f"{name} has no amounts")
    return facts


@dataclass(frozen=True)
class DocumentContext:
    """Lineage and identity for one statement file, from the manifest."""

    krs: str
    nip: str | None
    regon: str | None
    document_ref: str
    source_document_hash: str
    source_member: str
    known_from: date
    ingestion_run_id: str


CANONICAL_COLUMNS: dict[str, pl.DataType | type[pl.DataType]] = {
    "krs": pl.String,
    "nip": pl.String,
    "regon": pl.String,
    "fiscal_year": pl.Int32,
    "period_start": pl.Date,
    "period_end": pl.Date,
    "line_item": pl.String,
    "value": VALUE_DTYPE,
    "statement_type": pl.String,
    "variant": pl.String,
    "column": pl.String,
    "structure_version": pl.String,
    "source_document_hash": pl.String,
    "source_member": pl.String,
    "source_element_path": pl.String,
    "document_ref": pl.String,
    "known_from": pl.Date,
    "ingestion_run_id": pl.String,
    "quality_grade": pl.String,
}
SORT_KEY = [
    "krs",
    "period_end",
    "document_ref",
    "source_member",
    "statement_type",
    "line_item",
    "column",
]


def to_frame(parsed: ParsedStatement, ctx: DocumentContext) -> pl.DataFrame:
    """Long-format canonical rows (AGENT_SPEC §5), `quality_grade` still unset."""
    n = len(parsed.facts)
    data: dict[str, list[object]] = {
        "krs": [ctx.krs] * n,
        "nip": [ctx.nip] * n,
        "regon": [ctx.regon] * n,
        "fiscal_year": [parsed.period_end.year] * n,
        "period_start": [parsed.period_start] * n,
        "period_end": [parsed.period_end] * n,
        "line_item": [f.line_item for f in parsed.facts],
        "value": [f.value for f in parsed.facts],
        "statement_type": [f.statement_type for f in parsed.facts],
        "variant": [f.variant for f in parsed.facts],
        "column": [f.column for f in parsed.facts],
        "structure_version": [parsed.structure_version] * n,
        "source_document_hash": [ctx.source_document_hash] * n,
        "source_member": [ctx.source_member] * n,
        "source_element_path": [f.source_element_path for f in parsed.facts],
        "document_ref": [ctx.document_ref] * n,
        "known_from": [ctx.known_from] * n,
        "ingestion_run_id": [ctx.ingestion_run_id] * n,
        "quality_grade": [None] * n,
    }
    return pl.DataFrame(data, schema=CANONICAL_COLUMNS).sort(SORT_KEY)


def empty_frame() -> pl.DataFrame:
    return pl.DataFrame(schema=CANONICAL_COLUMNS)
