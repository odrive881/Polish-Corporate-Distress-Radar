"""The golden set: the labelling queue, its rows, the export to `evals/`, and the masking check
(plan 0013 step E, decisions 1 and 5; AGENT_SPEC §6 G3). No I/O: `extraction.label_queue` reads the
stores and writes the files.

- **A queue** per sample version (`config/extraction/golden_sample_<version>.yaml`) holds one row
  per masked text page, drawn from one kind of document:
  - the notes embedded in the statements (v1): every page `prefilter_version` selects for some
    signal, and a fixed random sample of pages it selects for none;
  - the auditor reports (v2, plan 0013 decision 0c): every text page of a sample of whole
    reports, those `rules_version` reads as a modified opinion and a fixed random draw of the
    rest, so the prefilter is measured on every page of a report and the rare opinions are in;
  - the notes, targeted (v3, 2026-10-21): every page `prefilter_version` selects for one of a few
    named signals, the ones with too few labelled positives, and a fixed random share of the pages
    it selects for none. The share is drawn by a threshold on each page's hash, so a later delivery
    adds pages to it without moving any already drawn. Pages already in another queue or in the
    committed golden set are left out, so no page is exported twice;
  - stratified (v4 on, plan 0013 decision 12, 2026-10-22): the text pages of one kind of document
    of the entities of one draw, each in one stratum by what the extractor in use already answered
    on it, and drawn by a threshold on its hash at its stratum's rate. A page's probability of being
    in the sample is its entity's π times that rate, exactly, and its weight the inverse: the
    estimates are the draw's segment's, the strata only make them cheaper. Nothing is left out: a
    drawn page already labelled in another queue or the golden set carries its labels over.
  The owner labels every page for every `signal_type`, except on a stratified sample's page, which
  is labelled for the signals its document kind is asked (`labelled`, decision 15). A queue is
  local and never committed: until the owner has read a page, the masker's misses are still in it.
- **Labelling a stratified sample in order** (decisions 16–23, 2026-11-06). Its rates are
  ceilings, and its pages are labelled in the order of their draw positions (`u / rate`). The
  labelled pages are those below the first unlabelled one's position, `cut`, so stopping at any
  point is the sample a rate of `cut × rate` would have drawn, and the export writes each page's
  π at that cut. The answered-present stratum is drawn by document, so a document the feature
  flags has every flagged page labelled; a held-out share of entities is kept out of prompt
  tuning; proposals are shown only where the model answered present; a share of pages is
  re-checked blind after some days, which measures the labeller against themself.
- **Hand masking.** A name the masker missed is replaced by the owner with its token, and counted:
  those counts against the masker's own are its recall (decision 1). Over-masking cannot be
  judged from masked text, and is accepted (ADR 0009, third addendum).
- **The export** writes only labelled pages: their masked text once, in `pages.jsonl`, and one
  `<signal_type>.jsonl` per signal with its label for every page. Rows are sorted and serialised
  one way, so the same labels give the same bytes.
- **The masking check** re-runs the masker over every text field of an eval file: anything it
  would still replace is a finding. The export refuses a page that fails it; the pre-commit scan
  and `make check` run it over `evals/`.
- **The hand-masking check** looks for what only the owner masks (`HAND_PATTERNS`: a register
  number, a bank account, a file name) in the same fields, by the wordings the golden set has
  shown. It needs no model, so the pre-commit scan runs it on every eval file it stages.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass, field
from datetime import date
from fractions import Fraction
from pathlib import Path
from statistics import median
from typing import Any, Literal, cast, get_args

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

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
from distress_radar.extraction.schemas import SIGNAL_VALUES
from distress_radar.parsing.canonical_schema import CONFIG_DIR

GOLDEN_DIR = CONFIG_DIR.parent / "evals" / "text_signals"
PAGES_FILE = "pages.jsonl"
# What the owner saw flagged by the masker and left unmasked, by page: the masking check skips
# exactly those spans (a span's kind and the hash of its text), nothing it has not been shown.
CLEARED_FILE = "masking_cleared.json"
# The fields of an eval file that hold document text, which the masking check reads. Ids and
# hashes are left out: a hash can hold an 11-digit run that is no PESEL.
TEXT_KEYS = frozenset({"text", "evidence", "evidence_span"})
# `opinion_type`'s values: the four opinions of the Polish auditing standards (plan 0013 decision
# 2), required when present. `post_balance_sheet_event` takes the kind of event (decision 9), which a
# label made before decision 9 lacks: until valued, it is scored on presence. Every other signal is
# present or absent.
OPINION_VALUES = SIGNAL_VALUES["opinion_type"]
# What a labeller may mask by hand: the masker's kinds, and three it does not find, which an
# auditor's report prints beside a name it does find (2026-10-15): a key auditor's or an audit
# firm's register number (a sole practitioner's identifies a person, and the firm number is never
# stored either way), and a natural-person firm's NIP or REGON; a sole practitioner's address; the
# bank account in such a firm's letterhead; and a filer's file name (ADR 0009, second addendum).
# Hand-masking only: the masker and `MASKING_VERSION` are unchanged.
HAND_TOKENS = {
    **masking.TOKENS,
    "register_number": "[nr]",
    "address": "[adres]",
    "bank_account": "[rachunek]",
    "file_name": "[plik]",
}
_TOKEN = re.compile("|".join(re.escape(t) for t in HAND_TOKENS.values()))
# What the hand-masking check finds, by `HAND_TOKENS` kind: the wordings an auditor's report
# printed beside a number in the golden set on 2026-10-15, every one masked by hand then.
# A register number has at most 5 digits, so a company's 10-digit KRS number after the same words
# does not match, nor does a resolution of the national council (`Krajowej Rady ... nr 3430`).
# A Polish account number (NRB) is 26 digits printed in groups; an audited company's NIP is
# legitimate on these pages and is not looked for. Run over text with its whitespace collapsed.
HAND_PATTERNS: dict[str, re.Pattern[str]] = {
    "register_number": re.compile(
        r"(?i)(pod (numerem|nr\.?)|nr\.? ewid\w*|numer\w* ewid\w*|w rejestrze:?|nr firmy\w*|"
        r"Izbie Biegłych Rewidentów|w rej\.( KRBR)?|Rejestr PIBR nr|sprawozdań finansowych nr|"
        r"\[osoba\],? \(?nr)\s*-?\s*\d{2,5}(?!\d)"
    ),
    "bank_account": re.compile(r"(?<!\d)\d{2}(?:\s?\d{4}){6}(?!\d)"),
    "file_name": re.compile(r"[\w\-]+\.(xml|xades|pdf|docx?|xlsx?)\b", re.IGNORECASE),
}

Sample = Literal["selected", "rejected"]
# A stratified sample's strata (decision 12), by the kept answers of the extractor the sample names.
Stratum = Literal["answered_present", "answered_absent", "not_selected"]
STRATA: tuple[Stratum, ...] = ("answered_present", "answered_absent", "not_selected")
# Why a label differs from the model's answer (decision 21): picked when the owner overrules a
# proposal, or labels present what the model did not answer. A fixed list, so the reasons count.
Reason = Literal[
    "template_sentence",  # a policy or boilerplate sentence, no threat or event stated
    "other_company",  # about another company (a shareholder, a subsidiary, a client)
    "earlier_year",  # carried over from an earlier year's statement
    "hypothetical",  # a risk described, not stated as the case
    "not_this_page",  # the signal is on the page, but its sentence starts on another
    "wrong_value",  # present, but another opinion or kind of event
    "missed_by_model",  # present, and the model said absent or was not asked
    "other",
]
REASONS: tuple[Reason, ...] = get_args(Reason)
GUIDE_FILE = "labelling_guide.md"
_GUIDE_VERSION = re.compile(r"^\*\*Version: (labelling_guide_v\d+)\*\*", re.MULTILINE)


def guide_version(directory: Path = GOLDEN_DIR) -> str:
    """The labelling guide's version, from its `**Version: labelling_guide_v<n>**` line
    (decision 20): a stratified page records the version it was labelled under."""
    found = _GUIDE_VERSION.search((directory / GUIDE_FILE).read_text(encoding="utf-8"))
    if found is None:
        raise ValueError(
            f"{directory / GUIDE_FILE} has no `**Version: labelling_guide_v<n>**` line"
        )
    return found.group(1)


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class ReportSample(_Frozen):
    """Whole auditor reports: every one `rules_version` reads as a modified opinion, and
    `random_reports` of the rest, drawn by hashing each report's hash with the version."""

    rules_version: str
    random_reports: int = Field(ge=0)


class TargetedSample(_Frozen):
    """The notes, targeted: every page the prefilter selects for one of `signals`, and the pages it
    selects for none whose hash with the version falls below `rejected_rate`."""

    signals: tuple[SignalType, ...] = Field(min_length=1)
    rejected_rate: float = Field(gt=0, le=1)


class DrawPin(_Frozen):
    """The draw a stratified sample is drawn from, pinned as a weighted backtest pins it
    (`models.splits.WeightsConfig`): its entities and their π are its `universe_draws` rows."""

    draw_spec_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    random: str
    distress: str | None = None


class Targets(_Frozen):
    """Decision 16: when labelling may stop. Each named signal has `positives` labelled positives
    below the cut (for a signal in `answered_values`, positives with one of those values), or every
    answered-present page is labelled. Fixed before the first label and read on counts only, never
    on a score, so stopping does not pick a result."""

    signals: tuple[SignalType, ...] = Field(min_length=1)
    positives: int = Field(default=20, ge=1)


class StratifiedSample(_Frozen):
    """Decision 12: every text page of the draw's entities, in one stratum by the kept answers
    of extractor `answers` for `signals`, drawn at its stratum's rate."""

    draw: DrawPin
    answers: str  # the extractor version whose kept answers in `text_signals` decide the strata
    signals: tuple[SignalType, ...] = Field(min_length=1)
    # Decision 16: ceilings. Pages are labelled in draw order, and the cut where labelling stops
    # scales every rate alike, so their ratios are the allocation and the cut the size.
    rates: dict[Stratum, float]
    targets: Targets
    # Decision 13: the share of pages labelled without the answers shown as proposals, drawn by
    # hash, so the owner's agreement with the model is measured with and without them. Within the
    # answered-present stratum only: the other two are always blind (decision 17).
    blind_rate: float = Field(default=0.2, gt=0, lt=1)
    # Decision 15: the signals a page of this document kind is labelled for. The others are not
    # asked on it, and its page is in no golden file of theirs.
    labelled: tuple[SignalType, ...] = SIGNAL_TYPES
    # Decision 18: a kept answer counts as answered present for the strata only with one of these
    # values (`opinion_type`: the modified opinions, so the unmodified ones are not oversampled).
    answered_values: dict[SignalType, tuple[str, ...]] = {}
    # Decision 19: the share of entities whose pages are held out of prompt tuning, by hash: a
    # whole entity, since its accountants' wording repeats across its pages.
    holdout_rate: float = Field(default=0.3, ge=0, lt=1)
    # Decision 20: the first pages in draw order, after which the guide is reviewed and frozen.
    pilot_pages: int = Field(default=30, ge=0)
    # Decision 22: the share of pages labelled again, blind to the first labels, after some days.
    recheck_rate: float = Field(default=0.05, ge=0, lt=1)
    recheck_after_days: int = Field(default=7, ge=1)

    @model_validator(mode="after")
    def _a_rate_per_stratum(self) -> StratifiedSample:
        if set(self.rates) != set(STRATA) or not all(0 < r <= 1 for r in self.rates.values()):
            raise ValueError(f"`rates` needs each of {STRATA}, in (0, 1]")
        labelled = set(self.labelled)
        if not labelled or len(labelled) != len(self.labelled):
            raise ValueError("`labelled` names each signal once, at least one")
        outside = [
            s
            for s in (*self.signals, *self.targets.signals, *self.answered_values)
            if s not in labelled
        ]
        if outside:
            raise ValueError(f"{sorted(set(outside))} are not in `labelled`")
        for signal, values in self.answered_values.items():
            if signal not in self.signals:
                raise ValueError(f"`answered_values` names {signal}, which does not stratify")
            if not values or not set(values) <= set(SIGNAL_VALUES.get(signal, ())):
                raise ValueError(f"`answered_values.{signal}` must be some of its values")
        return self


class GoldenSample(_Frozen):
    golden_sample_version: str
    prefilter_version: str
    document_kind: DocumentKind = "statement_notes"
    rejected_sample_size: int | None = Field(default=None, ge=0)  # the notes' design
    report_sample: ReportSample | None = None  # the reports' design
    targeted: TargetedSample | None = None  # the notes, targeted at named signals
    stratified: StratifiedSample | None = None  # either kind, from a draw (decision 12)
    # Signals parked by decision 10: "absent" is set in advance on an unlabelled page, one click
    # to change, never a model's answer.
    parked: tuple[SignalType, ...] = ()
    # The lists the documents are drawn from (`universe_candidates.discovery_source`); unset, every
    # stored entity. v1 and v2 were drawn when the store held the seed alone, so they name it: a
    # rebuild over a larger store must not redraw them.
    discovery_sources: tuple[str, ...] | None = Field(default=None, min_length=1)

    @model_validator(mode="after")
    def _one_design_per_kind(self) -> GoldenSample:
        notes = self.document_kind == "statement_notes"
        notes_designs = (self.rejected_sample_size is not None) + (self.targeted is not None)
        report_designs = self.report_sample is not None
        stratified = self.stratified is not None
        if (
            notes_designs + report_designs + stratified != 1
            or (notes and report_designs)
            or (not notes and notes_designs)
        ):
            raise ValueError(
                "the notes are sampled by `rejected_sample_size`, `targeted` or `stratified`, "
                "auditor reports by `report_sample` or `stratified`, and each by one only"
            )
        if stratified and self.discovery_sources is not None:
            raise ValueError("a stratified sample is drawn from its draw, not `discovery_sources`")
        if self.stratified is not None and not set(self.parked) <= set(self.stratified.labelled):
            raise ValueError("a parked signal must be one the sample labels")
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
    """One signal on one page. Present needs evidence: a verbatim span of the masked page.
    `unsure` marks a call the owner doubts (the guide's "when unsure, absent"), so a score can be
    read without them; `reason` says why the label differs from the model's answer (decision 21)."""

    present: bool
    value: str | None = None
    evidence: str | None = None
    unsure: bool = False
    reason: Reason | None = None

    @model_validator(mode="after")
    def _evidence_with_presence(self) -> Label:
        if self.present and not (self.evidence and self.evidence.strip()):
            raise ValueError("a present signal needs its evidence span")
        if not self.present and (self.value is not None or self.evidence is not None):
            raise ValueError("an absent signal has no value and no evidence")
        return self

    def written(self) -> dict[str, Any]:
        """As an eval file holds it: `unsure` and `reason` only when set, so a label without
        them keeps the bytes it had before decision 21."""
        data = self.model_dump()
        if not self.unsure:
            del data["unsure"]
        if self.reason is None:
            del data["reason"]
        return data


class Recheck(_Frozen):
    """A signal labelled a second time, blind to the first label (decision 22): presence, and
    the value of a valued signal. No evidence: only agreement is measured."""

    present: bool
    value: str | None = None


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
    cleared: tuple[str, ...] = ()  # `span_key`s the owner reviewed and left: false positives
    labels: dict[SignalType, Label] = {}
    labelled_by: str | None = None
    proposed_by: str | None = None  # a model id, when a model proposed the labels (decision 5)
    # A stratified sample's (decision 12): the page's stratum, and its probability of being drawn,
    # its entity's π times the stratum's rate. Both or neither.
    stratum: Stratum | None = None
    inclusion_probability: float | None = Field(default=None, gt=0, le=1)
    # Its entity (a company's KRS number, never a person's): the cluster its interval resamples,
    # since an entity's pages are drawn together (decision 14).
    krs: str | None = None
    # Decision 13: the answering extractor's kept answers on the page, by signal (a signal it was
    # not asked is missing), and whether the page is labelled without them (`blind`). Local only:
    # the export writes the owner's labels, and `proposed_by` when proposals were shown.
    proposals: dict[SignalType, Label] = {}
    blind: bool = False
    # Decision 15: the signals this page is labelled for; None, every one (the seed's samples).
    signals: tuple[SignalType, ...] | None = None
    # Decision 16: the page's draw position, u / rate, the order it is labelled in, and whether a
    # page labelled in another queue was carried over (it is no gap in this one's order).
    draw_position: float | None = Field(default=None, ge=0, lt=1)
    carried: bool = False
    # Decision 17: the signals the answering extractor kept present anywhere in the page's
    # document: what its document-level feature says.
    flagged: tuple[SignalType, ...] = ()
    holdout: bool | None = None  # decision 19: its entity is held out of prompt tuning
    guide_version: str | None = None  # decision 20: the guide it was labelled under
    labelled_on: date | None = None
    seconds: float | None = None  # how long the page was on screen before it was saved; local
    # Decision 22: drawn for a second, blind labelling, and that labelling once done.
    recheck: bool = False
    rechecks: dict[SignalType, Recheck] = {}
    rechecked_on: date | None = None

    @model_validator(mode="after")
    def _labels_fit_the_page(self) -> QueuePage:
        drawn = (self.stratum, self.inclusion_probability, self.krs)
        if len({v is None for v in drawn}) != 1:
            raise ValueError(f"{self.page_id}: a stratum needs its inclusion probability and krs")
        if self.stratum is None and (self.draw_position is not None or self.holdout is not None):
            raise ValueError(f"{self.page_id}: a draw position or holdout needs a stratum")
        outside = [s for s in (*self.labels, *self.rechecks) if s not in self.required]
        if outside:
            raise ValueError(f"{self.page_id}: {outside} are not labelled on this page")
        for signal, check in self.rechecks.items():
            if check.value is not None and check.value not in SIGNAL_VALUES.get(signal, ()):
                raise ValueError(f"{self.page_id} {signal}: recheck value {check.value!r}")
        for signal, label in [*self.labels.items(), *self.proposals.items()]:
            if label.evidence is not None and label.evidence not in self.text:
                raise ValueError(f"{self.page_id} {signal}: evidence is not a span of the page")
            if signal == "opinion_type" and label.present and label.value not in OPINION_VALUES:
                raise ValueError(f"{self.page_id}: opinion_type needs one of {OPINION_VALUES}")
            allowed = SIGNAL_VALUES.get(signal, ())
            if label.value is not None and label.value not in allowed:
                raise ValueError(
                    f"{self.page_id} {signal}: value {label.value!r} is not one of {allowed}"
                )
        return self

    @property
    def required(self) -> tuple[SignalType, ...]:
        """The signals this page is labelled for, in `SIGNAL_TYPES` order."""
        asked = set(self.signals) if self.signals is not None else set(SIGNAL_TYPES)
        return tuple(s for s in SIGNAL_TYPES if s in asked)

    @property
    def complete(self) -> bool:
        return self.labelled_by is not None and set(self.labels) == set(self.required)


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
    excluded: int = 0  # target or drawn pages already in another queue or the golden set
    strata: Counter[str] = field(default_factory=Counter[str])  # a stratified sample's pools
    drawn: Counter[str] = field(default_factory=Counter[str])  # and what it drew from each
    carried: int = 0  # drawn pages already labelled elsewhere, their labels carried over
    proposals_dropped: int = 0  # answers whose evidence is not on the masked page
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


def _drawn(version: str, pid: str, rate: float) -> bool:
    """Whether a page falls in a share `rate` of its pool: a threshold on its hash with the
    version, so the pages drawn stay drawn however many the pool gains."""
    return int(_sample_key(version, pid)[:16], 16) < rate * 2**64


def uniform(version: str, unit: str) -> float:
    """A unit's draw number in [0, 1): its hash with the version, as `_drawn` thresholds it."""
    return int(_sample_key(version, unit)[:16], 16) / 2**64


def build_targeted_queue(
    pages: Iterable[TextPage],
    prefilter: Prefilter,
    sample: GoldenSample,
    mask_nlp: Any,
    lemma_nlp: Any,
    stats: QueueStats,
    exclude: frozenset[str] = frozenset(),
) -> list[QueuePage]:
    """Every page of the notes the prefilter selects for one of the sample's signals, and its
    fixed random share of the pages it selects for none, less the page ids in `exclude`
    (already in another queue or the golden set), masked, sorted by page id."""
    _check_prefilter(prefilter, sample)
    design = sample.targeted
    if design is None or sample.document_kind != "statement_notes":
        raise ValueError(f"{sample.golden_sample_version} is not a targeted sample of the notes")
    version = sample.golden_sample_version
    queued: list[QueuePage] = []
    for page in pages:
        row, _sentences = _row(page, prefilter, sample, mask_nlp, lemma_nlp, stats)
        targeted = any(signal in row.candidates for signal in design.signals)
        if targeted:
            stats.selected += 1
        elif row.sample == "rejected":
            stats.rejected_pool += 1
            if not _drawn(version, row.page_id, design.rejected_rate):
                continue
            stats.rejected_sampled += 1
        else:
            continue  # selected for another signal only: not this sample's
        if row.page_id in exclude:
            stats.excluded += 1
            continue
        queued.append(row)
    return sorted(queued, key=lambda row: row.page_id)


@dataclass(frozen=True)
class StratifiedFrame:
    """What a stratified sample reads beside the pages (decision 12), from the stores."""

    entity_pi: Mapping[str, Fraction]  # krs → π, the draw's entities only
    answered_present: frozenset[str]  # page ids with a kept non-absent answer for the signals
    read: frozenset[tuple[str, str]]  # (document hash, member) the answering extractor read
    labelled_elsewhere: Mapping[str, QueuePage] = field(default_factory=dict[str, QueuePage])
    # page id → the answering extractor's kept answer per signal (decision 13), every signal
    proposals: Mapping[str, Mapping[SignalType, Label]] = field(
        default_factory=dict[str, Mapping[SignalType, Label]]
    )
    # (document hash, member) → the signals the answering extractor kept present in it (decision 17)
    flagged: Mapping[tuple[str, str], frozenset[SignalType]] = field(
        default_factory=dict[tuple[str, str], frozenset[SignalType]]
    )


def proposal(signal: SignalType, value: str, evidence: str | None) -> Label | None:
    """A kept answer in `text_signals` as a label: `absent`, `present`, or a value (the opinion,
    the kind of event) with its evidence; None when it does not fit a label (no evidence)."""
    if value == "absent":
        return Label(present=False)
    if not evidence:
        return None
    return Label(
        present=True,
        value=value if value != "present" and value in SIGNAL_VALUES.get(signal, ()) else None,
        evidence=evidence,
    )


def stratum_of(
    row: QueuePage, signals: tuple[SignalType, ...], answered: frozenset[str]
) -> Stratum:
    """A page's stratum: answered present, selected for a signal and not answered present, or
    selected for none of them."""
    selected = any(signal in row.candidates for signal in signals)
    if row.page_id in answered:
        if not selected:
            raise ValueError(
                f"{row.page_id}: answered present, but the prefilter selects it for none of "
                f"{signals}: the answers are not the prefilter's"
            )
        return "answered_present"
    return "answered_absent" if selected else "not_selected"


def build_stratified_queue(
    pages: Iterable[tuple[str, TextPage]],
    prefilter: Prefilter,
    sample: GoldenSample,
    frame: StratifiedFrame,
    mask_nlp: Any,
    lemma_nlp: Any,
    stats: QueueStats,
) -> list[QueuePage]:
    """The pages (with their entity's KRS) of the draw's entities drawn at their stratum's rate,
    each with its stratum, inclusion probability at the ceiling rate and draw position, masked,
    sorted by page id. Raises when a page's document was not read by the answering extractor: its
    stratum would be a missing run's.

    An answered-present page is drawn by its document's hash, so a document's answered-present
    pages are drawn together and share a position (decision 17); every other page by its own.
    Proposals are offered only on answered-present pages: on the others the model's answer is
    "absent", and showing it would pull the labels that measure its misses toward it (decision 17).
    The holdout is drawn by entity (decision 19), the recheck by page (decision 22)."""
    _check_prefilter(prefilter, sample)
    design = sample.stratified
    if design is None:
        raise ValueError(f"{sample.golden_sample_version} is not a stratified sample")
    version = sample.golden_sample_version
    unread: set[tuple[str, str]] = set()
    queued: list[QueuePage] = []
    for krs, page in pages:
        pi = frame.entity_pi.get(krs)
        if pi is None:
            continue
        document = (page.document_hash, page.source_member)
        if document not in frame.read:
            unread.add(document)
            continue
        row, _sentences = _row(page, prefilter, sample, mask_nlp, lemma_nlp, stats)
        stratum = stratum_of(row, design.signals, frame.answered_present)
        stats.strata[stratum] += 1
        rate = design.rates[stratum]
        unit = (
            f"{page.document_hash}/{page.source_member}"
            if stratum == "answered_present"
            else row.page_id
        )
        u = uniform(version, unit)
        if u >= rate:
            continue
        stats.drawn[stratum] += 1
        flagged = frame.flagged.get(document, frozenset())
        drawn = {
            "stratum": stratum,
            "inclusion_probability": float(pi * Fraction(str(rate))),
            "krs": krs,
            "draw_position": u / rate,
            "holdout": _drawn(f"{version}:holdout", krs, design.holdout_rate),
            "flagged": tuple(s for s in SIGNAL_TYPES if s in flagged),
            "recheck": _drawn(f"{version}:recheck", row.page_id, design.recheck_rate),
        }
        done = frame.labelled_elsewhere.get(row.page_id)
        if done is not None:  # its labelled row, hand masking and all, joins this sample
            stats.carried += 1
            row = done.model_copy(
                update={
                    **drawn,
                    "carried": True,
                    "golden_sample_version": version,
                    "prefilter_version": row.prefilter_version,
                    "sample": row.sample,
                    # this prefilter's signals; the offsets are the labelled text's, where it had them
                    "candidates": {s: done.candidates.get(s, ()) for s in row.candidates},
                }
            )
        else:
            offered = frame.proposals.get(row.page_id, {})
            kept = {  # evidence the masked page no longer holds is not offered
                s: lb
                for s, lb in offered.items()
                if s in design.labelled and (lb.evidence is None or lb.evidence in row.text)
            }
            stats.proposals_dropped += sum(s in design.labelled for s in offered) - len(kept)
            blind = stratum != "answered_present" or _drawn(
                f"{version}:blind", row.page_id, design.blind_rate
            )
            row = row.model_copy(
                update={
                    **drawn,
                    "proposals": kept,
                    "blind": blind,
                    "signals": design.labelled,
                }
            )
        queued.append(row)
    if unread:
        raise ValueError(
            f"{len(unread)} document(s) of the draw have no `text_coverage` row under "
            f"{design.answers}: run the `text` job before drawing {version}"
        )
    return sorted(queued, key=lambda row: row.page_id)


# What the owner put on a row, carried from the existing queue onto the rebuilt one. Everything
# else (the text, the candidates, the proposals, the stratum) is the fresh build's.
OWNER_WORK = frozenset(
    {
        "labels",
        "labelled_by",
        "proposed_by",
        "guide_version",
        "labelled_on",
        "seconds",
        "rechecks",
        "rechecked_on",
        "cleared",
    }
)


def merge(existing: list[QueuePage], fresh: list[QueuePage]) -> list[QueuePage]:
    """The fresh queue, with the owner's work on each existing row carried over (plan 0025): a
    page's text, proposals and stratum are what the pages read now, so a reading or answering
    change reaches the pages not yet labelled. A hand-masked row is kept as it is, its text being
    the owner's edit. A labelled row the fresh queue does not have, or whose evidence is no longer
    on the fresh page, is an error, never dropped."""
    fresh_ids = {row.page_id for row in fresh}
    orphaned = [row.page_id for row in existing if row.page_id not in fresh_ids and row.labels]
    if orphaned:
        raise ValueError(
            f"{len(orphaned)} labelled page(s) are not in the rebuilt queue (e.g. {orphaned[:3]}): "
            "a changed sample or prefilter is a new golden sample version"
        )
    kept = {row.page_id: row for row in existing}
    merged: list[QueuePage] = []
    moved: list[str] = []
    for row in fresh:
        old = kept.get(row.page_id)
        if old is None:
            merged.append(row)
        elif old.hand_masked:
            merged.append(old)
        else:
            try:
                merged.append(
                    QueuePage.model_validate(
                        {**row.model_dump(), **old.model_dump(include=set(OWNER_WORK))}
                    )
                )
            except ValidationError:
                moved.append(row.page_id)
    if moved:
        raise ValueError(
            f"{len(moved)} labelled page(s) no longer hold their evidence or labels in the rebuilt "
            f"queue (e.g. {moved[:3]}): set their labels aside and label them again"
        )
    return merged


# --- labelling -----------------------------------------------------------------------------------


def hand_mask(row: QueuePage, target: str, kind: str = "person") -> QueuePage:
    """Every occurrence of `target` replaced by `kind`'s token, in the text and in the evidence
    already labelled, and counted as the masker's miss."""
    if kind not in HAND_TOKENS:
        raise ValueError(f"kind must be one of {sorted(HAND_TOKENS)}")
    if len(target.strip()) < 2 or any(target in t or t in target for t in HAND_TOKENS.values()):
        raise ValueError("the text to mask must be at least two characters and not a token")
    occurrences = row.text.count(target)
    if not occurrences:
        raise ValueError("the text to mask is not on the page")
    token = HAND_TOKENS[kind]
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


def label(
    row: QueuePage,
    labels: dict[SignalType, Label],
    labelled_by: str,
    proposed_by: str | None = None,
    *,
    guide: str | None = None,
    on: date | None = None,
    seconds: float | None = None,
) -> QueuePage:
    """The page with every signal it is asked labelled; validated against the page. `proposed_by`
    names the extractor whose answers were shown as proposals (decision 13), None when none were;
    `guide` the labelling guide's version, `on` the day and `seconds` the time on screen, which a
    stratified page records (decisions 20–23)."""
    if set(labels) != set(row.required):
        missing = [s for s in row.required if s not in labels]
        extra = [s for s in labels if s not in row.required]
        raise ValueError(
            f"the page's signals need a label each; missing {missing}, not asked {extra}"
        )
    if not labelled_by.strip():
        raise ValueError("labelled_by names who labelled the page")
    return QueuePage.model_validate(
        {
            **row.model_dump(),
            "labels": {s: labels[s].model_dump() for s in row.required},
            "labelled_by": labelled_by,
            "proposed_by": proposed_by,
            "guide_version": guide,
            "labelled_on": on,
            "seconds": None if seconds is None else round(seconds, 1),
        }
    )


def shows_proposals(row: QueuePage) -> bool:
    """Whether the page is labelled with the model's answers shown (decision 13): never outside the
    answered-present stratum (decision 17), whatever its `blind` says."""
    return bool(row.proposals) and not row.blind and row.stratum in (None, "answered_present")


def prefill(row: QueuePage, parked: Iterable[SignalType] = ()) -> dict[SignalType, Label]:
    """What the labelling form starts from: the saved labels, else the proposals when shown, and
    "absent" for a parked signal with none. A signal with nothing is left out: the owner decides."""
    if row.labels:
        return dict(row.labels)
    start: dict[SignalType, Label] = {s: Label(present=False) for s in parked if s in row.required}
    if shows_proposals(row):
        start.update({s: lb for s, lb in row.proposals.items() if s in row.required})
    return start


# --- labelling in draw order (decision 16) -------------------------------------------------------


def in_draw_order(rows: Iterable[QueuePage]) -> list[QueuePage]:
    """A stratified queue's rows in the order they are labelled: by draw position, a document's
    answered-present pages together; a row without one (another design) after, by page id."""
    return sorted(
        rows,
        key=lambda r: (r.draw_position is None, r.draw_position or 0.0, r.document_hash, r.page_id),
    )


def cut(rows: Iterable[QueuePage]) -> dict[str, float]:
    """Each stratified sample's cut: the draw position of its first unlabelled page, 1.0 when
    every page is labelled. The pages below it are the labelled sample."""
    out: dict[str, float] = {}
    for row in rows:
        if row.draw_position is None:
            continue
        version = row.golden_sample_version
        out.setdefault(version, 1.0)
        if not row.complete:
            out[version] = min(out[version], row.draw_position)
    return out


def _counts_toward(row: QueuePage, signal: SignalType, design: StratifiedSample) -> bool:
    label = row.labels.get(signal)
    if label is None or not label.present:
        return False
    values = design.answered_values.get(signal)
    return values is None or label.value in values


@dataclass(frozen=True)
class Progress:
    """Where labelling a stratified sample stands (decision 16)."""

    cut: float
    queued: int
    labelled: int  # pages below the cut
    beyond: tuple[str, ...]  # pages labelled above the cut, not carried: gaps to fill first
    positives: dict[SignalType, int]  # the targets' positives below the cut
    target: int
    answered_present_done: bool  # every answered-present page labelled
    pilot_pages: int
    stale_guide: int  # pages labelled under another version of the guide

    @property
    def met(self) -> bool:
        """Whether the stopping rule holds: every target reached, or nothing left to find them in."""
        return self.answered_present_done or all(n >= self.target for n in self.positives.values())

    @property
    def in_pilot(self) -> bool:
        return self.labelled < self.pilot_pages


def progress(rows: list[QueuePage], sample: GoldenSample, guide: str | None = None) -> Progress:
    design = sample.stratified
    if design is None:
        raise ValueError(f"{sample.golden_sample_version} is not a stratified sample")
    version = sample.golden_sample_version
    ours = [r for r in rows if r.golden_sample_version == version and r.draw_position is not None]
    at = cut(ours).get(version, 1.0)
    below = [r for r in ours if r.draw_position is not None and r.draw_position < at]
    return Progress(
        cut=at,
        queued=len(ours),
        labelled=len(below),
        beyond=tuple(
            r.page_id
            for r in ours
            if r.complete
            and not r.carried
            and r.draw_position is not None
            and r.draw_position >= at
        ),
        positives={
            s: sum(_counts_toward(r, s, design) for r in below) for s in design.targets.signals
        },
        target=design.targets.positives,
        answered_present_done=all(r.complete for r in ours if r.stratum == "answered_present"),
        pilot_pages=design.pilot_pages,
        stale_guide=sum(
            r.complete and r.guide_version is not None and r.guide_version != guide
            for r in ours
            if guide is not None
        ),
    )


def at_cut(rows: Iterable[QueuePage]) -> list[QueuePage]:
    """The rows as the export writes them: a stratified page below its sample's cut with its π at
    the cut (π_entity × rate × cut), one above it left out. A page labelled above the cut that
    was not carried over is a gap left in the order, and refused: label the pages before it."""
    rows = list(rows)
    cuts = cut(rows)
    out: list[QueuePage] = []
    beyond: list[str] = []
    for row in rows:
        if row.draw_position is None or row.inclusion_probability is None:
            out.append(row)
            continue
        at = cuts[row.golden_sample_version]
        if row.draw_position >= at:
            if row.complete and not row.carried:
                beyond.append(row.page_id)
            continue  # not in the sample yet; a carried page is written from its own queue
        out.append(row.model_copy(update={"inclusion_probability": row.inclusion_probability * at}))
    if beyond:
        raise ValueError(
            f"{len(beyond)} page(s) are labelled after the first unlabelled one in draw order "
            f"(e.g. {beyond[:3]}): label the pages before them, so the labelled pages stay a draw"
        )
    return out


# --- rechecks (decision 22) ----------------------------------------------------------------------


def recheck_due(row: QueuePage, today: date, after_days: int) -> bool:
    """Whether a page drawn for a recheck is due: labelled at least `after_days` ago, not yet
    rechecked."""
    return (
        row.recheck
        and row.complete
        and not row.rechecks
        and row.labelled_on is not None
        and (today - row.labelled_on).days >= after_days
    )


def record_recheck(row: QueuePage, checks: dict[SignalType, Recheck], on: date) -> QueuePage:
    """The page with its second labelling, every signal it is asked; the first labels unchanged."""
    if set(checks) != set(row.required):
        raise ValueError(f"a recheck labels every signal of the page: {list(row.required)}")
    return QueuePage.model_validate(
        {
            **row.model_dump(),
            "rechecks": {s: checks[s].model_dump() for s in row.required},
            "rechecked_on": on,
        }
    )


def agrees(first: Label | Recheck, second: Label | Recheck) -> bool:
    """Two labellings of one signal agree on presence, and on the value when both give one."""
    return first.present == second.present and (
        not first.present
        or first.value is None
        or second.value is None
        or first.value == second.value
    )


def consistency(rows: Iterable[QueuePage]) -> dict[SignalType, tuple[int, int]]:
    """(rechecks agreeing with the first label, rechecks) by signal."""
    tally: dict[SignalType, list[int]] = {}
    for row in _once(r for r in rows if r.complete):
        for signal, check in row.rechecks.items():
            arm = tally.setdefault(signal, [0, 0])
            arm[0] += agrees(row.labels[signal], check)
            arm[1] += 1
    return {s: (a, t) for s, (a, t) in tally.items()}


# --- review (decisions 20, 21, 23) ---------------------------------------------------------------


@dataclass(frozen=True)
class Disagreement:
    """A label that differs from the model's answer: ids and labels only, never text."""

    position: int  # 1-based, in draw order
    page_id: str
    signal: SignalType
    model: str  # "present", "absent", a value, or "not asked"
    owner: str
    reason: str | None
    unsure: bool
    blind: bool


def _said(label: Label | None) -> str:
    if label is None:
        return "not asked"
    return (label.value or "present") if label.present else "absent"


def disagreements(rows: list[QueuePage]) -> list[Disagreement]:
    """Every labelled signal of a stratified queue that differs from the model's kept answer, in
    draw order: what the pilot's review reads before the guide is frozen (decision 20), and what
    the reasons sum over (decision 21). A page with no proposal for a signal counts as the model
    saying absent where the prefilter selected it, and not asked where it did not."""
    out: list[Disagreement] = []
    for n, row in enumerate(in_draw_order(rows), start=1):
        if not row.complete or row.stratum is None:
            continue
        for signal in row.required:
            mine = row.labels[signal]
            theirs = row.proposals.get(signal)
            if theirs is None and signal in row.candidates:
                theirs = Label(present=False)
            if theirs is None and not mine.present:
                continue
            if theirs is not None and agrees(mine, theirs):
                continue
            out.append(
                Disagreement(
                    n,
                    row.page_id,
                    signal,
                    _said(theirs),
                    _said(mine),
                    mine.reason,
                    mine.unsure,
                    row.blind,
                )
            )
    return out


def seconds_by_stratum(rows: Iterable[QueuePage]) -> dict[str, tuple[int, float]]:
    """(pages timed, median seconds on screen) by stratum (decision 23), to size the next sample."""
    times: dict[str, list[float]] = {}
    for row in rows:
        if row.complete and row.seconds is not None and row.stratum is not None:
            times.setdefault(row.stratum, []).append(row.seconds)
    return {s: (len(t), round(median(t), 1)) for s, t in sorted(times.items())}


def _agrees(label: Label, offered: Label) -> bool:
    return label.present == offered.present and (
        not label.present or offered.value is None or label.value == offered.value
    )


def proposal_agreement(rows: Iterable[QueuePage]) -> dict[str, tuple[int, int]]:
    """(signals where the owner's label agrees with the model's answer, signals with an answer),
    over the labelled pages that have answers: `shown` those labelled with them in view, `blind`
    those labelled without. The two rates are what decision 13 compares: a much higher `shown`
    rate means the proposals pull the labels toward the model."""
    tally = {"shown": [0, 0], "blind": [0, 0]}
    for row in rows:
        # within the answered-present stratum only: elsewhere every page is blind, and its base
        # rate is not the shown pages', so it would not measure the pull (decision 17)
        if not row.complete or not row.proposals or row.stratum not in (None, "answered_present"):
            continue
        arm = tally["blind" if row.blind else "shown"]
        for signal, offered in row.proposals.items():
            arm[0] += _agrees(row.labels[signal], offered)
            arm[1] += 1
    return {arm: (agreed, total) for arm, (agreed, total) in tally.items()}


def span_key(kind: str, text: str) -> str:
    """A flagged span as the masking check remembers it as reviewed: its kind and a hash of its
    text, never the text."""
    return f"{kind}:{hashlib.sha256(text.encode()).hexdigest()[:16]}"


def _flagged(texts: Iterable[str], nlp: Any) -> Iterator[str]:
    """The `span_key` of every span the masker would still replace in `texts`."""
    for text in texts:
        if text and _TOKEN.sub("", text).strip():
            for start, end, kind in masking.spans(text, nlp):
                yield span_key(kind, text[start:end])


def _page_texts(row: QueuePage) -> list[str]:
    return [row.text, *(lb.evidence or "" for lb in row.labels.values())]


def clear_reviewed(row: QueuePage, nlp: Any) -> QueuePage:
    """The page with whatever the masker still flags, in its text and its evidence, recorded as
    reviewed and not a personal datum. Called when the owner saves a label with the red spans on
    screen: a span they did not mask is one they judged a false positive. Spans that change the
    text later (new mask, new evidence) are new and block the export again."""
    keys = sorted({*row.cleared, *_flagged(_page_texts(row), nlp)})
    return QueuePage.model_validate({**row.model_dump(), "cleared": keys})


# --- files ---------------------------------------------------------------------------------------


def _line(obj: dict[str, Any]) -> str:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"


def dump_queue(rows: Iterable[QueuePage]) -> bytes:
    return "".join(_line(row.model_dump(mode="json")) for row in rows).encode("utf-8")


def load_queue(data: bytes) -> list[QueuePage]:
    return [QueuePage.model_validate_json(line) for line in data.splitlines() if line.strip()]


RECHECKS_FILE = "rechecks.jsonl"


def export(rows: Iterable[QueuePage], nlp: Any, guide: str | None = None) -> dict[str, bytes]:
    """`pages.jsonl` and one `<signal_type>.jsonl` per signal, from the labelled pages only, a
    stratified sample's at its cut (`at_cut`), and `rechecks.jsonl`. A page is in a signal's file
    only when it was labelled for it (decision 15). Raises when a page fails the masking check: it
    is fixed in the queue, never exported; and when a stratified page was labelled under a guide
    other than `guide`, the version in force: it is labelled again first (decision 20)."""
    done = _once(row for row in at_cut(rows) if row.complete)
    if not done:
        raise ValueError("no page is labelled yet: nothing to export")
    if guide is not None:
        stale = [r.page_id for r in done if r.guide_version not in (None, guide)]
        if stale:
            raise ValueError(
                f"{len(stale)} page(s) were labelled under another guide than {guide} "
                f"(e.g. {stale[:3]}): label them again before exporting"
            )
    failures = {
        row.page_id: findings
        for row in done
        if (findings := _unmasked(_page_texts(row), nlp, frozenset(row.cleared)))
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
    # A stratified sample's fields are written only on its pages: the seed's rows keep their bytes.
    drawn_keys = [
        "stratum",
        "inclusion_probability",
        "krs",
        "holdout",
        "flagged",
        "guide_version",
        "labelled_on",
    ]
    files = {
        PAGES_FILE: "".join(
            _line(
                {
                    k: v
                    for k, v in row.model_dump(mode="json").items()
                    if k in page_keys
                    or (k in drawn_keys and v is not None and row.stratum is not None)
                }
            )
            for row in done
        )
    }
    for signal in SIGNAL_TYPES:
        files[f"{signal}.jsonl"] = "".join(
            _line(
                {
                    "page_id": row.page_id,
                    "prefilter_selected": signal in row.candidates,
                    **row.labels[signal].written(),
                }
            )
            for row in done
            if signal in row.required
        )
    files[RECHECKS_FILE] = "".join(
        _line(
            {
                "page_id": row.page_id,
                "signal_type": signal,
                **check.model_dump(),
                "rechecked_on": str(row.rechecked_on),
            }
        )
        for row in done
        for signal, check in row.rechecks.items()
    )
    # Only the reviewed spans that are still flagged are written: the record never outlives them.
    cleared = {
        row.page_id: sorted(set(row.cleared) & set(_flagged(_page_texts(row), nlp))) for row in done
    }
    files[CLEARED_FILE] = _line({k: v for k, v in cleared.items() if v})
    return {name: text.encode("utf-8") for name, text in files.items()}


def _once(rows: Iterable[QueuePage]) -> list[QueuePage]:
    """One row per page, sorted by page id. A page labelled in two queues (a stratified sample
    drew a page already labelled) must carry the same labels in both, and is written as the drawn
    one, so it keeps its weight."""
    by_page: dict[str, QueuePage] = {}
    for row in rows:
        other = by_page.get(row.page_id)
        if other is None:
            by_page[row.page_id] = row
            continue
        if other.labels != row.labels:
            raise ValueError(f"{row.page_id} is labelled differently in two queues")
        if other.inclusion_probability is None:
            by_page[row.page_id] = row
    return [by_page[pid] for pid in sorted(by_page)]


def masker_recall(rows: Iterable[QueuePage]) -> tuple[int, int]:
    """(persons the masker replaced, persons the owner had to) over the labelled pages."""
    done = _once(row for row in rows if row.complete)
    return (
        sum(row.masked.get("person", 0) for row in done),
        sum(row.hand_masked.get("person", 0) for row in done),
    )


# --- the masking check ---------------------------------------------------------------------------


def _unmasked(
    texts: Iterable[str], nlp: Any, cleared: frozenset[str] = frozenset()
) -> dict[str, int]:
    """What the masker would still replace in `texts`, by kind, minus the spans in `cleared`."""
    found: Counter[str] = Counter()
    for text in texts:
        if text and _TOKEN.sub("", text).strip():
            found.update(
                kind
                for start, end, kind in masking.spans(text, nlp)
                if span_key(kind, text[start:end]) not in cleared
            )
    return dict(sorted(found.items()))


def load_cleared(directory: Path = GOLDEN_DIR) -> dict[str, frozenset[str]]:
    """The reviewed spans by page id, from the committed record beside the eval files."""
    path = directory / CLEARED_FILE
    if not path.exists():
        return {}
    raw = cast(dict[str, list[str]], json.loads(path.read_text(encoding="utf-8")))
    return {page: frozenset(keys) for page, keys in raw.items()}


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


def _page_id_of(doc: object) -> str:
    return str(cast(dict[str, object], doc).get("page_id")) if isinstance(doc, dict) else ""


def masking_findings(
    name: str, data: bytes, nlp: Any, cleared: dict[str, frozenset[str]] | None = None
) -> list[str]:
    """What the masker would still replace in an eval file's text fields, by line and kind; never
    the text. A `.jsonl` file is read line by line, anything else as one JSON document. Spans the
    owner reviewed and left (`masking_cleared.json`, by the line's `page_id`) are not findings."""
    cleared = load_cleared() if cleared is None else cleared
    docs = _eval_docs(name, data)
    if docs is None:
        return [UNREADABLE]
    return [
        f"line {n}: would mask {kinds}"
        for n, doc in enumerate(docs, start=1)
        if (kinds := _unmasked(_texts(doc), nlp, cleared.get(_page_id_of(doc), frozenset())))
    ]


UNREADABLE = "unreadable: not UTF-8 JSON, so its text cannot be checked"


def _eval_docs(name: str, data: bytes) -> list[object] | None:
    """An eval file's documents: a `.jsonl` file's lines, anything else one JSON document; None
    when it is not UTF-8 JSON."""
    try:
        text = data.decode("utf-8")
        return (
            [json.loads(line) for line in text.splitlines() if line.strip()]
            if name.endswith(".jsonl")
            else [json.loads(text)]
        )
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None


def hand_mask_findings(name: str, data: bytes) -> list[str]:
    """What `HAND_PATTERNS` finds in an eval file's text fields, by line and kind; never the
    text."""
    docs = _eval_docs(name, data)
    if docs is None:
        return [UNREADABLE]
    found: list[str] = []
    for n, doc in enumerate(docs, start=1):
        texts = [" ".join(t.split()) for t in _texts(doc)]
        kinds = sorted(k for k, p in HAND_PATTERNS.items() if any(p.search(t) for t in texts))
        if kinds:
            found.append(f"line {n}: holds {kinds}, masked only by hand")
    return found
