"""The golden set's queue, labels, export and masking check (plan 0013 step E). Every name and
page below is invented."""

# PyMuPDF's signatures reference types pyright cannot resolve.
# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false
# pyright: reportUnknownArgumentType=false

from __future__ import annotations

import base64
import json
from pathlib import Path
from typing import Any

import pymupdf
import pytest
from pydantic import ValidationError

from distress_radar.extraction import golden, masking, preprocessing
from distress_radar.extraction.golden import GoldenSample, Label, QueuePage, QueueStats, TextPage

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


def test_only_opinion_type_takes_a_value_and_from_its_list(queue: list[QueuePage]) -> None:
    row = _row(queue, 1)
    labels = _all_absent()
    labels["opinion_type"] = Label(present=True, value="clean", evidence="Spółki")
    with pytest.raises(ValidationError, match="opinion_type needs one of"):
        golden.label(row, labels, "owner")
    labels["opinion_type"] = Label(present=True, value="qualified", evidence="Spółki")
    labels["litigation"] = Label(present=True, value="qualified", evidence="Spółki")
    with pytest.raises(ValidationError, match="only opinion_type takes a value"):
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


# --- export and the masking check -----------------------------------------------------------------


def test_export_writes_labelled_pages_once_and_a_file_per_signal(
    queue: list[QueuePage], mask_nlp: Any
) -> None:
    rows = [
        _labelled(_row(queue, 1), going_concern_uncertainty="istotna niepewność"),
        *(r for r in queue if r.page != 1),
    ]
    files = golden.export(rows, mask_nlp)
    assert set(files) == {"pages.jsonl", *(f"{s}.jsonl" for s in preprocessing.SIGNAL_TYPES)}
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
