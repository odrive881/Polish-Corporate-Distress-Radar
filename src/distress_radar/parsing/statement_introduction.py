"""The statement's introduction as structured disclosures (plan 0013 step B), without I/O.

Every mapped structure version carries a going-concern block in its introduction, and wariant 2
(fiscal years from 2025) adds average employment and whether an audit is required. Where each
item sits, and how its text reads as true or false, is config
(`config/mappings/statement_introduction.yaml`), pinned to the vendored XSDs by a test: the
element names shift between introduction variants, and wariant 2 codes the flags 1 and 2.

One row per item a statement reports, in long format like the canonical facts, with its element
path and the file's lineage (invariant 3). An item the document does not carry has no row: not
reported is not false. The threats' description (P_5C) is free text that can name people
(ADR 0009): only whether it is present is kept, never the text.
"""

# polars's signatures reference types pyright cannot resolve; scoped to this module.
# pyright: reportUnknownMemberType=false

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Literal

import polars as pl
import yaml
from lxml import etree
from pydantic import BaseModel, ConfigDict, Field, model_validator

from distress_radar.parsing.canonical_schema import CONFIG_DIR
from distress_radar.parsing.containers import Element
from distress_radar.parsing.mapping_engine import MappingError

DATASET = "statement_disclosures"
Layout = Literal["full", "short"]
ItemKind = Literal["flag", "text_present", "number"]
NUMBER_DTYPE = pl.Decimal(precision=12, scale=2)  # TKwota2Nieujemna: non-negative, 2 decimals


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class Generation(_Frozen):
    encoding: str
    structure_versions: tuple[str, ...] = Field(min_length=1)


class Item(_Frozen):
    kind: ItemKind
    negate: bool = False
    generations: tuple[str, ...] = Field(min_length=1)
    path: dict[Layout, str]
    xsd_phrase: str = Field(min_length=1)


class IntroductionConfig(_Frozen):
    version: str
    blocks: dict[str, Layout]
    encodings: dict[str, dict[str, bool]]
    generations: dict[str, Generation]
    items: dict[str, Item]

    @model_validator(mode="after")
    def _consistent(self) -> IntroductionConfig:
        seen: dict[str, str] = {}
        for name, generation in self.generations.items():
            if generation.encoding not in self.encodings:
                raise ValueError(f"generation {name}: unknown encoding {generation.encoding}")
            for version in generation.structure_versions:
                if version in seen:
                    raise ValueError(f"{version} is in generations {seen[version]} and {name}")
                seen[version] = name
        for name, item in self.items.items():
            unknown = set(item.generations) - set(self.generations)
            if unknown:
                raise ValueError(f"item {name}: unknown generations {sorted(unknown)}")
            if set(item.path) != set(self.blocks.values()):
                raise ValueError(f"item {name}: needs a path for every layout")
            if item.negate and item.kind != "flag":
                raise ValueError(f"item {name}: only a flag can be negated")
        return self

    def generation_of(self, structure_version: str) -> str:
        for name, generation in self.generations.items():
            if structure_version in generation.structure_versions:
                return name
        raise KeyError(f"{structure_version} is in no generation of {self.version}")


def load_introduction_config(
    structure_versions: set[str] | None = None, config_dir: Path = CONFIG_DIR
) -> IntroductionConfig:
    """The config, checked to cover exactly the mapped structure versions when they are given."""
    path = config_dir / "mappings" / "statement_introduction.yaml"
    config = IntroductionConfig.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))
    if structure_versions is not None:
        covered = {v for g in config.generations.values() for v in g.structure_versions}
        if covered != structure_versions:
            raise ValueError(
                f"{path}: generations must list every mapped structure version; "
                f"missing {sorted(structure_versions - covered)}, "
                f"unknown {sorted(covered - structure_versions)}"
            )
    return config


@dataclass(frozen=True)
class Disclosure:
    item: str
    value_bool: bool | None
    value_number: Decimal | None
    raw_value: str | None  # the element's text; None for free text, which is never kept
    source_element_path: str


def _local(el: Element) -> str:
    return etree.QName(el).localname


def _path(el: Element) -> str:
    """The element's path by local names, from the document root."""
    names = [_local(a) for a in reversed(list(el.iterancestors()))]
    return "/" + "/".join([*names, _local(el)])


def _child(parent: Element, relative: str) -> Element | None:
    node: Element | None = parent
    for name in relative.split("/"):
        assert node is not None
        node = next((c for c in node.iterchildren("{*}*") if _local(c) == name), None)
        if node is None:
            return None
    return node


def read_introduction(
    root: Element, structure_version: str, config: IntroductionConfig
) -> list[Disclosure]:
    """The items the statement's introduction reports. Raises `MappingError` on a value the
    config cannot read, which XSD validation should already have ruled out."""
    # "{*}*": elements only, in any namespace or none; comments are skipped.
    blocks = [el for el in root.iter("{*}*") if _local(el) in config.blocks]
    if not blocks:
        return []
    if len(blocks) > 1:
        raise MappingError("introduction_ambiguous", f"{len(blocks)} introduction blocks")
    block = blocks[0]
    layout = config.blocks[_local(block)]
    generation = config.generation_of(structure_version)
    encoding = config.encodings[config.generations[generation].encoding]
    out: list[Disclosure] = []
    for name, item in config.items.items():
        if generation not in item.generations:
            continue
        el = _child(block, item.path[layout])
        if el is None:
            continue
        text = (el.text or "").strip()
        where = _path(el)
        if item.kind == "text_present":
            out.append(Disclosure(name, bool(text), None, None, where))
        elif item.kind == "flag":
            if text not in encoding:
                raise MappingError("introduction_value_unknown", f"{where}: {text!r}")
            value = encoding[text] != item.negate
            out.append(Disclosure(name, value, None, text, where))
        else:
            try:
                number = Decimal(text)
            except InvalidOperation as exc:
                raise MappingError("introduction_value_unknown", f"{where}: {text!r}") from exc
            out.append(Disclosure(name, None, number, text, where))
    return out


@dataclass(frozen=True)
class DisclosureContext:
    """Lineage and identity for one statement file, from the manifest."""

    krs: str
    document_ref: str
    period_start: date
    period_end: date
    structure_version: str
    source_document_hash: str
    source_member: str
    known_from: date
    ingestion_run_id: str
    config_version: str


DISCLOSURE_COLUMNS: dict[str, pl.DataType | type[pl.DataType]] = {
    "krs": pl.String,
    "fiscal_year": pl.Int32,
    "period_start": pl.Date,
    "period_end": pl.Date,
    "item": pl.String,
    "value_bool": pl.Boolean,
    "value_number": NUMBER_DTYPE,
    "raw_value": pl.String,
    "structure_version": pl.String,
    "config_version": pl.String,
    "source_document_hash": pl.String,
    "source_member": pl.String,
    "source_element_path": pl.String,
    "document_ref": pl.String,
    "known_from": pl.Date,
    "ingestion_run_id": pl.String,
}
SORT_KEY = ["krs", "period_end", "document_ref", "source_member", "item"]


def to_frame(disclosures: list[Disclosure], ctx: DisclosureContext) -> pl.DataFrame:
    n = len(disclosures)
    data: dict[str, list[object]] = {
        "krs": [ctx.krs] * n,
        "fiscal_year": [ctx.period_end.year] * n,
        "period_start": [ctx.period_start] * n,
        "period_end": [ctx.period_end] * n,
        "item": [d.item for d in disclosures],
        "value_bool": [d.value_bool for d in disclosures],
        "value_number": [d.value_number for d in disclosures],
        "raw_value": [d.raw_value for d in disclosures],
        "structure_version": [ctx.structure_version] * n,
        "config_version": [ctx.config_version] * n,
        "source_document_hash": [ctx.source_document_hash] * n,
        "source_member": [ctx.source_member] * n,
        "source_element_path": [d.source_element_path for d in disclosures],
        "document_ref": [ctx.document_ref] * n,
        "known_from": [ctx.known_from] * n,
        "ingestion_run_id": [ctx.ingestion_run_id] * n,
    }
    return pl.DataFrame(data, schema=DISCLOSURE_COLUMNS).sort(SORT_KEY)


def empty_frame() -> pl.DataFrame:
    return pl.DataFrame(schema=DISCLOSURE_COLUMNS)
