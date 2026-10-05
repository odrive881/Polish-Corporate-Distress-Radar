"""The golden set: the labelling queue, its rows, the export to `evals/`, and the masking check
(plan 0013 step E, decisions 1 and 5; AGENT_SPEC §6 G3). No I/O: `extraction.label_queue` reads the
stores and writes the files.

- **A queue** per sample version (`config/extraction/golden_sample_<version>.yaml`) holds one row
  per masked text page, drawn from one kind of document:
  - the notes embedded in the statements (v1): every page `prefilter_version` selects for some
    signal, and a fixed random sample of pages it selects for none;
  - the auditor reports (v2, plan 0013 decision 0c): every text page of a sample of whole
    reports, those `rules_version` reads as a modified opinion and a fixed random draw of the
    rest, so the prefilter is measured on every page of a report and the rare opinions are in.
  The owner labels every page for every `signal_type`. A queue is local and never committed:
  until the owner has read a page, the masker's misses are still in it.
- **Hand masking.** A name the masker missed is replaced by the owner with its token, and counted:
  those counts against the masker's own are its recall (decision 1). Over-masking cannot be
  judged from masked text, and is accepted (ADR 0009, third addendum).
- **The export** writes only labelled pages: their masked text once, in `pages.jsonl`, and one
  `<signal_type>.jsonl` per signal with its label for every page. Rows are sorted and serialised
  one way, so the same labels give the same bytes.
- **The masking check** re-runs the masker over every text field of an eval file: anything it
  would still replace is a finding. The export refuses a page that fails it; the pre-commit scan
  and `make check` run it over `evals/`.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, cast

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from distress_radar.extraction import masking, page_text, rules
from distress_radar.extraction.preprocessing import (
    SIGNAL_TYPES,
    DocumentKind,
    Prefilter,
    Sentence,
    SignalType,
    analyse,
    candidates,
)
from distress_radar.parsing.canonical_schema import CONFIG_DIR

GOLDEN_DIR = CONFIG_DIR.parent / "evals" / "text_signals"
PAGES_FILE = "pages.jsonl"
# The fields of an eval file that hold document text, which the masking check reads. Ids and
# hashes are left out: a hash can hold an 11-digit run that is no PESEL.
TEXT_KEYS = frozenset({"text", "evidence", "evidence_span"})
# `opinion_type`'s values: the four opinions of the Polish auditing standards (plan 0013 decision
# 2). Every other signal is present or absent.
OPINION_VALUES = ("unqualified", "qualified", "adverse", "disclaimer")
_TOKEN = re.compile("|".join(re.escape(t) for t in masking.TOKENS.values()))

Sample = Literal["selected", "rejected"]


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class ReportSample(_Frozen):
    """Whole auditor reports: every one `rules_version` reads as a modified opinion, and
    `random_reports` of the rest, drawn by hashing each report's hash with the version."""

    rules_version: str
    random_reports: int = Field(ge=0)


class GoldenSample(_Frozen):
    golden_sample_version: str
    prefilter_version: str
    document_kind: DocumentKind = "statement_notes"
    rejected_sample_size: int | None = Field(default=None, ge=0)  # the notes' design
    report_sample: ReportSample | None = None  # the reports' design

    @model_validator(mode="after")
    def _one_design_per_kind(self) -> GoldenSample:
        notes = self.document_kind == "statement_notes"
        if notes != (self.rejected_sample_size is not None) or notes == (
            self.report_sample is not None
        ):
            raise ValueError(
                "the notes are sampled by `rejected_sample_size`, auditor reports by "
                "`report_sample`, and each only by its own"
            )
        return self


def load_golden_sample(version: str, config_dir: Path = CONFIG_DIR) -> GoldenSample:
    path = config_dir / "extraction" / f"{version}.yaml"
    sample = GoldenSample.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))
    if sample.golden_sample_version != path.stem:
        raise ValueError(
            f"{path}: `golden_sample_version: {sample.golden_sample_version}` must match the "
            "file name"
        )
    return sample


# --- rows ----------------------------------------------------------------------------------------


class Label(_Frozen):
    """One signal on one page. Present needs evidence: a verbatim span of the masked page."""

    present: bool
    value: str | None = None
    evidence: str | None = None

    @model_validator(mode="after")
    def _evidence_with_presence(self) -> Label:
        if self.present and not (self.evidence and self.evidence.strip()):
            raise ValueError("a present signal needs its evidence span")
        if not self.present and (self.value is not None or self.evidence is not None):
            raise ValueError("an absent signal has no value and no evidence")
        return self


class QueuePage(_Frozen):
    page_id: str
    document_hash: str  # the stored download's SHA-256
    source_member: str  # the statement inside it
    attachment: int  # 1-based, `page_text.Attachment.index`
    page: int  # 1-based
    masking_version: str
    prefilter_version: str
    golden_sample_version: str
    document_kind: DocumentKind = "statement_notes"
    sample: Sample
    candidates: dict[SignalType, tuple[tuple[int, int], ...]]  # matched sentences' offsets
    text: str  # masked
    masked: dict[str, int]  # the masker's replacements by kind
    hand_masked: dict[str, int] = {}  # the owner's, by kind: the masker's misses
    labels: dict[SignalType, Label] = {}
    labelled_by: str | None = None
    proposed_by: str | None = None  # a model id, when a model proposed the labels (decision 5)

    @model_validator(mode="after")
    def _labels_fit_the_page(self) -> QueuePage:
        for signal, label in self.labels.items():
            if label.evidence is not None and label.evidence not in self.text:
                raise ValueError(f"{self.page_id} {signal}: evidence is not a span of the page")
            if signal == "opinion_type" and label.present and label.value not in OPINION_VALUES:
                raise ValueError(f"{self.page_id}: opinion_type needs one of {OPINION_VALUES}")
            if signal != "opinion_type" and label.value is not None:
                raise ValueError(f"{self.page_id} {signal}: only opinion_type takes a value")
        return self

    @property
    def complete(self) -> bool:
        return self.labelled_by is not None and set(self.labels) == set(SIGNAL_TYPES)


def page_id(document_hash: str, source_member: str, attachment: int, page: int) -> str:
    key = f"{document_hash}/{source_member}/{attachment}/{page}"
    return hashlib.sha256(key.encode()).hexdigest()[:16]


# --- the queue -----------------------------------------------------------------------------------


@dataclass(frozen=True)
class TextPage:
    document_hash: str
    source_member: str
    attachment: int
    page: int
    text: str  # raw: never stored, sent or printed before masking


@dataclass
class QueueStats:
    text_pages: int = 0
    selected: int = 0
    rejected_pool: int = 0
    rejected_sampled: int = 0
    reports: int = 0  # auditor reports with a text page
    reports_modified: int = 0  # read as a modified opinion by the sample's rules
    reports_sampled: int = 0
    attachment_errors: Counter[str] = field(default_factory=Counter[str])
    not_idempotent: int = 0  # pages the masker changes again: they fail the masking check


def text_pages(
    document_hash: str, source_member: str, statement: bytes, stats: QueueStats
) -> Iterator[TextPage]:
    """The pages with a text layer of every PDF embedded in a statement; errors are counted."""
    for attachment in page_text.attachments(statement):
        if attachment.kind != "pdf":
            continue
        try:
            pages = list(page_text.pages(attachment.data))
        except page_text.AttachmentError as exc:
            stats.attachment_errors[exc.reason_code] += 1
            continue
        for page in pages:
            if page.status == "text":
                yield TextPage(
                    document_hash, source_member, attachment.index, page.number, page.text
                )


def pdf_text_pages(
    document_hash: str, source_member: str, pdf: bytes, stats: QueueStats
) -> list[TextPage]:
    """The pages with a text layer of a separately filed PDF (an auditor report), as attachment
    1; an unreadable PDF is counted and gives none."""
    try:
        pages = list(page_text.pages(pdf))
    except page_text.AttachmentError as exc:
        stats.attachment_errors[exc.reason_code] += 1
        return []
    return [
        TextPage(document_hash, source_member, 1, page.number, page.text)
        for page in pages
        if page.status == "text"
    ]


def _sample_key(version: str, pid: str) -> str:
    return hashlib.sha256(f"{version}:{pid}".encode()).hexdigest()


def _check_prefilter(prefilter: Prefilter, sample: GoldenSample) -> None:
    if prefilter.prefilter_version != sample.prefilter_version:
        raise ValueError(
            f"{sample.golden_sample_version} samples {sample.prefilter_version}, "
            f"not {prefilter.prefilter_version}"
        )


def _row(
    page: TextPage,
    prefilter: Prefilter,
    sample: GoldenSample,
    mask_nlp: Any,
    lemma_nlp: Any,
    stats: QueueStats,
) -> tuple[QueuePage, list[Sentence]]:
    """One page masked, analysed and prefiltered, as a queue row."""
    stats.text_pages += 1
    masked = masking.mask(page.text, mask_nlp)
    if masking.mask(masked.text, mask_nlp).text != masked.text:
        stats.not_idempotent += 1
    sentences = analyse(masked.text, lemma_nlp)
    found = candidates(sentences, prefilter)
    row = QueuePage(
        page_id=page_id(page.document_hash, page.source_member, page.attachment, page.page),
        document_hash=page.document_hash,
        source_member=page.source_member,
        attachment=page.attachment,
        page=page.page,
        masking_version=masking.MASKING_VERSION,
        prefilter_version=prefilter.prefilter_version,
        golden_sample_version=sample.golden_sample_version,
        document_kind=sample.document_kind,
        sample="selected" if found else "rejected",
        candidates={
            signal: tuple(
                (sentences[s].start, sentences[s].end)
                for s in sorted({hit.sentence for hit in hits})
            )
            for signal, hits in found.items()
        },
        text=masked.text,
        masked=masked.counts,
    )
    return row, sentences


def build_report_queue(
    reports: Iterable[list[TextPage]],
    prefilter: Prefilter,
    opinion_rules: rules.Rules,
    sample: GoldenSample,
    mask_nlp: Any,
    lemma_nlp: Any,
    stats: QueueStats,
) -> list[QueuePage]:
    """Every text page of the sampled auditor reports (one list of pages per report), masked,
    sorted by page id: each report the rules read as a modified opinion on some page, and a fixed
    random draw of the others."""
    _check_prefilter(prefilter, sample)
    design = sample.report_sample
    if design is None or sample.document_kind != "auditor_report":
        raise ValueError(f"{sample.golden_sample_version} is not a sample of auditor reports")
    if opinion_rules.rules_version != design.rules_version:
        raise ValueError(
            f"{sample.golden_sample_version} draws by {design.rules_version}, "
            f"not {opinion_rules.rules_version}"
        )
    modified: list[list[QueuePage]] = []
    others: list[tuple[str, list[QueuePage]]] = []
    for pages in reports:
        if not pages:
            continue
        stats.reports += 1
        rows: list[QueuePage] = []
        is_modified = False
        for page in pages:
            row, sentences = _row(page, prefilter, sample, mask_nlp, lemma_nlp, stats)
            opinion = rules.opinion_type(row.text, sentences, opinion_rules)
            is_modified |= opinion.value in ("qualified", "adverse", "disclaimer")
            rows.append(row)
        if is_modified:
            modified.append(rows)
        else:
            others.append((_sample_key(sample.golden_sample_version, pages[0].document_hash), rows))
    others.sort(key=lambda item: item[0])
    chosen = [*modified, *(rows for _key, rows in others[: design.random_reports])]
    stats.reports_modified = len(modified)
    stats.reports_sampled = len(chosen)
    queued = [row for rows in chosen for row in rows]
    stats.selected = sum(row.sample == "selected" for row in queued)
    return sorted(queued, key=lambda row: row.page_id)


def build_queue(
    pages: Iterable[TextPage],
    prefilter: Prefilter,
    sample: GoldenSample,
    mask_nlp: Any,
    lemma_nlp: Any,
    stats: QueueStats,
) -> list[QueuePage]:
    """Every selected page of the notes and the fixed sample of rejected ones, masked, sorted by
    page id."""
    _check_prefilter(prefilter, sample)
    if sample.rejected_sample_size is None or sample.document_kind != "statement_notes":
        raise ValueError(f"{sample.golden_sample_version} is not a sample of the notes")
    selected: list[QueuePage] = []
    rejected: list[QueuePage] = []
    for page in pages:
        row, _sentences = _row(page, prefilter, sample, mask_nlp, lemma_nlp, stats)
        (selected if row.sample == "selected" else rejected).append(row)
    stats.selected = len(selected)
    stats.rejected_pool = len(rejected)
    version = sample.golden_sample_version
    rejected.sort(key=lambda row: _sample_key(version, row.page_id))
    sampled = rejected[: sample.rejected_sample_size]
    stats.rejected_sampled = len(sampled)
    return sorted([*selected, *sampled], key=lambda row: row.page_id)


def merge(existing: list[QueuePage], fresh: list[QueuePage]) -> list[QueuePage]:
    """The fresh queue, keeping every existing row as it is (labels and hand masking). A labelled
    row the fresh queue does not have is an error, never dropped."""
    fresh_ids = {row.page_id for row in fresh}
    orphaned = [row.page_id for row in existing if row.page_id not in fresh_ids and row.labels]
    if orphaned:
        raise ValueError(
            f"{len(orphaned)} labelled page(s) are not in the rebuilt queue (e.g. {orphaned[:3]}): "
            "a changed sample or prefilter is a new golden sample version"
        )
    kept = {row.page_id: row for row in existing}
    return [kept.get(row.page_id, row) for row in fresh]


# --- labelling -----------------------------------------------------------------------------------


def hand_mask(row: QueuePage, target: str, kind: str = "person") -> QueuePage:
    """Every occurrence of `target` replaced by `kind`'s token, in the text and in the evidence
    already labelled, and counted as the masker's miss."""
    if kind not in masking.TOKENS:
        raise ValueError(f"kind must be one of {sorted(masking.TOKENS)}")
    if len(target.strip()) < 2 or any(target in t or t in target for t in masking.TOKENS.values()):
        raise ValueError("the text to mask must be at least two characters and not a token")
    occurrences = row.text.count(target)
    if not occurrences:
        raise ValueError("the text to mask is not on the page")
    token = masking.TOKENS[kind]
    labels = {
        signal: label.model_copy(
            update={
                "evidence": None
                if label.evidence is None
                else label.evidence.replace(target, token)
            }
        )
        for signal, label in row.labels.items()
    }
    counts = Counter(row.hand_masked)
    counts[kind] += occurrences
    starts: list[int] = []
    at = row.text.find(target)
    while at >= 0:  # the occurrences `str.replace` replaces: left to right, not overlapping
        starts.append(at)
        at = row.text.find(target, at + len(target))

    def shift(offset: int, is_end: bool) -> int:
        """An offset in the old text as an offset in the new one; one inside a replaced span
        moves to the token's start (or, for an end, its end)."""
        delta = len(token) - len(target)
        moved = 0
        for n, start in enumerate(starts):
            if offset >= start + len(target):
                moved = (n + 1) * delta
            elif offset > start or (is_end and offset == start + len(target)):
                return start + n * delta + (len(token) if is_end else 0)
        return offset + moved

    return QueuePage.model_validate(
        {
            **row.model_dump(),
            "candidates": {
                signal: [(shift(a, False), shift(b, True)) for a, b in spans]
                for signal, spans in row.candidates.items()
            },
            "text": row.text.replace(target, token),
            "labels": {s: label.model_dump() for s, label in labels.items()},
            "hand_masked": dict(sorted(counts.items())),
        }
    )


def label(row: QueuePage, labels: dict[SignalType, Label], labelled_by: str) -> QueuePage:
    """The page with every signal labelled; validated against the page."""
    if set(labels) != set(SIGNAL_TYPES):
        missing = [s for s in SIGNAL_TYPES if s not in labels]
        raise ValueError(f"every signal_type needs a label; missing {missing}")
    if not labelled_by.strip():
        raise ValueError("labelled_by names who labelled the page")
    return QueuePage.model_validate(
        {
            **row.model_dump(),
            "labels": {s: labels[s].model_dump() for s in SIGNAL_TYPES},
            "labelled_by": labelled_by,
        }
    )


# --- files ---------------------------------------------------------------------------------------


def _line(obj: dict[str, Any]) -> str:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"


def dump_queue(rows: Iterable[QueuePage]) -> bytes:
    return "".join(_line(row.model_dump(mode="json")) for row in rows).encode("utf-8")


def load_queue(data: bytes) -> list[QueuePage]:
    return [QueuePage.model_validate_json(line) for line in data.splitlines() if line.strip()]


def export(rows: Iterable[QueuePage], nlp: Any) -> dict[str, bytes]:
    """`pages.jsonl` and one `<signal_type>.jsonl` per signal, from the labelled pages only.
    Raises when a page fails the masking check: it is fixed in the queue, never exported."""
    done = sorted((row for row in rows if row.complete), key=lambda row: row.page_id)
    if not done:
        raise ValueError("no page is labelled yet: nothing to export")
    failures = {
        row.page_id: findings
        for row in done
        if (
            findings := _unmasked(
                [row.text, *(lb.evidence or "" for lb in row.labels.values())], nlp
            )
        )
    }
    if failures:
        raise ValueError(
            f"{len(failures)} labelled page(s) fail the masking check, by kind: "
            f"{dict(sorted(failures.items())[:5])}; mask them in the queue first"
        )
    page_keys = [
        "page_id",
        "document_hash",
        "source_member",
        "attachment",
        "page",
        "sample",
        "masking_version",
        "prefilter_version",
        "golden_sample_version",
        "document_kind",
        "masked",
        "hand_masked",
        "labelled_by",
        "proposed_by",
        "text",
    ]
    files = {
        PAGES_FILE: "".join(_line({k: row.model_dump()[k] for k in page_keys}) for row in done)
    }
    for signal in SIGNAL_TYPES:
        files[f"{signal}.jsonl"] = "".join(
            _line(
                {
                    "page_id": row.page_id,
                    "prefilter_selected": signal in row.candidates,
                    **row.labels[signal].model_dump(),
                }
            )
            for row in done
        )
    return {name: text.encode("utf-8") for name, text in files.items()}


def masker_recall(rows: Iterable[QueuePage]) -> tuple[int, int]:
    """(persons the masker replaced, persons the owner had to) over the labelled pages."""
    done = [row for row in rows if row.complete]
    return (
        sum(row.masked.get("person", 0) for row in done),
        sum(row.hand_masked.get("person", 0) for row in done),
    )


# --- the masking check ---------------------------------------------------------------------------


def _unmasked(texts: Iterable[str], nlp: Any) -> dict[str, int]:
    found: Counter[str] = Counter()
    for text in texts:
        if text and _TOKEN.sub("", text).strip():
            found.update(masking.mask(text, nlp).counts)
    return dict(sorted(found.items()))


def _texts(obj: object) -> Iterator[str]:
    if isinstance(obj, dict):
        for key, value in cast(dict[str, object], obj).items():
            if key in TEXT_KEYS and isinstance(value, str):
                yield value
            else:
                yield from _texts(value)
    elif isinstance(obj, list):
        for item in cast(list[object], obj):
            yield from _texts(item)


def remaining(text: str, nlp: Any) -> list[tuple[int, int, str]]:
    """The spans the masker would still replace in a masked text, as (start, end, kind): what the
    labelling notebook shows the owner to mask by hand."""
    return masking.spans(text, nlp)


def masking_findings(name: str, data: bytes, nlp: Any) -> list[str]:
    """What the masker would still replace in an eval file's text fields, by line and kind; never
    the text. A `.jsonl` file is read line by line, anything else as one JSON document."""
    try:
        text = data.decode("utf-8")
        docs = (
            [json.loads(line) for line in text.splitlines() if line.strip()]
            if name.endswith(".jsonl")
            else [json.loads(text)]
        )
    except (UnicodeDecodeError, json.JSONDecodeError):
        return ["unreadable: not UTF-8 JSON, so its text cannot be checked"]
    return [
        f"line {n}: would mask {kinds}"
        for n, doc in enumerate(docs, start=1)
        if (kinds := _unmasked(_texts(doc), nlp))
    ]
