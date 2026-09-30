"""Dagster assets for stage G (text signals). See AGENT_SPEC.md §6G, plan 0013 step H.

Thin wrapper: the logic lives in `distress_radar.extraction`. `ingestion_run_id` on every row is
the run that first read the statement file under the current pipeline (`text_extractions`), so
unchanged input re-materializes to identical Parquet.

No `from __future__ import annotations` here, as in the other asset modules.
"""

from collections import Counter
from datetime import UTC, datetime
from typing import TYPE_CHECKING, cast

import dagster as dg

from dagster_defs.assets.parsing import financial_statements_canonical
from distress_radar.acquisition import manifest as acquisition_manifest
from distress_radar.acquisition.models import QuarantineRecord
from distress_radar.extraction import extractor as ex
from distress_radar.extraction import manifest, masking, preprocessing, response_store
from distress_radar.extraction import text_signals as ts
from distress_radar.extraction.contracts import TEXT_COVERAGE, TEXT_SIGNALS
from distress_radar.parsing.containers import unwrap
from distress_radar.settings import Settings
from distress_radar.warehouse import write_dataset

if TYPE_CHECKING:
    from dagster_defs.definitions import PostgresResource, RawObjectStoreResource

MASKING_CHECK = "evidence_masked"


def _masking_spec() -> dg.AssetCheckSpec:
    return dg.AssetCheckSpec(
        MASKING_CHECK,
        asset="text_signals",
        blocking=True,
        description=(
            "No evidence span holds anything the masker would still replace: a person, a PESEL, "
            "an e-mail, a phone number (invariant 6; ADR 0009, third addendum)."
        ),
    )


@dg.asset(
    group_name="text",
    deps=[financial_statements_canonical],
    required_resource_keys={"postgres", "raw_object_store"},
    check_specs=[_masking_spec()],
)
def text_signals(context: dg.AssetExecutionContext) -> dg.MaterializeResult:
    """G1 + G2: the notes embedded in every stored statement, read into text signals.

    Inputs: the XML statement files `parsed_documents` holds whose filing is dated and not deleted,
    their stored bytes in MinIO, the Polish spaCy model (`make models`), and
    `config/extraction/` (the extractor in use, `EXTRACTOR_VERSION`, with its prefilter and
    rules) and `prompts/extraction/`. Model signals are extracted only once the owner has
    confirmed the provider's terms (`EXTRACTION_API_CONFIRMED`, plan 0013 decision 2), through the
    Batches API; stored responses are replayed, never re-requested (decision 3).
    Outputs:
    - `WAREHOUSE_DIR/text_signals/fiscal_year=YYYY/`: one row per kept extraction, with its masked
      evidence and lineage (AGENT_SPEC §5);
    - `WAREHOUSE_DIR/text_coverage/fiscal_year=YYYY/`: one row per (statement file, signal_type):
      what was read, and a status (`read`, `partial`, `not_run`, `no_text`);
    - `text_extractions` (Postgres), the first run per (file, pipeline hash); new model responses
      in MinIO under `extraction/responses/` with their `extraction_responses` rows;
    - `quarantine_events` rows, stage `G1` (an unreadable attachment) and `G2` (a discarded
      extraction, per signal).
    Both datasets are rebuilt on each run.
    Check: `evidence_masked`, blocking.
    Partition scheme: none (unpartitioned).
    """
    postgres = cast("PostgresResource", context.resources.postgres)
    store = cast("RawObjectStoreResource", context.resources.raw_object_store).store()
    settings = Settings()
    extractor = ex.load_extractor(settings.extractor_version)
    prefilter = preprocessing.load_prefilter(extractor.config.prefilter_version)
    confirmed = settings.extraction_api_confirmed
    runnable = {s for s, m in extractor.config.signals.items() if confirmed or m.method == "rule"}
    pipeline = ts.pipeline_hash(extractor, runnable)
    mask_nlp, lemma_nlp = masking.load_model(), preprocessing.load_model()
    run_id = context.run_id
    now = datetime.now(UTC)
    stats = ts.TextStats()
    skipped: Counter[str] = Counter()

    with postgres.connect() as conn:
        manifest.ensure_schema(conn)
        response_store.ensure_schema(conn)
        conn.commit()
        notes: list[ts.StatementNotes] = []
        for source in manifest.text_sources(conn):
            members = {m.source_member: m for m in unwrap(store.get(source.object_key))}
            member = members.get(source.source_member)
            if member is None or not member.data.lstrip().startswith(b"<"):
                skipped["member_not_xml"] += 1
                continue
            first_run = manifest.record_text_extraction(conn, source, pipeline, run_id, now)
            statement = ts.StatementFile(
                krs=source.krs,
                document_ref=source.document_ref,
                period_end=source.period_end,
                known_from=source.submission_date,
                source_document_hash=source.sha256,
                source_member=source.source_member,
                ingestion_run_id=first_run,
            )
            notes.append(ts.read_notes(statement, member.data, prefilter, mask_nlp, lemma_nlp))
        conn.commit()

        responses = response_store.ResponseStore(store)
        transport: ex.Transport = (
            ex.BatchTransport(ex.anthropic_client(settings)) if confirmed else ex.NoCalls()
        )
        signals, coverage, detections = ts.build(
            notes, extractor, runnable, responses, transport, stats
        )
        response_store.record(conn, responses.new)
        acquisition_manifest.insert_quarantine(
            conn,
            [
                QuarantineRecord(
                    stage=d.stage,
                    entity_key=f"{d.statement.krs}:{d.statement.document_ref}"
                    + (f":{d.signal_type}" if d.signal_type else ""),
                    reason_code=d.reason_code,
                    detail=d.detail,
                    source_document_hash=d.statement.source_document_hash,
                    ingestion_run_id=run_id,
                    created_at=now,
                    krs=d.statement.krs,
                    document_ref=d.statement.document_ref,
                )
                for d in detections
            ],
        )
        conn.commit()

    signals = TEXT_SIGNALS.validate(signals)
    coverage = TEXT_COVERAGE.validate(coverage)
    write_dataset(signals, settings.warehouse_dir, ts.DATASET, "fiscal_year")
    write_dataset(coverage, settings.warehouse_dir, ts.COVERAGE, "fiscal_year")
    unmasked = ts.masking_findings(signals, mask_nlp)
    return dg.MaterializeResult(
        metadata={
            "extractor_version": extractor.config.extractor_version,
            "signals_run": sorted(runnable),
            "statements": stats.statements,
            "skipped": dict(skipped),
            "pages_by_status": dict(sorted(stats.pages.items())),
            "signal_rows": signals.height,
            "present_by_signal": dict(
                sorted(
                    Counter(
                        signals.filter(signals["value"] != "absent")
                        .get_column("signal_type")
                        .to_list()
                    ).items()
                )
            ),
            "coverage": dict(sorted(stats.coverage.items())),
            "model_calls": stats.extraction.called,
            "replayed_from_store": stats.extraction.replayed,
            "discarded": dict(sorted(stats.extraction.discarded.items())),
            "quarantine_detections": dict(
                sorted(Counter(f"{d.stage}:{d.reason_code}" for d in detections).items())
            ),
        },
        check_results=[
            dg.AssetCheckResult(
                check_name=MASKING_CHECK,
                passed=not unmasked,
                severity=dg.AssetCheckSeverity.ERROR,
                metadata={"would_mask_by_kind": dict(unmasked)},
            )
        ],
    )


text_assets = [text_signals]
