"""Typed views of the statutory configs the features read (plan 0010 step B; invariant 7).

- `config/statutory/ksh_tripwires.yaml`: the KSH loss tripwires (AGENT_SPEC §4.5), resolved by
  legal form on a statement's balance-sheet date;
- `config/statutory/filing_deadlines.yaml`: when an annual statement is due at the register,
  computed from its balance-sheet date, with the COVID-era extensions.

Both are dated, and the loaders reject what would otherwise be resolved ambiguously: two rules for
one legal form in force on one date, or two extensions covering one balance-sheet date.
"""

from __future__ import annotations

import calendar
from collections import Counter
from datetime import date, timedelta
from fractions import Fraction
from itertools import pairwise
from pathlib import Path
from typing import Annotated, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from distress_radar.parsing.canonical_schema import CONFIG_DIR

LegalForm = Literal["sp_z_oo", "sa"]

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


def _unique_ids(statutes: tuple[Statute, ...]) -> dict[str, Statute]:
    dupes = [i for i, n in Counter(s.id for s in statutes).items() if n > 1]
    if dupes:
        raise ValueError(f"duplicate statute ids: {dupes}")
    return {s.id: s for s in statutes}


# --- ksh_tripwires.yaml --------------------------------------------------------------------------


class TripwireRule(_Frozen):
    rule: str
    legal_forms: tuple[LegalForm, ...] = Field(min_length=1)
    statute: str
    # Narrow the statute's range; both default to the statute's own dates.
    effective_from: date | None = None
    effective_to: date | None = None
    losses: tuple[str, ...] = Field(min_length=1)
    # input → coefficient, written as a fraction ("1/2") so the comparison is exact
    threshold: dict[str, Annotated[str, Field(pattern=r"^\d+(/\d+)?$")]] = Field(min_length=1)

    @model_validator(mode="after")
    def _positive(self) -> TripwireRule:
        zero = [k for k, v in self.coefficients().items() if v == 0]
        if zero:
            raise ValueError(f"{self.rule}: threshold coefficients must be positive: {zero}")
        return self

    def coefficients(self) -> dict[str, Fraction]:
        return {k: Fraction(v) for k, v in self.threshold.items()}

    def inputs(self) -> set[str]:
        return set(self.losses) | set(self.threshold)


class KshTripwires(_Frozen):
    version: int
    statutes: tuple[Statute, ...]
    rules: tuple[TripwireRule, ...]

    @model_validator(mode="after")
    def _consistent(self) -> KshTripwires:
        statutes = _unique_ids(self.statutes)
        dupes = [r for r, n in Counter(r.rule for r in self.rules).items() if n > 1]
        if dupes:
            raise ValueError(f"duplicate rules: {dupes}")
        by_form: dict[str, list[tuple[date, date, str]]] = {}
        for r in self.rules:
            if r.statute not in statutes:
                raise ValueError(f"{r.rule}: unknown statute {r.statute}")
            start, end = self.rule_range(r)
            if end < start or not (
                statutes[r.statute].covers(start) and statutes[r.statute].covers(end)
            ):
                raise ValueError(f"{r.rule}: {start}..{end} is not inside statute {r.statute}")
            for form in r.legal_forms:
                by_form.setdefault(form, []).append((start, end, r.rule))
        for form, ranges in by_form.items():
            ranges.sort()
            for (_, prev_end, prev), (start, _, rule) in pairwise(ranges):
                if start <= prev_end:
                    raise ValueError(f"{prev} and {rule} both apply to {form} on {start}")
        return self

    def rule_range(self, r: TripwireRule) -> tuple[date, date]:
        statute = next(s for s in self.statutes if s.id == r.statute)
        return (
            r.effective_from or statute.effective_from,
            r.effective_to or statute.effective_to or _OPEN_END,
        )

    def rule(self, name: str) -> TripwireRule | None:
        return next((r for r in self.rules if r.rule == name), None)

    def resolve(self, name: str, legal_form: LegalForm, on: date) -> TripwireRule | None:
        """The rule `name` for `legal_form` on a balance-sheet date, or None: the feature is null."""
        r = self.rule(name)
        if r is None or legal_form not in r.legal_forms:
            return None
        start, end = self.rule_range(r)
        return r if start <= on <= end else None


def load_ksh_tripwires(config_dir: Path = CONFIG_DIR) -> KshTripwires:
    path = config_dir / "statutory" / "ksh_tripwires.yaml"
    return KshTripwires.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))


# --- filing_deadlines.yaml -----------------------------------------------------------------------


def add_months(on: date, months: int) -> date:
    """`on` plus `months`, keeping a month end a month end (31 Dec + 6 → 30 Jun)."""
    index = on.year * 12 + on.month - 1 + months
    year, month = divmod(index, 12)
    last = calendar.monthrange(year, month + 1)[1]
    at_month_end = on.day == calendar.monthrange(on.year, on.month)[1]
    return date(year, month + 1, last if at_month_end else min(on.day, last))


class DeadlineBase(_Frozen):
    statute: str
    approval_months: int = Field(ge=1)
    filing_days: int = Field(ge=0)


class DeadlineExtension(_Frozen):
    id: str
    statute: str
    period_end_from: date
    period_end_to: date  # inclusive
    approval_extra_months: int = Field(ge=1)

    @model_validator(mode="after")
    def _ordered(self) -> DeadlineExtension:
        if self.period_end_to < self.period_end_from:
            raise ValueError(f"{self.id}: period_end_to before period_end_from")
        return self

    def covers(self, period_end: date) -> bool:
        return self.period_end_from <= period_end <= self.period_end_to


class FilingDeadlines(_Frozen):
    version: int
    statutes: tuple[Statute, ...]
    base: DeadlineBase
    extensions: tuple[DeadlineExtension, ...] = ()

    @model_validator(mode="after")
    def _consistent(self) -> FilingDeadlines:
        statutes = _unique_ids(self.statutes)
        if self.base.statute not in statutes:
            raise ValueError(f"base: unknown statute {self.base.statute}")
        dupes = [i for i, n in Counter(e.id for e in self.extensions).items() if n > 1]
        if dupes:
            raise ValueError(f"duplicate extensions: {dupes}")
        for e in self.extensions:
            if e.statute not in statutes:
                raise ValueError(f"{e.id}: unknown statute {e.statute}")
        ordered = sorted(self.extensions, key=lambda e: e.period_end_from)
        for prev, nxt in pairwise(ordered):
            if nxt.period_end_from <= prev.period_end_to:
                raise ValueError(f"{prev.id} and {nxt.id} both cover {nxt.period_end_from}")
        return self

    def extension_for(self, period_end: date) -> DeadlineExtension | None:
        return next((e for e in self.extensions if e.covers(period_end)), None)

    def deadline(self, period_end: date) -> date:
        """The last day a statement for the year ending `period_end` is filed on time."""
        extension = self.extension_for(period_end)
        months = self.base.approval_months + (extension.approval_extra_months if extension else 0)
        return add_months(period_end, months) + timedelta(days=self.base.filing_days)


def load_filing_deadlines(config_dir: Path = CONFIG_DIR) -> FilingDeadlines:
    path = config_dir / "statutory" / "filing_deadlines.yaml"
    return FilingDeadlines.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))
