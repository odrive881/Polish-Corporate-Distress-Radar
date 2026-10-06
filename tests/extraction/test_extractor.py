"""The constrained extractor, its schemas, rules and response store (plan 0013 step F). No call to
any API: a fake transport answers. Every text below is invented."""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any, get_args

import pytest
from pydantic import ValidationError

from distress_radar.acquisition.raw_store import InMemoryObjectStore
from distress_radar.extraction import extractor as ex
from distress_radar.extraction import preprocessing, rules, schemas
from distress_radar.extraction.response_store import ResponseStore, object_key, request_key
from distress_radar.extraction.schemas import Discarded, Extraction
from distress_radar.settings import Settings

REPO = Path(__file__).resolve().parents[2]
LITIGATION = "Przeciwko Spółce toczy się postępowanie sądowe o zapłatę 120 tys. zł."
PAGE = f"Należności wzrosły o 10%.\n{LITIGATION}\nZapasy wyceniono w cenach nabycia."


@pytest.fixture(scope="module")
def extractor() -> ex.Extractor:
    return ex.load_extractor("extractor_v1")


@pytest.fixture(scope="module")
def lemma_nlp() -> Any:
    return preprocessing.load_model()


def _message(answer: dict[str, Any] | str, stop_reason: str = "end_turn") -> bytes:
    text = answer if isinstance(answer, str) else json.dumps(answer, ensure_ascii=False)
    return json.dumps(
        {
            "id": "msg_test",
            "type": "message",
            "role": "assistant",
            "model": "claude-opus-5-5",
            "content": [{"type": "thinking", "thinking": ""}, {"type": "text", "text": text}],
            "stop_reason": stop_reason,
        },
        ensure_ascii=False,
    ).encode()


class FakeTransport:
    """Answers every request with `answer`; counts what it was asked."""

    def __init__(self, answer: bytes | None) -> None:
        self.answer = answer
        self.calls: list[str] = []

    def run(self, requests: Mapping[str, dict[str, Any]]) -> tuple[dict[str, bytes], set[str]]:
        self.calls.extend(requests)
        if self.answer is None:
            return {}, set(requests)
        return {k: self.answer for k in requests}, set()


def _page(page_id: str, text: str, *signals: preprocessing.SignalType) -> ex.PageInput:
    return ex.PageInput(page_id, text, [], signals)


# --- schemas -------------------------------------------------------------------------------------


@pytest.mark.parametrize("signal", preprocessing.SIGNAL_TYPES)
def test_the_sent_schema_and_the_validating_model_agree(signal: preprocessing.SignalType) -> None:
    schema = schemas.json_schema(signal)
    model = schemas.response_model(signal)
    assert set(schema["properties"]) == set(model.model_fields) == set(schema["required"])
    assert schema["additionalProperties"] is False
    assert schema["properties"]["confidence"]["enum"] == list(get_args(schemas.Confidence))
    if signal == "opinion_type":
        assert set(schema["properties"]["value"]["enum"]) == {
            *get_args(schemas.OpinionValue),
            "none",
        }


def test_an_answer_with_an_extra_field_is_invalid() -> None:
    with pytest.raises(ValidationError):
        schemas.SignalAnswer.model_validate(
            {"present": False, "evidence": "", "confidence": "high", "note": "x"}
        )


# --- config and requests -------------------------------------------------------------------------


def test_the_config_names_a_prompt_for_every_model_signal(extractor: ex.Extractor) -> None:
    llm = {s for s, m in extractor.config.signals.items() if m.method == "llm"}
    assert llm == set(preprocessing.SIGNAL_TYPES) - {"opinion_type"}
    prompts = {p.stem for p in (REPO / "prompts" / "extraction").glob("*.md")}
    assert {m.prompt for m in extractor.config.signals.values() if m.prompt} == prompts


def test_the_version_must_be_the_file_name(tmp_path: Path) -> None:
    (tmp_path / "extraction").mkdir()
    source = REPO / "config" / "extraction" / "extractor_v1.yaml"
    (tmp_path / "extraction" / "extractor_v9.yaml").write_bytes(source.read_bytes())
    with pytest.raises(ValueError, match="must match the file name"):
        ex.load_extractor("extractor_v9", tmp_path)


def test_a_request_is_the_prompt_the_masked_page_and_the_schema(extractor: ex.Extractor) -> None:
    params = ex.request_params(extractor, "litigation", PAGE)
    assert params["model"] == "claude-opus-5-5"
    assert params["system"] == extractor.prompts["litigation_v1"]
    assert PAGE in params["messages"][0]["content"]
    assert params["output_config"]["format"]["schema"] == schemas.json_schema("litigation")
    assert "thinking" not in params and "fallbacks" not in params
    with pytest.raises(ValueError, match="rule"):
        ex.request_params(extractor, "opinion_type", PAGE)


def test_any_change_to_the_request_is_a_new_key(extractor: ex.Extractor) -> None:
    key = request_key(ex.request_params(extractor, "litigation", PAGE))
    assert key == request_key(ex.request_params(extractor, "litigation", PAGE))
    assert key != request_key(ex.request_params(extractor, "litigation", PAGE + " "))
    assert key != request_key(ex.request_params(extractor, "covenant_breach", PAGE))
    other = ex.Extractor(
        extractor.config.model_copy(update={"effort": "high"}), extractor.prompts, extractor.rules
    )
    assert key != request_key(ex.request_params(other, "litigation", PAGE))


# --- extraction, replay and discards -------------------------------------------------------------


def test_a_second_run_makes_no_call_and_gives_the_same_answers(extractor: ex.Extractor) -> None:
    store = ResponseStore(InMemoryObjectStore())
    answer = _message({"present": True, "evidence": LITIGATION, "confidence": "high"})
    pages = [_page("p1", PAGE, "litigation")]
    first = FakeTransport(answer)
    got = ex.extract(pages, extractor, store, first, ex.ExtractionStats())
    stats = ex.ExtractionStats()
    second = FakeTransport(None)
    assert ex.extract(pages, extractor, store, second, stats) == got
    assert len(first.calls) == 1 and second.calls == []
    assert (stats.called, stats.replayed, stats.kept) == (0, 1, 1)
    [result] = got.values()
    assert isinstance(result, Extraction)
    assert result.evidence == LITIGATION and result.evidence_start == PAGE.index(LITIGATION)
    assert result.method == "llm" and result.response_key == first.calls[0]


def test_the_stored_body_is_the_response_as_returned(extractor: ex.Extractor) -> None:
    objects = InMemoryObjectStore()
    store = ResponseStore(objects)
    answer = _message({"present": False, "evidence": "", "confidence": "high"})
    transport = FakeTransport(answer)
    ex.extract([_page("p1", PAGE, "litigation")], extractor, store, transport, ex.ExtractionStats())
    assert objects.objects[object_key(transport.calls[0])] == answer
    [meta] = store.new
    assert (meta.model, meta.prompt, meta.stop_reason) == (
        "claude-opus-5-5",
        "litigation_v1",
        "end_turn",
    )


def test_pages_with_the_same_text_share_one_call(extractor: ex.Extractor) -> None:
    transport = FakeTransport(_message({"present": False, "evidence": "", "confidence": "low"}))
    got = ex.extract(
        [_page("p1", PAGE, "litigation"), _page("p2", PAGE, "litigation")],
        extractor,
        ResponseStore(InMemoryObjectStore()),
        transport,
        ex.ExtractionStats(),
    )
    assert len(transport.calls) == 1 and set(got) == {("p1", "litigation"), ("p2", "litigation")}


@pytest.mark.parametrize(
    ("answer", "stop_reason", "reason"),
    [
        (
            {
                "present": True,
                "evidence": "postępowanie sądowe o zapłatę 1 mln zł",
                "confidence": "high",
            },
            "end_turn",
            "evidence_not_on_page",
        ),
        ({"present": True, "evidence": "  ", "confidence": "high"}, "end_turn", "no_evidence"),
        ('{"present": true', "end_turn", "invalid_output"),
        (
            {"present": True, "evidence": LITIGATION, "confidence": "sure"},
            "end_turn",
            "invalid_output",
        ),
        ("", "refusal", "refusal"),
        ('{"present": true, "evid', "max_tokens", "max_tokens"),
    ],
)
def test_an_answer_that_cannot_be_kept_is_discarded_with_its_reason(
    extractor: ex.Extractor, answer: dict[str, Any] | str, stop_reason: str, reason: str
) -> None:
    stats = ex.ExtractionStats()
    got = ex.extract(
        [_page("p1", PAGE, "litigation")],
        extractor,
        ResponseStore(InMemoryObjectStore()),
        FakeTransport(_message(answer, stop_reason)),
        stats,
    )
    assert got == {
        ("p1", "litigation"): Discarded(
            "litigation", reason, got[("p1", "litigation")].response_key
        )
    }  # type: ignore[arg-type]
    assert stats.discarded == {reason: 1}


def test_no_response_is_an_api_error_not_stored_and_retried(extractor: ex.Extractor) -> None:
    store = ResponseStore(InMemoryObjectStore())
    pages = [_page("p1", PAGE, "litigation")]
    got = ex.extract(pages, extractor, store, FakeTransport(None), ex.ExtractionStats())
    assert got == {("p1", "litigation"): Discarded("litigation", "api_error", None)}
    retry = FakeTransport(_message({"present": False, "evidence": "", "confidence": "high"}))
    ex.extract(pages, extractor, store, retry, ex.ExtractionStats())
    assert len(retry.calls) == 1


def test_an_absent_opinion_says_none_and_a_present_one_a_value(extractor: ex.Extractor) -> None:
    key = "k" * 64
    ok = ex.interpret(
        "opinion_type",
        _message({"present": False, "evidence": "", "confidence": "high", "value": "none"}),
        PAGE,
        key,
    )
    assert isinstance(ok, Extraction) and ok.value is None
    bad = ex.interpret(
        "opinion_type",
        _message({"present": True, "evidence": LITIGATION, "confidence": "high", "value": "none"}),
        PAGE,
        key,
    )
    assert bad == Discarded("opinion_type", "invalid_output", key)


# --- rules ---------------------------------------------------------------------------------------


def test_every_rule_example_matches_its_term_in_every_sentence(lemma_nlp: Any) -> None:
    loaded = rules.load_rules("rules_v1")
    missed = [
        (value, term.words, sentence)
        for value, terms in loaded.opinion_type.items()
        for term in terms
        for sentence in preprocessing.analyse(term.example, lemma_nlp)
        if rules.sentence_opinion(sentence, loaded) != value
    ]
    assert not missed


@pytest.mark.parametrize(
    ("text", "value"),
    [
        ("Sprawozdanie zbadano. Biegły rewident wydał opinię bez zastrzeżeń.", "unqualified"),
        ("Wyrażamy opinię bez zastrzeżeń. Wydaliśmy opinię z zastrzeżeniem.", "qualified"),
        ("Opinia z zastrzeżeniem. Nie wyrażamy opinii o sprawozdaniu.", "disclaimer"),
        ("Zapasy wyceniono w cenach nabycia.", None),
    ],
)
def test_the_opinion_rule_takes_the_most_severe_with_its_sentence(
    text: str, value: str | None, lemma_nlp: Any, extractor: ex.Extractor
) -> None:
    sentences = preprocessing.analyse(text, lemma_nlp)
    got = ex.extract(
        [ex.PageInput("p1", text, sentences, ("opinion_type",))],
        extractor,
        ResponseStore(InMemoryObjectStore()),
        FakeTransport(None),
        ex.ExtractionStats(),
    )[("p1", "opinion_type")]
    assert isinstance(got, Extraction) and got.method == "rule" and got.value == value
    assert got.present == (value is not None)
    if got.evidence is not None:
        assert got.evidence_start is not None
        assert text[got.evidence_start :].startswith(got.evidence)


# --- the gate on live calls ----------------------------------------------------------------------


def test_no_client_before_the_owner_confirms_the_terms() -> None:
    with pytest.raises(PermissionError, match="data-retention"):
        ex.anthropic_client(Settings(extraction_api_confirmed=False, anthropic_api_key="sk-test"))  # type: ignore[arg-type]
    with pytest.raises(PermissionError, match="ANTHROPIC_API_KEY"):
        ex.anthropic_client(Settings(extraction_api_confirmed=True, anthropic_api_key=""))  # type: ignore[arg-type]


def test_an_identity_linked_key_needs_its_workspace() -> None:
    with pytest.raises(PermissionError, match="ANTHROPIC_WORKSPACE_ID"):
        ex.anthropic_client(
            Settings(
                extraction_api_confirmed=True,
                anthropic_api_key="sk-ant-usr-test",  # type: ignore[arg-type]
                anthropic_workspace_id=None,
            )
        )
    client = ex.anthropic_client(
        Settings(
            extraction_api_confirmed=True,
            anthropic_api_key="sk-ant-usr-test",  # type: ignore[arg-type]
            anthropic_workspace_id="wrkspc_test",
        )
    )
    assert client.default_headers[ex.WORKSPACE_HEADER] == "wrkspc_test"


def test_a_workspace_key_sends_no_workspace_header() -> None:
    client = ex.anthropic_client(
        Settings(
            extraction_api_confirmed=True,
            anthropic_api_key="sk-ant-api03-test",  # type: ignore[arg-type]
            anthropic_workspace_id=None,
        )
    )
    assert ex.WORKSPACE_HEADER not in client.default_headers


# --- transports, against a fake client -----------------------------------------------------------


class _Msg:
    def __init__(self, body: bytes) -> None:
        self.body = body

    def to_json(self) -> str:
        return self.body.decode()


class _Obj:
    def __init__(self, **kw: Any) -> None:
        self.__dict__.update(kw)


class _Batches:
    def __init__(self, fail: set[str]) -> None:
        self.fail = fail
        self.requests: list[Any] = []
        self.polls = 0

    def create(self, requests: list[Any]) -> _Obj:
        self.requests = requests
        return _Obj(id="b1", processing_status="in_progress")

    def retrieve(self, batch_id: str) -> _Obj:
        self.polls += 1
        return _Obj(id=batch_id, processing_status="ended")

    def results(self, batch_id: str) -> list[_Obj]:
        body = _message({"present": False, "evidence": "", "confidence": "high"})
        return [
            _Obj(
                custom_id=r["custom_id"],
                result=_Obj(type="errored")
                if r["custom_id"] in self.fail
                else _Obj(type="succeeded", message=_Msg(body)),
            )
            for r in reversed(self.requests)  # any order: matched by custom_id
        ]


def test_the_batch_transport_matches_results_by_key_and_reports_the_rest() -> None:
    batches = _Batches(fail={"b" * 64})
    client = _Obj(messages=_Obj(batches=batches))
    transport = ex.BatchTransport(client, poll_seconds=0)  # type: ignore[arg-type]
    responses, failed = transport.run({"a" * 64: {"model": "m"}, "b" * 64: {"model": "m"}})
    assert set(responses) == {"a" * 64} and failed == {"b" * 64} and batches.polls == 1
    assert [r["custom_id"] for r in batches.requests] == ["a" * 64, "b" * 64]


def test_the_sync_transport_leaves_failed_calls_for_the_next_run() -> None:
    import anthropic
    import httpx2

    def create(**params: Any) -> _Msg:
        if params["model"] == "down":
            raise anthropic.APIConnectionError(request=httpx2.Request("POST", "https://x.invalid"))
        return _Msg(b'{"stop_reason": "end_turn"}')

    transport = ex.SyncTransport(_Obj(messages=_Obj(create=create)))  # type: ignore[arg-type]
    responses, failed = transport.run({"a" * 64: {"model": "up"}, "b" * 64: {"model": "down"}})
    assert set(responses) == {"a" * 64} and failed == {"b" * 64}


# --- rules_v2: the opinion by the report's headings ------------------------------------------------


@pytest.fixture(scope="module")
def headings() -> rules.Rules:
    return rules.load_rules("rules_v2")


@pytest.mark.parametrize(
    ("text", "value", "confidence", "evidence"),
    [
        # invented reports, in the KSB 700/705 layout
        (
            (
                "Opinia\nNaszym zdaniem sprawozdanie przedstawia rzetelny obraz.\nPodstawa opinii\n"
                "Badanie przeprowadziliśmy zgodnie z KSB."
            ),
            "unqualified",
            "high",
            "Opinia",
        ),
        (
            (
                "1. Opinia z zastrzeżeniem:\nNaszym zdaniem, z wyjątkiem kwestii opisanej niżej...\n"
                "2. Podstawa opinii z zastrzeżeniem"
            ),
            "qualified",
            "high",
            "1. Opinia z zastrzeżeniem:",
        ),
        (
            "ODMOWA WYRAŻENIA OPINII\nNie wyrażamy opinii.",
            "disclaimer",
            "high",
            "ODMOWA WYRAŻENIA OPINII",
        ),
        # no opinion heading on the page: its basis heading says which opinion it is
        (
            (
                "Naszym zdaniem sprawozdanie nie przedstawia rzetelnego obrazu.\n"
                "  Podstawa opinii negatywnej  \nSpółka nie ujęła rezerwy."
            ),
            "adverse",
            "medium",
            "Podstawa opinii negatywnej",
        ),
        # the opinion on the management report, and a sentence mentioning a qualification: no
        # heading of the opinion on the statements
        (
            (
                "Opinia o sprawozdaniu z działalności\nW poprzednim roku wydaliśmy opinię z "
                "zastrzeżeniem."
            ),
            None,
            "high",
            None,
        ),
    ],
)
def test_the_heading_rule_reads_the_opinion_section_s_heading(
    text: str,
    value: str | None,
    confidence: str,
    evidence: str | None,
    headings: rules.Rules,
) -> None:
    got = rules.opinion_type(text, [], headings)
    assert (got.value, got.present, got.confidence, got.evidence) == (
        value,
        value is not None,
        confidence,
        evidence,
    )
    if got.evidence is not None:
        assert got.evidence_start is not None
        assert text[got.evidence_start :].startswith(got.evidence)


def test_the_first_opinion_heading_wins_over_a_basis_heading_before_it(
    headings: rules.Rules,
) -> None:
    text = "Podstawa opinii\nOpinia z zastrzeżeniem\nOpinia"
    got = rules.opinion_type(text, [], headings)
    assert (got.value, got.confidence) == ("qualified", "high")


def test_every_heading_is_written_as_its_key(headings: rules.Rules) -> None:
    assert headings.opinion_headings is not None
    assert {v for v in headings.opinion_headings} == set(rules.SEVERITY)
    with pytest.raises(ValidationError, match="not written as its key"):
        rules.OpinionHeadings(opinion=("Opinia",), basis=("podstawa opinii",))


def test_rules_give_exactly_one_form(headings: rules.Rules) -> None:
    terms = rules.load_rules("rules_v1").opinion_type
    with pytest.raises(ValidationError, match="exactly one"):
        rules.Rules(
            rules_version="x", opinion_type=terms, opinion_headings=headings.opinion_headings
        )
    with pytest.raises(ValidationError, match="exactly one"):
        rules.Rules(rules_version="x")
    assert headings.opinion_headings is not None
    twice = {**headings.opinion_headings}
    twice["adverse"] = twice["unqualified"]
    with pytest.raises(ValidationError, match="more than once"):
        rules.Rules(rules_version="x", opinion_headings=twice)


def test_extractor_v2_reads_the_opinion_by_headings_and_keeps_v1_s_requests() -> None:
    v1, v2 = ex.load_extractor("extractor_v1"), ex.load_extractor("extractor_v2")
    assert v2.rules.opinion_headings is not None
    assert v2.config.prefilter_version == "prefilter_v2"
    for signal, method in v2.config.signals.items():
        if method.method == "llm":
            assert ex.request_params(v1, signal, "x") == ex.request_params(v2, signal, "x")
