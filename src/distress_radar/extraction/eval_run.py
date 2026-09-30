"""Run the extractors over the golden set and write their results; accept one (plan 0013 step G).
The scoring and the gate are `extraction.eval_harness`'s; this reads the stores and calls the API.

    uv run python -m distress_radar.extraction.eval_run run [--sync]
    uv run python -m distress_radar.extraction.eval_run accept <signal_type> --by <name>

`run` (`make eval`) reads the committed golden files, selects pages with the prefilter in use, runs
the extractor in use (`EXTRACTOR_VERSION`) and writes one result per signal with a golden file to
`evals/text_signals/results/`, plus the masker's recall to `results/masking/`. Rule signals run
offline. Model signals need `make dev-up` (responses are stored in MinIO, decision 3) and the
owner's confirmation of the provider's terms (`EXTRACTION_API_CONFIRMED`, decision 2); without it
they are skipped, and say so. Batches by default (half price); `--sync`
calls one at a time. A result that comes out the same keeps its acceptance; a changed one loses it.

`accept` (`make eval-accept SIGNAL=... BY=...`) marks the method in use's result for a signal as
accepted, after checking it is current: the owner's act, done after reading its scores.
"""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import psycopg

from distress_radar.acquisition.raw_store import InMemoryObjectStore, ObjectStore, S3ObjectStore
from distress_radar.extraction import eval_harness as h
from distress_radar.extraction import extractor as ex
from distress_radar.extraction import masking, preprocessing, response_store
from distress_radar.extraction.golden import GOLDEN_DIR, PAGES_FILE
from distress_radar.extraction.preprocessing import SignalType
from distress_radar.settings import Settings


def _objects(settings: Settings) -> ObjectStore:
    return S3ObjectStore.from_endpoint(
        settings.minio_endpoint,
        settings.minio_access_key,
        settings.minio_secret_key.get_secret_value(),
        settings.minio_bucket,
    )


def masker_summary(golden_dir: Path = GOLDEN_DIR) -> dict[str, Any]:
    """Persons the masker replaced and the owner had to, over the labelled pages (decision 1)."""
    rows = [
        json.loads(line)
        for line in (golden_dir / PAGES_FILE).read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    by_masker = sum(int(r["masked"].get("person", 0)) for r in rows)
    by_hand = sum(int(r["hand_masked"].get("person", 0)) for r in rows)
    return {
        "masking_version": masking.MASKING_VERSION,
        "pages": len(rows),
        "persons_by_masker": by_masker,
        "persons_by_hand": by_hand,
        "masker_recall": round(by_masker / (by_masker + by_hand), 4)
        if by_masker + by_hand
        else None,
        "note": "over-masking is not measured: masked text does not show what was masked",
    }


def run(settings: Settings, sync: bool = False, golden_dir: Path = GOLDEN_DIR) -> dict[str, str]:
    """Every signal with a golden file: its result written, or why it was skipped."""
    extractor = ex.load_extractor(settings.extractor_version)
    prefilter = preprocessing.load_prefilter(extractor.config.prefilter_version)
    texts = h.page_texts(golden_dir)
    signals: list[SignalType] = [
        s for s in preprocessing.SIGNAL_TYPES if h.golden_labels(s, golden_dir) is not None
    ]
    if not signals:
        return {"all": "no golden file yet: label pages and `make label-export` first"}
    nlp = preprocessing.load_model()
    sentences = {pid: preprocessing.analyse(text, nlp) for pid, text in sorted(texts.items())}
    selected = {
        pid: tuple(preprocessing.candidates(sents, prefilter)) for pid, sents in sentences.items()
    }
    llm = [s for s in signals if extractor.config.signals[s].method == "llm"]
    confirmed = settings.extraction_api_confirmed
    runnable: list[SignalType] = [
        s for s in signals if confirmed or extractor.config.signals[s].method == "rule"
    ]
    status = {
        s: "skipped: model signal before EXTRACTION_API_CONFIRMED" for s in llm if not confirmed
    }
    store = response_store.ResponseStore(
        _objects(settings) if any(s in llm for s in runnable) else InMemoryObjectStore()
    )
    transport: ex.Transport
    if confirmed and llm:
        client = ex.anthropic_client(settings)
        transport = ex.SyncTransport(client) if sync else ex.BatchTransport(client)
    else:
        transport = _NoCalls()
    pages = [
        ex.PageInput(
            pid, texts[pid], sentences[pid], tuple(s for s in selected[pid] if s in runnable)
        )
        for pid in sorted(texts)
    ]
    stats = ex.ExtractionStats()
    results = ex.extract(pages, extractor, store, transport, stats)
    if store.new:
        with psycopg.connect(settings.postgres_conninfo) as conn:
            response_store.ensure_schema(conn)
            response_store.record(conn, store.new)
    rejected = h.rejected_sample(golden_dir)
    for signal in runnable:
        labels = h.golden_labels(signal, golden_dir) or []
        examples = tuple(
            h.example(lb.page_id, signal in selected[lb.page_id], results.get((lb.page_id, signal)))
            for lb in sorted(labels, key=lambda lb: lb.page_id)
        )
        mid = h.method_id(extractor, signal)
        result = h.EvalResult(
            signal_type=signal,
            method=extractor.config.signals[signal].method,
            method_id=mid,
            extractor_version=extractor.config.extractor_version,
            prefilter_version=extractor.config.prefilter_version,
            hashes=h.fingerprint(extractor, signal, golden_dir),
            examples=examples,
            scores=h.score(labels, examples, texts, rejected),
        )
        path = h.result_path(signal, mid, golden_dir / "results")
        if path.exists():
            previous = h.load_result(path)
            if previous.model_copy(update={"accepted": None}) == result:
                result = previous  # unchanged: it keeps its acceptance
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(h.dump_result(result))
        e2e = result.scores.end_to_end
        status[signal] = (
            f"{mid}: {result.scores.positives} positives in {result.scores.pages} pages; "
            f"end to end P {e2e.precision} R {e2e.recall} F1 {e2e.f1}; "
            f"discarded {result.scores.discarded or 0}"
            + ("" if result.accepted else "; not accepted")
        )
    summary = masker_summary(golden_dir)
    masking_path = golden_dir / "results" / "masking" / f"masking_v{masking.MASKING_VERSION}.json"
    masking_path.parent.mkdir(parents=True, exist_ok=True)
    masking_path.write_text(json.dumps(summary, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    status["masking"] = (
        f"masker recall {summary['masker_recall']} ({summary['persons_by_hand']} by hand)"
    )
    status["calls"] = f"{stats.called} sent, {stats.replayed} replayed from the store"
    return status


class _NoCalls:
    """The transport before the owner's confirmation: it is never asked, and refuses if it is."""

    def run(self, requests: Any) -> tuple[dict[str, bytes], set[str]]:
        if requests:
            raise PermissionError("no model call before EXTRACTION_API_CONFIRMED (decision 2)")
        return {}, set()


def accept(settings: Settings, signal: SignalType, by: str, golden_dir: Path = GOLDEN_DIR) -> Path:
    if not by.strip():
        raise ValueError("--by names who accepted the result")
    extractor = ex.load_extractor(settings.extractor_version)
    path = h.result_path(signal, h.method_id(extractor, signal), golden_dir / "results")
    if not path.exists():
        raise ValueError(f"no result at {path}: run `make eval` first")
    result = h.load_result(path)
    stale = sorted(
        k
        for k, v in h.fingerprint(extractor, signal, golden_dir).items()
        if result.hashes.get(k) != v
    )
    if stale:
        raise ValueError(
            f"{path.name} was scored on other {', '.join(stale)}: run `make eval` first"
        )
    accepted = result.model_copy(
        update={"accepted": h.Acceptance(by=by, on=datetime.now(UTC).date())}
    )
    path.write_bytes(h.dump_result(accepted))
    return path


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    settings = Settings()
    try:
        if args[:1] == ["run"] and set(args[1:]) <= {"--sync"}:
            for signal, line in run(settings, sync="--sync" in args).items():
                print(f"{signal}: {line}")
            return 0
        if len(args) == 4 and args[0] == "accept" and args[2] == "--by":
            chosen: SignalType | None = next(
                (s for s in preprocessing.SIGNAL_TYPES if s == args[1]), None
            )
            if chosen is None:
                raise ValueError(f"{args[1]} is not a signal_type")
            print(f"accepted: {accept(settings, chosen, args[3])}")
            return 0
    except (ValueError, PermissionError) as exc:
        print(f"not done: {exc}", file=sys.stderr)
        return 1
    print(__doc__, file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
