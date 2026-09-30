"""Sentences, lemmas and the lemma prefilter (plan 0013 step D; AGENT_SPEC §6 G1).

Polish inflects: *kontynuacja*, *kontynuacji*, *kontynuowania* are one idea, so a candidate page is
chosen by lemma, never by surface form. `analyse` splits a masked page into sentences with
`pl_core_news_lg`'s sentence recogniser and lemmatiser; `candidates` says which sentences match
which `signal_type`'s terms in `config/extraction/prefilter_<version>.yaml`. A page with no
candidate for a signal is not extracted for it, so the prefilter's recall is measured (plan 0013
decision 5), never assumed.

Only masked text comes here (ADR 0009, third addendum): the input is `extraction.masking`'s
output. The text is lowercased before it is lemmatised, one character for one so offsets hold:
the model leaves a sentence's capitalised first word unlemmatised (`Występuje` stays itself).
Deterministic: the same text and model give the same sentences and lemmas.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from functools import cached_property
from pathlib import Path
from typing import Any, Literal, get_args

import yaml
from pydantic import BaseModel, ConfigDict, field_validator, model_validator

from distress_radar.extraction.masking import load_pinned
from distress_radar.parsing.canonical_schema import CONFIG_DIR

# The `signal_type` enum of AGENT_SPEC §5, in its order.
SignalType = Literal[
    "going_concern_uncertainty",
    "opinion_type",
    "emphasis_of_matter",
    "covenant_breach",
    "key_customer_loss",
    "litigation",
    "post_balance_sheet_event",
    "loss_coverage_resolution",
    "continued_existence_vote",
]
SIGNAL_TYPES: tuple[SignalType, ...] = get_args(SignalType)


def load_model(model_dir: Path | None = None) -> Any:
    """The pinned pipeline with sentences and lemmas, without NER or the dependency parser."""
    return load_pinned(("ner", "parser"), ("senter",), model_dir)


# --- sentences and lemmas ------------------------------------------------------------------------


@dataclass(frozen=True)
class Sentence:
    start: int  # character offsets into the analysed text
    end: int
    lemmas: tuple[str, ...]  # lowercased, one per word token (punctuation and spaces dropped)
    forms: tuple[str, ...]  # the lowercased surface forms, aligned with `lemmas`


def _lower(text: str) -> str:
    """Lowercase, one character for one, so offsets into the result are offsets into `text`."""
    return "".join(c if len(low := c.lower()) != 1 else low for c in text)


def analyse(text: str, nlp: Any | None = None) -> list[Sentence]:
    """The sentences of a masked text, each with its tokens' lemmas and forms."""
    model: Any = load_model() if nlp is None else nlp
    out: list[Sentence] = []
    for sent in model(_lower(text)).sents:
        words = [t for t in sent if not (t.is_punct or t.is_space)]
        if words:
            out.append(
                Sentence(
                    sent.start_char,
                    sent.end_char,
                    tuple(str(t.lemma_).lower() for t in words),
                    tuple(str(t.text) for t in words),
                )
            )
    return out


# --- config/extraction/prefilter_*.yaml ----------------------------------------------------------


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class Term(_Frozen):
    """Every word in one sentence, in any order; a word is `|`-separated alternatives, each an
    exact lemma or a `stem*` prefix of the lemma or the surface form."""

    words: tuple[str, ...]
    example: str

    @field_validator("words")
    @classmethod
    def _well_formed(cls, words: tuple[str, ...]) -> tuple[str, ...]:
        if not words:
            raise ValueError("a term needs at least one word")
        for word in words:
            for alternative in word.split("|"):
                stem = alternative.removesuffix("*")
                if not stem or "*" in stem or stem != stem.lower() or stem != stem.strip():
                    raise ValueError(
                        f"{word!r}: each alternative is a lowercase lemma or `stem*`, not empty"
                    )
        return words

    @cached_property
    def _compiled(self) -> tuple[tuple[frozenset[str], tuple[str, ...]], ...]:
        compiled: list[tuple[frozenset[str], tuple[str, ...]]] = []
        for word in self.words:
            alternatives = word.split("|")
            exact = frozenset(a for a in alternatives if not a.endswith("*"))
            prefixes = tuple(a[:-1] for a in alternatives if a.endswith("*"))
            compiled.append((exact, prefixes))
        return tuple(compiled)

    def matches(self, sentence: Sentence) -> bool:
        return all(
            any(
                lemma in exact or lemma.startswith(prefixes) or form.startswith(prefixes)
                for lemma, form in zip(sentence.lemmas, sentence.forms, strict=True)
            )
            if prefixes
            else not exact.isdisjoint(sentence.lemmas)
            for exact, prefixes in self._compiled
        )


class Prefilter(_Frozen):
    prefilter_version: str
    signals: dict[SignalType, tuple[Term, ...]]

    @model_validator(mode="after")
    def _every_signal_has_terms(self) -> Prefilter:
        missing = [s for s in SIGNAL_TYPES if not self.signals.get(s)]
        if missing:
            raise ValueError(f"no terms for {missing}: every signal_type needs at least one")
        return self


@dataclass(frozen=True)
class Hit:
    signal_type: SignalType
    term: int  # index into the signal's terms in the config
    sentence: int  # index into `analyse`'s sentences


def load_prefilter(version: str, config_dir: Path = CONFIG_DIR) -> Prefilter:
    path = config_dir / "extraction" / f"{version}.yaml"
    prefilter = Prefilter.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))
    if prefilter.prefilter_version != path.stem:
        raise ValueError(
            f"{path}: `prefilter_version: {prefilter.prefilter_version}` must match the file name"
        )
    return prefilter


def prefilter_hash(version: str, config_dir: Path = CONFIG_DIR) -> str:
    """SHA-256 of the prefilter's file, recorded with what it selected."""
    return hashlib.sha256((config_dir / "extraction" / f"{version}.yaml").read_bytes()).hexdigest()


def candidates(
    sentences: list[Sentence], prefilter: Prefilter
) -> dict[SignalType, tuple[Hit, ...]]:
    """Each signal with at least one matching sentence, and every (term, sentence) that matched,
    in the enum's order, then by sentence, then by term."""
    out: dict[SignalType, tuple[Hit, ...]] = {}
    for signal in SIGNAL_TYPES:
        terms = prefilter.signals[signal]
        hits = tuple(
            Hit(signal, t, s)
            for s, sentence in enumerate(sentences)
            for t, term in enumerate(terms)
            if term.matches(sentence)
        )
        if hits:
            out[signal] = hits
    return out
