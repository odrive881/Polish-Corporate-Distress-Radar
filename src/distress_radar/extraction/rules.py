"""Lemma rules for signals with standard wording (plan 0013 step F, decision 2; AGENT_SPEC §6 G2).

`config/extraction/rules_<version>.yaml` holds the terms; this applies them to a masked page's
sentences (`preprocessing.analyse`). A rule answers like an extractor, present or absent, with the
matched sentence as its evidence, so the eval harness scores it the same way. Deterministic: no
model, no stored response.
"""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, model_validator

from distress_radar.extraction.preprocessing import Sentence, Term
from distress_radar.extraction.schemas import Extraction, OpinionValue
from distress_radar.parsing.canonical_schema import CONFIG_DIR

# Most severe first: over a page, the first category found in this order wins.
SEVERITY: tuple[OpinionValue, ...] = ("disclaimer", "adverse", "qualified", "unqualified")
# Per sentence, the first category matched in this order: `bez zastrzeżeń` before `zastrzeżenie`.
SENTENCE_ORDER: tuple[OpinionValue, ...] = ("disclaimer", "adverse", "unqualified", "qualified")


class Rules(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    rules_version: str
    opinion_type: dict[OpinionValue, tuple[Term, ...]]

    @model_validator(mode="after")
    def _every_opinion_has_terms(self) -> Rules:
        missing = [v for v in SEVERITY if not self.opinion_type.get(v)]
        if missing:
            raise ValueError(f"opinion_type: no terms for {missing}")
        return self


def load_rules(version: str, config_dir: Path = CONFIG_DIR) -> Rules:
    path = config_dir / "extraction" / f"{version}.yaml"
    rules = Rules.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))
    if rules.rules_version != path.stem:
        raise ValueError(f"{path}: `rules_version: {rules.rules_version}` must match the file name")
    return rules


def sentence_opinion(sentence: Sentence, rules: Rules) -> OpinionValue | None:
    return next(
        (v for v in SENTENCE_ORDER if any(t.matches(sentence) for t in rules.opinion_type[v])),
        None,
    )


def opinion_type(text: str, sentences: list[Sentence], rules: Rules) -> Extraction:
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
    return Extraction("opinion_type", False, None, None, None, "high", "rule", None)
