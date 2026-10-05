"""Scoring extractors against the golden set, and the gate on their changes (plan 0013 step G,
decision 4; AGENT_SPEC §6 G3). No I/O beyond reading committed files: `extraction.eval_run` runs
the extractors and writes results.

**A result** (`evals/text_signals/results/<signal_type>/<method id>.json`, the method id being
`<prompt>__<model>` for a model and `<rules_version>__rule` for a rule) holds every golden page's
outcome for one signal, its scores, and the SHA-256 of everything the outcome depends on: the golden
files, the prompt or rules, the output schema, the prefilter and the model. Scores are counted on
the golden pages, with their counts beside them:
- `prefilter`: of the labelled positives, how many the prefilter selects;
- `extractor`: precision, recall and F1 on the pages the prefilter selected, the extractor alone;
- `end_to_end`: the same over every golden page, a page not selected counting as absent. This is
  what reaches the features, and what the gate holds.
A discarded extraction counts as absent, and its reason is counted. For `opinion_type`, a present
answer with the wrong opinion is both a false positive and a false negative. The rejected pages in
the golden set are a sample (`golden_sample_*.yaml`): of the notes, a random sample of the pool, so a
positive the prefilter misses there stands for several in the whole rejected pool; of the auditor
reports, every rejected page of a sample of whole reports. Counts, not rates, are the honest reading.

**The gate** (`make check`, offline): once a signal has any result, the method in use must have a
result for the current files, that result must re-score to its own stored scores, and its end-to-end
precision and recall may not fall below those of the owner's last accepted result on the same golden
file by more than `eval_gate_*.yaml` allows. A new golden file needs a newly accepted result: scores on
different labels are not comparable. Until a signal has a result, the gate reports it inactive.
"""

from __future__ import annotations

import hashlib
import json
from datetime import date
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field

from distress_radar.extraction.extractor import PROMPTS_DIR, Extractor
from distress_radar.extraction.golden import GOLDEN_DIR, PAGES_FILE
from distress_radar.extraction.preprocessing import SignalType
from distress_radar.extraction.response_store import canonical
from distress_radar.extraction.schemas import SCHEMA_VERSION, Discarded, Extraction, json_schema
from distress_radar.parsing.canonical_schema import CONFIG_DIR

RESULTS_DIR = GOLDEN_DIR / "results"


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


# --- config/extraction/eval_gate_*.yaml ----------------------------------------------------------


class Tolerance(_Frozen):
    precision: float = Field(ge=0, le=1)
    recall: float = Field(ge=0, le=1)


class Gate(_Frozen):
    eval_gate_version: str
    tolerance: Tolerance


def load_gate(version: str, config_dir: Path = CONFIG_DIR) -> Gate:
    path = config_dir / "extraction" / f"{version}.yaml"
    gate = Gate.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))
    if gate.eval_gate_version != path.stem:
        raise ValueError(
            f"{path}: `eval_gate_version: {gate.eval_gate_version}` must match the file name"
        )
    return gate


# --- the golden files ----------------------------------------------------------------------------


class GoldenLabel(_Frozen):
    page_id: str
    prefilter_selected: bool  # by the sample's prefilter, when the queue was built
    present: bool
    value: str | None
    evidence: str | None


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def golden_labels(signal: SignalType, golden_dir: Path = GOLDEN_DIR) -> list[GoldenLabel] | None:
    """The signal's labels, by page id; None when the signal has no golden file yet."""
    path = golden_dir / f"{signal}.jsonl"
    if not path.exists():
        return None
    return [
        GoldenLabel.model_validate_json(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def page_texts(golden_dir: Path = GOLDEN_DIR) -> dict[str, str]:
    path = golden_dir / PAGES_FILE
    if not path.exists():
        return {}
    rows = [
        json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()
    ]
    return {str(row["page_id"]): str(row["text"]) for row in rows}


# --- results -------------------------------------------------------------------------------------

Outcome = Literal["present", "absent", "discarded", "not_selected"]


class Example(_Frozen):
    page_id: str
    selected: bool  # by the prefilter in use
    outcome: Outcome
    discard_reason: str | None = None
    value: str | None = None
    evidence: str | None = None
    response_key: str | None = None


class Counts(_Frozen):
    tp: int
    fp: int
    fn: int
    tn: int
    precision: float | None  # None when nothing was predicted present
    recall: float | None  # None when nothing is labelled present
    f1: float | None


class Scores(_Frozen):
    pages: int
    positives: int
    prefilter_selected_positives: int
    prefilter_missed_in_rejected_sample: int  # positives on sampled pages it selected for nothing
    discarded: dict[str, int]
    evidence_overlaps: int  # true positives whose evidence overlaps the labelled evidence
    extractor: Counts
    end_to_end: Counts


class Acceptance(_Frozen):
    by: str
    on: date


class EvalResult(_Frozen):
    signal_type: SignalType
    method: Literal["llm", "rule"]
    method_id: str
    extractor_version: str
    prefilter_version: str
    hashes: dict[str, str]
    examples: tuple[Example, ...]
    scores: Scores
    accepted: Acceptance | None = None


def method_id(extractor: Extractor, signal: SignalType) -> str:
    method = extractor.config.signals[signal]
    if method.method == "rule":
        return f"{extractor.config.rules_version}__rule"
    return f"{method.prompt}__{extractor.config.model}"


def result_path(signal: SignalType, mid: str, results_dir: Path = RESULTS_DIR) -> Path:
    return results_dir / signal / f"{mid}.json"


def fingerprint(
    extractor: Extractor,
    signal: SignalType,
    golden_dir: Path = GOLDEN_DIR,
    config_dir: Path = CONFIG_DIR,
    prompts_dir: Path = PROMPTS_DIR,
) -> dict[str, str]:
    """The SHA-256 of everything a result depends on, from the files as they are now."""
    method = extractor.config.signals[signal]
    schema = canonical({"schema_version": SCHEMA_VERSION, "schema": json_schema(signal)})
    hashes = {
        "golden": _sha256(golden_dir / f"{signal}.jsonl"),
        "pages": _sha256(golden_dir / PAGES_FILE),
        "prefilter": _sha256(
            config_dir / "extraction" / f"{extractor.config.prefilter_version}.yaml"
        ),
    }
    if method.method == "rule":
        hashes["rules"] = _sha256(
            config_dir / "extraction" / f"{extractor.config.rules_version}.yaml"
        )
    else:
        hashes["prompt"] = _sha256(prompts_dir / f"{method.prompt}.md")
        hashes["schema"] = hashlib.sha256(schema).hexdigest()
        hashes["model"] = hashlib.sha256(extractor.config.model.encode()).hexdigest()
        hashes["effort"] = hashlib.sha256(extractor.config.effort.encode()).hexdigest()
    return dict(sorted(hashes.items()))


def example(page_id: str, selected: bool, result: Extraction | Discarded | None) -> Example:
    """One golden page's outcome: not selected, discarded, or the extraction's answer."""
    if not selected or result is None:
        return Example(page_id=page_id, selected=selected, outcome="not_selected")
    if isinstance(result, Discarded):
        return Example(
            page_id=page_id,
            selected=True,
            outcome="discarded",
            discard_reason=result.reason_code,
            response_key=result.response_key,
        )
    return Example(
        page_id=page_id,
        selected=True,
        outcome="present" if result.present else "absent",
        value=result.value,
        evidence=result.evidence,
        response_key=result.response_key,
    )


def _ratio(num: int, den: int) -> float | None:
    return round(num / den, 4) if den else None


def _counts(pairs: list[tuple[GoldenLabel, Example]]) -> Counts:
    tp = fp = fn = tn = 0
    for label, ex in pairs:
        predicted = ex.outcome == "present"
        right_value = label.value == ex.value
        if label.present and predicted and right_value:
            tp += 1
        elif label.present and predicted:  # the wrong opinion: claimed, and the right one missed
            fp += 1
            fn += 1
        elif predicted:
            fp += 1
        elif label.present:
            fn += 1
        else:
            tn += 1
    precision, recall = _ratio(tp, tp + fp), _ratio(tp, tp + fn)
    f1 = (
        round(2 * precision * recall / (precision + recall), 4)
        if precision is not None and recall is not None and precision + recall > 0
        else None
    )
    return Counts(tp=tp, fp=fp, fn=fn, tn=tn, precision=precision, recall=recall, f1=f1)


def _overlaps(text: str, a: str | None, b: str | None) -> bool:
    if not a or not b:
        return False
    i, j = text.find(a), text.find(b)
    return i >= 0 and j >= 0 and i < j + len(b) and j < i + len(a)


def score(
    labels: list[GoldenLabel],
    examples: tuple[Example, ...],
    texts: dict[str, str],
    rejected_sample: set[str],
) -> Scores:
    """Scores of one signal's examples against its labels (same page ids, any order)."""
    by_page = {e.page_id: e for e in examples}
    if set(by_page) != {lb.page_id for lb in labels} or len(by_page) != len(examples):
        raise ValueError("the examples are not one per golden page")
    pairs = [(lb, by_page[lb.page_id]) for lb in labels]
    discarded: dict[str, int] = {}
    for _lb, ex in pairs:
        if ex.outcome == "discarded":
            reason = str(ex.discard_reason)
            discarded[reason] = discarded.get(reason, 0) + 1
    return Scores(
        pages=len(pairs),
        positives=sum(lb.present for lb in labels),
        prefilter_selected_positives=sum(lb.present and ex.selected for lb, ex in pairs),
        prefilter_missed_in_rejected_sample=sum(
            lb.present and not ex.selected and lb.page_id in rejected_sample for lb, ex in pairs
        ),
        discarded=dict(sorted(discarded.items())),
        evidence_overlaps=sum(
            lb.present
            and ex.outcome == "present"
            and lb.value == ex.value
            and _overlaps(texts.get(lb.page_id, ""), lb.evidence, ex.evidence)
            for lb, ex in pairs
        ),
        extractor=_counts([(lb, ex) for lb, ex in pairs if ex.selected]),
        end_to_end=_counts(pairs),
    )


def rejected_sample(golden_dir: Path = GOLDEN_DIR) -> set[str]:
    path = golden_dir / PAGES_FILE
    if not path.exists():
        return set()
    rows = [
        json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()
    ]
    return {str(row["page_id"]) for row in rows if row["sample"] == "rejected"}


def dump_result(result: EvalResult) -> bytes:
    return (
        json.dumps(result.model_dump(mode="json"), ensure_ascii=False, sort_keys=True, indent=1)
        + "\n"
    ).encode("utf-8")


def load_result(path: Path) -> EvalResult:
    return EvalResult.model_validate_json(path.read_bytes())


# --- the gate ------------------------------------------------------------------------------------


def _at_least(current: float | None, accepted: float | None, tolerance: float) -> bool:
    return (current or 0.0) + tolerance + 1e-9 >= (accepted or 0.0)


def gate_problems(
    extractor: Extractor,
    signal: SignalType,
    gate: Gate,
    golden_dir: Path = GOLDEN_DIR,
    results_dir: Path = RESULTS_DIR,
    config_dir: Path = CONFIG_DIR,
    prompts_dir: Path = PROMPTS_DIR,
) -> list[str] | None:
    """What stops the signal's extraction from passing; None while the gate is inactive for it."""
    labels = golden_labels(signal, golden_dir)
    results = (
        sorted((results_dir / signal).glob("*.json")) if (results_dir / signal).is_dir() else []
    )
    if labels is None or not results:
        return None
    mid = method_id(extractor, signal)
    path = result_path(signal, mid, results_dir)
    if not path.exists():
        return [f"{signal}: no result for the method in use ({mid}); run `make eval`"]
    current = load_result(path)
    now = fingerprint(extractor, signal, golden_dir, config_dir, prompts_dir)
    stale = sorted(
        k for k in now.keys() | current.hashes.keys() if now.get(k) != current.hashes.get(k)
    )
    if stale:
        return [f"{signal}: {mid} was scored on other {', '.join(stale)}; run `make eval`"]
    rescored = score(labels, current.examples, page_texts(golden_dir), rejected_sample(golden_dir))
    if rescored != current.scores:
        return [f"{signal}: {mid}'s stored scores are not its examples' scores"]
    if current.accepted is not None:
        return []
    accepted = [
        r
        for r in (load_result(p) for p in results)
        if r.accepted is not None and r.hashes.get("golden") == now["golden"]
    ]
    if not accepted:
        return [
            f"{signal}: no accepted result on the current golden file; review and `make eval-accept`"
        ]
    last = max(accepted, key=lambda r: (r.accepted.on if r.accepted else date.min, r.method_id))
    ours, theirs = current.scores.end_to_end, last.scores.end_to_end
    problems: list[str] = []
    if not _at_least(ours.precision, theirs.precision, gate.tolerance.precision):
        problems.append(
            f"{signal}: precision {ours.precision} below the accepted {theirs.precision} ({last.method_id})"
        )
    if not _at_least(ours.recall, theirs.recall, gate.tolerance.recall):
        problems.append(
            f"{signal}: recall {ours.recall} below the accepted {theirs.recall} ({last.method_id})"
        )
    return problems
