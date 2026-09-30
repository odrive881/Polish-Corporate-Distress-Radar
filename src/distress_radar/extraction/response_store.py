"""Stored model responses, content-addressed (plan 0013 step F, decision 3).

A model's answer is not deterministic, and invariant 5 wants reruns to give the same bytes. So every
response is kept, keyed by the SHA-256 of its full request body (model, effort, prompt, output
schema, masked page), written once and never overwritten, and an extraction is always read back from
the store. A run calls the API only for keys the store lacks; a new prompt, model or schema is a new
key. The body is the API's response as JSON, unchanged.

Objects sit in the raw bucket under `extraction/responses/`, beside `raw/`; their Postgres manifest
is `extraction_responses` (same DDL conventions as `acquisition/manifest.py`, ADR 0006). What is
stored holds masked text only: the request is built from masked pages (ADR 0009, third addendum).
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from typing import Any, LiteralString

from psycopg import Connection

from distress_radar.acquisition.raw_store import ObjectStore

PREFIX = "extraction/responses"

SCHEMA_DDL: tuple[LiteralString, ...] = (
    """
    CREATE TABLE IF NOT EXISTS extraction_responses (
        request_key      text PRIMARY KEY,
        object_key       text NOT NULL,
        model            text NOT NULL,
        prompt           text NOT NULL,
        schema_version   text NOT NULL,
        page_sha256      text NOT NULL,
        stop_reason      text,
        first_stored_at  timestamptz NOT NULL
    )
    """,
)


def canonical(params: dict[str, Any]) -> bytes:
    return json.dumps(params, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()


def request_key(params: dict[str, Any]) -> str:
    """SHA-256 of the request body in canonical JSON: the response's address."""
    return hashlib.sha256(canonical(params)).hexdigest()


def object_key(key: str) -> str:
    return f"{PREFIX}/{key[:2]}/{key}.json"


@dataclass(frozen=True)
class ResponseMeta:
    request_key: str
    model: str
    prompt: str  # the prompt's file name, without `.md`
    schema_version: str
    page_sha256: str  # of the masked page text
    stop_reason: str | None
    stored_at: datetime


class ResponseStore:
    """Responses in the object store, write-once. `new` lists what this process stored."""

    def __init__(self, objects: ObjectStore) -> None:
        self.objects = objects
        self.new: list[ResponseMeta] = []

    def get(self, key: str) -> bytes | None:
        return self.objects.get(object_key(key)) if self.objects.exists(object_key(key)) else None

    def put(self, meta: ResponseMeta, body: bytes) -> None:
        if self.objects.put_if_absent(object_key(meta.request_key), body, "application/json"):
            self.new.append(meta)


def ensure_schema(conn: Connection) -> None:
    for statement in SCHEMA_DDL:
        conn.execute(statement)


def record(conn: Connection, metas: Iterable[ResponseMeta]) -> int:
    """Manifest rows for stored responses; an existing row is left as it is."""
    rows = [
        (
            m.request_key,
            object_key(m.request_key),
            m.model,
            m.prompt,
            m.schema_version,
            m.page_sha256,
            m.stop_reason,
            m.stored_at,
        )
        for m in metas
    ]
    with conn.cursor() as cur:
        cur.executemany(
            "INSERT INTO extraction_responses (request_key, object_key, model, prompt,"
            " schema_version, page_sha256, stop_reason, first_stored_at)"
            " VALUES (%s, %s, %s, %s, %s, %s, %s, %s) ON CONFLICT (request_key) DO NOTHING",
            rows,
        )
    return len(rows)
