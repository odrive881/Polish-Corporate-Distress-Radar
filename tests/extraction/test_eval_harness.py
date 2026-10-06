"""Scoring and the eval gate (plan 0013 step G). The golden sets here are synthetic, written to a
temporary directory; the last test runs the gate over the committed files, as `make check` does."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from distress_radar.extraction import eval_harness as h
from distress_radar.extraction import eval_run, preprocessing
from distress_radar.extraction import extractor as ex
from distress_radar.extraction.golden import GOLDEN_DIR
from distress_radar.extraction.schemas import Discarded, Extraction
from distress_radar.settings import Settings

LITIGATION = "Przeciwko Spółce toczy się postępowanie sądowe."
OPINION = "Biegły rewident wydał opinię z zastrzeżeniem."
FIGURES = "Zapasy wyceniono w cenach nabycia."
PAGES = {
    "p1": f"{LITIGATION} {FIGURES}",
    "p2": FIGURES,
    "p3": OPINION,
    "p4": f"{FIGURES} {LITIGATION}",
}


@pytest.fixture(scope="module")
def extractor() -> ex.Extractor:
    return ex.load_extractor("extractor_v1")


@pytest.fixture(scope="module")
def gate() -> h.Gate:
    return h.load_gate("eval_gate_v1")


def _label(
    pid: str, present: bool, evidence: str | None = None, value: str | None = None
) -> h.GoldenLabel:
    return h.GoldenLabel(
        page_id=pid, prefilter_selected=True, present=present, value=value, evidence=evidence
    )


def _write_golden(root: Path, labels: dict[str, list[h.GoldenLabel]]) -> Path:
    golden = root / "golden"
    golden.mkdir(parents=True, exist_ok=True)
    pages = [
        {
            "page_id": pid,
            "text": text,
            "sample": "rejected" if pid == "p4" else "selected",
            "masked": {"person": 3},
            "hand_masked": {"person": 1} if pid == "p1" else {},
        }
        for pid, text in PAGES.items()
    ]
    (golden / "pages.jsonl").write_text("".join(json.dumps(p) + "\n" for p in pages), "utf-8")
    for signal, rows in labels.items():
        (golden / f"{signal}.jsonl").write_text(
            "".join(r.model_dump_json() + "\n" for r in rows), "utf-8"
        )
    return golden


LIT_LABELS = [
    _label("p1", True, LITIGATION),
    _label("p2", False),
    _label("p3", False),
    _label("p4", True, LITIGATION),
]


def _extraction(present: bool, evidence: str | None = None, value: str | None = None) -> Extraction:
    return Extraction(
        "litigation", present, value, evidence, 0 if evidence else None, "high", "llm", "k" * 64
    )


def _result(
    extractor: ex.Extractor, golden: Path, examples: tuple[h.Example, ...], mid: str | None = None
) -> h.EvalResult:
    labels = h.golden_labels("litigation", golden) or []
    return h.EvalResult(
        signal_type="litigation",
        method="llm",
        method_id=mid or h.method_id(extractor, "litigation"),
        extractor_version="extractor_v1",
        prefilter_version="prefilter_v1",
        hashes=h.fingerprint(extractor, "litigation", golden),
        examples=examples,
        scores=h.score(labels, examples, h.page_texts(golden), h.rejected_sample(golden)),
    )


def _examples(
    p1: Extraction | Discarded | None, p4_selected: bool = False
) -> tuple[h.Example, ...]:
    return (
        h.example("p1", True, p1),
        h.example("p2", True, _extraction(False)),
        h.example("p3", False, None),
        h.example("p4", p4_selected, _extraction(True, LITIGATION) if p4_selected else None),
    )


def _save(result: h.EvalResult, golden: Path) -> Path:
    path = h.result_path(result.signal_type, result.method_id, golden / "results")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(h.dump_result(result))
    return path


# --- scoring -------------------------------------------------------------------------------------


def test_scores_count_the_prefilter_the_extractor_and_the_whole(
    extractor: ex.Extractor, tmp_path: Path
) -> None:
    golden = _write_golden(tmp_path, {"litigation": LIT_LABELS})
    scores = _result(extractor, golden, _examples(_extraction(True, LITIGATION))).scores
    assert (scores.pages, scores.positives, scores.prefilter_selected_positives) == (4, 2, 1)
    assert scores.prefilter_missed_in_rejected_sample == 1  # p4, sampled from the rejected pool
    assert (scores.extractor.tp, scores.extractor.fp, scores.extractor.fn) == (1, 0, 0)
    assert (scores.end_to_end.tp, scores.end_to_end.fn, scores.end_to_end.recall) == (1, 1, 0.5)
    assert scores.end_to_end.precision == 1.0 and scores.evidence_overlaps == 1


def test_a_discarded_extraction_counts_as_absent_with_its_reason(
    extractor: ex.Extractor, tmp_path: Path
) -> None:
    golden = _write_golden(tmp_path, {"litigation": LIT_LABELS})
    scores = _result(
        extractor, golden, _examples(Discarded("litigation", "evidence_not_on_page", "k" * 64))
    ).scores
    assert scores.discarded == {"evidence_not_on_page": 1}
    assert scores.end_to_end.tp == 0 and scores.end_to_end.precision is None


def test_the_wrong_opinion_is_a_false_positive_and_a_false_negative() -> None:
    labels = [_label("p3", True, OPINION, "qualified")]
    wrong = (
        h.Example(
            page_id="p3", selected=True, outcome="present", value="unqualified", evidence=OPINION
        ),
    )
    counts = h.score(labels, wrong, PAGES, set()).end_to_end
    assert (counts.tp, counts.fp, counts.fn) == (0, 1, 1)


def test_the_wrong_kind_of_event_counts_twice_and_an_unvalued_label_on_presence() -> None:
    def ex_(value: str) -> tuple[h.Example, ...]:
        return (
            h.Example(
                page_id="p3", selected=True, outcome="present", value=value, evidence=OPINION
            ),
        )

    valued = [_label("p3", True, OPINION, "adverse")]
    assert h.score(valued, ex_("adverse"), PAGES, set()).end_to_end.tp == 1
    counts = h.score(valued, ex_("favourable"), PAGES, set()).end_to_end
    assert (counts.tp, counts.fp, counts.fn) == (0, 1, 1)
    unvalued = [_label("p3", True, OPINION, None)]  # labelled before decision 9
    assert h.score(unvalued, ex_("neutral"), PAGES, set()).end_to_end.tp == 1


def test_examples_must_be_one_per_golden_page() -> None:
    with pytest.raises(ValueError, match="one per golden page"):
        h.score([_label("p1", True, LITIGATION)], (), PAGES, set())


# --- the gate ------------------------------------------------------------------------------------


def _problems(extractor: ex.Extractor, gate: h.Gate, golden: Path) -> list[str] | None:
    return h.gate_problems(extractor, "litigation", gate, golden, golden / "results")


def test_the_gate_is_inactive_until_a_signal_has_a_result(
    extractor: ex.Extractor, gate: h.Gate, tmp_path: Path
) -> None:
    golden = _write_golden(tmp_path, {"litigation": LIT_LABELS})
    assert _problems(extractor, gate, golden) is None


def test_the_method_in_use_needs_a_current_result(
    extractor: ex.Extractor, gate: h.Gate, tmp_path: Path
) -> None:
    golden = _write_golden(tmp_path, {"litigation": LIT_LABELS})
    _save(_result(extractor, golden, _examples(None), mid="litigation_v0__claude-opus-5"), golden)
    [problem] = _problems(extractor, gate, golden) or []
    assert "no result for the method in use" in problem
    _save(_result(extractor, golden, _examples(None)), golden)
    _write_golden(tmp_path, {"litigation": [*LIT_LABELS[:3], _label("p4", False)]})
    [problem] = _problems(extractor, gate, golden) or []
    assert "scored on other golden" in problem


def test_stored_scores_must_be_the_examples_scores(
    extractor: ex.Extractor, gate: h.Gate, tmp_path: Path
) -> None:
    golden = _write_golden(tmp_path, {"litigation": LIT_LABELS})
    result = _result(extractor, golden, _examples(None))
    better = result.scores.model_copy(update={"positives": 99})
    _save(result.model_copy(update={"scores": better}), golden)
    [problem] = _problems(extractor, gate, golden) or []
    assert "stored scores" in problem


def test_a_result_needs_acceptance_and_may_not_fall_below_the_accepted_one(
    extractor: ex.Extractor, gate: h.Gate, tmp_path: Path
) -> None:
    golden = _write_golden(tmp_path, {"litigation": LIT_LABELS})
    good = _result(extractor, golden, _examples(_extraction(True, LITIGATION), p4_selected=True))
    _save(good, golden)
    [problem] = _problems(extractor, gate, golden) or []
    assert "no accepted result" in problem

    accepted = good.model_copy(
        update={
            "method_id": "litigation_v0__claude-opus-5",
            "accepted": h.Acceptance(by="owner", on=date(2026, 9, 30)),
        }
    )
    _save(accepted, golden)
    assert _problems(extractor, gate, golden) == []  # holds the accepted scores

    _save(_result(extractor, golden, _examples(_extraction(False))), golden)  # misses both
    assert [p.split(" below")[0] for p in _problems(extractor, gate, golden) or []] == [
        "litigation: precision None",
        "litigation: recall 0.0",
    ]
    loose = h.Gate(
        eval_gate_version="eval_gate_t", tolerance=h.Tolerance(precision=1.0, recall=1.0)
    )
    assert _problems(extractor, loose, golden) == []


def test_a_result_reproduced_under_a_new_extractor_version_keeps_its_acceptance(
    extractor: ex.Extractor, tmp_path: Path
) -> None:
    golden = _write_golden(tmp_path, {"litigation": LIT_LABELS})
    current = _result(extractor, golden, _examples(_extraction(True, LITIGATION)))
    stored = current.model_copy(
        update={
            "extractor_version": "extractor_v0",
            "accepted": h.Acceptance(by="owner", on=date(2026, 10, 6)),
        }
    )
    assert h.unchanged(stored, current)
    rescored = current.model_copy(update={"examples": _examples(_extraction(False))})
    assert not h.unchanged(stored, rescored)


# --- make eval, offline --------------------------------------------------------------------------


def test_make_eval_scores_a_rule_offline_and_skips_model_signals(tmp_path: Path) -> None:
    golden = _write_golden(
        tmp_path,
        {
            "opinion_type": [
                _label(
                    p, p == "p3", OPINION if p == "p3" else None, "qualified" if p == "p3" else None
                )
                for p in PAGES
            ],
            "litigation": LIT_LABELS,
        },
    )
    # The harness, on v1's sentence rule: the golden pages state the opinion in a sentence.
    settings = Settings(extraction_api_confirmed=False, extractor_version="extractor_v1")
    status = eval_run.run(settings, golden_dir=golden)
    assert status["litigation"].startswith("skipped")
    assert "P 1.0 R 1.0" in status["opinion_type"] and "0 sent" in status["calls"]
    path = h.result_path("opinion_type", "rules_v1__rule", golden / "results")
    first = path.read_bytes()
    eval_run.accept(settings, "opinion_type", "owner", golden)
    eval_run.run(settings, golden_dir=golden)
    assert h.load_result(path).accepted is not None  # unchanged, so still accepted
    assert h.load_result(path).model_copy(
        update={"accepted": None}
    ) == h.EvalResult.model_validate_json(first)
    masking = json.loads((golden / "results" / "masking" / "masking_v1.json").read_text("utf-8"))
    assert (masking["persons_by_masker"], masking["persons_by_hand"], masking["masker_recall"]) == (
        12,
        1,
        0.9231,
    )


def test_accepting_needs_a_current_result(tmp_path: Path) -> None:
    golden = _write_golden(tmp_path, {"litigation": LIT_LABELS})
    with pytest.raises(ValueError, match="make eval"):
        eval_run.accept(Settings(), "litigation", "owner", golden)


# --- the committed files --------------------------------------------------------------------------


@pytest.mark.parametrize("signal", preprocessing.SIGNAL_TYPES)
def test_the_committed_results_pass_the_gate(signal: preprocessing.SignalType) -> None:
    settings = Settings()
    extractor = ex.load_extractor(settings.extractor_version)
    problems = h.gate_problems(
        extractor, signal, h.load_gate(settings.eval_gate_version), GOLDEN_DIR
    )
    assert not problems, problems
