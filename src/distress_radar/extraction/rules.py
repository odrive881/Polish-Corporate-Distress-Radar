"""Rules for signals with standard wording (plan 0013 step F, decision 2; AGENT_SPEC §6 G2).

`config/extraction/rules_<version>.yaml` holds them, in one of two forms for `opinion_type`:
- **terms** (`rules_v1`): lemma terms over a masked page's sentences (`preprocessing.analyse`);
  the page's most severe opinion wins, with its sentence as evidence;
- **headings** (`rules_v2`, plan 0013 decision 0c): the section headings of an auditor's report
  under the Polish auditing standards (KSB 700/705). A line that is, whole, an opinion heading
  ("Opinia z zastrzeżeniem") gives the opinion; failing one, a basis heading ("Podstawa opinii z
  zastrzeżeniem") does, at `medium` confidence. The first such line on the page wins, with the
  line as evidence. A sentence that only mentions an opinion is never read as one.
A rule answers like an extractor, present or absent, so the eval harness scores it the same way.

`audit_firm` (`rules_v3`, plan 0013 decision 0c): patterns that find the audit firm's number on
the list of audit firms in a report's text, only to tell whether the firm changed. The number is
never stored, returned beyond its caller or printed: a firm can be a sole practitioner, a natural
person (owner, 2026-10-05). A pattern must not reach the key auditor's own registration number.
Deterministic: no model, no stored response.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable
from functools import cached_property
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, field_validator, model_validator

from distress_radar.extraction.preprocessing import Sentence, Term
from distress_radar.extraction.schemas import Confidence, Extraction, OpinionValue
from distress_radar.parsing.canonical_schema import CONFIG_DIR

# Most severe first: over a page, the first category found in this order wins.
SEVERITY: tuple[OpinionValue, ...] = ("disclaimer", "adverse", "qualified", "unqualified")
# Per sentence, the first category matched in this order: `bez zastrzeżeń` before `zastrzeżenie`.
SENTENCE_ORDER: tuple[OpinionValue, ...] = ("disclaimer", "adverse", "unqualified", "qualified")


# A heading line's key: leading section numbering ("1.", "II.", "a)") and trailing colons, full
# stops and spaces removed, the rest lowercased with single spaces.
_NUMBERING = re.compile(r"^(?:\d+(?:\.\d+)*\.?|[ivx]+\.|[a-z]\))\s*")
_TRAILING = re.compile(r"[\s:.]+$")
_SPACES = re.compile(r"\s+")


def heading_key(line: str) -> str:
    key = unicodedata.normalize("NFC", line).lower().strip()
    return _SPACES.sub(" ", _TRAILING.sub("", _NUMBERING.sub("", key)))


class OpinionHeadings(BaseModel):
    """The headings that state one opinion: its own section's, and its basis section's."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    opinion: tuple[str, ...]
    basis: tuple[str, ...]

    @field_validator("opinion", "basis")
    @classmethod
    def _keys(cls, headings: tuple[str, ...]) -> tuple[str, ...]:
        if not headings:
            raise ValueError("at least one heading")
        for heading in headings:
            if heading != heading_key(heading):
                raise ValueError(f"{heading!r} is not written as its key: {heading_key(heading)!r}")
        return headings


class FirmPattern(BaseModel):
    """A regular expression whose one group is the firm's number, with an invented example."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    pattern: str
    example: str
    number: str  # what the pattern must find in the example, and nothing else

    @cached_property
    def compiled(self) -> re.Pattern[str]:
        return re.compile(self.pattern, re.IGNORECASE)

    @model_validator(mode="after")
    def _finds_its_example(self) -> FirmPattern:
        if self.compiled.groups != 1:
            raise ValueError(f"{self.pattern!r}: exactly one group, the number")
        found = self.compiled.findall(self.example)
        if found != [self.number]:
            raise ValueError(f"{self.pattern!r} finds {found} in its example, not [{self.number}]")
        return self


class Rules(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    rules_version: str
    # Exactly one of the two forms (module docstring).
    opinion_type: dict[OpinionValue, tuple[Term, ...]] | None = None
    opinion_headings: dict[OpinionValue, OpinionHeadings] | None = None
    audit_firm: tuple[FirmPattern, ...] = ()

    @model_validator(mode="after")
    def _one_form_covering_every_opinion(self) -> Rules:
        if (self.opinion_type is None) == (self.opinion_headings is None):
            raise ValueError("give exactly one of `opinion_type` (terms) and `opinion_headings`")
        if self.opinion_type is not None:
            missing = [v for v in SEVERITY if not self.opinion_type.get(v)]
            if missing:
                raise ValueError(f"opinion_type: no terms for {missing}")
        if self.opinion_headings is not None:
            missing = [v for v in SEVERITY if v not in self.opinion_headings]
            if missing:
                raise ValueError(f"opinion_headings: no headings for {missing}")
            keys = [k for h in self.opinion_headings.values() for k in (*h.opinion, *h.basis)]
            twice = sorted({k for k in keys if keys.count(k) > 1})
            if twice:
                raise ValueError(f"opinion_headings: {twice} listed more than once")
        return self


def load_rules(version: str, config_dir: Path = CONFIG_DIR) -> Rules:
    path = config_dir / "extraction" / f"{version}.yaml"
    rules = Rules.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))
    if rules.rules_version != path.stem:
        raise ValueError(f"{path}: `rules_version: {rules.rules_version}` must match the file name")
    return rules


def sentence_opinion(sentence: Sentence, rules: Rules) -> OpinionValue | None:
    terms = rules.opinion_type or {}
    return next(
        (v for v in SENTENCE_ORDER if any(t.matches(sentence) for t in terms.get(v, ()))),
        None,
    )


_ABSENT = Extraction("opinion_type", False, None, None, None, "high", "rule", None)


def opinion_type(text: str, sentences: list[Sentence], rules: Rules) -> Extraction:
    """The page's opinion, by the rules' form (module docstring)."""
    if rules.opinion_headings is not None:
        return _by_heading(text, rules.opinion_headings)
    return _by_sentence(text, sentences, rules)


def _lines(text: str) -> list[tuple[int, str]]:
    """Each line's start offset and the line without surrounding spaces."""
    out: list[tuple[int, str]] = []
    start = 0
    for line in text.split("\n"):
        stripped = line.strip()
        if stripped:
            out.append((start + line.index(stripped), stripped))
        start += len(line) + 1
    return out


def _by_heading(text: str, headings: dict[OpinionValue, OpinionHeadings]) -> Extraction:
    opinion: dict[str, OpinionValue] = {k: v for v, h in headings.items() for k in h.opinion}
    basis: dict[str, OpinionValue] = {k: v for v, h in headings.items() for k in h.basis}
    lines = [(start, line, heading_key(line)) for start, line in _lines(text)]
    tiers: tuple[tuple[dict[str, OpinionValue], Confidence], ...] = (
        (opinion, "high"),
        (basis, "medium"),
    )
    for table, confidence in tiers:
        for start, line, key in lines:
            value = table.get(key)
            if value is not None:
                return Extraction(
                    signal_type="opinion_type",
                    present=True,
                    value=value,
                    evidence=line,
                    evidence_start=start,
                    confidence=confidence,
                    method="rule",
                    response_key=None,
                )
    return _ABSENT


def _by_sentence(text: str, sentences: list[Sentence], rules: Rules) -> Extraction:
    """The page's most severe opinion, with the first sentence stating it as evidence."""
    found = [(v, s) for s in sentences if (v := sentence_opinion(s, rules)) is not None]
    for value in SEVERITY:
        sentence = next((s for v, s in found if v == value), None)
        if sentence is not None:
            return Extraction(
                signal_type="opinion_type",
                present=True,
                value=value,
                evidence=text[sentence.start : sentence.end],
                evidence_start=sentence.start,
                confidence="high",
                method="rule",
                response_key=None,
            )
    return _ABSENT


def audit_firm_number(pages: Iterable[str], rules: Rules) -> str | None:
    """The one audit-firm number a report's masked pages state, or None when they state none or
    more than one. Kept in memory by the caller, never stored (module docstring)."""
    found = {n for text in pages for p in rules.audit_firm for n in p.compiled.findall(text)}
    return next(iter(found)) if len(found) == 1 else None
