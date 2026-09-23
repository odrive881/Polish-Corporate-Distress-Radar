"""A4 — full KRS extracts from the open KRS API (plan 0008 step C, ADR 0011).

`GET https://api-krs.ms.gov.pl/api/krs/OdpisPelny/{krs}?rejestr=P&format=json` returns the
full extract with history, per entity, with no key and no bot protection. Each response is
redacted of natural persons before it is hashed (ADR 0009 addendum) and then stored.

**Unchanged extracts are not stored twice.** The API stamps every response with the time it
was generated (`naglowekP.dataCzasOdpisu`) and sends no cache headers, so no two responses
are byte-identical and the HTTP cache never answers. The fingerprint is therefore the redacted
extract without that timestamp. When it matches the entity's latest fetch, nothing new is
stored: the fetch is recorded against the object already held, and it still counts as a
complete fetch for the source cutoff. The stored object stays exactly the (redacted)
response it was.

A KRS the API does not know (HTTP 404) is quarantined (`krs_extract_not_found`); an extract
that cannot be redacted safely is quarantined (`krs_extract_unredactable`) and not stored.
A response of the wrong shape raises `PermanentSourceError`: the API has changed, and the run
should stop rather than guess.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, cast

import httpx

from distress_radar.acquisition.base import (
    PermanentSourceError,
    SourceClient,
    SourceNotFound,
    SourcePolicy,
)
from distress_radar.acquisition.models import (
    LegalSourceFetch,
    QuarantineRecord,
    RawFetchRecord,
)
from distress_radar.acquisition.raw_store import ObjectStore, RawDocumentMeta, put_raw, sha256_hex
from distress_radar.acquisition.redaction import (
    REGISTRY_REDACTION_VERSION,
    RedactionError,
    redact_registry_extract,
)

KRS_API = "https://api-krs.ms.gov.pl/api/krs"
RAW_SOURCE = "krs_api"
EXTRACT_PARAMS = {"rejestr": "P", "format": "json"}
_SKIPPED_HEADERS = frozenset({"set-cookie"})


def krs_api_policy(requests_per_minute: int) -> SourcePolicy:
    return SourcePolicy(name=RAW_SOURCE, requests_per_minute=requests_per_minute)


def extract_url(krs: str) -> str:
    return f"{KRS_API}/OdpisPelny/{krs}"


@dataclass(frozen=True)
class PreviousFetch:
    sha256: str
    content_sha256: str


@dataclass
class A4Result:
    krs: str
    raw_fetches: list[RawFetchRecord] = field(default_factory=list[RawFetchRecord])
    fetch: LegalSourceFetch | None = None
    quarantine: list[QuarantineRecord] = field(default_factory=list[QuarantineRecord])


def parse_extract(krs: str, body: bytes) -> dict[str, Any]:
    """The extract as JSON, checked to be the full extract of `krs`."""
    try:
        document = json.loads(body)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise PermanentSourceError(f"KRS {krs}: extract is not JSON ({exc})") from exc
    odpis = _object(document).get("odpis")
    header = _object(odpis).get("naglowekP")
    if not isinstance(header, dict) or not isinstance(_object(odpis).get("dane"), dict):
        raise PermanentSourceError(f"KRS {krs}: extract lacks odpis.naglowekP / odpis.dane")
    header = cast("dict[str, Any]", header)
    if header.get("numerKRS") != krs:
        raise PermanentSourceError(f"KRS {krs}: extract is for {header.get('numerKRS')!r}")
    if not isinstance(header.get("wpis"), list):
        raise PermanentSourceError(f"KRS {krs}: extract has no entry list (naglowekP.wpis)")
    return cast("dict[str, Any]", document)


def content_fingerprint(redacted: bytes) -> str:
    """SHA-256 of a redacted extract without its per-request timestamp."""
    document = json.loads(redacted)
    document["odpis"]["naglowekP"].pop("dataCzasOdpisu", None)
    canonical = json.dumps(document, ensure_ascii=False, indent=1, sort_keys=True) + "\n"
    return sha256_hex(canonical.encode("utf-8"))


async def fetch_extract(
    krs: str,
    *,
    client: SourceClient,
    store: ObjectStore,
    ingestion_run_id: str,
    previous: PreviousFetch | None,
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> A4Result:
    """Fetch, redact and store one entity's full extract.

    Raw bytes are written before anything parses them further (invariant 2); the
    redacted response is the stored object (ADR 0009 addendum).
    """
    result = A4Result(krs)

    def quarantine(reason: str, detail: str) -> A4Result:
        result.quarantine.append(
            QuarantineRecord(
                stage="A4",
                entity_key=krs,
                reason_code=reason,
                detail=detail,
                source_document_hash=None,
                ingestion_run_id=ingestion_run_id,
                created_at=now(),
                krs=krs,
                document_ref=None,
            )
        )
        return result

    url = extract_url(krs)
    try:
        response = await client.get(url, params=EXTRACT_PARAMS)
    except SourceNotFound as exc:
        return quarantine("krs_extract_not_found", str(exc))
    fetched_at = now()
    document = parse_extract(krs, response.content)
    try:
        redaction = redact_registry_extract(document)
    except RedactionError as exc:
        return quarantine("krs_extract_unredactable", str(exc))

    fingerprint = content_fingerprint(redaction.data)
    if previous is not None and previous.content_sha256 == fingerprint:
        sha256, stored_new = previous.sha256, False
    else:
        meta = RawDocumentMeta(
            source=RAW_SOURCE,
            source_url=str(httpx.URL(url, params=EXTRACT_PARAMS)),
            content_type="application/json",
            fetched_at=fetched_at,
            http_headers=_headers(response),
            ingestion_run_id=ingestion_run_id,
            redaction_version=REGISTRY_REDACTION_VERSION,
            received_sha256=sha256_hex(response.content),
        )
        sha256 = put_raw(store, redaction.data, meta)
        result.raw_fetches.append(
            RawFetchRecord(sha256=sha256, byte_size=len(redaction.data), meta=meta)
        )
        stored_new = True
    result.fetch = LegalSourceFetch(
        krs=krs,
        source="KRS",
        sha256=sha256,
        content_sha256=fingerprint,
        fetched_at=fetched_at,
        ingestion_run_id=ingestion_run_id,
        stored_new=stored_new,
    )
    return result


def _object(value: object) -> dict[str, Any]:
    return cast("dict[str, Any]", value) if isinstance(value, dict) else {}


def _headers(response: httpx.Response) -> dict[str, str]:
    return {k: v for k, v in response.headers.items() if k.lower() not in _SKIPPED_HEADERS}
