"""Shared acquisition base (stage A, AGENT_SPEC.md §6A).

Every adapter gets the same four things from here:

- **Failure taxonomy.** `TransientSourceError` (network errors, 429, 5xx) is
  retried by `tenacity`; `PermanentSourceError` (other 4xx, malformed
  responses) is not. `ContentCheckFailed` is neither: the response arrived but
  is not real content (e.g. an anti-bot challenge page). It is the seam where
  plan 0003 routes to a Playwright tier (ADR 0004), so it is never retried here.
- **Persistent pacing.** A `pyrate-limiter` bucket in Postgres, so pacing
  survives process restarts. Limits come from per-source settings.
- **HTTP cache.** A `hishel` SQLite cache under `HTTP_CACHE_DIR`. The limiter
  sits *below* the cache in the transport stack, so cache hits spend no tokens
  and never touch the source.
- **Raw-write-before-parse** is the caller's contract: take `response.content`
  to `raw_store.put_raw` before parsing it.

Stack, outermost first: retry → content check → cache → rate limit → network.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Self

import hishel
import httpx
from hishel.httpx import AsyncCacheTransport
from psycopg import Connection
from psycopg.rows import TupleRow
from psycopg_pool import ConnectionPool
from pyrate_limiter import Duration, InMemoryBucket, Limiter, PostgresBucket, Rate
from tenacity import (
    AsyncRetrying,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential_jitter,
)
from tenacity.wait import WaitBaseT

from distress_radar.settings import Settings

USER_AGENT = "distress-radar/0.1 (research; low-rate per-entity lookups)"


class SourceError(Exception):
    """Base for failures talking to an external source."""


class TransientSourceError(SourceError):
    """Worth retrying: network failure, throttling, server-side error."""


class PermanentSourceError(SourceError):
    """Retrying will not help: client error, malformed or unexpected response."""


class SourceNotFound(PermanentSourceError):
    """HTTP 404: the source does not know the requested record."""


class ContentCheckFailed(SourceError):
    """Response received but not real content (challenge page, JS gate, empty shell)."""

    def __init__(self, url: str, status_code: int, reason: str) -> None:
        super().__init__(f"content check failed for {url} (HTTP {status_code}): {reason}")
        self.url = url
        self.status_code = status_code
        self.reason = reason


ContentCheck = Callable[[httpx.Response], str | None]
"""Returns `None` if the response is real content, else a short reason string."""


@dataclass(frozen=True)
class SourcePolicy:
    """Per-source pacing and retry behaviour."""

    name: str
    requests_per_minute: int
    max_attempts: int = 4
    timeout_seconds: float = 30.0

    def rates(self) -> list[Rate]:
        return [Rate(self.requests_per_minute, Duration.MINUTE)]


def raise_for_status_class(response: httpx.Response) -> None:
    """Map HTTP status onto the transient/permanent taxonomy."""
    code = response.status_code
    if code == 429 or code >= 500:
        raise TransientSourceError(f"HTTP {code} from {response.request.url}")
    if code == 404:
        raise SourceNotFound(f"HTTP 404 from {response.request.url}")
    if code >= 400:
        raise PermanentSourceError(f"HTTP {code} from {response.request.url}")


def postgres_limiter(settings: Settings, policy: SourcePolicy) -> Limiter:
    """Persistent limiter: bucket table `ratelimit___<source>` in the manifest DB."""
    pool: ConnectionPool[Connection[TupleRow]] = ConnectionPool(
        settings.postgres_conninfo, min_size=1, max_size=2, open=True
    )
    bucket = PostgresBucket(pool, policy.name, policy.rates())
    return Limiter(bucket)


def in_memory_limiter(policy: SourcePolicy) -> Limiter:
    """Non-persistent limiter for tests and one-off notebooks without Postgres."""
    return Limiter(InMemoryBucket(policy.rates()))


class RateLimitedTransport(httpx.AsyncBaseTransport):
    """Acquires one limiter token per request that actually reaches the network."""

    def __init__(self, inner: httpx.AsyncBaseTransport, limiter: Limiter, name: str) -> None:
        self._inner = inner
        self._limiter = limiter
        self._name = name

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        await self._limiter.try_acquire_async(self._name)
        return await self._inner.handle_async_request(request)

    async def aclose(self) -> None:
        await self._inner.aclose()


class SourceClient:
    """Async HTTP client for one external source. Use as an async context manager."""

    def __init__(
        self,
        policy: SourcePolicy,
        client: httpx.AsyncClient,
        *,
        wait: WaitBaseT | None = None,
    ) -> None:
        self.policy = policy
        self._client = client
        self._wait: WaitBaseT = wait if wait is not None else wait_exponential_jitter(1, 60)

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._client.aclose()

    async def get(
        self,
        url: str,
        *,
        params: dict[str, str] | None = None,
        content_check: ContentCheck | None = None,
    ) -> httpx.Response:
        """GET with retries on transient failures; raises on permanent ones.

        Redirects are followed; the returned response's `.content` is fully read.
        """
        async for attempt in AsyncRetrying(
            retry=retry_if_exception_type(TransientSourceError),
            stop=stop_after_attempt(self.policy.max_attempts),
            wait=self._wait,
            reraise=True,
        ):
            with attempt:
                return await self._get_once(url, params, content_check)
        raise AssertionError("unreachable: AsyncRetrying reraises")  # pragma: no cover

    async def _get_once(
        self,
        url: str,
        params: dict[str, str] | None,
        content_check: ContentCheck | None,
    ) -> httpx.Response:
        try:
            response = await self._client.get(url, params=params)
        except (httpx.TransportError, httpx.DecodingError) as exc:
            raise TransientSourceError(f"{type(exc).__name__} fetching {url}: {exc}") from exc
        raise_for_status_class(response)
        if content_check is not None:
            reason = content_check(response)
            if reason is not None:
                raise ContentCheckFailed(str(response.url), response.status_code, reason)
        return response


def build_source_client(
    policy: SourcePolicy,
    *,
    cache_dir: Path,
    limiter: Limiter,
    transport: httpx.AsyncBaseTransport | None = None,
    wait: WaitBaseT | None = None,
    headers: dict[str, str] | None = None,
) -> SourceClient:
    """Assemble cache → rate limit → network for `policy`.

    `transport` replaces the network layer (tests pass `httpx.MockTransport`).
    """
    cache_dir.mkdir(parents=True, exist_ok=True)
    network = transport if transport is not None else httpx.AsyncHTTPTransport()
    storage = hishel.AsyncSqliteStorage(database_path=cache_dir / f"{policy.name}.sqlite")
    stack = AsyncCacheTransport(
        next_transport=RateLimitedTransport(network, limiter, policy.name),
        storage=storage,
    )
    client = httpx.AsyncClient(
        transport=stack,
        timeout=policy.timeout_seconds,
        follow_redirects=True,
        headers={"User-Agent": USER_AGENT, **(headers or {})},
    )
    return SourceClient(policy, client, wait=wait)
