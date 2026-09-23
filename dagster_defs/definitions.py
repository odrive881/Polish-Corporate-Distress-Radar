"""Dagster Definitions entry point.

Run locally with:

    uv run dagster dev -m dagster_defs.definitions

(module form, run from the repo root — not `-f dagster_defs/definitions.py`,
which loads this file outside its package and breaks the absolute imports
below).

Orchestration wiring only: this module and its `assets/`/`checks/` siblings
import from `src/distress_radar/`, never the reverse. See
`DIRECTORY_STRUCTURE.md` §3 "The core boundary".

Resources are built from `distress_radar.settings.Settings` (environment /
`.env`), so they carry no Dagster config of their own. Assets reach them by
resource key (`postgres`, `raw_object_store`, `bir1`, `rdf_browser`, `krs_api`,
`msig_api`).
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager, contextmanager

import dagster as dg
import psycopg

from distress_radar.acquisition.base import (
    SourceClient,
    SourcePolicy,
    build_source_client,
    postgres_limiter,
)
from distress_radar.acquisition.document_retrieval import (
    RDF_SPA_SPEC,
    PlaywrightFilingBrowser,
    rdf_policy,
)
from distress_radar.acquisition.krs_extract import krs_api_policy
from distress_radar.acquisition.msig_client import msig_policy
from distress_radar.acquisition.raw_store import S3ObjectStore
from distress_radar.acquisition.regon_client import ENDPOINTS, ZeepBir1Service, bir1_policy
from distress_radar.settings import Settings


class PostgresResource(dg.ConfigurableResource):
    """Manifest database (B2). Connections are not autocommit; assets commit."""

    @contextmanager
    def connect(self) -> Iterator[psycopg.Connection]:
        with psycopg.connect(Settings().postgres_conninfo) as conn:
            yield conn


class RawObjectStoreResource(dg.ConfigurableResource):
    """Content-addressed raw store (B1) in MinIO."""

    def store(self) -> S3ObjectStore:
        settings = Settings()
        return S3ObjectStore.from_endpoint(
            settings.minio_endpoint,
            settings.minio_access_key,
            settings.minio_secret_key.get_secret_value(),
            settings.minio_bucket,
        )


class Bir1Resource(dg.ConfigurableResource):
    """GUS BIR1 (A2) with Postgres-persistent pacing. Test endpoint unless configured."""

    @contextmanager
    def service(self) -> Iterator[ZeepBir1Service]:
        settings = Settings()
        policy = bir1_policy(settings.bir1_requests_per_minute)
        limiter = postgres_limiter(settings, policy)
        try:
            with ZeepBir1Service(
                ENDPOINTS[settings.gus_bir1_endpoint],
                settings.bir1_api_key(),
                limiter=limiter,
                policy=policy,
            ) as service:
                yield service
        finally:
            limiter.close()


class RdfBrowserResource(dg.ConfigurableResource):
    """RDF (A3) through one serial Playwright Chromium context, Postgres-persistent pacing.

    The browser launches once per asset run and closes at the end (ADR 0007).
    Set `headless: false` in the launchpad to watch a run (recommended for the
    first live one); it needs a display (WSLg).
    """

    headless: bool = True

    @contextmanager
    def browser(self) -> Iterator[PlaywrightFilingBrowser]:
        settings = Settings()
        policy = rdf_policy(settings.rdf_requests_per_minute)
        limiter = postgres_limiter(settings, policy)
        try:
            with PlaywrightFilingBrowser(
                RDF_SPA_SPEC, limiter=limiter, policy=policy, headless=self.headless
            ) as browser:
                yield browser
        finally:
            limiter.close()


@asynccontextmanager
async def _paced_client(settings: Settings, policy: SourcePolicy) -> AsyncIterator[SourceClient]:
    limiter = postgres_limiter(settings, policy)
    try:
        async with build_source_client(
            policy, cache_dir=settings.http_cache_dir, limiter=limiter
        ) as client:
            yield client
    finally:
        limiter.close()


class KrsApiResource(dg.ConfigurableResource):
    """The open KRS API (A4) with Postgres-persistent pacing (ADR 0011)."""

    @asynccontextmanager
    async def client(self) -> AsyncIterator[SourceClient]:
        settings = Settings()
        policy = krs_api_policy(settings.krs_api_requests_per_minute)
        async with _paced_client(settings, policy) as client:
            yield client


class MsigApiResource(dg.ConfigurableResource):
    """The MSiG notice search API (A4) with Postgres-persistent pacing (ADR 0011)."""

    @asynccontextmanager
    async def client(self) -> AsyncIterator[SourceClient]:
        settings = Settings()
        policy = msig_policy(settings.msig_requests_per_minute)
        async with _paced_client(settings, policy) as client:
            yield client


from dagster_defs.assets.acquisition import acquisition_assets
from dagster_defs.assets.dq import dq_assets
from dagster_defs.assets.labels import label_assets
from dagster_defs.assets.legal import legal_assets
from dagster_defs.assets.parsing import parsing_assets
from dagster_defs.checks.accounting_identities import accounting_identity_checks

defs = dg.Definitions(
    assets=[*acquisition_assets, *parsing_assets, *dq_assets, *legal_assets, *label_assets],
    asset_checks=accounting_identity_checks,
    resources={
        "postgres": PostgresResource(),
        "raw_object_store": RawObjectStoreResource(),
        "bir1": Bir1Resource(),
        "rdf_browser": RdfBrowserResource(),
        "krs_api": KrsApiResource(),
        "msig_api": MsigApiResource(),
    },
)
