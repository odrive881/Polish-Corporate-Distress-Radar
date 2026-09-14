import hashlib
import json
from datetime import UTC, datetime

import pytest

from distress_radar.acquisition.raw_store import (
    InMemoryObjectStore,
    RawDocumentMeta,
    put_raw,
    raw_key,
    sidecar_key,
)


def _meta(run_id: str = "run-1", url: str = "https://example.test/doc.xml") -> RawDocumentMeta:
    return RawDocumentMeta(
        source="test",
        source_url=url,
        content_type="application/xml",
        fetched_at=datetime(2026, 9, 14, 12, 0, tzinfo=UTC),
        http_headers={"etag": '"abc"'},
        ingestion_run_id=run_id,
    )


def test_content_addressing_and_key_layout():
    store = InMemoryObjectStore()
    data = b"<root>\r\n  <dane/>\r\n</root>"

    digest = put_raw(store, data, _meta())

    assert digest == hashlib.sha256(data).hexdigest()
    assert raw_key(digest) == f"raw/sha256/{digest[:2]}/{digest}"
    assert sidecar_key(digest) == f"raw/sha256/{digest[:2]}/{digest}.meta.json"
    assert store.get(raw_key(digest)) == data  # bytes unmodified, CRLF included
    assert set(store.objects) == {raw_key(digest), sidecar_key(digest)}


def test_sidecar_carries_fetch_metadata():
    store = InMemoryObjectStore()
    digest = put_raw(store, b"payload", _meta())

    sidecar = json.loads(store.get(sidecar_key(digest)))

    assert sidecar["sha256"] == digest
    assert sidecar["byte_size"] == len(b"payload")
    assert sidecar["source_url"] == "https://example.test/doc.xml"
    assert sidecar["content_type"] == "application/xml"
    assert sidecar["fetched_at"] == "2026-09-14T12:00:00Z"
    assert sidecar["http_headers"] == {"etag": '"abc"'}
    assert sidecar["ingestion_run_id"] == "run-1"


def test_idempotent_reput_leaves_object_and_sidecar_unchanged():
    store = InMemoryObjectStore()
    digest = put_raw(store, b"payload", _meta())
    before = dict(store.objects)
    writes = store.write_count

    again = put_raw(store, b"payload", _meta(run_id="run-2", url="https://other.test/x"))

    assert again == digest
    assert store.objects == before
    assert store.write_count == writes


def test_crash_between_object_and_sidecar_is_repaired_without_overwrite():
    store = InMemoryObjectStore()
    data = b"payload"
    digest = hashlib.sha256(data).hexdigest()
    store.put_if_absent(raw_key(digest), data, "application/xml")  # object only, no sidecar

    put_raw(store, data, _meta())

    assert store.get(raw_key(digest)) == data
    assert store.exists(sidecar_key(digest))


def test_naive_fetch_timestamp_is_rejected():
    with pytest.raises(ValueError, match="timezone-aware"):
        RawDocumentMeta(
            source="test",
            source_url="u",
            content_type="application/xml",
            fetched_at=datetime(2026, 9, 14, 12, 0),  # noqa: DTZ001 - deliberately naive
            http_headers={},
            ingestion_run_id="run-1",
        )
