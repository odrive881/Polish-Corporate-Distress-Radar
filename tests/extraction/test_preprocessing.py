"""The lemma prefilter (plan 0013 step D; AGENT_SPEC §6 G1). All text below is invented."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest
import yaml
from pydantic import ValidationError

from distress_radar.extraction.preprocessing import (
    SIGNAL_TYPES,
    Prefilter,
    Sentence,
    Term,
    analyse,
    candidates,
    load_model,
    load_prefilter,
)

REPO = Path(__file__).resolve().parents[2]
VERSION = "prefilter_v1"


@pytest.fixture(scope="module")
def nlp() -> Any:
    return load_model()


@pytest.fixture(scope="module")
def prefilter() -> Prefilter:
    return load_prefilter(VERSION)


def _sentence(*lemmas: str) -> Sentence:
    return Sentence(0, 0, lemmas, lemmas)


# --- the config ----------------------------------------------------------------------------------


def test_the_signal_types_are_agent_spec_s_enum() -> None:
    spec = (REPO / "AGENT_SPEC.md").read_text(encoding="utf-8")
    line = next(line for line in spec.splitlines() if line.startswith("`signal_type` enum:"))
    assert tuple(re.findall(r"`([a-z_]+)`", line.split(":", 1)[1])) == SIGNAL_TYPES


def test_every_example_is_matched_by_its_term_in_every_sentence(
    prefilter: Prefilter, nlp: Any
) -> None:
    missed = [
        (signal, term.words, text[sentence.start : sentence.end])
        for signal, terms in prefilter.signals.items()
        for term in terms
        for text in (term.example,)
        for sentence in analyse(text, nlp)
        if not term.matches(sentence)
    ]
    assert not missed


def test_a_signal_without_terms_fails_at_load(tmp_path: Path) -> None:
    raw = yaml.safe_load((REPO / "config" / "extraction" / f"{VERSION}.yaml").read_text("utf-8"))
    del raw["signals"]["litigation"]
    with pytest.raises(ValidationError, match="litigation"):
        Prefilter.model_validate(raw)


def test_the_version_must_be_the_file_name(tmp_path: Path) -> None:
    source = REPO / "config" / "extraction" / f"{VERSION}.yaml"
    (tmp_path / "extraction").mkdir()
    (tmp_path / "extraction" / "prefilter_v9.yaml").write_bytes(source.read_bytes())
    with pytest.raises(ValueError, match="must match the file name"):
        load_prefilter("prefilter_v9", tmp_path)


@pytest.mark.parametrize("word", ["", "Upadłość", "upad*łość", "*", "strata|"])
def test_a_malformed_word_is_refused(word: str) -> None:
    with pytest.raises(ValidationError):
        Term(words=(word,), example="x")


# --- matching ------------------------------------------------------------------------------------


def test_a_term_needs_every_word_in_one_sentence_in_any_order() -> None:
    term = Term(words=("pokryć", "strata"), example="x")
    assert term.matches(_sentence("strata", "zostać", "pokryć"))
    assert not term.matches(_sentence("strata", "netto"))


def test_a_stem_matches_the_lemma_or_the_surface_form() -> None:
    term = Term(words=("zwróceni*", "uwaga"), example="x")
    assert term.matches(Sentence(0, 0, ("zwrócć", "uwaga"), ("zwrócenie", "uwagi")))
    assert not term.matches(Sentence(0, 0, ("zwrot", "uwaga"), ("zwrot", "uwagi")))


def test_a_capitalised_first_word_is_lemmatised(nlp: Any) -> None:
    [sentence] = analyse("Występuje istotna niepewność.", nlp)
    assert sentence.lemmas[0] == "występować"


def test_offsets_point_into_the_original_text(nlp: Any) -> None:
    text = "Spółka ZŁOŻYŁA wniosek. Sąd oddalił POZEW."
    sentences = analyse(text, nlp)
    assert [text[s.start : s.end] for s in sentences] == [
        "Spółka ZŁOŻYŁA wniosek.",
        "Sąd oddalił POZEW.",
    ]


def test_candidates_on_a_masked_page(prefilter: Prefilter, nlp: Any) -> None:
    page = (
        "Sprawozdanie finansowe sporządzono przy założeniu kontynuowania działalności. "
        "Przychody ze sprzedaży wyniosły 12 345 678 zł. "
        "Po dniu bilansowym [osoba] złożył pozew przeciwko Spółce."
    )
    got = candidates(analyse(page, nlp), prefilter)
    assert {signal: [h.sentence for h in hits] for signal, hits in got.items()} == {
        "going_concern_uncertainty": [0],
        "litigation": [2],
        "post_balance_sheet_event": [2],
    }


def test_a_page_about_figures_only_has_no_candidate(prefilter: Prefilter, nlp: Any) -> None:
    page = "Należności krótkoterminowe wzrosły o 10%. Zapasy materiałów wyceniono w cenach nabycia."
    assert candidates(analyse(page, nlp), prefilter) == {}


def test_analysis_is_deterministic(nlp: Any) -> None:
    page = "Bank wypowiedział umowę kredytową. Spółka utraciła płynność finansową."
    assert analyse(page, nlp) == analyse(page, nlp)
