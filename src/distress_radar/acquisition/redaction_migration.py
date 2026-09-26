"""Replace stored objects that still hold natural persons' data (ADR 0009 and its addenda).

For every raw object with personal-data markers (`redaction.personal_data_markers`, given the
filings a download holds):

1. redact it under the current version and check the result is clean: a download with
   `redact_download`, its members named from its `filing_index` rows' file names; an RDF
   detail with `redact_rdf_detail`;
2. store the redacted bytes as a new content-addressed object, with a sidecar naming the
   redaction and the hash of the file as first received, the file name as a token, and no
   `content-disposition`;
3. in one transaction, point every manifest reference at the new hash
   (`filing_index`, `raw_document_fetches`, `quarantine_events`), set a detail's rows'
   `file_name` to the token, drop the old file's `parsed_documents` rows (re-parsing recreates
   them), log the replacement in `raw_redactions`, and delete the old `raw_documents` row;
4. only then delete the old object and its sidecar.

**Downloads go first** (plan 0011 step E): their members are named from `file_name`, which the
detail phase replaces with tokens, so details are migrated only once no download is left. A
download of several filings whose names are already tokens cannot be named any more and is
refused.

Re-running finds nothing to do. Derived datasets must be re-materialized afterwards, because
`source_document_hash` and `source_member` change.

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
    file_token,
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


def _download_refs(conn: Connection) -> dict[str, list[str]]:
    """The filings each stored download holds, by its hash."""
    refs: dict[str, list[str]] = {}
    for sha, ref in conn.execute(
        "SELECT sha256, document_ref FROM filing_index WHERE sha256 IS NOT NULL ORDER BY 1, 2"
    ):
        refs.setdefault(sha, []).append(ref)
    return refs


def find_unredacted(conn: Connection, store: RedactableObjectStore) -> list[str]:
    """Objects with markers, downloads before everything else, each group by hash."""
    downloads = _download_refs(conn)
    hashes = [row[0] for row in conn.execute("SELECT sha256 FROM raw_documents ORDER BY sha256")]
    found = [
        sha for sha in hashes if personal_data_markers(store.get(raw_key(sha)), downloads.get(sha))
    ]
    return sorted(found, key=lambda sha: (sha not in downloads, sha))


def stored_markers(conn: Connection, store: RedactableObjectStore) -> dict[str, list[str]]:
    """Every stored object that still holds personal data, with its markers (none quote a name).

    The standing check behind invariant 6 (plan 0011 step F): the A3 assets run it after each
    materialization. About 20 s over the seed.
    """
    downloads = _download_refs(conn)
    return {
        sha: personal_data_markers(store.get(raw_key(sha)), downloads.get(sha))
        for sha in find_unredacted(conn, store)
    }


def _is_rdf_detail(data: bytes) -> bool:
    return data.lstrip().startswith(b"{") and b'"nazwaPliku"' in data


def _redact(conn: Connection, sha256: str, data: bytes) -> tuple[Redaction, str, list[str] | None]:
    """The current redaction of a stored object, its version, and the filings it holds."""
    if _is_rdf_detail(data):
        return redact_rdf_detail(data), RDF_DETAIL_REDACTION_VERSION, None
    names: dict[str, str | None] = dict(
        conn.execute(
            "SELECT document_ref, file_name FROM filing_index WHERE sha256 = %s ORDER BY 1",
            (sha256,),
        ).fetchall()
    )
    tokened = [
        ref for ref, name in names.items() if name is not None and name == file_token(ref, name)
    ]
    if len(names) > 1 and tokened:
        raise RuntimeError(
            f"{sha256}: filings {tokened} already hold tokens, so this download's members "
            "cannot be named; migrate downloads before details"
        )
    return redact_download(data, names or None), REDACTION_VERSION, list(names) or None


def replace_document(
    conn: Connection,
    store: RedactableObjectStore,
    old_sha256: str,
    *,
    run_id: str,
    now: datetime,
) -> Replacement:
    redaction, version, refs = _redact(conn, old_sha256, store.get(raw_key(old_sha256)))
    left = personal_data_markers(redaction.data, refs)
    if left:
        raise RuntimeError(f"{old_sha256}: redaction left personal data: {left}")
    old_meta = _stored_meta(store, old_sha256)
    token: str | None = None
    detail_ref: str | None = None
    if refs is None and _is_rdf_detail(redaction.data):
        detail = json.loads(redaction.data)
        token, detail_ref = detail.get("nazwaPliku"), detail.get("identyfikator")
    meta = old_meta.model_copy(
        update={
            "redaction_version": version,
            # The chain back to the file as first received, across redaction passes.
            "received_sha256": old_meta.received_sha256 or old_sha256,
            "original_filename": (
                file_token(refs[0], old_meta.original_filename)
                if refs is not None and len(refs) == 1
                else None
            ),
            "http_headers": {
                k: v for k, v in old_meta.http_headers.items() if k.lower() != "content-disposition"
            },
        }
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
        if detail_ref is not None:
            conn.execute(
                "UPDATE filing_index SET file_name = %s WHERE detail_sha256 = %s AND document_ref = %s",
                (token, old_sha256, detail_ref),
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
        downloads = _download_refs(conn)
        print(
            f"{len(found)} stored objects hold personal data "
            f"({sum(sha in downloads for sha in found)} downloads)"
        )
        if not args.apply:
            for sha in found:
                markers = personal_data_markers(store.get(raw_key(sha)), downloads.get(sha))
                print(f"  {sha}: {len(markers)} markers, e.g. {markers[:2]}")
            return 0
        # Downloads come first, and a failure raises: no detail is touched while one is left.
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
