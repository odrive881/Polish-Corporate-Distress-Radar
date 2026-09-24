"""Typed view of `config/statutory/procedure_taxonomy.yaml` (plan 0008 step B).

The taxonomy maps a source event (a KRS extract record, later a KRZ or MSiG notice) to a project
event type and the AGENT_SPEC §4.6 outcome class it starts, dated by the statute it falls under.
`load_procedure_taxonomy()` validates the cross-references, so a bad edit fails at load time
rather than as mislabelled events:
- an event type is defined once, so it has one class;
- a class outside §4.6 does not validate;
- two mappings for the same source event never overlap in time;
- a mapping's dates lie inside its statute's;
- a `change` mapping (plan 0010 step B) is dated by its entry, so it takes no `when` or date field.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Mapping
from datetime import date
from itertools import pairwise
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, model_validator

from distress_radar.parsing.canonical_schema import CONFIG_DIR

# §4.6 classes an event can start; `alive` is the absence of one, never an event's class.
EventOutcomeClass = Literal["bankruptcy", "restructuring", "liquidation", "silent_exit"]
EVENT_OUTCOME_CLASSES: tuple[EventOutcomeClass, ...] = (
    "bankruptcy",
    "restructuring",
    "liquidation",
    "silent_exit",
)
Source = Literal["KRS", "KRZ", "MSiG"]
Stage = Literal["petition", "opening", "closing", "exit", "signal"]

_OPEN_END = date.max


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class Statute(_Frozen):
    id: str
    act: str
    effective_from: date
    effective_to: date | None = None  # inclusive; None while in force

    @model_validator(mode="after")
    def _ordered(self) -> Statute:
        if self.effective_to is not None and self.effective_to < self.effective_from:
            raise ValueError(f"statute {self.id}: effective_to before effective_from")
        return self

    def covers(self, on: date) -> bool:
        return self.effective_from <= on <= (self.effective_to or _OPEN_END)


class RegimeWindow(_Frozen):
    start: date
    end: date

    @model_validator(mode="after")
    def _ordered(self) -> RegimeWindow:
        if self.end < self.start:
            raise ValueError("regime_window: end before start")
        return self


class EventType(_Frozen):
    event_type: str
    stage: Stage
    outcome_class: EventOutcomeClass | None = None
    ends: tuple[EventOutcomeClass, ...] = ()
    precludes_silent_exit: bool

    @model_validator(mode="after")
    def _consistent(self) -> EventType:
        opens = self.outcome_class not in (None, "silent_exit")
        if opens and not self.precludes_silent_exit:
            raise ValueError(
                f"{self.event_type}: an event starting {self.outcome_class} must preclude silent_exit"
            )
        if self.outcome_class is not None and self.stage in ("closing", "signal"):
            raise ValueError(f"{self.event_type}: a {self.stage} event cannot start a class")
        return self


class When(_Frozen):
    field: str
    equals: str | None = None
    contains: str | None = None
    present: Literal[True] | None = None  # the record is an event only once the field is filled

    @model_validator(mode="after")
    def _one_test(self) -> When:
        if sum(t is not None for t in (self.equals, self.contains, self.present)) != 1:
            raise ValueError(
                f"when on {self.field}: give exactly one of `equals`, `contains` or `present`"
            )
        return self

    def matches(self, fields: Mapping[str, str]) -> bool:
        value = (fields.get(self.field) or "").strip()
        if not value:
            return False
        if self.equals is not None:
            return value == self.equals
        if self.contains is not None:
            return self.contains in value
        return True


class Change(_Frozen):
    """An event read from a section's history, not from one record (plan 0010 step B).

    `replaced`: a record an entry introduced while removing another that differs on `compare`.
    `membership`: a member (a record and all its parts) joining or leaving.
    """

    kind: Literal["replaced", "membership"]
    compare: tuple[str, ...] = ()

    @model_validator(mode="after")
    def _compare_iff_replaced(self) -> Change:
        if (self.kind == "replaced") != bool(self.compare):
            raise ValueError("`compare` is required for `replaced` and not allowed otherwise")
        return self


class SourceMapping(_Frozen):
    source: Source
    locator: str
    when: When | None = None
    change: Change | None = None
    event_type: str
    statute: str
    # Narrow the statute's range; both default to the statute's own dates.
    effective_from: date | None = None
    effective_to: date | None = None
    date_field: str | None = None  # None: dated by the registry entry
    date_parse: Literal["exact", "leading"] = "exact"

    @model_validator(mode="after")
    def _date_parse_needs_field(self) -> SourceMapping:
        if self.date_parse == "leading" and self.date_field is None:
            raise ValueError(f"{self.locator}: `date_parse: leading` needs a `date_field`")
        if self.change is not None:
            if self.source != "KRS":
                raise ValueError(f"{self.locator}: `change` reads a KRS extract's history")
            if self.when is not None or self.date_field is not None:
                raise ValueError(f"{self.locator}: a `change` takes no `when` or `date_field`")
        return self

    @property
    def key(self) -> tuple[str, str, When | None]:
        return (self.source, self.locator, self.when)


class ResolvedEvent(_Frozen):
    """What a source event means on a given date."""

    mapping: SourceMapping
    event_type: EventType
    statute: Statute


class ProcedureTaxonomy(_Frozen):
    version: int
    statutes: tuple[Statute, ...]
    krz_launch: date
    regime_window: RegimeWindow
    event_types: tuple[EventType, ...]
    mappings: tuple[SourceMapping, ...]

    @model_validator(mode="after")
    def _consistent(self) -> ProcedureTaxonomy:
        for what, names in (
            ("statute ids", [s.id for s in self.statutes]),
            ("event types", [e.event_type for e in self.event_types]),
        ):
            dupes = [n for n, k in Counter(names).items() if k > 1]
            if dupes:
                raise ValueError(f"duplicate {what}: {dupes}")
        classes = {e.outcome_class for e in self.event_types}
        unreachable = [c for c in EVENT_OUTCOME_CLASSES if c not in classes]
        if unreachable:
            raise ValueError(f"no event type starts {unreachable}")

        statutes = {s.id: s for s in self.statutes}
        types = {e.event_type for e in self.event_types}
        by_key: defaultdict[tuple[str, str, When | None], list[tuple[date, date]]] = defaultdict(list)
        locators: defaultdict[tuple[str, str], set[bool]] = defaultdict(set)
        for m in self.mappings:
            if m.event_type not in types:
                raise ValueError(f"{m.locator}: unknown event type {m.event_type}")
            if m.statute not in statutes:
                raise ValueError(f"{m.locator}: unknown statute {m.statute}")
            if m.change is not None and self.event_type(m.event_type).stage != "signal":
                # A change has no decision date, so it can never date a label.
                raise ValueError(f"{m.locator}: a `change` mapping must type a signal event")
            start, end = self.mapping_range(m)
            statute = statutes[m.statute]
            if end < start or not (statute.covers(start) and statute.covers(end)):
                raise ValueError(
                    f"{m.locator} ({m.event_type}): {start}..{end} is not inside statute {m.statute}"
                )
            by_key[m.key].append((start, end))
            locators[(m.source, m.locator)].add(m.when is None)
        # A bare mapping would match every record a `when` mapping also matches.
        mixed = [loc for loc, bare in locators.items() if len(bare) > 1]
        if mixed:
            raise ValueError(f"locators mapped both with and without `when`: {mixed}")
        for key, ranges in by_key.items():
            ranges.sort()
            for (_, prev_end), (start, _) in pairwise(ranges):
                if start <= prev_end:
                    raise ValueError(f"overlapping effective ranges for {key[:2]}, from {start}")
        return self

    def mapping_range(self, m: SourceMapping) -> tuple[date, date]:
        statute = next(s for s in self.statutes if s.id == m.statute)
        start = m.effective_from or statute.effective_from
        end = m.effective_to or statute.effective_to or _OPEN_END
        return start, end

    def event_type(self, name: str) -> EventType:
        return next(e for e in self.event_types if e.event_type == name)

    def locators(self, source: Source) -> set[str]:
        return {m.locator for m in self.mappings if m.source == source}

    def resolve(
        self, source: Source, locator: str, fields: Mapping[str, str], on: date
    ) -> ResolvedEvent | None:
        """The mapping for a source record on `on`, or None: the caller quarantines it.

        `on` is the event's decision date, or its `known_from` when the source gives none.
        """
        hits = [
            m
            for m in self.mappings
            if m.source == source
            and m.locator == locator
            and (m.when is None or m.when.matches(fields))
            and self.mapping_range(m)[0] <= on <= self.mapping_range(m)[1]
        ]
        if not hits:
            return None
        if len(hits) > 1:
            # Two `contains` tests can both match one value; config review cannot rule it out.
            raise ValueError(f"{source} {locator} on {on}: {len(hits)} mappings match {dict(fields)}")
        m = hits[0]
        statute = next(s for s in self.statutes if s.id == m.statute)
        return ResolvedEvent(mapping=m, event_type=self.event_type(m.event_type), statute=statute)

    def source_era(self, on: date) -> Literal["pre_krz", "krz"]:
        return "krz" if on >= self.krz_launch else "pre_krz"


def load_procedure_taxonomy(config_dir: Path = CONFIG_DIR) -> ProcedureTaxonomy:
    path = config_dir / "statutory" / "procedure_taxonomy.yaml"
    return ProcedureTaxonomy.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))
