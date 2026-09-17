"""Dagster assets for stages C (parsing) and E2 (identity grading). See AGENT_SPEC.md §6C, §6E.

Thin wrappers: all logic lives in `distress_radar.parsing`. `ingestion_run_id`
on canonical facts is the run that first parsed each file under the current
mapping config (`parsed_documents`), so unchanged input re-materializes to
identical Parquet.

No `from __future__ import annotations` here: Dagster inspects the `config`
parameter annotation at runtime.
"""

from collections import Counter
from datetime import UTC, datetime
from typing import TYPE_CHECKING, cast

import dagster as dg
import polars as pl

from dagster_defs.assets.acquisition import raw_filing_documents, rdf_manual_import
from distress_radar.acquisition import manifest as acquisition_manifest
from distress_radar.acquisition.document_retrieval import load_document_types
from distress_radar.acquisition.models import QuarantineRecord, QuarantineStage
from distress_radar.acquisition.raw_store import raw_key
from distress_radar.parsing import manifest
from distress_radar.parsing.accounting_identities import (
    grade,
    prior_year_consistency,
    run_identity_checks,
)
from distress_radar.parsing.canonical_schema import MappingConfig, load_mapping_config
from distress_radar.parsing.contracts import FINANCIAL_STATEMENTS_CANONICAL, RESTATEMENT_EVENTS
from distress_radar.parsing.mapping_engine import SORT_KEY, MappingError, empty_frame
from distress_radar.parsing.statements import FileOutcome, classify_download, map_file
from distress_radar.parsing.xsd_validation import XsdValidator
from distress_radar.settings import Settings
from distress_radar.warehouse import read_dataset, write_dataset

if TYPE_CHECKING:
    from psycopg import Connection

    from dagster_defs.definitions import PostgresResource, RawObjectStoreResource

CANONICAL = "financial_statements_canonical"
RESTATEMENTS = "restatement_events"


def _statement_type_codes() -> list[str]:
    types = load_document_types()
    return sorted(
        code for code, t in types.types.items() if t.download and t.canonical == "statement"
    )


def _entity_key(krs: str, outcome_ref: str | None, source_member: str) -> str:
    return f"{krs}:{outcome_ref or source_member}"


def _quarantine(
    conn: "Connection",
    stage: QuarantineStage,
    entity_key: str,
    reason_code: str,
    detail: str,
    sha256: str,
    run_id: str,
    now: datetime,
) -> None:
    acquisition_manifest.insert_quarantine(
        conn,
        [
            QuarantineRecord(
                stage=stage,
                entity_key=entity_key,
                reason_code=reason_code,
                detail=detail,
                source_document_hash=sha256,
                ingestion_run_id=run_id,
                created_at=now,
            )
        ],
    )


def _record(
    conn: "Connection",
    config: MappingConfig,
    sha256: str,
    krs: str,
    outcome: FileOutcome,
    run_id: str,
    now: datetime,
    status: manifest.ParseStatus | None = None,
) -> str:
    spec = outcome.spec
    return manifest.record_parsed_document(
        conn,
        manifest.ParsedDocumentRow(
            sha256=sha256,
            source_member=outcome.source_member,
            spec_hash=config.spec_hashes[spec.structure_version] if spec else "",
            krs=krs,
            document_ref=outcome.filing.document_ref if outcome.filing else None,
            member_kind=outcome.member_kind,
            structure_key=outcome.structure_key,
            structure_version=spec.structure_version if spec else None,
            status=status or outcome.status,
        ),
        run_id,
        now,
    )


@dg.asset(
    group_name="parsing",
    deps=[rdf_manual_import, raw_filing_documents],
    required_resource_keys={"postgres", "raw_object_store"},
)
def financial_statements_canonical(context: dg.AssetExecutionContext) -> dg.MaterializeResult:
    """C1 + C2 + E2 grading: every downloaded annual statement as canonical facts.

    Inputs: `filing_index` rows with a stored file whose RDF type is a
    downloaded statement (`config/mappings/rdf_document_types.yaml`), the stored
    bytes in MinIO, `entity_master`, and `config/mappings/` + `config/xsd/`.
    Outputs:
    - `parsed_documents` (Postgres): one row per statement file with its
      structure version and status (`valid`, `not_yet_mapped`,
      `needs_pdf_tier`, `quarantined`);
    - `quarantine` rows, stage `C1` (container, detection, XSD), `C2` (mapping)
      or `E2` (identity failures that grade a file `quarantined`);
    - `WAREHOUSE_DIR/financial_statements_canonical/fiscal_year=YYYY/`: the
      canonical facts (AGENT_SPEC §5 plus `document_ref`, `source_member`),
      with `quality_grade` set. Quarantined files keep their rows.
    The whole dataset is rebuilt on each run.
    Partition scheme: none (unpartitioned).
    """
    postgres = cast("PostgresResource", context.resources.postgres)
    store = cast("RawObjectStoreResource", context.resources.raw_object_store).store()
    settings = Settings()
    config = load_mapping_config()
    validator = XsdValidator()
    run_id = context.run_id
    now = datetime.now(UTC)
    statuses: Counter[str] = Counter()
    reasons: Counter[str] = Counter()
    frames: list[pl.DataFrame] = []

    with postgres.connect() as conn:
        acquisition_manifest.ensure_schema(conn)
        manifest.ensure_schema(conn)
        conn.commit()
        sources = manifest.statement_sources(conn, _statement_type_codes())
        context.log.info(f"{len(sources)} stored statement downloads")
        for source in sources:
            outcomes = classify_download(
                source, store.get(raw_key(source.sha256)), config, validator
            )
            for outcome in outcomes:
                label = outcome.spec.structure_version if outcome.spec else outcome.member_kind
                if outcome.status == "quarantined":
                    assert outcome.stage is not None and outcome.reason_code is not None
                    _record(conn, config, source.sha256, source.krs, outcome, run_id, now)
                    _quarantine(
                        conn,
                        cast("QuarantineStage", outcome.stage),
                        _entity_key(
                            source.krs,
                            outcome.filing.document_ref if outcome.filing else None,
                            outcome.source_member,
                        ),
                        outcome.reason_code,
                        outcome.detail or "",
                        source.sha256,
                        run_id,
                        now,
                    )
                    statuses[f"{label}:quarantined"] += 1
                    reasons[f"{outcome.stage}:{outcome.reason_code}"] += 1
                    continue
                first_run = _record(conn, config, source.sha256, source.krs, outcome, run_id, now)
                if outcome.status != "valid":
                    statuses[f"{label}:{outcome.status}"] += 1
                    continue
                try:
                    frames.append(map_file(outcome, source, config, first_run))
                except MappingError as exc:
                    _record(
                        conn,
                        config,
                        source.sha256,
                        source.krs,
                        outcome,
                        run_id,
                        now,
                        status="quarantined",
                    )
                    assert outcome.filing is not None
                    _quarantine(
                        conn,
                        "C2",
                        _entity_key(source.krs, outcome.filing.document_ref, outcome.source_member),
                        exc.reason_code,
                        exc.detail,
                        source.sha256,
                        run_id,
                        now,
                    )
                    statuses[f"{label}:quarantined"] += 1
                    reasons[f"C2:{exc.reason_code}"] += 1
                    continue
                statuses[f"{label}:valid"] += 1
        conn.commit()

        facts = pl.concat(frames) if frames else empty_frame()
        results = run_identity_checks(facts, config, settings.identity_tolerance_pln)
        graded = FINANCIAL_STATEMENTS_CANONICAL.validate(
            grade(facts, results, config).sort(SORT_KEY)
        )
        failing = (
            results.filter(pl.col("status") == "fail")
            .group_by("source_document_hash", "source_member", "krs", "document_ref", "check")
            .agg(pl.len().alias("n"), pl.col("line_item").sort().first().alias("example"))
            .sort("source_document_hash", "source_member", "check")
        )
        quarantined = set(
            graded.filter(pl.col("quality_grade") == "quarantined")
            .select("source_document_hash", "source_member")
            .unique()
            .iter_rows()
        )
        for row in failing.iter_rows(named=True):
            if (row["source_document_hash"], row["source_member"]) not in quarantined:
                continue
            _quarantine(
                conn,
                "E2",
                _entity_key(row["krs"], row["document_ref"], row["source_member"]),
                row["check"],
                f"{row['n']} failing lines, e.g. {row['example']}",
                row["source_document_hash"],
                run_id,
                now,
            )
            reasons[f"E2:{row['check']}"] += 1
        conn.commit()

    write_dataset(graded, settings.warehouse_dir, CANONICAL, "fiscal_year")
    grades = graded.select("source_document_hash", "source_member", "quality_grade").unique()
    return dg.MaterializeResult(
        metadata={
            "statement_downloads": len(sources),
            "files_by_version_and_status": dict(sorted(statuses.items())),
            "quarantine_reasons_this_run": dict(sorted(reasons.items())),
            "fact_rows": graded.height,
            "files_by_quality_grade": dict(
                sorted(Counter(grades.get_column("quality_grade").to_list()).items())
            ),
            "identity_results": dict(
                sorted(
                    (f"{c}:{s}", n)
                    for c, s, n in results.group_by("check", "status").len().iter_rows()
                )
            ),
        }
    )


@dg.asset(group_name="parsing", deps=[financial_statements_canonical])
def restatement_events(context: dg.AssetExecutionContext) -> dg.MaterializeResult:
    """`prior_year_consistency` findings (AGENT_SPEC §4.3, §5).

    Inputs: `financial_statements_canonical` from `WAREHOUSE_DIR`, files graded
    `pass` or `warn` only (a quarantined file's figures are not trusted).
    Outputs: `WAREHOUSE_DIR/restatement_events/fiscal_year=YYYY/`, one row per
    prior-year figure that differs from the previously filed one.
    Partition scheme: none (unpartitioned).
    """
    settings = Settings()
    facts = read_dataset(settings.warehouse_dir, CANONICAL).filter(
        pl.col("quality_grade") != "quarantined"
    )
    events = RESTATEMENT_EVENTS.validate(
        prior_year_consistency(facts, settings.identity_tolerance_pln).sort(
            "krs",
            "period_end",
            "restating_document_ref",
            "restating_source_member",
            "restated_column",
            "line_item",
        )
    )
    write_dataset(events, settings.warehouse_dir, RESTATEMENTS, "fiscal_year")
    context.log.info(f"{events.height} restatement events")
    return dg.MaterializeResult(
        metadata={
            "restatement_events": events.height,
            "restating_files": events.select("restating_document_hash", "restating_source_member")
            .unique()
            .height,
            "by_column": dict(sorted(events.group_by("restated_column").len().iter_rows())),
        }
    )


parsing_assets = [financial_statements_canonical, restatement_events]
