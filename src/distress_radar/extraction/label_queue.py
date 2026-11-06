"""Build the labelling queues from the stored statements and auditor reports, and export what is
labelled to `evals/` (plan 0013 step E, decision 0c). The logic is `extraction.golden`'s; this
reads Postgres and MinIO and writes files. Counts only are printed, never text.

    uv run python -m distress_radar.extraction.label_queue build [golden_sample_v<n>]
    uv run python -m distress_radar.extraction.label_queue export
    uv run python -m distress_radar.extraction.label_queue clear
    uv run python -m distress_radar.extraction.label_queue review [golden_sample_v<n>]

`review` (`make label-review`) prints where a stratified queue stands: its cut and the targets'
positives, the labels that differ from the model's answer with their reasons (ids only), agreement
shown and blind, the rechecks' agreement, and the median time a page (plan 0013 decisions 16–23).

`build` needs `make dev-up` and `make models`. For each sample version (`golden_sample_v*.yaml`),
or the one named, it writes `<LABELLING_DIR>/<version>.jsonl`, local and gitignored, and keeps
every row already there as it is: labels and hand masking survive a rebuild. A targeted sample
leaves out the pages already in another local queue or in the committed golden set. A stratified
sample (plan 0013 decision 12) reads its draw from `universe_draws`, and the strata from the
answering extractor's `text_signals` and `text_coverage` in `WAREHOUSE_DIR`: run the `text` job
first. It leaves nothing out: a drawn page labelled in another queue carries its labels over. `export` writes the
labelled pages of every local queue to `evals/text_signals/`, after the masking check, and refuses
to drop a page already exported. A span the masker flags that the owner saved a label over without
masking is recorded as reviewed (`golden.clear_reviewed`) and no longer blocks the export; `clear`
does the same for every labelled page at once, for pages saved before that. Labelling itself is `notebooks/labelling/golden_set.py`.
"""

from __future__ import annotations

import json
import sys
from collections import Counter
from collections.abc import Iterator
from datetime import UTC, datetime
from fractions import Fraction
from pathlib import Path

import polars as pl
import psycopg

from distress_radar.acquisition.document_retrieval import load_document_types
from distress_radar.acquisition.raw_store import ObjectStore, S3ObjectStore
from distress_radar.acquisition.report_import import auditor_codes
from distress_radar.extraction import extractor as ex
from distress_radar.extraction import golden, manifest, masking, preprocessing, rules
from distress_radar.extraction import text_signals as ts
from distress_radar.models.dataset import WEIGHT
from distress_radar.models.splits import WeightsConfig
from distress_radar.models.weights import read_draw_weights
from distress_radar.parsing.canonical_schema import CONFIG_DIR
from distress_radar.parsing.containers import ContainerError, unwrap
from distress_radar.settings import Settings
from distress_radar.warehouse import read_dataset

DEFAULT_VERSION = "golden_sample_v1"


def versions(config_dir: Path = CONFIG_DIR) -> list[str]:
    """Every golden sample version, oldest first."""
    paths = (config_dir / "extraction").glob("golden_sample_v*.yaml")
    return sorted((p.stem for p in paths), key=lambda v: int(v.rsplit("_v", 1)[1]))


def queue_path(settings: Settings, version: str) -> Path:
    return settings.labelling_dir / f"{version}.jsonl"


def drawn_from(conn: psycopg.Connection, sample: golden.GoldenSample) -> frozenset[str] | None:
    """The KRS numbers of the lists `sample` is drawn from, or None when it names none."""
    if sample.discovery_sources is None:
        return None
    rows = conn.execute(
        "SELECT DISTINCT trim(krs) FROM universe_candidates WHERE discovery_source = ANY(%s)",
        (list(sample.discovery_sources),),
    ).fetchall()
    return frozenset(str(r[0]) for r in rows)


def _notes_pages(
    conn: psycopg.Connection,
    store: ObjectStore,
    stats: golden.QueueStats,
    entities: frozenset[str] | None = None,
) -> Iterator[golden.TextPage]:
    statements = conn.execute(
        "SELECT DISTINCT p.sha256, p.source_member, r.object_key, trim(p.krs) FROM parsed_documents p"
        " JOIN raw_documents r ON r.sha256 = p.sha256 ORDER BY 1, 2"
    ).fetchall()
    for sha256, source_member, object_key, krs in statements:
        if entities is not None and krs not in entities:
            continue
        try:
            members = {m.source_member: m for m in unwrap(store.get(object_key))}
        except ContainerError as exc:
            stats.attachment_errors[exc.reason_code] += 1
            continue
        member = members.get(source_member)
        if member is None or not member.data.lstrip().startswith(b"<"):
            continue
        yield from golden.text_pages(sha256, source_member, member.data, stats)


def _text_source_pages(
    conn: psycopg.Connection,
    store: ObjectStore,
    stats: golden.QueueStats,
    entities: frozenset[str] | None = None,
) -> Iterator[tuple[str, golden.TextPage]]:
    """The notes' pages, with their entity's KRS, of every statement the text stage reads
    (`manifest.text_sources`), so a sample measures the extractor on its own population; a
    download that cannot be unwrapped is counted, as the reports' reader does."""
    for source in manifest.text_sources(conn):
        if entities is not None and source.krs not in entities:
            continue
        try:
            members = {m.source_member: m for m in unwrap(store.get(source.object_key))}
        except ContainerError as exc:
            stats.attachment_errors[exc.reason_code] += 1
            continue
        member = members.get(source.source_member)
        if member is None or not member.data.lstrip().startswith(b"<"):
            stats.attachment_errors["member_not_xml"] += 1
            continue
        for page in golden.text_pages(source.sha256, source.source_member, member.data, stats):
            yield source.krs, page


def _report_pages(
    conn: psycopg.Connection,
    store: ObjectStore,
    stats: golden.QueueStats,
    entities: frozenset[str] | None = None,
) -> Iterator[tuple[str, list[golden.TextPage]]]:
    """Each stored auditor report the text stage reads (dated: decision 6), as its entity's KRS
    and its pages."""
    for report in manifest.report_sources(conn, auditor_codes(load_document_types())):
        if report.submission_date is None:
            continue
        if entities is not None and report.krs not in entities:
            continue
        try:
            pdfs = [m for m in unwrap(store.get(report.object_key)) if m.kind == "pdf"]
        except ContainerError:
            pdfs = []
        if len(pdfs) != 1:
            stats.attachment_errors["report_not_one_pdf"] += 1
            continue
        yield (
            report.krs,
            golden.pdf_text_pages(report.sha256, pdfs[0].source_member, pdfs[0].data, stats),
        )


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
        entities = drawn_from(conn, sample)
        if sample.report_sample is not None:
            fresh = golden.build_report_queue(
                (pages for _krs, pages in _report_pages(conn, store, stats, entities)),
                prefilter,
                rules.load_rules(sample.report_sample.rules_version),
                sample,
                mask_nlp,
                lemma_nlp,
                stats,
            )
        elif sample.targeted is not None:
            fresh = golden.build_targeted_queue(
                (page for _krs, page in _text_source_pages(conn, store, stats, entities)),
                prefilter,
                sample,
                mask_nlp,
                lemma_nlp,
                stats,
                elsewhere(settings, version),
            )
        elif sample.stratified is not None:
            frame = stratified_frame(conn, settings, sample, version)
            pages = (
                _text_source_pages(conn, store, stats, frozenset(frame.entity_pi))
                if sample.document_kind == "statement_notes"
                else (
                    (krs, page)
                    for krs, report in _report_pages(conn, store, stats, frozenset(frame.entity_pi))
                    for page in report
                )
            )
            fresh = golden.build_stratified_queue(
                pages, prefilter, sample, frame, mask_nlp, lemma_nlp, stats
            )
        else:
            fresh = golden.build_queue(
                _notes_pages(conn, store, stats, entities),
                prefilter,
                sample,
                mask_nlp,
                lemma_nlp,
                stats,
            )
    path = queue_path(settings, version)
    existing = golden.load_queue(path.read_bytes()) if path.exists() else []
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(golden.dump_queue(golden.merge(existing, fresh)))
    return stats


def stratified_frame(
    conn: psycopg.Connection, settings: Settings, sample: golden.GoldenSample, version: str
) -> golden.StratifiedFrame:
    """What a stratified sample reads beside the pages: its draw's π per entity, the pages the
    answering extractor answered present for the sample's signals, the documents it read, and
    the pages labelled in the other local queues."""
    design = sample.stratified
    if design is None:
        raise ValueError(f"{version} is not a stratified sample")
    answering = ex.load_extractor(design.answers)
    if answering.config.prefilter_version != sample.prefilter_version:
        raise ValueError(
            f"{version} samples {sample.prefilter_version}, but {design.answers}'s answers are "
            f"{answering.config.prefilter_version}'s"
        )
    draw = read_draw_weights(
        conn,
        WeightsConfig(
            draw_spec_sha256=design.draw.draw_spec_sha256,
            random=design.draw.random,
            distress=design.draw.distress,
            bootstrap="stratified",
        ),
    )
    entity_pi = {
        str(krs): 1 / Fraction(weight)
        for krs, weight in draw.frame.select("krs", WEIGHT).iter_rows()
    }
    ours = (pl.col("extractor_version") == design.answers) & (
        pl.col("document_kind") == sample.document_kind
    )
    kept = read_dataset(settings.warehouse_dir, ts.DATASET).filter(ours)
    # decision 18: a signal with `answered_values` is answered present only with one of them
    counts_as_present = pl.lit(False)
    for signal in design.signals:
        values = design.answered_values.get(signal)
        counts_as_present = counts_as_present | (
            (pl.col("signal_type") == signal)
            & (pl.col("value").is_in(list(values)) if values else pl.col("value") != "absent")
        )
    answered = kept.filter(counts_as_present)
    flagged: dict[tuple[str, str], set[preprocessing.SignalType]] = {}
    for h, m, signal in (
        kept.filter(pl.col("value") != "absent")
        .select("source_document_hash", "source_member", "signal_type")
        .iter_rows()
    ):
        flagged.setdefault((str(h), str(m)), set()).add(signal)
    proposals: dict[str, dict[preprocessing.SignalType, golden.Label]] = {}
    for h, m, a, p, signal, value, evidence in kept.select(
        "source_document_hash",
        "source_member",
        "attachment",
        "page",
        "signal_type",
        "value",
        "evidence_span",
    ).iter_rows():
        offered = golden.proposal(signal, value, evidence)
        if offered is not None:
            proposals.setdefault(golden.page_id(h, m, a, p), {})[signal] = offered
    covered = read_dataset(settings.warehouse_dir, ts.COVERAGE).filter(ours)
    others = [
        row
        for other in versions()
        if other != version and (path := queue_path(settings, other)).exists()
        for row in golden.load_queue(path.read_bytes())
        if row.complete
    ]
    return golden.StratifiedFrame(
        entity_pi=entity_pi,
        answered_present=frozenset(
            golden.page_id(h, m, a, p)
            for h, m, a, p in answered.select(
                "source_document_hash", "source_member", "attachment", "page"
            ).iter_rows()
        ),
        read=frozenset(
            (str(h), str(m))
            for h, m in covered.select("source_document_hash", "source_member").iter_rows()
        ),
        labelled_elsewhere={row.page_id: row for row in others},
        proposals=proposals,
        flagged={doc: frozenset(signals) for doc, signals in flagged.items()},
    )


def elsewhere(
    settings: Settings, version: str, golden_dir: Path = golden.GOLDEN_DIR
) -> frozenset[str]:
    """The page ids in every other local queue and in the committed golden set."""
    ids = {
        row.page_id
        for other in versions()
        if other != version and (path := queue_path(settings, other)).exists()
        for row in golden.load_queue(path.read_bytes())
    }
    committed = golden_dir / golden.PAGES_FILE
    if committed.exists():
        ids |= {
            str(json.loads(line)["page_id"])
            for line in committed.read_text(encoding="utf-8").splitlines()
            if line.strip()
        }
    return frozenset(ids)


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
    files = golden.export(rows, masking.load_model(), golden.guide_version())
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


def progress_lines(rows: list[golden.QueuePage], sample: golden.GoldenSample) -> list[str]:
    """Where a stratified queue stands, as printed by `build` and `review`: counts only."""
    if sample.stratified is None:
        return []
    p = golden.progress(rows, sample, golden.guide_version())
    lines = [
        f"labelled {p.labelled} of {p.queued} in draw order (cut {p.cut:.4f}); "
        + ", ".join(f"{s} {n} of {p.target} positives" for s, n in p.positives.items())
        + ("; every answered-present page labelled" if p.answered_present_done else "")
        + ("; stopping rule met: you may stop" if p.met else "")
    ]
    if p.in_pilot:
        lines.append(
            f"pilot: {p.labelled} of {p.pilot_pages} pages; then `make label-review`, tighten the "
            "guide and raise its version before labelling on (decision 20)"
        )
    if p.beyond:
        lines.append(
            f"{len(p.beyond)} page(s) labelled past the first unlabelled one: fill the gap"
        )
    if p.stale_guide:
        lines.append(f"{p.stale_guide} page(s) labelled under an earlier guide: label them again")
    return lines


def review(settings: Settings, version: str) -> list[str]:
    """A stratified queue's review (decisions 20–23): its progress, each label that differs from
    the model's answer with its reason, the reasons summed, agreement shown and blind, the
    rechecks' agreement, and the time per page by stratum. Ids and labels only, never text."""
    sample = golden.load_golden_sample(version)
    path = queue_path(settings, version)
    if sample.stratified is None or not path.exists():
        return [f"{version}: not a stratified sample, or no queue yet"]
    rows = golden.load_queue(path.read_bytes())
    lines = [f"{version}: " + line for line in progress_lines(rows, sample)]
    found = golden.disagreements(rows)
    lines.append(f"labels that differ from the model's answer: {len(found)}")
    lines += [
        f"  #{d.position} {d.page_id} {d.signal}: model {d.model}, you {d.owner}"
        f"{f', reason {d.reason}' if d.reason else ', no reason given'}"
        f"{', unsure' if d.unsure else ''}{', blind' if d.blind else ''}"
        for d in found
    ]
    reasons = Counter(d.reason or "none given" for d in found)
    if reasons:
        lines.append("reasons: " + ", ".join(f"{r} {n}" for r, n in reasons.most_common()))
    unsure = sum(lb.unsure for r in rows if r.complete for lb in r.labels.values())
    lines.append(f"labels marked unsure: {unsure}")
    agreement = golden.proposal_agreement(rows)
    lines.append(
        "agreement with the model's answers, answered-present pages: "
        + ", ".join(f"{arm} {a} of {t}" for arm, (a, t) in agreement.items())
    )
    checks = golden.consistency(rows)
    today = datetime.now(UTC).date()
    due = sum(golden.recheck_due(r, today, sample.stratified.recheck_after_days) for r in rows)
    lines.append(
        "rechecks agreeing with the first label: "
        + (", ".join(f"{s} {a} of {t}" for s, (a, t) in checks.items()) or "none yet")
        + f"; {due} due"
    )
    timing = golden.seconds_by_stratum(rows)
    if timing:
        lines.append(
            "median seconds a page: "
            + ", ".join(f"{s} {sec} ({n} pages)" for s, (n, sec) in timing.items())
        )
    return lines


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if not args or args[0] not in {"build", "export", "clear", "review"} or len(args) > 2:
        print(__doc__, file=sys.stderr)
        return 2
    settings = Settings()
    if args[0] == "review":
        stratified = [v for v in versions() if golden.load_golden_sample(v).stratified is not None]
        for version in args[1:] or stratified:
            print("\n".join(review(settings, version)))
        return 0
    if args[0] == "build":
        for version in args[1:] or versions():
            try:
                stats = build(settings, version)
            except ValueError as exc:  # a stratified sample before its `text` run, say
                print(f"{version}: not built: {exc}", file=sys.stderr)
                continue
            drawn = (
                "drawn by stratum "
                + ", ".join(f"{k} {stats.drawn[k]} of {stats.strata[k]}" for k in golden.STRATA)
                + (f"; {stats.carried} already labelled, carried over" if stats.carried else "")
                + (
                    f"; {stats.proposals_dropped} answer(s) not offered, evidence off the page"
                    if stats.proposals_dropped
                    else ""
                )
                if stats.strata
                else f"{stats.reports_sampled} of {stats.reports} reports "
                f"({stats.reports_modified} read as a modified opinion)"
                if stats.reports
                else f"{stats.rejected_sampled} of {stats.rejected_pool} rejected sampled"
            )
            if stats.excluded:
                drawn += f"; {stats.excluded} left out, already queued or labelled elsewhere"
            print(
                f"{version}: {stats.text_pages} text pages, {stats.selected} selected, {drawn}; "
                f"attachment errors {dict(stats.attachment_errors) or 'none'}; "
                f"pages the masker changes on a second pass: {stats.not_idempotent}"
            )
            rows = golden.load_queue(queue_path(settings, version).read_bytes())
            print(f"queue: {len(rows)} pages, {sum(r.complete for r in rows)} labelled")
            for line in progress_lines(rows, golden.load_golden_sample(version)):
                print(line)
    elif args[0] == "clear":
        if len(args) > 1:
            print("clear takes no version: it reads every local queue", file=sys.stderr)
            return 2
        nlp = masking.load_model()
        for version in versions():
            path = queue_path(settings, version)
            if not path.exists():
                continue
            rows = golden.load_queue(path.read_bytes())
            cleared = [golden.clear_reviewed(r, nlp) if r.complete else r for r in rows]
            added = sum(len(c.cleared) - len(r.cleared) for r, c in zip(rows, cleared, strict=True))
            path.write_bytes(golden.dump_queue(cleared))
            print(f"{version}: {added} flagged span(s) recorded as reviewed")
    else:
        if len(args) > 1:
            print("export takes no version: it writes every local queue", file=sys.stderr)
            return 2
        try:
            counts = export(settings)
        except ValueError as exc:
            print(f"not exported: {exc}", file=sys.stderr)
            return 1
        queued = _queued(settings)
        replaced, missed = golden.masker_recall(queued)
        print(f"exported to {golden.GOLDEN_DIR}: {counts}")
        print(f"persons masked by the masker {replaced}, by hand {missed}")
        agreement = golden.proposal_agreement(queued)
        if any(total for _agreed, total in agreement.values()):
            print(
                "agreement with the model's answers (decision 13): "
                + ", ".join(f"{arm} {a} of {t}" for arm, (a, t) in agreement.items())
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
