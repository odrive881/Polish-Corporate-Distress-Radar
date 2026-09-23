import asyncio
from pathlib import Path

import httpx
import pytest
from tenacity import wait_none

from distress_radar.acquisition.base import (
    ContentCheckFailed,
    PermanentSourceError,
    SourceNotFound,
    SourcePolicy,
    TransientSourceError,
    build_source_client,
    in_memory_limiter,
)

POLICY = SourcePolicy(name="test_source", requests_per_minute=10_000, max_attempts=3)


def _get(tmp_path: Path, handler, url: str = "https://source.test/doc", **kwargs):
    calls: list[httpx.Request] = []

    def recording(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return handler(request, len(calls))

    async def run() -> httpx.Response:
        client = build_source_client(
            POLICY,
            cache_dir=tmp_path / "cache",
            limiter=in_memory_limiter(POLICY),
            transport=httpx.MockTransport(recording),
            wait=wait_none(),
        )
        async with client:
            return await client.get(url, **kwargs)

    try:
        return asyncio.run(run()), calls
    except Exception as exc:  # re-raised with the call log attached for assertions
        exc.calls = calls  # type: ignore[attr-defined]
        raise


@pytest.mark.parametrize("status", [429, 500, 503])
def test_transient_status_retries_then_succeeds(tmp_path: Path, status: int):
    def handler(_: httpx.Request, n: int) -> httpx.Response:
        return httpx.Response(status if n < 3 else 200, content=b"ok")

    response, calls = _get(tmp_path, handler)

    assert response.status_code == 200
    assert response.content == b"ok"
    assert len(calls) == 3


def test_transient_errors_give_up_after_max_attempts(tmp_path: Path):
    def handler(_: httpx.Request, n: int) -> httpx.Response:
        return httpx.Response(503)

    with pytest.raises(TransientSourceError) as info:
        _get(tmp_path, handler)
    assert len(info.value.calls) == POLICY.max_attempts  # type: ignore[attr-defined]


def test_network_errors_are_transient(tmp_path: Path):
    def handler(request: httpx.Request, n: int) -> httpx.Response:
        if n == 1:
            raise httpx.ConnectError("refused", request=request)
        return httpx.Response(200, content=b"ok")

    response, calls = _get(tmp_path, handler)

    assert response.content == b"ok"
    assert len(calls) == 2


@pytest.mark.parametrize("status", [400, 403, 404])
def test_permanent_status_does_not_retry(tmp_path: Path, status: int):
    def handler(_: httpx.Request, n: int) -> httpx.Response:
        return httpx.Response(status)

    with pytest.raises(PermanentSourceError) as info:
        _get(tmp_path, handler)
    assert len(info.value.calls) == 1  # type: ignore[attr-defined]


def test_not_found_is_its_own_permanent_error(tmp_path: Path):
    with pytest.raises(SourceNotFound):
        _get(tmp_path, lambda _, n: httpx.Response(404))
    with pytest.raises(PermanentSourceError) as info:
        _get(tmp_path, lambda _, n: httpx.Response(403))
    assert not isinstance(info.value, SourceNotFound)


def test_content_check_failure_is_distinct_and_not_retried(tmp_path: Path):
    def handler(_: httpx.Request, n: int) -> httpx.Response:
        return httpx.Response(200, content=b"<html>Please enable JavaScript</html>")

    def check(response: httpx.Response) -> str | None:
        return "js challenge" if b"enable JavaScript" in response.content else None

    with pytest.raises(ContentCheckFailed) as info:
        _get(tmp_path, handler, content_check=check)
    assert info.value.reason == "js challenge"
    assert len(info.value.calls) == 1  # type: ignore[attr-defined]


def test_cacheable_response_is_served_from_disk_cache(tmp_path: Path):
    def handler(_: httpx.Request, n: int) -> httpx.Response:
        return httpx.Response(200, content=b"doc", headers={"Cache-Control": "max-age=3600"})

    _, first_calls = _get(tmp_path, handler)
    response, second_calls = _get(tmp_path, handler)  # fresh client, same cache dir

    assert response.content == b"doc"
    assert len(first_calls) == 1
    assert len(second_calls) == 0
    assert (tmp_path / "cache" / "test_source.sqlite").exists()
