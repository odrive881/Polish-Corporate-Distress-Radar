"""B1 — content-addressed raw object store (AGENT_SPEC.md §6B, invariant 2).

Layout: `raw/sha256/<h[:2]>/<h>` holds the bytes exactly as downloaded;
`<key>.meta.json` holds first-fetch metadata. Objects are never overwritten:
re-putting known bytes is a no-op, and S3 writes are conditional
(`If-None-Match: *`) so a concurrent writer cannot clobber either key.

Repeat fetches of the same bytes are recorded in the B2 manifest
(`raw_document_fetches`), not by rewriting the sidecar.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import TYPE_CHECKING, Protocol

from botocore.exceptions import ClientError
from pydantic import BaseModel, ConfigDict, field_validator

if TYPE_CHECKING:
    from mypy_boto3_s3 import S3Client


class RawDocumentMeta(BaseModel):
    """Sidecar metadata for a raw object."""

    model_config = ConfigDict(frozen=True)

    source: str
    source_url: str
    content_type: str
    fetched_at: datetime
    http_headers: dict[str, str]
    ingestion_run_id: str
    original_filename: str | None = None
    # How the bytes were fetched, when not plain HTTP (A3: "playwright", ADR 0007).
    fetch_tier: str | None = None
    browser_version: str | None = None

    @field_validator("fetched_at")
    @classmethod
    def _aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("fetched_at must be timezone-aware (UTC)")
        return value


class ObjectStore(Protocol):
    def exists(self, key: str) -> bool: ...

    def put_if_absent(self, key: str, data: bytes, content_type: str) -> bool:
        """Write `data` unless `key` exists. Returns True if this call wrote it."""
        ...

    def get(self, key: str) -> bytes: ...


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def raw_key(sha256: str) -> str:
    return f"raw/sha256/{sha256[:2]}/{sha256}"


def sidecar_key(sha256: str) -> str:
    return f"{raw_key(sha256)}.meta.json"


def _sidecar_bytes(sha256: str, byte_size: int, meta: RawDocumentMeta) -> bytes:
    body = {"sha256": sha256, "byte_size": byte_size, **meta.model_dump(mode="json")}
    return json.dumps(body, sort_keys=True, ensure_ascii=False, indent=2).encode("utf-8")


def put_raw(store: ObjectStore, data: bytes, meta: RawDocumentMeta) -> str:
    """Store `data` unmodified under its SHA-256; return the hash.

    The sidecar is the completion marker: it is written after the object, so
    a crash between the two is repaired by the next put without overwriting.
    """
    digest = sha256_hex(data)
    key = raw_key(digest)
    if store.exists(sidecar_key(digest)):
        return digest
    store.put_if_absent(key, data, meta.content_type)
    store.put_if_absent(
        sidecar_key(digest), _sidecar_bytes(digest, len(data), meta), "application/json"
    )
    return digest


class InMemoryObjectStore:
    """`ObjectStore` for unit tests."""

    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}
        self.content_types: dict[str, str] = {}
        self.write_count = 0

    def exists(self, key: str) -> bool:
        return key in self.objects

    def put_if_absent(self, key: str, data: bytes, content_type: str) -> bool:
        if key in self.objects:
            return False
        self.objects[key] = bytes(data)
        self.content_types[key] = content_type
        self.write_count += 1
        return True

    def get(self, key: str) -> bytes:
        return self.objects[key]


class S3ObjectStore:
    """`ObjectStore` over the S3 API (MinIO locally). Creates the bucket on first use."""

    def __init__(self, client: S3Client, bucket: str) -> None:
        self._client = client
        self._bucket = bucket
        self._bucket_ready = False

    @classmethod
    def from_endpoint(
        cls, endpoint_url: str, access_key: str, secret_key: str, bucket: str
    ) -> S3ObjectStore:
        import boto3

        client: S3Client = boto3.client(  # pyright: ignore[reportUnknownMemberType]
            "s3",
            endpoint_url=endpoint_url,
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
            region_name="us-east-1",
        )
        return cls(client, bucket)

    def _ensure_bucket(self) -> None:
        if self._bucket_ready:
            return
        try:
            self._client.head_bucket(Bucket=self._bucket)
        except ClientError as exc:
            if _error_code(exc) not in {"404", "NoSuchBucket", "NotFound"}:
                raise
            try:
                self._client.create_bucket(Bucket=self._bucket)
            except ClientError as create_exc:
                if _error_code(create_exc) not in {"BucketAlreadyOwnedByYou"}:
                    raise
        self._bucket_ready = True

    def exists(self, key: str) -> bool:
        self._ensure_bucket()
        try:
            self._client.head_object(Bucket=self._bucket, Key=key)
        except ClientError as exc:
            if _error_code(exc) in {"404", "NoSuchKey", "NotFound"}:
                return False
            raise
        return True

    def put_if_absent(self, key: str, data: bytes, content_type: str) -> bool:
        self._ensure_bucket()
        try:
            self._client.put_object(
                Bucket=self._bucket,
                Key=key,
                Body=data,
                ContentType=content_type,
                IfNoneMatch="*",
            )
        except ClientError as exc:
            if _error_code(exc) in {"412", "PreconditionFailed"}:
                return False
            raise
        return True

    def get(self, key: str) -> bytes:
        return self._client.get_object(Bucket=self._bucket, Key=key)["Body"].read()


def _error_code(exc: ClientError) -> str:
    return str(exc.response.get("Error", {}).get("Code", ""))
