"""Replace stored downloads that still hold natural persons' data (ADR 0009).

For every raw object with personal-data markers:

1. redact it (`redaction.redact_download`) and check the result is clean;
2. store the redacted bytes as a new content-addressed object, with a sidecar
   naming the redaction and the replaced hash;
3. in one transaction, point every manifest reference at the new hash
   (`filing_index`, `raw_document_fetches`, `quarantine_events`), drop the old file's
   `parsed_documents` rows (re-parsing recreates them), log the replacement in
   `raw_redactions`, and delete the old `raw_documents` row;
4. only then delete the old object and its sidecar.

Re-running finds nothing to do. Derived datasets must be re-materialized
afterwards, because `source_document_hash` changes.

Run: `uv run python -m distress_radar.acquisition.redaction_migration [--apply]`
(without `--apply` it only reports).
"""

from __future__ import annotations

import argparse
import json
import sys
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

import psycopg
from psycopg import Connection

from distress_radar.acquisition import manifest
from distress_radar.acquisition.raw_store import (
    RawDocumentMeta,
    RedactableObjectStore,
    S3ObjectStore,
    put_raw,
    raw_key,
    sidecar_key,
)
from distress_radar.acquisition.redaction import (
    RDF_DETAIL_REDACTION_VERSION,
    REDACTION_VERSION,
    Redaction,
    personal_data_markers,
    redact_download,
    redact_rdf_detail,
)
from distress_radar.settings import Settings


@dataclass(frozen=True)
class Replacement:
    old_sha256: str
    new_sha256: str
    actions: tuple[str, ...]


def _stored_meta(store: RedactableObjectStore, sha256: str) -> RawDocumentMeta:
    body = json.loads(store.get(sidecar_key(sha256)))
    fields = set(RawDocumentMeta.model_fields)
    return RawDocumentMeta.model_validate({k: v for k, v in body.items() if k in fields})


def find_unredacted(conn: Connection, store: RedactableObjectStore) -> list[str]:
    hashes = [row[0] for row in conn.execute("SELECT sha256 FROM raw_documents ORDER BY sha256")]
    return [sha for sha in hashes if personal_data_markers(store.get(raw_key(sha)))]


def _redact(conn: Connection, sha256: str, data: bytes) -> tuple[Redaction, str]:
    """The current redaction of a stored object, and its version.

    A download's members are named from the file names its `filing_index` rows hold. Plan 0011
    step E re-derives those rows' `file_name` (and every derived path) with the objects.
    """
    if data.lstrip().startswith(b"{"):
        return redact_rdf_detail(data), RDF_DETAIL_REDACTION_VERSION
    names: dict[str, str | None] = dict(
        conn.execute(
            "SELECT document_ref, file_name FROM filing_index WHERE sha256 = %s ORDER BY 1",
            (sha256,),
        ).fetchall()
    )
    return redact_download(data, names or None), REDACTION_VERSION


def replace_document(
    conn: Connection,
    store: RedactableObjectStore,
    old_sha256: str,
    *,
    run_id: str,
    now: datetime,
) -> Replacement:
    redaction, version = _redact(conn, old_sha256, store.get(raw_key(old_sha256)))
    left = personal_data_markers(redaction.data)
    if left:
        raise RuntimeError(f"{old_sha256}: redaction left personal data: {left}")
    meta = _stored_meta(store, old_sha256).model_copy(
        update={"redaction_version": version, "received_sha256": old_sha256}
    )
    new_sha256 = put_raw(store, redaction.data, meta)
    with conn.transaction():
        conn.execute(
            """
            INSERT INTO raw_documents
                (sha256, object_key, byte_size, content_type, first_fetched_at, first_ingestion_run_id)
            SELECT %s, %s, %s, content_type, first_fetched_at, first_ingestion_run_id
            FROM raw_documents WHERE sha256 = %s
            ON CONFLICT DO NOTHING
            """,
            (new_sha256, raw_key(new_sha256), len(redaction.data), old_sha256),
        )
        conn.execute(
            """
            INSERT INTO raw_document_fetches (sha256, source_url, fetched_at, ingestion_run_id, source)
            SELECT %s, source_url, fetched_at, ingestion_run_id, source
            FROM raw_document_fetches WHERE sha256 = %s
            ON CONFLICT DO NOTHING
            """,
            (new_sha256, old_sha256),
        )
        conn.execute("DELETE FROM raw_document_fetches WHERE sha256 = %s", (old_sha256,))
        conn.execute(
            "UPDATE filing_index SET sha256 = %s WHERE sha256 = %s", (new_sha256, old_sha256)
        )
        conn.execute(
            "UPDATE filing_index SET detail_sha256 = %s WHERE detail_sha256 = %s",
            (new_sha256, old_sha256),
        )
        conn.execute(
            "UPDATE quarantine_events SET source_document_hash = %s WHERE source_document_hash = %s",
            (new_sha256, old_sha256),
        )
        if conn.execute("SELECT to_regclass('parsed_documents')").fetchone() != (None,):
            conn.execute("DELETE FROM parsed_documents WHERE sha256 = %s", (old_sha256,))
        manifest.insert_redaction(
            conn,
            received_sha256=old_sha256,
            redacted_sha256=new_sha256,
            redaction_version=version,
            redacted_at=now,
            ingestion_run_id=run_id,
        )
        conn.execute("DELETE FROM raw_documents WHERE sha256 = %s", (old_sha256,))
    store.delete_for_redaction(raw_key(old_sha256))
    store.delete_for_redaction(sidecar_key(old_sha256))
    return Replacement(old_sha256, new_sha256, tuple(redaction.actions))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n", 1)[0])
    parser.add_argument(
        "--apply", action="store_true", help="replace the objects (default: report only)"
    )
    args = parser.parse_args(argv)
    settings = Settings()
    store = S3ObjectStore.from_endpoint(
        settings.minio_endpoint,
        settings.minio_access_key,
        settings.minio_secret_key.get_secret_value(),
        settings.minio_bucket,
    )
    run_id = f"redaction-{uuid.uuid4().hex[:8]}"
    with psycopg.connect(settings.postgres_conninfo) as conn:
        manifest.ensure_schema(conn)
        conn.commit()
        found = find_unredacted(conn, store)
        print(f"{len(found)} stored objects hold personal data")
        if not args.apply:
            for sha in found:
                print(f"  {sha}: {personal_data_markers(store.get(raw_key(sha)))[:3]}")
            return 0
        for sha in found:
            replaced = replace_document(conn, store, sha, run_id=run_id, now=datetime.now(UTC))
            print(
                f"  {replaced.old_sha256[:12]} -> {replaced.new_sha256[:12]}: {len(replaced.actions)} actions"
            )
        left = find_unredacted(conn, store)
        print(f"run {run_id}: {len(found)} replaced, {len(left)} still hold personal data")
        return 1 if left else 0


if __name__ == "__main__":
    sys.exit(main())
