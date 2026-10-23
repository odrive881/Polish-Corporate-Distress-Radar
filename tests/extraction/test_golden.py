"""The golden set's queue, labels, export and masking check (plan 0013 step E). Every name and
page below is invented."""

# PyMuPDF's signatures reference types pyright cannot resolve.
# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false
# pyright: reportUnknownArgumentType=false

from __future__ import annotations

import base64
import json
from fractions import Fraction
from pathlib import Path
from typing import Any

import pymupdf
import pytest
from pydantic import ValidationError

from distress_radar.extraction import golden, label_queue, masking, preprocessing, rules
from distress_radar.extraction.golden import GoldenSample, Label, QueuePage, QueueStats, TextPage
from distress_radar.settings import Settings

REPO = Path(__file__).resolve().parents[2]
SAMPLE = GoldenSample(
    golden_sample_version="golden_sample_t",
    prefilter_version="prefilter_v1",
    rejected_sample_size=2,
)
GOING_CONCERN = "Istnieje istotna niepewność co do kontynuacji działalności Spółki."
LITIGATION = "Przeciwko Spółce toczy się postępowanie sądowe o zapłatę."
FIGURES = "Zapasy materiałów wyceniono w cenach nabycia. Należności wzrosły o 10%."


@pytest.fixture(scope="module")
def mask_nlp() -> Any:
    return masking.load_model()


@pytest.fixture(scope="module")
def lemma_nlp() -> Any:
    return preprocessing.load_model()


@pytest.fixture(scope="module")
def prefilter() -> preprocessing.Prefilter:
    return preprocessing.load_prefilter("prefilter_v1")


def _pages() -> list[TextPage]:
    texts = [GOING_CONCERN, LITIGATION, *(f"{FIGURES} Pozycja {n}." for n in range(6))]
    return [TextPage("d" * 64, "sf-1.xml", 1, n, text) for n, text in enumerate(texts, start=1)]


@pytest.fixture(scope="module")
def queue(prefilter: preprocessing.Prefilter, mask_nlp: Any, lemma_nlp: Any) -> list[QueuePage]:
    return golden.build_queue(_pages(), prefilter, SAMPLE, mask_nlp, lemma_nlp, QueueStats())


def _all_absent() -> dict[preprocessing.SignalType, Label]:
    return {s: Label(present=False) for s in preprocessing.SIGNAL_TYPES}


def _labelled(row: QueuePage, **present: str) -> QueuePage:
    labels = _all_absent()
    for signal, evidence in present.items():
        labels[signal] = Label(present=True, evidence=evidence)  # type: ignore[index]
    return golden.label(row, labels, "owner")


# --- the queue -----------------------------------------------------------------------------------


def test_the_queue_holds_every_selected_page_and_a_fixed_rejected_sample(
    prefilter: preprocessing.Prefilter, mask_nlp: Any, lemma_nlp: Any, queue: list[QueuePage]
) -> None:
    stats = QueueStats()
    again = golden.build_queue(_pages(), prefilter, SAMPLE, mask_nlp, lemma_nlp, stats)
    assert again == queue
    assert (stats.text_pages, stats.selected, stats.rejected_pool, stats.rejected_sampled) == (
        8,
        2,
        6,
        2,
    )
    assert [r.page_id for r in queue] == sorted(r.page_id for r in queue)
    selected = {r.page for r in queue if r.sample == "selected"}
    assert selected == {1, 2}
    by_page = {r.page: r for r in queue}
    assert set(by_page[1].candidates) == {"going_concern_uncertainty"}
    assert set(by_page[2].candidates) == {"litigation"}


def test_the_queue_is_masked(
    prefilter: preprocessing.Prefilter, mask_nlp: Any, lemma_nlp: Any
) -> None:
    page = TextPage(
        "e" * 64, "sf-1.xml", 1, 1, f"Prezes Zarządu Jan Kowalski oświadcza: {GOING_CONCERN}"
    )
    [row] = golden.build_queue([page], prefilter, SAMPLE, mask_nlp, lemma_nlp, QueueStats())
    assert "Kowalski" not in row.text and row.masked.get("person", 0) >= 1


def test_a_prefilter_other_than_the_sample_s_is_refused(
    mask_nlp: Any, lemma_nlp: Any, prefilter: preprocessing.Prefilter
) -> None:
    other = SAMPLE.model_copy(update={"prefilter_version": "prefilter_v9"})
    with pytest.raises(ValueError, match="prefilter_v9"):
        golden.build_queue([], prefilter, other, mask_nlp, lemma_nlp, QueueStats())


def test_text_pages_reads_the_embedded_pdfs() -> None:
    doc = pymupdf.open()
    doc.new_page().insert_textbox(pymupdf.Rect(50, 50, 550, 800), GOING_CONCERN * 5)
    doc.new_page()
    pdf = doc.tobytes()
    statement = (
        "<Sprawozdanie><Plik><Nazwa>plik-1.pdf</Nazwa><Zawartosc>"
        + base64.b64encode(pdf).decode()
        + "</Zawartosc></Plik></Sprawozdanie>"
    ).encode()
    stats = QueueStats()
    [page] = golden.text_pages("f" * 64, "sf-1.xml", statement, stats)
    assert (page.attachment, page.page) == (1, 1) and "kontynuacji" in page.text
    assert not stats.attachment_errors


def test_a_rebuild_keeps_labels_and_never_drops_a_labelled_page(queue: list[QueuePage]) -> None:
    labelled = _labelled(queue[0])
    merged = golden.merge([labelled, *queue[1:]], queue)
    assert merged[0] == labelled and merged[1:] == queue[1:]
    with pytest.raises(ValueError, match="not in the rebuilt queue"):
        golden.merge([labelled], queue[1:])


def test_the_queue_file_round_trips(queue: list[QueuePage]) -> None:
    data = golden.dump_queue([_labelled(queue[0]), *queue[1:]])
    assert golden.dump_queue(golden.load_queue(data)) == data


# --- labelling -----------------------------------------------------------------------------------


def _row(queue: list[QueuePage], page: int) -> QueuePage:
    return next(r for r in queue if r.page == page)


def test_a_label_needs_every_signal_and_evidence_on_the_page(queue: list[QueuePage]) -> None:
    row = _row(queue, 1)
    with pytest.raises(ValueError, match="missing"):
        golden.label(row, {"litigation": Label(present=False)}, "owner")
    with pytest.raises(ValidationError, match="not a span of the page"):
        _labelled(row, going_concern_uncertainty="niepewność co do przyszłości")
    done = _labelled(row, going_concern_uncertainty="istotna niepewność co do kontynuacji")
    assert done.complete and done.labels["going_concern_uncertainty"].present


@pytest.mark.parametrize(
    ("kwargs", "match"),
    [
        ({"present": True}, "needs its evidence"),
        ({"present": False, "evidence": "x"}, "no value and no evidence"),
    ],
)
def test_a_label_is_consistent(kwargs: dict[str, Any], match: str) -> None:
    with pytest.raises(ValidationError, match=match):
        Label(**kwargs)


def test_only_a_valued_signal_takes_a_value_and_from_its_list(queue: list[QueuePage]) -> None:
    row = _row(queue, 1)
    labels = _all_absent()
    labels["opinion_type"] = Label(present=True, value="clean", evidence="Spółki")
    with pytest.raises(ValidationError, match="opinion_type needs one of"):
        golden.label(row, labels, "owner")
    labels["opinion_type"] = Label(present=True, value="qualified", evidence="Spółki")
    labels["litigation"] = Label(present=True, value="qualified", evidence="Spółki")
    with pytest.raises(ValidationError, match="litigation: value 'qualified' is not one of"):
        golden.label(row, labels, "owner")
    labels["litigation"] = Label(present=False)
    labels["post_balance_sheet_event"] = Label(present=True, value="qualified", evidence="Spółki")
    with pytest.raises(ValidationError, match="is not one of"):
        golden.label(row, labels, "owner")
    # Decision 9: the kind of event; a label made before it, with no kind, still loads.
    for kind in ("adverse", None):
        labels["post_balance_sheet_event"] = Label(present=True, value=kind, evidence="Spółki")
        golden.label(row, labels, "owner")


def test_hand_masking_keeps_the_prefilter_highlights_on_their_sentences(
    queue: list[QueuePage],
) -> None:
    row = QueuePage.model_validate(
        {
            **_row(queue, 2).model_dump(),
            "text": f"Jan Nowak podpisał. {LITIGATION} Jan Nowak.",
            "candidates": {"litigation": [(20, 20 + len(LITIGATION))]},
        }
    )
    masked = golden.hand_mask(row, "Jan Nowak")
    [(start, end)] = masked.candidates["litigation"]
    assert masked.text[start:end] == LITIGATION


def test_hand_masking_replaces_counts_and_follows_the_evidence(queue: list[QueuePage]) -> None:
    row = _labelled(_row(queue, 2), litigation="toczy się postępowanie sądowe")
    masked = golden.hand_mask(row, "sądowe")
    assert "sądowe" not in masked.text and masked.hand_masked == {"person": 1}
    assert masked.labels["litigation"].evidence == "toczy się postępowanie [osoba]"
    for bad in ("osoba", "[osoba]", "x", "Nieobecny Tekst"):
        with pytest.raises(ValueError):
            golden.hand_mask(row, bad)


def test_hand_masking_has_kinds_the_masker_does_not_find(queue: list[QueuePage]) -> None:
    """A register number, a sole practitioner's address, a file name (2026-10-15): masked by hand
    with their own tokens, counted by kind, and never mistaken for the masker's."""
    row = QueuePage.model_validate(
        {**_row(queue, 2).model_dump(), "text": "Biegły rewident nr 12345, ul. Polna 1. plik.xml"}
    )
    for target, kind in (
        ("12345", "register_number"),
        ("ul. Polna 1", "address"),
        ("plik.xml", "file_name"),
    ):
        row = golden.hand_mask(row, target, kind)
    assert row.text == "Biegły rewident nr [nr], [adres]. [plik]"
    assert row.hand_masked == {"address": 1, "file_name": 1, "register_number": 1}
    assert set(masking.TOKENS) < set(golden.HAND_TOKENS)  # the masker's own kinds are unchanged
    with pytest.raises(ValueError, match="kind must be one of"):
        golden.hand_mask(row, "Biegły", "nickname")


def test_no_committed_eval_file_holds_a_register_number_a_bank_account_or_a_file_name() -> None:
    """The hand-masking check over every committed eval file (2026-10-15; the pre-commit scan's
    since 2026-10-23)."""
    for path in sorted(golden.GOLDEN_DIR.rglob("*.json*")):
        assert golden.hand_mask_findings(path.name, path.read_bytes()) == [], path


@pytest.mark.parametrize(
    ("text", "kind"),
    [
        ("Biegły rewident wpisany pod numerem 12345.", "register_number"),
        ("[osoba] (nr 9876), kluczowy biegły rewident", "register_number"),
        ("firma audytorska wpisana na listę\npod nr\n 3546", "register_number"),
        ("rachunek 12 3456 7890 1234 5678 9012 3456", "bank_account"),
        ("Załącznik: SF_2023.xml", "file_name"),
    ],
)
def test_the_hand_masking_check_finds_each_kind_and_never_prints_the_text(
    text: str, kind: str
) -> None:
    data = json.dumps({"page_id": "p", "text": text}).encode()
    [finding] = golden.hand_mask_findings("pages.jsonl", data)
    assert finding == f"line 1: holds {[kind]}, masked only by hand"


def test_the_hand_masking_check_passes_tokens_krs_numbers_resolutions_and_non_text_fields() -> None:
    lines = [
        {"page_id": "p1", "text": "Biegły rewident nr [nr], [adres]. [plik]"},
        {"page_id": "p2", "text": "Spółka wpisana do rejestru pod numerem 0000123456."},
        {"page_id": "p3", "text": "uchwała Krajowej Rady Biegłych Rewidentów nr 3430/52a/2019"},
        {"page_id": "p4", "document_hash": "a.xml", "text": "brak"},
    ]
    data = "\n".join(json.dumps(line) for line in lines).encode()
    assert golden.hand_mask_findings("pages.jsonl", data) == []
    assert golden.hand_mask_findings("x.json", b"\xff") == [golden.UNREADABLE]


# --- export and the masking check -----------------------------------------------------------------


def test_export_writes_labelled_pages_once_and_a_file_per_signal(
    queue: list[QueuePage], mask_nlp: Any
) -> None:
    rows = [
        _labelled(_row(queue, 1), going_concern_uncertainty="istotna niepewność"),
        *(r for r in queue if r.page != 1),
    ]
    files = golden.export(rows, mask_nlp)
    assert set(files) == {
        "pages.jsonl",
        golden.CLEARED_FILE,
        *(f"{s}.jsonl" for s in preprocessing.SIGNAL_TYPES),
    }
    assert json.loads(files[golden.CLEARED_FILE]) == {}
    [page] = [json.loads(line) for line in files["pages.jsonl"].splitlines()]
    assert page["text"] == GOING_CONCERN and page["labelled_by"] == "owner"
    [gc] = [json.loads(line) for line in files["going_concern_uncertainty.jsonl"].splitlines()]
    assert gc == {
        "page_id": page["page_id"],
        "prefilter_selected": True,
        "present": True,
        "value": None,
        "evidence": "istotna niepewność",
    }
    assert golden.export(rows, mask_nlp) == files


def test_export_refuses_when_nothing_is_labelled(queue: list[QueuePage], mask_nlp: Any) -> None:
    with pytest.raises(ValueError, match="nothing to export"):
        golden.export(queue, mask_nlp)


def test_export_refuses_a_page_the_masker_would_still_change(
    queue: list[QueuePage], mask_nlp: Any
) -> None:
    leaked = QueuePage.model_validate(
        {**_row(queue, 2).model_dump(), "text": f"{LITIGATION} Pozew podpisał Jan Kowalski."}
    )
    with pytest.raises(ValueError, match="fail the masking check") as exc:
        golden.export([_labelled(leaked)], mask_nlp)
    assert "Kowalski" not in str(exc.value)


def test_a_reviewed_false_positive_no_longer_blocks_the_export(
    queue: list[QueuePage], mask_nlp: Any
) -> None:
    leaked = QueuePage.model_validate(
        {**_row(queue, 2).model_dump(), "text": f"{LITIGATION} Pozew podpisał Jan Kowalski."}
    )
    reviewed = golden.clear_reviewed(_labelled(leaked), mask_nlp)
    assert reviewed.cleared and "Kowalski" not in "".join(reviewed.cleared)
    files = golden.export([reviewed], mask_nlp)
    cleared = json.loads(files[golden.CLEARED_FILE])
    assert cleared == {reviewed.page_id: list(reviewed.cleared)}
    # the committed files pass the downstream check only with the record beside them
    pages = files["pages.jsonl"]
    assert golden.masking_findings("pages.jsonl", pages, mask_nlp, {})
    loaded = {k: frozenset(v) for k, v in cleared.items()}
    assert golden.masking_findings("pages.jsonl", pages, mask_nlp, loaded) == []


def test_a_reviewed_page_blocks_again_on_anything_new(
    queue: list[QueuePage], mask_nlp: Any
) -> None:
    leaked = QueuePage.model_validate(
        {**_row(queue, 2).model_dump(), "text": f"{LITIGATION} Pozew podpisał Jan Kowalski."}
    )
    reviewed = golden.clear_reviewed(_labelled(leaked), mask_nlp)
    other = QueuePage.model_validate(
        {**reviewed.model_dump(), "text": f"{reviewed.text} Dodał też Anna Nowak."}
    )
    with pytest.raises(ValueError, match="fail the masking check"):
        golden.export([other], mask_nlp)


def test_the_masking_check_reads_text_fields_and_quotes_nothing(mask_nlp: Any) -> None:
    rows = [
        {"page_id": "12345678901abcde", "text": "Należności wzrosły.", "evidence": None},
        {"page_id": "x", "text": "Umowę podpisał Jan Kowalski, PESEL 90010112345."},
    ]
    data = "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows).encode()
    [finding] = golden.masking_findings("evals/text_signals/litigation.jsonl", data, mask_nlp)
    assert finding.startswith("line 2:") and "pesel" in finding and "Kowalski" not in finding
    assert golden.masking_findings("a.json", b"{", mask_nlp) == [
        "unreadable: not UTF-8 JSON, so its text cannot be checked"
    ]


def test_every_committed_eval_file_passes_the_masking_check(mask_nlp: Any) -> None:
    files = sorted(p for p in (REPO / "evals").rglob("*") if p.suffix in {".json", ".jsonl"})
    findings = {
        str(p.relative_to(REPO)): f
        for p in files
        if (f := golden.masking_findings(p.name, p.read_bytes(), mask_nlp))
    }
    assert not findings


def test_the_golden_sample_version_must_be_the_file_name(tmp_path: Path) -> None:
    assert golden.load_golden_sample("golden_sample_v1").prefilter_version == "prefilter_v1"
    (tmp_path / "extraction").mkdir()
    source = REPO / "config" / "extraction" / "golden_sample_v1.yaml"
    (tmp_path / "extraction" / "golden_sample_v2.yaml").write_bytes(source.read_bytes())
    with pytest.raises(ValueError, match="must match the file name"):
        golden.load_golden_sample("golden_sample_v2", tmp_path)


# --- golden sample v2: whole auditor reports ------------------------------------------------------

REPORT_SAMPLE = GoldenSample(
    golden_sample_version="golden_sample_r",
    prefilter_version="prefilter_v2",
    document_kind="auditor_report",
    report_sample=golden.ReportSample(rules_version="rules_v2", random_reports=1),
)
OPINION = "Opinia\nNaszym zdaniem sprawozdanie przedstawia rzetelny obraz."
QUALIFIED = "Opinia z zastrzeżeniem\nNaszym zdaniem, z wyjątkiem kwestii opisanej niżej."


def _report(n: int, first: str) -> list[TextPage]:
    texts = [first, f"{FIGURES} Pozycja {n}.", "Podstawa opinii\nBadanie przeprowadziliśmy."]
    return [TextPage(f"{n:x}" * 64, f"zip:{n}.pdf", 1, p, t) for p, t in enumerate(texts, 1)]


def _reports() -> list[list[TextPage]]:
    return [_report(1, OPINION), _report(2, QUALIFIED), _report(3, OPINION), _report(4, OPINION)]


def _report_queue(mask_nlp: Any, lemma_nlp: Any, stats: QueueStats) -> list[QueuePage]:
    return golden.build_report_queue(
        _reports(),
        preprocessing.load_prefilter("prefilter_v2"),
        rules.load_rules("rules_v2"),
        REPORT_SAMPLE,
        mask_nlp,
        lemma_nlp,
        stats,
    )


def test_the_report_sample_takes_every_modified_opinion_and_a_fixed_draw_of_whole_reports(
    mask_nlp: Any, lemma_nlp: Any
) -> None:
    stats = QueueStats()
    queue = _report_queue(mask_nlp, lemma_nlp, stats)
    assert queue == _report_queue(mask_nlp, lemma_nlp, QueueStats())
    assert (stats.reports, stats.reports_modified, stats.reports_sampled) == (4, 1, 2)
    by_report: dict[str, list[int]] = {}
    for row in queue:
        by_report.setdefault(row.document_hash, []).append(row.page)
    # the qualified report, and one other, each with every page, rejected pages included
    assert "2" * 64 in by_report and len(by_report) == 2
    assert all(sorted(pages) == [1, 2, 3] for pages in by_report.values())
    assert {row.sample for row in queue} == {"selected", "rejected"}
    assert {(row.document_kind, row.golden_sample_version) for row in queue} == {
        ("auditor_report", "golden_sample_r")
    }


def test_each_design_takes_only_its_own_kind_of_document(
    mask_nlp: Any, lemma_nlp: Any, prefilter: preprocessing.Prefilter
) -> None:
    with pytest.raises(ValidationError, match="each by one only"):
        GoldenSample(
            golden_sample_version="x",
            prefilter_version="prefilter_v2",
            document_kind="auditor_report",
            rejected_sample_size=5,
        )
    with pytest.raises(ValidationError, match="each by one only"):
        GoldenSample(golden_sample_version="x", prefilter_version="prefilter_v1")
    with pytest.raises(ValidationError, match="each by one only"):
        GoldenSample(
            golden_sample_version="x",
            prefilter_version="prefilter_v1",
            rejected_sample_size=5,
            targeted=golden.TargetedSample(signals=("litigation",), rejected_rate=0.5),
        )
    with pytest.raises(ValueError, match="not a sample of the notes"):
        golden.build_queue(
            [],
            preprocessing.load_prefilter("prefilter_v2"),
            REPORT_SAMPLE,
            mask_nlp,
            lemma_nlp,
            QueueStats(),
        )
    with pytest.raises(ValueError, match="draws by rules_v2, not rules_v1"):
        golden.build_report_queue(
            _reports(),
            preprocessing.load_prefilter("prefilter_v2"),
            rules.load_rules("rules_v1"),
            REPORT_SAMPLE,
            mask_nlp,
            lemma_nlp,
            QueueStats(),
        )


def test_a_report_page_is_exported_with_its_kind(mask_nlp: Any, lemma_nlp: Any) -> None:
    queue = _report_queue(mask_nlp, lemma_nlp, QueueStats())
    row = next(r for r in queue if r.text == QUALIFIED)
    labels = _all_absent()
    labels["opinion_type"] = Label(
        present=True, value="qualified", evidence="Opinia z zastrzeżeniem"
    )
    files = golden.export([golden.label(row, labels, "owner")], mask_nlp)
    [page] = [json.loads(line) for line in files["pages.jsonl"].splitlines()]
    assert (page["document_kind"], page["golden_sample_version"]) == (
        "auditor_report",
        "golden_sample_r",
    )


def test_the_committed_samples_are_the_notes_the_reports_and_the_targeted_notes() -> None:
    assert label_queue.versions() == ["golden_sample_v1", "golden_sample_v2", "golden_sample_v3"]
    v3 = golden.load_golden_sample("golden_sample_v3")
    assert (v3.document_kind, v3.prefilter_version, v3.targeted) == (
        "statement_notes",
        "prefilter_v3",
        golden.TargetedSample(
            signals=("covenant_breach", "key_customer_loss"), rejected_rate=0.002
        ),
    )
    v2 = golden.load_golden_sample("golden_sample_v2")
    assert (v2.document_kind, v2.prefilter_version, v2.report_sample) == (
        "auditor_report",
        "prefilter_v2",
        golden.ReportSample(rules_version="rules_v2", random_reports=16),
    )


def test_an_export_never_drops_a_page_already_exported(
    tmp_path: Path, queue: list[QueuePage], mask_nlp: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    labelling, out = tmp_path / "queues", tmp_path / "evals"
    labelling.mkdir()
    out.mkdir()
    settings = Settings(labelling_dir=labelling)
    monkeypatch.setattr(label_queue, "versions", lambda: ["golden_sample_t"])
    (labelling / "golden_sample_t.jsonl").write_bytes(
        golden.dump_queue([_labelled(_row(queue, 1)), *(r for r in queue if r.page != 1)])
    )
    counts = label_queue.export(settings, out)
    assert counts["pages.jsonl"] == 1
    with (out / "pages.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps({"page_id": "labelled-elsewhere"}) + "\n")
    with pytest.raises(ValueError, match="in no labelled local queue"):
        label_queue.export(settings, out)


# --- the targeted notes sample (golden_sample_v3's design) ----------------------------------------

COVENANT = "Bank wypowiedział umowę kredytową."
CUSTOMER = "Spółka utraciła głównego odbiorcę."
TARGETED = GoldenSample(
    golden_sample_version="golden_sample_g",
    prefilter_version="prefilter_v3",
    targeted=golden.TargetedSample(
        signals=("covenant_breach", "key_customer_loss"), rejected_rate=0.5
    ),
)


def _targeted_pages(figures: int) -> list[TextPage]:
    texts = [COVENANT, CUSTOMER, LITIGATION, *(f"{FIGURES} Pozycja {n}." for n in range(figures))]
    return [TextPage("f" * 64, "sf-1.xml", 1, n, text) for n, text in enumerate(texts, start=1)]


def _targeted(
    pages: list[TextPage], mask_nlp: Any, lemma_nlp: Any, stats: QueueStats, **kwargs: Any
) -> list[QueuePage]:
    return golden.build_targeted_queue(
        pages,
        preprocessing.load_prefilter("prefilter_v3"),
        TARGETED,
        mask_nlp,
        lemma_nlp,
        stats,
        **kwargs,
    )


def test_the_targeted_sample_takes_the_target_pages_and_a_share_of_the_rejected(
    mask_nlp: Any, lemma_nlp: Any
) -> None:
    stats = QueueStats()
    queue = _targeted(_targeted_pages(12), mask_nlp, lemma_nlp, stats)
    assert queue == _targeted(_targeted_pages(12), mask_nlp, lemma_nlp, QueueStats())
    by_page = {row.page: row for row in queue}
    # both target pages; litigation's page is selected for another signal only, so not drawn
    assert {1, 2} <= set(by_page) and 3 not in by_page
    assert by_page[1].sample == by_page[2].sample == "selected"
    assert "covenant_breach" in by_page[1].candidates
    assert "key_customer_loss" in by_page[2].candidates
    drawn = [row for row in queue if row.sample == "rejected"]
    assert (stats.selected, stats.rejected_pool, stats.rejected_sampled) == (2, 12, len(drawn))
    assert 0 < len(drawn) < 12
    assert [r.page_id for r in queue] == sorted(r.page_id for r in queue)


def test_a_later_delivery_adds_to_the_targeted_draw_without_moving_it(
    mask_nlp: Any, lemma_nlp: Any
) -> None:
    before = _targeted(_targeted_pages(12), mask_nlp, lemma_nlp, QueueStats())
    after = _targeted(_targeted_pages(30), mask_nlp, lemma_nlp, QueueStats())
    assert {r.page_id for r in before} <= {r.page_id for r in after}


def test_the_targeted_sample_leaves_out_pages_queued_or_labelled_elsewhere(
    mask_nlp: Any, lemma_nlp: Any
) -> None:
    full = _targeted(_targeted_pages(12), mask_nlp, lemma_nlp, QueueStats())
    covenant = next(r for r in full if r.page == 1)
    stats = QueueStats()
    queue = _targeted(
        _targeted_pages(12), mask_nlp, lemma_nlp, stats, exclude=frozenset({covenant.page_id})
    )
    assert covenant.page_id not in {r.page_id for r in queue}
    assert (stats.excluded, len(queue)) == (1, len(full) - 1)


def test_a_targeted_queue_needs_a_targeted_sample(mask_nlp: Any, lemma_nlp: Any) -> None:
    with pytest.raises(ValueError, match="not a targeted sample of the notes"):
        golden.build_targeted_queue(
            [],
            preprocessing.load_prefilter("prefilter_v1"),
            SAMPLE,
            mask_nlp,
            lemma_nlp,
            QueueStats(),
        )


def test_elsewhere_reads_the_other_queues_and_the_committed_pages(
    tmp_path: Path, queue: list[QueuePage]
) -> None:
    settings = Settings(labelling_dir=tmp_path / "queues")
    (tmp_path / "queues").mkdir()
    label_queue.queue_path(settings, "golden_sample_v1").write_bytes(golden.dump_queue(queue[:1]))
    label_queue.queue_path(settings, "golden_sample_v3").write_bytes(golden.dump_queue(queue[1:2]))
    golden_dir = tmp_path / "golden"
    golden_dir.mkdir()
    (golden_dir / golden.PAGES_FILE).write_text(
        json.dumps({"page_id": "committed"}) + "\n", encoding="utf-8"
    )
    ids = label_queue.elsewhere(settings, "golden_sample_v3", golden_dir)
    assert ids == {queue[0].page_id, "committed"}


# --- what a sample is drawn from -------------------------------------------------------------------


def test_v1_and_v2_are_pinned_to_the_seed_and_v3_reads_the_whole_store() -> None:
    """v1 and v2 were drawn when the store held the seed alone: a rebuild over a larger store must
    not add every later list's pages to them (2026-10-21)."""
    for version in ("golden_sample_v1", "golden_sample_v2"):
        assert golden.load_golden_sample(version).discovery_sources == ("manual_seed",)
    assert golden.load_golden_sample("golden_sample_v3").discovery_sources is None


class _Rows:
    def __init__(self, rows: list[tuple[object, ...]]) -> None:
        self._rows = rows

    def fetchall(self) -> list[tuple[object, ...]]:
        return self._rows


class _Conn:
    def __init__(self, rows: list[tuple[object, ...]]) -> None:
        self.rows = rows

    def execute(self, _sql: str, _params: object = None) -> _Rows:
        return _Rows(self.rows)


class _Store:
    def __init__(self) -> None:
        self.fetched: list[str] = []

    def get(self, key: str) -> bytes:
        self.fetched.append(key)
        return b"neither a ZIP, an XML nor a PDF"


def test_the_notes_reader_skips_other_lists_and_counts_an_unreadable_download() -> None:
    conn = _Conn(
        [("a" * 64, "sf.xml", "key-seed", "0000000001"), ("b" * 64, "sf.xml", "key-v1", "2")]
    )
    store = _Store()
    stats = QueueStats()
    pages = list(
        label_queue._notes_pages(conn, store, stats, frozenset({"0000000001"}))  # pyright: ignore[reportPrivateUsage, reportArgumentType]
    )
    assert pages == [] and store.fetched == ["key-seed"]
    assert stats.attachment_errors == {"unknown_container": 1}


# --- the stratified sample (plan 0013 decision 12) ------------------------------------------------

DRAW = golden.DrawPin(
    draw_spec_sha256="a" * 64, random="rejestr_io_v3_random", distress="rejestr_io_v3_distress"
)


def _stratified(**rates: float) -> GoldenSample:
    return GoldenSample(
        golden_sample_version="golden_sample_s",
        prefilter_version="prefilter_v1",
        stratified=golden.StratifiedSample(
            draw=DRAW,
            answers="extractor_v1",
            signals=("going_concern_uncertainty",),
            rates={stratum: rates.get(stratum, 1.0) for stratum in golden.STRATA},
        ),
    )


def _drawn_pages(figures: int) -> list[tuple[str, TextPage]]:
    """Entity 1's two going-concern pages, entity 2's figures, and entity 3's, not in the draw."""
    doc1, doc2, doc3 = "1" * 64, "2" * 64, "3" * 64
    return [
        ("0000000001", TextPage(doc1, "sf-1.xml", 1, 1, GOING_CONCERN)),
        ("0000000001", TextPage(doc1, "sf-1.xml", 1, 2, f"{FIGURES} {GOING_CONCERN}")),
        *(
            ("0000000002", TextPage(doc2, "sf-2.xml", 1, n, f"{FIGURES} Pozycja {n}."))
            for n in range(1, figures + 1)
        ),
        ("0000000003", TextPage(doc3, "sf-3.xml", 1, 1, GOING_CONCERN)),
    ]


def _frame(**kwargs: Any) -> golden.StratifiedFrame:
    return golden.StratifiedFrame(
        entity_pi={"0000000001": Fraction(1, 2), "0000000002": Fraction(1, 3)},
        answered_present=kwargs.pop(
            "answered_present", frozenset({golden.page_id("1" * 64, "sf-1.xml", 1, 1)})
        ),
        read=frozenset({("1" * 64, "sf-1.xml"), ("2" * 64, "sf-2.xml")}),
        **kwargs,
    )


def _build(
    sample: GoldenSample,
    pages: list[tuple[str, TextPage]],
    frame: golden.StratifiedFrame,
    nlps: tuple[Any, Any],
    stats: QueueStats | None = None,
) -> list[QueuePage]:
    return golden.build_stratified_queue(
        pages,
        preprocessing.load_prefilter("prefilter_v1"),
        sample,
        frame,
        nlps[0],
        nlps[1],
        stats or QueueStats(),
    )


def test_a_stratified_sample_puts_each_drawn_entity_s_page_in_one_stratum(
    mask_nlp: Any, lemma_nlp: Any
) -> None:
    stats = QueueStats()
    queue = _build(_stratified(), _drawn_pages(3), _frame(), (mask_nlp, lemma_nlp), stats)
    by_page = {(row.document_hash[0], row.page): row for row in queue}
    assert set(by_page) == {("1", 1), ("1", 2), ("2", 1), ("2", 2), ("2", 3)}  # not entity 3's
    assert by_page[("1", 1)].stratum == "answered_present"
    assert by_page[("1", 2)].stratum == "answered_absent"  # selected, no answer kept present
    assert {by_page[("2", n)].stratum for n in (1, 2, 3)} == {"not_selected"}
    # π_page = π_entity × rate, exactly
    assert by_page[("1", 1)].inclusion_probability == 0.5
    assert {row.krs for row in queue} == {"0000000001", "0000000002"}
    assert by_page[("2", 1)].inclusion_probability == float(Fraction(1, 3))
    assert stats.strata == {"answered_present": 1, "answered_absent": 1, "not_selected": 3}
    assert stats.drawn == stats.strata


def test_a_stratum_s_rate_draws_a_fixed_share_that_a_later_delivery_only_adds_to(
    mask_nlp: Any, lemma_nlp: Any
) -> None:
    sample = _stratified(not_selected=0.5)
    before = _build(sample, _drawn_pages(12), _frame(), (mask_nlp, lemma_nlp))
    assert before == _build(sample, _drawn_pages(12), _frame(), (mask_nlp, lemma_nlp))
    after = _build(sample, _drawn_pages(40), _frame(), (mask_nlp, lemma_nlp))
    assert {r.page_id for r in before} <= {r.page_id for r in after}
    drawn = [r for r in after if r.stratum == "not_selected"]
    assert 0 < len(drawn) < 40
    assert {r.inclusion_probability for r in drawn} == {float(Fraction(1, 3) * Fraction(1, 2))}


def test_a_stratified_sample_refuses_a_document_the_text_job_has_not_read(
    mask_nlp: Any, lemma_nlp: Any
) -> None:
    frame = golden.StratifiedFrame(
        entity_pi={"0000000001": Fraction(1, 2), "0000000002": Fraction(1, 3)},
        answered_present=frozenset(),
        read=frozenset({("1" * 64, "sf-1.xml")}),
    )
    with pytest.raises(ValueError, match="1 document"):
        _build(_stratified(), _drawn_pages(3), frame, (mask_nlp, lemma_nlp))


def test_an_answer_the_prefilter_would_not_have_asked_for_is_refused(
    mask_nlp: Any, lemma_nlp: Any
) -> None:
    figures = golden.page_id("2" * 64, "sf-2.xml", 1, 1)
    with pytest.raises(ValueError, match="answers are not the prefilter's"):
        _build(
            _stratified(),
            _drawn_pages(3),
            _frame(answered_present=frozenset({figures})),
            (mask_nlp, lemma_nlp),
        )


def test_a_drawn_page_labelled_elsewhere_carries_its_labels_over_and_is_exported_once(
    mask_nlp: Any, lemma_nlp: Any
) -> None:
    plain = _build(_stratified(), _drawn_pages(3), _frame(), (mask_nlp, lemma_nlp))
    first = next(r for r in plain if r.stratum == "answered_present")
    elsewhere = _labelled(
        first.model_copy(
            update={
                "golden_sample_version": "golden_sample_v1",
                "stratum": None,
                "inclusion_probability": None,
                "krs": None,
            }
        ),
        going_concern_uncertainty=GOING_CONCERN,
    )
    stats = QueueStats()
    queue = _build(
        _stratified(),
        _drawn_pages(3),
        _frame(labelled_elsewhere={elsewhere.page_id: elsewhere}),
        (mask_nlp, lemma_nlp),
        stats,
    )
    carried = next(r for r in queue if r.page_id == first.page_id)
    assert stats.carried == 1 and carried.complete
    assert (carried.golden_sample_version, carried.inclusion_probability) == (
        "golden_sample_s",
        0.5,
    )
    files = golden.export([elsewhere, carried], mask_nlp)
    pages = [json.loads(line) for line in files[golden.PAGES_FILE].decode().splitlines()]
    assert len(pages) == 1
    assert (pages[0]["stratum"], pages[0]["inclusion_probability"], pages[0]["krs"]) == (
        "answered_present",
        0.5,
        "0000000001",
    )
    relabelled = _labelled(elsewhere)  # every signal absent: a different label
    with pytest.raises(ValueError, match="labelled differently"):
        golden.export([relabelled, carried], mask_nlp)


def test_a_page_outside_a_stratified_sample_is_exported_without_its_fields(
    queue: list[QueuePage], mask_nlp: Any
) -> None:
    files = golden.export([_labelled(queue[0])], mask_nlp)
    page = json.loads(files[golden.PAGES_FILE].decode().splitlines()[0])
    assert not {"stratum", "inclusion_probability", "krs"} & set(page)


def test_a_stratified_sample_needs_a_rate_per_stratum_and_no_list() -> None:
    with pytest.raises(ValidationError, match="rates"):
        golden.StratifiedSample(
            draw=DRAW,
            answers="extractor_v1",
            signals=("going_concern_uncertainty",),
            rates={"answered_present": 1.0},  # type: ignore[dict-item]
        )
    with pytest.raises(ValidationError, match="drawn from its draw"):
        GoldenSample.model_validate(
            {**_stratified().model_dump(), "discovery_sources": ["rejestr_io_v3_random"]}
        )
    with pytest.raises(ValidationError, match="each by one only"):
        GoldenSample.model_validate({**_stratified().model_dump(), "rejected_sample_size": 5})
    with pytest.raises(ValidationError, match="inclusion probability"):
        QueuePage.model_validate({**_stratified_row().model_dump(), "inclusion_probability": None})


def _stratified_row() -> QueuePage:
    return QueuePage(
        page_id="x" * 16,
        document_hash="1" * 64,
        source_member="sf-1.xml",
        attachment=1,
        page=1,
        masking_version="1",
        prefilter_version="prefilter_v1",
        golden_sample_version="golden_sample_s",
        sample="rejected",
        candidates={},
        text=FIGURES,
        masked={},
        stratum="not_selected",
        inclusion_probability=0.25,
        krs="0000000001",
    )


# --- proposals and parked signals (plan 0013 decision 13) -----------------------------------------


def test_a_kept_answer_becomes_a_proposal() -> None:
    assert golden.proposal("litigation", "absent", None) == Label(present=False)
    assert golden.proposal("litigation", "present", LITIGATION) == Label(
        present=True, evidence=LITIGATION
    )
    assert golden.proposal("opinion_type", "qualified", "opinia z zastrzeżeniem") == Label(
        present=True, value="qualified", evidence="opinia z zastrzeżeniem"
    )
    assert golden.proposal("litigation", "present", None) is None  # no evidence, no label


def _proposed_frame(blind_rate: float) -> tuple[GoldenSample, golden.StratifiedFrame]:
    sample = _stratified()
    assert sample.stratified is not None
    sample = sample.model_copy(
        update={"stratified": sample.stratified.model_copy(update={"blind_rate": blind_rate})}
    )
    present = golden.page_id("1" * 64, "sf-1.xml", 1, 1)
    proposals = {
        present: {
            "going_concern_uncertainty": Label(present=True, evidence=GOING_CONCERN),
            "litigation": Label(present=True, evidence="not on this page"),
        },
        **{
            golden.page_id("2" * 64, "sf-2.xml", 1, n): {
                "going_concern_uncertainty": Label(present=False)
            }
            for n in range(1, 41)
        },
    }
    return sample, _frame(proposals=proposals)


def test_a_stratified_queue_offers_the_answers_on_the_page_and_blinds_a_fixed_share(
    mask_nlp: Any, lemma_nlp: Any
) -> None:
    sample, frame = _proposed_frame(0.5)
    stats = QueueStats()
    queue = _build(sample, _drawn_pages(40), frame, (mask_nlp, lemma_nlp), stats)
    first = next(r for r in queue if r.stratum == "answered_present")
    # the litigation answer's evidence is not on the masked page: not offered, and counted
    assert set(first.proposals) == {"going_concern_uncertainty"}
    assert stats.proposals_dropped == 1
    blind = [r.blind for r in queue]
    assert any(blind) and not all(blind)
    again = _build(sample, _drawn_pages(40), frame, (mask_nlp, lemma_nlp))
    assert [r.blind for r in again] == blind  # by hash, not by chance


def test_the_form_starts_from_labels_then_proposals_then_parked_absent() -> None:
    row = _stratified_row().model_copy(
        update={"proposals": {"litigation": Label(present=True, evidence=FIGURES)}}
    )
    parked: tuple[preprocessing.SignalType, ...] = ("covenant_breach", "key_customer_loss")
    start = golden.prefill(row, parked)
    assert start == {
        "covenant_breach": Label(present=False),
        "key_customer_loss": Label(present=False),
        "litigation": Label(present=True, evidence=FIGURES),
    }
    assert golden.shows_proposals(row)
    blind = row.model_copy(update={"blind": True})
    assert not golden.shows_proposals(blind)
    assert golden.prefill(blind, parked) == {s: Label(present=False) for s in parked}
    saved = _labelled(row)
    assert golden.prefill(saved, parked) == saved.labels  # a saved page shows what was saved


def test_proposed_by_is_recorded_and_agreement_is_counted_by_arm(mask_nlp: Any) -> None:
    offered = {
        "litigation": Label(present=True, evidence=FIGURES),
        "going_concern_uncertainty": Label(present=False),
    }
    shown = _stratified_row().model_copy(update={"proposals": offered})
    blind = _stratified_row().model_copy(
        update={"page_id": "y" * 16, "proposals": offered, "blind": True}
    )
    labels = _all_absent()
    labels["litigation"] = Label(present=True, evidence=FIGURES)
    shown_done = golden.label(shown, labels, "owner", "extractor_v7")
    blind_done = golden.label(blind, _all_absent(), "owner")  # disagrees on litigation
    assert (shown_done.proposed_by, blind_done.proposed_by) == ("extractor_v7", None)
    assert golden.proposal_agreement([shown_done, blind_done, _stratified_row()]) == {
        "shown": (2, 2),
        "blind": (1, 2),
    }
    files = golden.export([shown_done], mask_nlp)
    page = json.loads(files[golden.PAGES_FILE].decode().splitlines()[0])
    assert page["proposed_by"] == "extractor_v7" and "proposals" not in page


def test_a_proposal_s_evidence_must_be_on_the_page() -> None:
    with pytest.raises(ValidationError, match="not a span of the page"):
        QueuePage.model_validate(
            {
                **_stratified_row().model_dump(),
                "proposals": {"litigation": {"present": True, "evidence": "elsewhere"}},
            }
        )
