"""Build the labelling queue from the stored statements, and export what is labelled to `evals/`
(plan 0013 step E). The logic is `extraction.golden`'s; this reads Postgres and MinIO and writes
files. Counts only are printed, never text.

    uv run python -m distress_radar.extraction.label_queue build [golden_sample_v1]
    uv run python -m distress_radar.extraction.label_queue export [golden_sample_v1]

`build` needs `make dev-up` and `make models`. It writes `<LABELLING_DIR>/<version>.jsonl`, local
and gitignored, and keeps every row already there as it is: labels and hand masking survive a
rebuild. `export` writes the labelled pages to `evals/text_signals/`, after the masking check.
Labelling itself is `notebooks/labelling/golden_set.py`.
"""

from __future__ import annotations

import sys
from collections.abc import Iterator
from pathlib import Path

import psycopg

from distress_radar.acquisition.raw_store import S3ObjectStore
from distress_radar.extraction import golden, masking, preprocessing
from distress_radar.parsing.containers import unwrap
from distress_radar.settings import Settings

DEFAULT_VERSION = "golden_sample_v1"


def queue_path(settings: Settings, version: str) -> Path:
    return settings.labelling_dir / f"{version}.jsonl"


def build(settings: Settings, version: str) -> golden.QueueStats:
    sample = golden.load_golden_sample(version)
    prefilter = preprocessing.load_prefilter(sample.prefilter_version)
    store = S3ObjectStore.from_endpoint(
        settings.minio_endpoint,
        settings.minio_access_key,
        settings.minio_secret_key.get_secret_value(),
        settings.minio_bucket,
    )
    with psycopg.connect(settings.postgres_conninfo) as conn:
        statements = conn.execute(
            "SELECT DISTINCT p.sha256, p.source_member, r.object_key FROM parsed_documents p"
            " JOIN raw_documents r ON r.sha256 = p.sha256 ORDER BY 1, 2"
        ).fetchall()
    stats = golden.QueueStats()

    def pages() -> Iterator[golden.TextPage]:
        for sha256, source_member, object_key in statements:
            members = {m.source_member: m for m in unwrap(store.get(object_key))}
            member = members.get(source_member)
            if member is None or not member.data.lstrip().startswith(b"<"):
                continue
            yield from golden.text_pages(sha256, source_member, member.data, stats)

    fresh = golden.build_queue(
        pages(), prefilter, sample, masking.load_model(), preprocessing.load_model(), stats
    )
    path = queue_path(settings, version)
    existing = golden.load_queue(path.read_bytes()) if path.exists() else []
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(golden.dump_queue(golden.merge(existing, fresh)))
    return stats


def update_queue(path: Path, row: golden.QueuePage) -> list[golden.QueuePage]:
    """The queue at `path` with `row` in place of the row with its page id, written back."""
    rows = golden.load_queue(path.read_bytes())
    if row.page_id not in {r.page_id for r in rows}:
        raise KeyError(f"{row.page_id} is not in {path}")
    rows = [row if r.page_id == row.page_id else r for r in rows]
    path.write_bytes(golden.dump_queue(rows))
    return rows


def export(settings: Settings, version: str, out_dir: Path = golden.GOLDEN_DIR) -> dict[str, int]:
    rows = golden.load_queue(queue_path(settings, version).read_bytes())
    files = golden.export(rows, masking.load_model())
    out_dir.mkdir(parents=True, exist_ok=True)
    for name, data in files.items():
        (out_dir / name).write_bytes(data)
    return {name: data.count(b"\n") for name, data in files.items()}


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if not args or args[0] not in {"build", "export"} or len(args) > 2:
        print(__doc__, file=sys.stderr)
        return 2
    version = args[1] if len(args) == 2 else DEFAULT_VERSION
    settings = Settings()
    if args[0] == "build":
        stats = build(settings, version)
        print(
            f"{version}: {stats.text_pages} text pages, {stats.selected} selected, "
            f"{stats.rejected_sampled} of {stats.rejected_pool} rejected sampled; "
            f"attachment errors {dict(stats.attachment_errors) or 'none'}; "
            f"pages the masker changes on a second pass: {stats.not_idempotent}"
        )
        rows = golden.load_queue(queue_path(settings, version).read_bytes())
        print(f"queue: {len(rows)} pages, {sum(r.complete for r in rows)} labelled")
    else:
        try:
            counts = export(settings, version)
        except ValueError as exc:
            print(f"not exported: {exc}", file=sys.stderr)
            return 1
        rows = golden.load_queue(queue_path(settings, version).read_bytes())
        replaced, missed = golden.masker_recall(rows)
        print(f"exported to {golden.GOLDEN_DIR}: {counts}")
        print(f"persons masked by the masker {replaced}, by hand {missed}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
