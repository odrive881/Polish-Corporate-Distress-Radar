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
A discarded extraction counts as absent, and its reason is counted. For a signal that takes a value
(`opinion_type`, and `post_balance_sheet_event` once valued, decision 9), a present answer with the
wrong value is both a false positive and a false negative; a positive labelled without a value (one
labelled before decision 9) is scored on presence alone. The rejected pages in
the golden set are a sample (`golden_sample_*.yaml`): of the notes, a random sample of the pool, so a
positive the prefilter misses there stands for several in the whole rejected pool; of the auditor
reports, every rejected page of a sample of whole reports. Counts, not rates, are the honest reading.

**Weighted** (plan 0013 decision 14, 2026-10-22): the pages of a stratified sample (decision 12) carry
their probability of being drawn in `pages.jsonl`. Over those pages alone, each counted 1/π times, the
end-to-end true positives, false positives and false negatives are estimates for the draw's segment,
and so are the precision and recall they give. The seed's samples are purposive and carry no weight,
so they are never in it; until a stratified page is labelled, a result has no weighted scores.

**Intervals** (decision 14, K2): 95%, so a result is read with its uncertainty, not accepted on noise.
- Counted: the Wilson score interval of each precision and recall (`counted_intervals`), computed from the
  stored counts, never stored, so no accepted result moves.
- Weighted: the ratio's variance linearised (Taylor), with each entity a cluster, since an entity's
  pages are drawn together; between clusters, sampling with replacement is assumed and the first stage's
  strata are ignored, both of which widen it. The variance gives an effective sample size, and the
  interval is Wilson's at that size (Korn and Graubard), so it does not collapse to a point when every
  drawn page agrees. It needs two entities in the denominator, and with few it is a guide.

**The gate** (`make check`, offline): once a signal has any result, the method in use must have a
result for the current files, that result must re-score to its own stored scores, and its end-to-end
precision and recall may not fall below those of the owner's last accepted result on the same golden
file by more than `eval_gate_*.yaml` allows. A new golden file needs a newly accepted result: scores on
different labels are not comparable. Until a signal has a result, the gate reports it inactive.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass
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


class Interval(_Frozen):
    low: float
    high: float


class WeightedCounts(_Frozen):
    """End-to-end counts over the weighted pages, each counted 1/π times (estimates)."""

    pages: int  # weighted pages scored
    clusters: int  # their entities
    tp: float
    fp: float
    fn: float
    precision: float | None
    recall: float | None
    precision_interval: Interval | None  # None below two clusters
    recall_interval: Interval | None


class Scores(_Frozen):
    pages: int
    positives: int
    prefilter_selected_positives: int
    prefilter_missed_in_rejected_sample: int  # positives on sampled pages it selected for nothing
    discarded: dict[str, int]
    evidence_overlaps: int  # true positives whose evidence overlaps the labelled evidence
    extractor: Counts
    end_to_end: Counts
    weighted: WeightedCounts | None = None  # decision 14; None when no page carries a weight


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
    schema = canonical(
        {"schema_version": SCHEMA_VERSION, "schema": json_schema(signal, method.valued)}
    )
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


def _same_value(label: GoldenLabel, ex: Example) -> bool:
    """A labelled value must be matched; a positive labelled with none is matched on presence."""
    return label.value is None or label.value == ex.value


def _counts(pairs: list[tuple[GoldenLabel, Example]]) -> Counts:
    tp = fp = fn = tn = 0
    for label, ex in pairs:
        predicted = ex.outcome == "present"
        right_value = _same_value(label, ex)
        if label.present and predicted and right_value:
            tp += 1
        elif label.present and predicted:  # the wrong value: claimed, and the right one missed
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


Z95 = 1.959963984540054  # the standard normal's 97.5% quantile


def wilson(successes: int, trials: int, z: float = Z95) -> Interval | None:
    """The Wilson score interval of a proportion; None with no trials."""
    if trials == 0:
        return None
    return _wilson(successes / trials, trials, z)


def _wilson(p: float, trials: float, z: float) -> Interval:
    """The Wilson interval of proportion `p` over `trials`, which may be an effective size."""
    centre = (p + z * z / (2 * trials)) / (1 + z * z / trials)
    half = (
        z * math.sqrt(p * (1 - p) / trials + z * z / (4 * trials * trials)) / (1 + z * z / trials)
    )
    return Interval(low=round(max(0.0, centre - half), 4), high=round(min(1.0, centre + half), 4))


def counted_intervals(counts: Counts) -> dict[str, Interval | None]:
    """Wilson intervals of a result's counted precision and recall."""
    return {
        "precision": wilson(counts.tp, counts.tp + counts.fp),
        "recall": wilson(counts.tp, counts.tp + counts.fn),
    }


@dataclass(frozen=True)
class DrawnPage:
    """A stratified sample's page: its weight, 1/π, and its entity, the cluster."""

    weight: float
    cluster: str


def ratio_interval(rows: list[tuple[str, float, float, float]], z: float = Z95) -> Interval | None:
    """The interval of R = Σ w·y / Σ w·x over (cluster, w, y, x) rows (y ≤ x, both 0 or 1).

    The variance is linearised: each cluster's residual Σ w·(y − R·x) / Σ w·x, their variance
    between clusters (with replacement). It sets an effective size, R·(1 − R) / variance, no more
    than the pages in the denominator, and the interval is Wilson's at that size, so it stays in
    [0, 1] and does not collapse at 0 or 1 (Korn and Graubard's approach). When the variance is 0
    (R at 0 or 1, every cluster alike), the size is the clusters' design effect instead, by
    Kish's formula over each cluster's Σ w·x. None below two clusters in the denominator."""
    mass: defaultdict[str, float] = defaultdict(float)
    for cluster, w, _y, x in rows:
        mass[cluster] += w * x
    total = sum(mass.values())
    clusters = [c for c, m in mass.items() if m > 0]
    if len(clusters) < 2:
        return None
    ratio = sum(w * y for _c, w, y, _x in rows) / total
    residual: defaultdict[str, float] = defaultdict(float)
    for cluster, w, y, x in rows:
        residual[cluster] += w * (y - ratio * x) / total
    n = len(mass)
    mean = sum(residual.values()) / n
    variance = n / (n - 1) * sum((r - mean) ** 2 for r in residual.values())
    pages = sum(1 for *_c, x in rows if x)
    kish = total**2 / sum(m * m for m in mass.values())
    size = min(ratio * (1 - ratio) / variance, pages) if variance > 1e-15 else kish
    return _wilson(ratio, max(size, 1.0), z)


def _weighted(
    pairs: list[tuple[GoldenLabel, Example]], drawn_pages: Mapping[str, DrawnPage]
) -> WeightedCounts | None:
    """`_counts`'s end-to-end tally over the drawn pages, each counted its weight's times, with
    the linearised intervals of its precision and recall."""
    drawn = [(lb, ex, drawn_pages[lb.page_id]) for lb, ex in pairs if lb.page_id in drawn_pages]
    if not drawn:
        return None
    # per page: (cluster, weight, true positive, predicted present, labelled present)
    tally: list[tuple[str, float, float, float, float]] = []
    for label, ex, page in drawn:
        predicted = ex.outcome == "present"
        hit = label.present and predicted and _same_value(label, ex)
        tally.append(
            (page.cluster, page.weight, float(hit), float(predicted), float(label.present))
        )
    tp = sum(w * t for _c, w, t, _p, _l in tally)
    predicted_w = sum(w * p for _c, w, _t, p, _l in tally)
    labelled_w = sum(w * lab for _c, w, _t, _p, lab in tally)
    return WeightedCounts(
        pages=len(drawn),
        clusters=len({c for c, *_ in tally}),
        tp=round(tp, 4),
        fp=round(predicted_w - tp, 4),
        fn=round(labelled_w - tp, 4),
        precision=round(tp / predicted_w, 4) if predicted_w else None,
        recall=round(tp / labelled_w, 4) if labelled_w else None,
        precision_interval=ratio_interval([(c, w, t, p) for c, w, t, p, _l in tally]),
        recall_interval=ratio_interval([(c, w, t, lab) for c, w, t, _p, lab in tally]),
    )


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
    weights: Mapping[str, DrawnPage] | None = None,
) -> Scores:
    """Scores of one signal's examples against its labels (same page ids, any order); `weights`
    (`page_weights`: the drawn pages' weights and clusters) adds the weighted end-to-end estimate."""
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
            and _same_value(lb, ex)
            and _overlaps(texts.get(lb.page_id, ""), lb.evidence, ex.evidence)
            for lb, ex in pairs
        ),
        extractor=_counts([(lb, ex) for lb, ex in pairs if ex.selected]),
        end_to_end=_counts(pairs),
        weighted=_weighted(pairs, weights or {}),
    )


def rejected_sample(golden_dir: Path = GOLDEN_DIR) -> set[str]:
    path = golden_dir / PAGES_FILE
    if not path.exists():
        return set()
    rows = [
        json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()
    ]
    return {str(row["page_id"]) for row in rows if row["sample"] == "rejected"}


def page_weights(golden_dir: Path = GOLDEN_DIR) -> dict[str, DrawnPage]:
    """Page id → its weight (1/π) and entity, for every golden page a stratified sample drew."""
    path = golden_dir / PAGES_FILE
    if not path.exists():
        return {}
    rows = [
        json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()
    ]
    return {
        str(row["page_id"]): DrawnPage(1 / float(row["inclusion_probability"]), str(row["krs"]))
        for row in rows
        if row.get("inclusion_probability") is not None
    }


def unchanged(previous: EvalResult, current: EvalResult) -> bool:
    """Whether a rerun reproduces a stored result, so it keeps the stored one and its acceptance.
    `extractor_version` is left out: it names the run, not the result, and a new extractor version
    that leaves a signal's method alone (decision 9's `extractor_v4`, for eight of nine) must not
    cost that signal its acceptance. The method, every hash, every example and the scores must match."""
    return (
        previous.model_copy(
            update={"accepted": None, "extractor_version": current.extractor_version}
        )
        == current
    )


def dump_result(result: EvalResult) -> bytes:
    data = result.model_dump(mode="json")
    if data["scores"]["weighted"] is None:  # results without weighted pages keep their bytes
        del data["scores"]["weighted"]
    return (json.dumps(data, ensure_ascii=False, sort_keys=True, indent=1) + "\n").encode("utf-8")


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
