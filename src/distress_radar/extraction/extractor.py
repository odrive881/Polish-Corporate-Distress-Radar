"""The constrained extractor (plan 0013 step F, decisions 2 and 3; AGENT_SPEC §6 G2).

For each masked page and each signal_type the prefilter selected on it, `extract` gives one
`Extraction` or one `Discarded`:
- a `rule` signal is read by `extraction.rules`, no model;
- an `llm` signal is one request: the signal's prompt (`prompts/extraction/<prompt>.md`) as the
  system prompt, the masked page as the user turn, the answer constrained to
  `schemas.json_schema(signal)`. Its response is read from the `ResponseStore`; only requests the
  store lacks go to the `Transport`, and what comes back is stored before it is read. A second run
  over the same pages makes no call and gives the same answers.
- an answer is kept only when it validates against `schemas.response_model(signal)` and, when
  present, its evidence is a verbatim substring of the masked page; otherwise it is `Discarded` with
  a reason code, counted, never silently dropped.

Two transports: `SyncTransport` (one call each, for development) and `BatchTransport` (the Message
Batches API, half price, for backfills). Neither runs until the owner has confirmed the provider's
data-retention terms (decision 2): `anthropic_client` refuses without `EXTRACTION_API_CONFIRMED`.
Only masked text is ever in a request (ADR 0009, third addendum).
"""

from __future__ import annotations

import hashlib
import json
import time
from collections import Counter
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, Protocol

import anthropic
import yaml
from anthropic.types.message_create_params import MessageCreateParamsNonStreaming
from anthropic.types.messages.batch_create_params import Request
from pydantic import BaseModel, ConfigDict, ValidationError, model_validator

from distress_radar.extraction import rules as rules_module
from distress_radar.extraction.preprocessing import SIGNAL_TYPES, Sentence, SignalType
from distress_radar.extraction.response_store import ResponseMeta, ResponseStore, request_key
from distress_radar.extraction.schemas import (
    SCHEMA_VERSION,
    Discarded,
    DiscardReason,
    Extraction,
    OpinionAnswer,
    json_schema,
    response_model,
)
from distress_radar.parsing.canonical_schema import CONFIG_DIR
from distress_radar.settings import Settings

PROMPTS_DIR = CONFIG_DIR.parent / "prompts" / "extraction"
Effort = Literal["low", "medium", "high", "xhigh", "max"]


# --- config/extraction/extractor_*.yaml ----------------------------------------------------------


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class SignalMethod(_Frozen):
    method: Literal["llm", "rule"]
    prompt: str | None = None

    @model_validator(mode="after")
    def _prompt_with_llm(self) -> SignalMethod:
        if (self.method == "llm") != (self.prompt is not None):
            raise ValueError("an `llm` signal names its prompt, a `rule` signal none")
        return self


class ExtractorConfig(_Frozen):
    extractor_version: str
    model: str
    effort: Effort
    max_tokens: int
    rules_version: str
    prefilter_version: str
    signals: dict[SignalType, SignalMethod]

    @model_validator(mode="after")
    def _every_signal_listed(self) -> ExtractorConfig:
        missing = [s for s in SIGNAL_TYPES if s not in self.signals]
        if missing:
            raise ValueError(f"every signal_type needs a method; missing {missing}")
        rule_signals = {s for s, m in self.signals.items() if m.method == "rule"}
        if not rule_signals <= {"opinion_type"}:
            raise ValueError(f"no rule is written for {sorted(rule_signals - {'opinion_type'})}")
        return self


@dataclass(frozen=True)
class Extractor:
    config: ExtractorConfig
    prompts: Mapping[str, str]  # prompt name → its text
    rules: rules_module.Rules


def load_extractor(
    version: str, config_dir: Path = CONFIG_DIR, prompts_dir: Path = PROMPTS_DIR
) -> Extractor:
    path = config_dir / "extraction" / f"{version}.yaml"
    config = ExtractorConfig.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))
    if config.extractor_version != path.stem:
        raise ValueError(
            f"{path}: `extractor_version: {config.extractor_version}` must match the file name"
        )
    prompts: dict[str, str] = {}
    for signal, method in config.signals.items():
        if method.prompt is None:
            continue
        if not method.prompt.startswith(f"{signal}_v"):
            raise ValueError(f"{signal}: its prompt is named `{signal}_v<n>`, not {method.prompt}")
        prompts[method.prompt] = (prompts_dir / f"{method.prompt}.md").read_text(encoding="utf-8")
    return Extractor(config, prompts, rules_module.load_rules(config.rules_version, config_dir))


# --- requests ------------------------------------------------------------------------------------


def request_params(extractor: Extractor, signal: SignalType, page_text: str) -> dict[str, Any]:
    """The full request body for one signal on one masked page; its hash is the response key."""
    method = extractor.config.signals[signal]
    if method.prompt is None:
        raise ValueError(f"{signal} is read by a rule, not a model")
    return {
        "model": extractor.config.model,
        "max_tokens": extractor.config.max_tokens,
        "system": extractor.prompts[method.prompt],
        "messages": [{"role": "user", "content": f"<page>\n{page_text}\n</page>"}],
        "output_config": {
            "effort": extractor.config.effort,
            "format": {"type": "json_schema", "schema": json_schema(signal)},
        },
    }


class Transport(Protocol):
    def run(self, requests: Mapping[str, dict[str, Any]]) -> tuple[dict[str, bytes], set[str]]:
        """Responses (the API's JSON) by request key, and the keys that got none."""
        ...


def anthropic_client(settings: Settings) -> anthropic.Anthropic:
    """The API client, only once the owner has confirmed the provider's terms (decision 2)."""
    if not settings.extraction_api_confirmed:
        raise PermissionError(
            "No extraction call before the owner confirms the provider's data-retention terms "
            "(plan 0013 decision 2); then set EXTRACTION_API_CONFIRMED=true."
        )
    key = settings.anthropic_api_key.get_secret_value() if settings.anthropic_api_key else ""
    if not key:
        raise PermissionError("ANTHROPIC_API_KEY is not set")
    return anthropic.Anthropic(api_key=key)


def _params(params: dict[str, Any]) -> MessageCreateParamsNonStreaming:
    return MessageCreateParamsNonStreaming(**params)  # pyright: ignore[reportArgumentType]


class NoCalls:
    """The transport before the owner's confirmation (decision 2): never asked, and refuses if
    it is. Callers pass only rule signals and stored responses with it."""

    def run(self, requests: Mapping[str, dict[str, Any]]) -> tuple[dict[str, bytes], set[str]]:
        if requests:
            raise PermissionError("no model call before EXTRACTION_API_CONFIRMED (decision 2)")
        return {}, set()


class SyncTransport:
    """One call per request, for development. Transient failures are left for the next run."""

    def __init__(self, client: anthropic.Anthropic) -> None:
        self.client = client

    def run(self, requests: Mapping[str, dict[str, Any]]) -> tuple[dict[str, bytes], set[str]]:
        responses: dict[str, bytes] = {}
        failed: set[str] = set()
        for key, params in requests.items():
            try:
                message = self.client.messages.create(**_params(params))
            except (anthropic.APIStatusError, anthropic.APIConnectionError):
                failed.add(key)
                continue
            responses[key] = message.to_json().encode("utf-8")
        return responses, failed


class BatchTransport:
    """The Message Batches API (asynchronous, half price), for backfills: one batch, polled until
    it ends; results are matched by `custom_id`, the request key, never by position."""

    def __init__(self, client: anthropic.Anthropic, poll_seconds: float = 60.0) -> None:
        self.client = client
        self.poll_seconds = poll_seconds

    def run(self, requests: Mapping[str, dict[str, Any]]) -> tuple[dict[str, bytes], set[str]]:
        if not requests:
            return {}, set()
        batch = self.client.messages.batches.create(
            requests=[Request(custom_id=k, params=_params(p)) for k, p in requests.items()]
        )
        while batch.processing_status != "ended":
            time.sleep(self.poll_seconds)
            batch = self.client.messages.batches.retrieve(batch.id)
        responses: dict[str, bytes] = {}
        for result in self.client.messages.batches.results(batch.id):
            if result.result.type == "succeeded":
                responses[result.custom_id] = result.result.message.to_json().encode("utf-8")
        return responses, set(requests) - set(responses)


# --- extraction ----------------------------------------------------------------------------------


@dataclass(frozen=True)
class PageInput:
    page_id: str
    text: str  # masked
    sentences: list[Sentence]  # `preprocessing.analyse(text)`
    signals: tuple[SignalType, ...]  # what the prefilter selected on the page


@dataclass
class ExtractionStats:
    requests: int = 0  # distinct llm requests (pages with the same text share one)
    called: int = 0  # of them, sent to the transport this run
    replayed: int = 0  # read from the store without a call
    kept: int = 0
    discarded: Counter[str] = field(default_factory=Counter[str])


def interpret(signal: SignalType, body: bytes, page_text: str, key: str) -> Extraction | Discarded:
    """A stored response read as an extraction, or discarded with its reason."""
    message = json.loads(body)
    stop_reason = message.get("stop_reason")
    if stop_reason in ("refusal", "max_tokens"):
        reason: DiscardReason = stop_reason
        return Discarded(signal, reason, key)
    text = next(
        (b.get("text", "") for b in message.get("content", []) if b.get("type") == "text"), None
    )
    try:
        answer = response_model(signal).model_validate_json(text or "")
    except ValidationError:
        return Discarded(signal, "invalid_output", key)
    value = answer.value if isinstance(answer, OpinionAnswer) else None
    if not answer.present:
        if value not in (None, "none"):
            return Discarded(signal, "invalid_output", key)
        return Extraction(signal, False, None, None, None, answer.confidence, "llm", key)
    if value == "none":
        return Discarded(signal, "invalid_output", key)
    if not answer.evidence.strip():
        return Discarded(signal, "no_evidence", key)
    start = page_text.find(answer.evidence)
    if start < 0:
        return Discarded(signal, "evidence_not_on_page", key)
    return Extraction(signal, True, value, answer.evidence, start, answer.confidence, "llm", key)


def extract(
    pages: Iterable[PageInput],
    extractor: Extractor,
    store: ResponseStore,
    transport: Transport,
    stats: ExtractionStats,
) -> dict[tuple[str, SignalType], Extraction | Discarded]:
    """Every selected (page, signal): kept or discarded, keyed by (page id, signal)."""
    out: dict[tuple[str, SignalType], Extraction | Discarded] = {}
    # Pages with the same masked text share a request, and so a key: one call answers them all.
    pending: dict[str, tuple[SignalType, dict[str, Any], list[PageInput]]] = {}
    for page in pages:
        for signal in page.signals:
            method = extractor.config.signals[signal]
            if method.method == "rule":
                out[(page.page_id, signal)] = rules_module.opinion_type(
                    page.text, page.sentences, extractor.rules
                )
                continue
            params = request_params(extractor, signal, page.text)
            pending.setdefault(request_key(params), (signal, params, []))[2].append(page)
    stats.requests += len(pending)
    missing = {k: p for k, (_signal, p, _pages) in pending.items() if store.get(k) is None}
    stats.replayed += len(pending) - len(missing)
    if missing:
        stats.called += len(missing)
        responses, _failed = transport.run(missing)
        now = datetime.now(UTC)
        for key, body in sorted(responses.items()):
            signal, _params, (page, *_same) = pending[key]
            meta = ResponseMeta(
                request_key=key,
                model=extractor.config.model,
                prompt=str(extractor.config.signals[signal].prompt),
                schema_version=SCHEMA_VERSION,
                page_sha256=hashlib.sha256(page.text.encode()).hexdigest(),
                stop_reason=json.loads(body).get("stop_reason"),
                stored_at=now,
            )
            store.put(meta, body)
    for key, (signal, _params, same_text) in pending.items():
        body = store.get(key)
        for page in same_text:
            out[(page.page_id, signal)] = (
                Discarded(signal, "api_error", None)
                if body is None
                else interpret(signal, body, page.text, key)
            )
    for result in out.values():
        if isinstance(result, Discarded):
            stats.discarded[result.reason_code] += 1
        else:
            stats.kept += 1
    return dict(sorted(out.items()))
