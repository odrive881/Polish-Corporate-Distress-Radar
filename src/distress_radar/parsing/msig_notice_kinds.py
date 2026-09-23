"""Typing reduced MSiG notices (plan 0008 step F): `config/mappings/msig_notice_kinds.yaml`.

A notice's text is never stored (ADR 0009 addendum, item 3), so a notice is typed from its
reduced record: chapter code, vocabulary terms, and dates with the terms before them. The
first matching rule gives the notice kind and picks its `event_date`.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date
from pathlib import Path
from typing import Any, Literal, cast

import yaml
from pydantic import BaseModel, ConfigDict, model_validator

from distress_radar.acquisition.msig_client import Vocabulary, load_vocabulary
from distress_radar.parsing.canonical_schema import CONFIG_DIR


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class PreferDates(_Frozen):
    prefer: tuple[tuple[str, ...], ...]


class NoticeRule(_Frozen):
    kind: str
    chapters: tuple[str, ...]
    all: tuple[str, ...] = ()
    any: tuple[str, ...] = ()
    none: tuple[str, ...] = ()
    event: bool = True
    date: Literal["publication"] | PreferDates | None = None

    # No `date` on an event kind: the first body date, else none.
    @model_validator(mode="after")
    def _procedural_is_undated(self) -> NoticeRule:
        if not self.event and self.date is not None:
            raise ValueError(f"{self.kind}: a procedural kind has no date")
        return self

    def terms(self) -> set[str]:
        preferred = self.date.prefer if isinstance(self.date, PreferDates) else ()
        return {*self.all, *self.any, *self.none, *(t for group in preferred for t in group)}

    def matches(self, chapter: str | None, terms: set[str]) -> bool:
        return (
            chapter in self.chapters
            and all(t in terms for t in self.all)
            and (not self.any or any(t in terms for t in self.any))
            and not any(t in terms for t in self.none)
        )


class NoticeKinds(_Frozen):
    version: int
    rules: tuple[NoticeRule, ...]

    def classify(self, extracted: Mapping[str, Any]) -> NoticeRule | None:
        terms: set[str] = set(cast("list[str]", extracted.get("terms") or []))
        chapter = extracted.get("chapter_code")
        return next((r for r in self.rules if r.matches(chapter, terms)), None)


def event_date(rule: NoticeRule, extracted: Mapping[str, Any], published: date) -> date | None:
    """The notice's event date under `rule`'s date policy."""
    if rule.date == "publication":
        return published
    body: Sequence[Mapping[str, Any]] = [
        d for d in extracted.get("dates") or () if d["in"] == "body"
    ]
    groups = rule.date.prefer if isinstance(rule.date, PreferDates) else ()
    for group in groups:
        for d in body:
            if any(t in d["terms_before"] for t in group):
                return date.fromisoformat(d["date"])
    return date.fromisoformat(body[0]["date"]) if body else None


def load_notice_kinds(
    config_dir: Path = CONFIG_DIR, vocabulary: Vocabulary | None = None
) -> NoticeKinds:
    path = config_dir / "mappings" / "msig_notice_kinds.yaml"
    kinds = NoticeKinds.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))
    known = set((vocabulary or load_vocabulary()).terms)
    unknown = sorted({t for r in kinds.rules for t in r.terms()} - known)
    if unknown:
        raise ValueError(f"{path}: terms not in the MSiG vocabulary: {unknown}")
    return kinds
