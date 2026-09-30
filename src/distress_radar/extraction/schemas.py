"""What an extractor returns (plan 0013 step F; AGENT_SPEC §6 G2).

A model's answer is constrained twice: the API decodes against `json_schema(signal)` (structured
outputs), and the stored answer is validated by `response_model(signal)`, on the first call and on
every replay alike, so nothing is ever parsed from free text. The schema is written out rather than
generated from the Pydantic model, so the request is one fixed body for a single call and a batch,
and its bytes are part of the response key (decision 3); a test keeps the two in step.

`Extraction` is a kept answer: present or absent, with evidence when present. `Discarded` is an
answer that could not be kept, with its reason code (§6G2: an extraction without evidence is
discarded).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, get_args

from pydantic import BaseModel, ConfigDict

from distress_radar.extraction.preprocessing import SignalType

# Changes with any change to the schemas below: a new schema is a new response key.
SCHEMA_VERSION = "1"

Confidence = Literal["high", "medium", "low"]
OpinionValue = Literal["unqualified", "qualified", "adverse", "disclaimer"]
Method = Literal["llm", "rule"]
DiscardReason = Literal[
    "no_evidence",  # present, with an empty evidence span
    "evidence_not_on_page",  # the span is not a verbatim substring of the masked page
    "invalid_output",  # the answer does not validate against the schema
    "refusal",  # stop_reason `refusal`
    "max_tokens",  # cut off before the answer was complete
    "api_error",  # no response: not stored, retried on the next run
]


class SignalAnswer(BaseModel):
    """The model's answer for one signal on one page."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    present: bool
    evidence: str  # verbatim span of the page; empty when absent
    confidence: Confidence


class OpinionAnswer(SignalAnswer):
    value: OpinionValue | Literal["none"]  # "none" exactly when absent


def response_model(signal: SignalType) -> type[SignalAnswer]:
    return OpinionAnswer if signal == "opinion_type" else SignalAnswer


def json_schema(signal: SignalType) -> dict[str, Any]:
    """The output schema sent with the request: every field required, nothing else allowed."""
    properties: dict[str, Any] = {
        "present": {"type": "boolean"},
        "evidence": {"type": "string"},
        "confidence": {"type": "string", "enum": list(get_args(Confidence))},
    }
    if signal == "opinion_type":
        properties["value"] = {"type": "string", "enum": [*get_args(OpinionValue), "none"]}
    return {
        "type": "object",
        "properties": properties,
        "required": list(properties),
        "additionalProperties": False,
    }


@dataclass(frozen=True)
class Extraction:
    signal_type: SignalType
    present: bool
    value: str | None  # `opinion_type` only
    evidence: str | None  # verbatim span of the masked page, when present
    evidence_start: int | None  # its offset in the masked page (the first occurrence)
    confidence: Confidence
    method: Method
    response_key: str | None  # the stored response it was read from (llm only)


@dataclass(frozen=True)
class Discarded:
    signal_type: SignalType
    reason_code: DiscardReason
    response_key: str | None
