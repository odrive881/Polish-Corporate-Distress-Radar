"""A4 KRS extract adapter (plan 0008 step C), against a mock transport: no network."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest
from tenacity import wait_none

from distress_radar.acquisition.base import (
    PermanentSourceError,
    SourcePolicy,
    build_source_client,
    in_memory_limiter,
)
from distress_radar.acquisition.krs_extract import (
    A4Result,
    PreviousFetch,
    content_fingerprint,
    fetch_extract,
)
from distress_radar.acquisition.raw_store import (
    InMemoryObjectStore,
    raw_key,
    sha256_hex,
    sidecar_key,
)
from distress_radar.acquisition.redaction import REGISTRY_REDACTION_VERSION

from .test_registry_redaction import synthetic_extract

POLICY = SourcePolicy(name="krs_api_test", requests_per_minute=10_000, max_attempts=2)
KRS = "0000000042"
NOW = datetime(2026, 9, 23, 8, 0, tzinfo=UTC)


def _body(generated_at: str = "23.09.2026 10:19:13", **dane: Any) -> bytes:
    doc = synthetic_extract()
    doc["odpis"]["naglowekP"]["dataCzasOdpisu"] = generated_at
    doc["odpis"]["dane"].update(dane)
    return json.dumps(doc, ensure_ascii=False).encode("utf-8")


def _run(
    tmp_path: Path,
    responses: list[httpx.Response],
    store: InMemoryObjectStore,
    previous: PreviousFetch | None = None,
    calls: list[httpx.Request] | None = None,
) -> list[A4Result]:
    log = calls if calls is not None else []

    def handler(request: httpx.Request) -> httpx.Response:
        log.append(request)
        return responses[len(log) - 1]

    async def run() -> list[A4Result]:
        client = build_source_client(
            POLICY,
            cache_dir=tmp_path / "cache",
            limiter=in_memory_limiter(POLICY),
            transport=httpx.MockTransport(handler),
            wait=wait_none(),
        )
        results: list[A4Result] = []
        prev = previous
        async with client:
            for _ in responses:
                result = await fetch_extract(
                    KRS, client=client, store=store, ingestion_run_id="run-1",
                    previous=prev, now=lambda: NOW,
                )
                results.append(result)
                if result.fetch is not None:
                    prev = PreviousFetch(result.fetch.sha256, result.fetch.content_sha256)
        return results

    return asyncio.run(run())


def _ok(body: bytes) -> httpx.Response:
    return httpx.Response(200, content=body, headers={"content-type": "application/json", "set-cookie": "s=1"})


def test_stores_the_redacted_extract_with_its_redaction(tmp_path: Path) -> None:
    store = InMemoryObjectStore()
    calls: list[httpx.Request] = []
    [result] = _run(tmp_path, [_ok(_body())], store, calls=calls)

    assert str(calls[0].url) == f"https://api-krs.ms.gov.pl/api/krs/OdpisPelny/{KRS}?rejestr=P&format=json"
    assert result.fetch is not None and result.fetch.stored_new
    stored = store.get(raw_key(result.fetch.sha256))
    assert b"PRZYK\xc5\x81ADOWSKI" not in stored and b"90010112345" not in stored
    sidecar = json.loads(store.get(sidecar_key(result.fetch.sha256)))
    assert sidecar["redaction_version"] == REGISTRY_REDACTION_VERSION
    assert sidecar["received_sha256"] == sha256_hex(_body())
    assert sidecar["source"] == "krs_api"
    assert "set-cookie" not in {k.lower() for k in sidecar["http_headers"]}
    assert [f.sha256 for f in result.raw_fetches] == [result.fetch.sha256]
    assert result.quarantine == []


def test_unchanged_extract_is_fetched_but_not_stored_again(tmp_path: Path) -> None:
    store = InMemoryObjectStore()
    calls: list[httpx.Request] = []
    first, second = _run(
        tmp_path,
        [_ok(_body("23.09.2026 10:19:13")), _ok(_body("23.09.2026 10:19:18"))],
        store,
        calls=calls,
    )
    # Both reach the source (no cache headers, so no cache answer): the cutoff is honest.
    assert len(calls) == 2
    assert first.fetch is not None and second.fetch is not None
    assert not second.fetch.stored_new
    assert second.fetch.sha256 == first.fetch.sha256
    assert second.raw_fetches == []
    assert store.write_count == 2  # one object and its sidecar


def test_changed_extract_is_stored_as_a_new_object(tmp_path: Path) -> None:
    store = InMemoryObjectStore()
    changed = _body(dzial4={"zaleglosci": [{"wszczecieEgzekucji": [{"dataWszczeciaEgzekucji": "09.10.2024"}]}]})
    first, second = _run(tmp_path, [_ok(_body()), _ok(changed)], store)
    assert first.fetch is not None and second.fetch is not None
    assert second.fetch.stored_new
    assert second.fetch.sha256 != first.fetch.sha256


def test_fingerprint_ignores_only_the_generation_time() -> None:
    from distress_radar.acquisition.redaction import redact_registry_extract

    def fingerprint(body: bytes) -> str:
        return content_fingerprint(redact_registry_extract(json.loads(body)).data)

    assert fingerprint(_body("01.01.2026 00:00:00")) == fingerprint(_body("02.01.2026 00:00:00"))
    assert fingerprint(_body()) != fingerprint(_body(dzial4={"x": "y"}))


def test_unknown_krs_is_quarantined(tmp_path: Path) -> None:
    store = InMemoryObjectStore()
    not_found = httpx.Response(404, json={"title": "Not Found", "status": 404})
    [result] = _run(tmp_path, [not_found], store)
    assert result.fetch is None and store.write_count == 0
    [row] = result.quarantine
    assert (row.stage, row.reason_code, row.krs, row.entity_key) == ("A4", "krs_extract_not_found", KRS, KRS)


def test_unredactable_extract_is_quarantined_and_not_stored(tmp_path: Path) -> None:
    store = InMemoryObjectStore()
    [result] = _run(tmp_path, [_ok(_body(dzial5={"kontakt": "12345678901"}))], store)
    assert result.fetch is None and store.write_count == 0
    assert [q.reason_code for q in result.quarantine] == ["krs_extract_unredactable"]


@pytest.mark.parametrize(
    "body",
    [b"<html>maintenance</html>", json.dumps({"odpis": {"naglowekP": {"numerKRS": "0000000001", "wpis": []}, "dane": {}}}).encode()],
    ids=["not-json", "other-entity"],
)
def test_wrong_shape_stops_the_run(tmp_path: Path, body: bytes) -> None:
    with pytest.raises(PermanentSourceError):
        _run(tmp_path, [_ok(body)], InMemoryObjectStore())
