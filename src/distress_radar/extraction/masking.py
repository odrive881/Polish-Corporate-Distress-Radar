"""Person-free text: the masker every text passes before it is stored, sent or committed
(plan 0013 decision 1; ADR 0009, third addendum).

Filed notes and reports name people: board members sign them, and the text can name owners,
employees or counterparties who are natural persons. Page text from `extraction.page_text` is
raw; this is the only way out of it. What is replaced:
- **person names** found by the NER of spaCy's `pl_core_news_lg` (`persName`), with the
  inflected forms Polish gives them, by `[osoba]`;
- **PESEL-shaped numbers**, any run of exactly 11 digits (as the registry redaction treats them),
  by `[pesel]`;
- **e-mail addresses** by `[email]`, and **phone numbers** after a phone marker (`tel`, `tel.`,
  `telefon`, `fax`, `kom.`, `+48`) by `[telefon]`. A bare group of nine digits is left alone:
  in notes it is more often an amount written with spaces (`123 456 789`).
Company names (`orgName`) are legal entities and stay.

The masker is measured, not trusted: its recall on the golden set is reported beside the
extractors' (plan 0013 decision 5). The model's exact version is part of `MASKING_VERSION`, and
loading any other version fails: a different model masks differently, and a masked text's hash
keys the response store (decision 3).
"""

# spaCy's signatures reference types pyright cannot resolve; scoped to this module.
# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false
# pyright: reportUnknownArgumentType=false, reportMissingTypeStubs=false

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import Any

from distress_radar.settings import Settings

MASKING_VERSION = "1"
MODEL = "pl_core_news_lg"
MODEL_VERSION = "3.8.0"
PERSON_LABEL = "persName"
TOKENS = {"person": "[osoba]", "pesel": "[pesel]", "email": "[email]", "phone": "[telefon]"}

_PESEL = re.compile(r"(?<!\d)\d{11}(?!\d)")
_EMAIL = re.compile(r"(?<![\w.+-])[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
# A marker (a word, or the country code itself), then 7 to 15 digits with spaces, dashes, dots
# or brackets between them; the country code is part of the number masked.
_PHONE = re.compile(
    r"(?i)(?:\b(?:tel|telefon|fax|kom)\b\.?:?\s*|(?=\+48))"
    r"(?P<number>(?:\+48[\s-]?)?\(?\d(?:[\s().-]?\d){6,14})"
)


@dataclass(frozen=True)
class Masked:
    text: str
    counts: dict[str, int]  # replacements by kind: "person", "pesel", "email", "phone"


def model_path(model_dir: Path | None = None) -> Path:
    """Where `make models` unpacks the pinned model: `<SPACY_MODEL_DIR>/<name>/<name>-<version>`."""
    root = Settings().spacy_model_dir if model_dir is None else model_dir
    return root / MODEL / f"{MODEL}-{MODEL_VERSION}"


@cache
def load_model(model_dir: Path | None = None) -> Any:
    """The pinned Polish pipeline, NER and what it needs; any other version is refused."""
    import spacy

    path = model_path(model_dir)
    if not (path / "meta.json").is_file():
        raise FileNotFoundError(f"{MODEL} {MODEL_VERSION} is not at {path}: run `make models`")
    nlp = spacy.load(path, exclude=["lemmatizer", "morphologizer", "parser", "attribute_ruler"])
    version = str(nlp.meta.get("version"))
    if version != MODEL_VERSION:
        raise RuntimeError(
            f"{MODEL} {version} is installed; masking version {MASKING_VERSION} needs "
            f"{MODEL_VERSION}. A new model is a new masking version (ADR 0009, third addendum)."
        )
    return nlp


def _spans(text: str, nlp: Any) -> list[tuple[int, int, str]]:
    """Every span to replace, as (start, end, kind); overlaps resolved to the earliest, longest."""
    spans: list[tuple[int, int, str]] = []
    spans += [(m.start(), m.end(), "email") for m in _EMAIL.finditer(text)]
    spans += [(m.start("number"), m.end("number"), "phone") for m in _PHONE.finditer(text)]
    spans += [(m.start(), m.end(), "pesel") for m in _PESEL.finditer(text)]
    doc = nlp(text)
    spans += [(e.start_char, e.end_char, "person") for e in doc.ents if e.label_ == PERSON_LABEL]
    spans.sort(key=lambda s: (s[0], -(s[1] - s[0])))
    kept: list[tuple[int, int, str]] = []
    for start, end, kind in spans:
        if kept and start < kept[-1][1]:
            continue  # inside an earlier span
        kept.append((start, end, kind))
    return kept


def mask(text: str, nlp: Any | None = None) -> Masked:
    """The text with every person, PESEL, e-mail and phone number replaced by its token."""
    nlp = load_model() if nlp is None else nlp
    out: list[str] = []
    counts: Counter[str] = Counter()
    position = 0
    for start, end, kind in _spans(text, nlp):
        out.append(text[position:start])
        out.append(TOKENS[kind])
        counts[kind] += 1
        position = end
    out.append(text[position:])
    return Masked("".join(out), dict(sorted(counts.items())))
