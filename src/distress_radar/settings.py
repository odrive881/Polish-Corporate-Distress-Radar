"""Typed runtime settings, read from the environment and `.env`.

Variable names match `.env.example`. Defaults mirror the `docker-compose.yml`
fallbacks so `make dev-up` works without a `.env`. Empty values (as left by a
freshly copied `.env.example`) are ignored and fall back to the defaults.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

# Published by GUS for the BIR1 test environment; not a secret.
BIR1_PUBLIC_TEST_KEY = "abcde12345abcde12345"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_ignore_empty=True,
        extra="ignore",
    )

    postgres_host: str = "localhost"
    postgres_port: int = 5432
    postgres_db: str = "distress_radar"
    postgres_user: str = "distress_radar"
    postgres_password: SecretStr = SecretStr("distress_radar")

    minio_endpoint: str = "http://localhost:9000"
    minio_access_key: str = "distress_radar"
    minio_secret_key: SecretStr = SecretStr("distress_radar_secret")
    minio_bucket: str = "distress-radar-raw"

    gus_bir1_api_key: SecretStr | None = None
    gus_bir1_endpoint: Literal["test", "prod"] = "test"

    http_cache_dir: Path = Path(".cache/http")
    # Where HAR files of manual RDF sessions are dropped for import (gitignored).
    rdf_manual_inbox: Path = Path(".cache/rdf_inbox")

    # Per-source request pacing (A2/A3).
    # BIR1 publishes no rate limit (ADR 0004): deliberately conservative.
    bir1_requests_per_minute: int = 30
    # RDF: KRS support confirmed, informally, 3 documents/minute for a
    # non-invasive automation script (ADR 0007). Counted per RDF request —
    # every filing-list open and every document download spends one token, not
    # one per entity. Do not raise without a new ADR.
    rdf_requests_per_minute: int = 3

    @property
    def postgres_conninfo(self) -> str:
        return (
            f"host={self.postgres_host} port={self.postgres_port} dbname={self.postgres_db} "
            f"user={self.postgres_user} password={self.postgres_password.get_secret_value()}"
        )

    def bir1_api_key(self) -> str:
        """The configured BIR1 key; the public test key when on the test endpoint."""
        if self.gus_bir1_api_key is not None:
            return self.gus_bir1_api_key.get_secret_value()
        if self.gus_bir1_endpoint == "test":
            return BIR1_PUBLIC_TEST_KEY
        raise ValueError("GUS_BIR1_API_KEY is required when GUS_BIR1_ENDPOINT=prod")
