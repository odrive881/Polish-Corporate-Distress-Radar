"""Build the labelling queues from the stored statements and auditor reports, and export what is
labelled to `evals/` (plan 0013 step E, decision 0c). The logic is `extraction.golden`'s; this
reads Postgres and MinIO and writes files. Counts only are printed, never text.

    uv run python -m distress_radar.extraction.label_queue build [golden_sample_v<n>]
    uv run python -m distress_radar.extraction.label_queue export

`build` needs `make dev-up` and `make models`. For each sample version (`golden_sample_v*.yaml`),
or the one named, it writes `<LABELLING_DIR>/<version>.jsonl`, local and gitignored, and keeps
every row already there as it is: labels and hand masking survive a rebuild. `export` writes the
labelled pages of every local queue to `evals/text_signals/`, after the masking check, and refuses
to drop a page already exported. Labelling itself is `notebooks/labelling/golden_set.py`.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Iterator
from pathlib import Path

import psycopg

from distress_radar.acquisition.document_retrieval import load_document_types
from distress_radar.acquisition.raw_store import ObjectStore, S3ObjectStore
from distress_radar.acquisition.report_import import auditor_codes
from distress_radar.extraction import golden, manifest, masking, preprocessing, rules
from distress_radar.parsing.canonical_schema import CONFIG_DIR
from distress_radar.parsing.containers import ContainerError, unwrap
from distress_radar.settings import Settings

DEFAULT_VERSION = "golden_sample_v1"


def versions(config_dir: Path = CONFIG_DIR) -> list[str]:
    """Every golden sample version, oldest first."""
    paths = (config_dir / "extraction").glob("golden_sample_v*.yaml")
    return sorted((p.stem for p in paths), key=lambda v: int(v.rsplit("_v", 1)[1]))


def queue_path(settings: Settings, version: str) -> Path:
    return settings.labelling_dir / f"{version}.jsonl"


def _notes_pages(
    conn: psycopg.Connection, store: ObjectStore, stats: golden.QueueStats
) -> Iterator[golden.TextPage]:
    statements = conn.execute(
        "SELECT DISTINCT p.sha256, p.source_member, r.object_key FROM parsed_documents p"
        " JOIN raw_documents r ON r.sha256 = p.sha256 ORDER BY 1, 2"
    ).fetchall()
    for sha256, source_member, object_key in statements:
        members = {m.source_member: m for m in unwrap(store.get(object_key))}
        member = members.get(source_member)
        if member is None or not member.data.lstrip().startswith(b"<"):
            continue
        yield from golden.text_pages(sha256, source_member, member.data, stats)


def _report_pages(
    conn: psycopg.Connection, store: ObjectStore, stats: golden.QueueStats
) -> Iterator[list[golden.TextPage]]:
    """Each stored auditor report the text stage reads (dated: decision 6), as its pages."""
    for report in manifest.report_sources(conn, auditor_codes(load_document_types())):
        if report.submission_date is None:
            continue
        try:
            pdfs = [m for m in unwrap(store.get(report.object_key)) if m.kind == "pdf"]
        except ContainerError:
            pdfs = []
        if len(pdfs) != 1:
            stats.attachment_errors["report_not_one_pdf"] += 1
            continue
        yield golden.pdf_text_pages(report.sha256, pdfs[0].source_member, pdfs[0].data, stats)


def build(settings: Settings, version: str) -> golden.QueueStats:
    sample = golden.load_golden_sample(version)
    prefilter = preprocessing.load_prefilter(sample.prefilter_version)
    store = S3ObjectStore.from_endpoint(
        settings.minio_endpoint,
        settings.minio_access_key,
        settings.minio_secret_key.get_secret_value(),
        settings.minio_bucket,
    )
    stats = golden.QueueStats()
    mask_nlp, lemma_nlp = masking.load_model(), preprocessing.load_model()
    with psycopg.connect(settings.postgres_conninfo) as conn:
        if sample.report_sample is not None:
            fresh = golden.build_report_queue(
                _report_pages(conn, store, stats),
                prefilter,
                rules.load_rules(sample.report_sample.rules_version),
                sample,
                mask_nlp,
                lemma_nlp,
                stats,
            )
        else:
            fresh = golden.build_queue(
                _notes_pages(conn, store, stats), prefilter, sample, mask_nlp, lemma_nlp, stats
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


def _queued(settings: Settings) -> list[golden.QueuePage]:
    """The rows of every local queue."""
    return [
        row
        for version in versions()
        if (path := queue_path(settings, version)).exists()
        for row in golden.load_queue(path.read_bytes())
    ]


def export(settings: Settings, out_dir: Path = golden.GOLDEN_DIR) -> dict[str, int]:
    """Every local queue's labelled pages to `out_dir`. Raises when a page already exported there
    is not among them (a queue missing on this machine, say): an export never drops a label."""
    rows = _queued(settings)
    files = golden.export(rows, masking.load_model())
    committed = out_dir / golden.PAGES_FILE
    if committed.exists():
        exported = {
            json.loads(line)["page_id"]
            for line in files[golden.PAGES_FILE].decode("utf-8").splitlines()
        }
        missing = [
            pid
            for line in committed.read_text(encoding="utf-8").splitlines()
            if line.strip() and (pid := json.loads(line)["page_id"]) not in exported
        ]
        if missing:
            raise ValueError(
                f"{len(missing)} page(s) in {committed} are in no labelled local queue "
                f"(e.g. {missing[:3]}); build or restore their queue before exporting"
            )
    out_dir.mkdir(parents=True, exist_ok=True)
    for name, data in files.items():
        (out_dir / name).write_bytes(data)
    return {name: data.count(b"\n") for name, data in files.items()}


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if not args or args[0] not in {"build", "export"} or len(args) > 2:
        print(__doc__, file=sys.stderr)
        return 2
    settings = Settings()
    if args[0] == "build":
        for version in args[1:] or versions():
            stats = build(settings, version)
            drawn = (
                f"{stats.reports_sampled} of {stats.reports} reports "
                f"({stats.reports_modified} read as a modified opinion)"
                if stats.reports
                else f"{stats.rejected_sampled} of {stats.rejected_pool} rejected sampled"
            )
            print(
                f"{version}: {stats.text_pages} text pages, {stats.selected} selected, {drawn}; "
                f"attachment errors {dict(stats.attachment_errors) or 'none'}; "
                f"pages the masker changes on a second pass: {stats.not_idempotent}"
            )
            rows = golden.load_queue(queue_path(settings, version).read_bytes())
            print(f"queue: {len(rows)} pages, {sum(r.complete for r in rows)} labelled")
    else:
        if len(args) > 1:
            print("export takes no version: it writes every local queue", file=sys.stderr)
            return 2
        try:
            counts = export(settings)
        except ValueError as exc:
            print(f"not exported: {exc}", file=sys.stderr)
            return 1
        replaced, missed = golden.masker_recall(_queued(settings))
        print(f"exported to {golden.GOLDEN_DIR}: {counts}")
        print(f"persons masked by the masker {replaced}, by hand {missed}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
