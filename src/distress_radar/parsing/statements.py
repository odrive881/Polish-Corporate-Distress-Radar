"""C1 + C2 for one stored download, without I/O: unwrap, match, detect, validate, map.

The Dagster assets read bytes and manifest rows, call these functions, and
write the results. Every file ends in exactly one `FileOutcome` status:

- `valid`: a mapped structure version that passed XSD validation;
- `not_yet_mapped`: a catalogued version with no spec yet (Phase 3);
- `needs_pdf_tier`: a PDF statement, for C3;
- `quarantined`: anything else, with a reason code.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import polars as pl
from lxml import etree

from distress_radar.parsing.canonical_schema import MappingConfig, StructureSpec
from distress_radar.parsing.containers import (
    ContainerError,
    ContainerMember,
    FilingRow,
    match_members,
    safe_parser,
    unwrap,
)
from distress_radar.parsing.manifest import ParseStatus, StatementSource
from distress_radar.parsing.mapping_engine import (
    DocumentContext,
    MappingError,
    parse_statement,
    to_frame,
)
from distress_radar.parsing.version_detection import DetectionError, detect
from distress_radar.parsing.xsd_validation import XsdValidator


@dataclass(frozen=True)
class Filing:
    document_ref: str
    submission_date: date | None
    period_start: date
    period_end: date


@dataclass(frozen=True)
class FileOutcome:
    source_member: str
    member_kind: str
    status: ParseStatus
    filing: Filing | None
    structure_key: str | None = None
    spec: StructureSpec | None = None
    stage: str | None = None  # quarantine stage (C1 / C2)
    reason_code: str | None = None
    detail: str | None = None
    data: bytes = b""  # the statement bytes, kept for `valid` files only


def _key_text(key: tuple[str, str, str, str]) -> str:
    return "|".join(key)


def classify_download(
    source: StatementSource, raw: bytes, config: MappingConfig, validator: XsdValidator
) -> list[FileOutcome]:
    """C1 for every file in one stored download."""
    rows = {
        ref: Filing(ref, submitted, start, end) for ref, _, submitted, start, end in source.rows
    }
    try:
        members = unwrap(raw)
    except ContainerError as exc:
        return [
            FileOutcome(
                "raw",
                "unknown",
                "quarantined",
                None,
                stage="C1",
                reason_code=exc.reason_code,
                detail=exc.detail,
            )
        ]
    matched, unmatched = match_members(
        members, [FilingRow(ref, name) for ref, name, *_ in source.rows]
    )
    outcomes: list[FileOutcome] = []
    for member in unmatched:
        outcomes.append(
            FileOutcome(
                member.source_member,
                member.kind,
                "quarantined",
                None,
                stage="C1",
                reason_code="member_not_in_filing_index",
                detail=f"{member.member_name!r} matches none of {sorted(str(r[1]) for r in source.rows)}",
            )
        )
    for member in members:
        if member.kind == "detached_signature" or member.source_member not in matched:
            continue
        filing = rows[matched[member.source_member].document_ref]
        outcomes.append(_classify_member(member, filing, config, validator))
    return outcomes


def _classify_member(
    member: ContainerMember, filing: Filing, config: MappingConfig, validator: XsdValidator
) -> FileOutcome:
    if member.kind == "pdf":
        return FileOutcome(member.source_member, member.kind, "needs_pdf_tier", filing)
    root = etree.fromstring(member.data, safe_parser())
    try:
        detection = detect(root, config)
    except DetectionError as exc:
        return FileOutcome(
            member.source_member,
            member.kind,
            "quarantined",
            filing,
            stage="C1",
            reason_code="unknown_structure_version",
            detail=str(exc),
        )
    key = _key_text(detection.key)
    if detection.status == "not_yet_mapped":
        return FileOutcome(
            member.source_member, member.kind, "not_yet_mapped", filing, structure_key=key
        )
    if detection.spec is None:
        return FileOutcome(
            member.source_member,
            member.kind,
            "quarantined",
            filing,
            structure_key=key,
            stage="C1",
            reason_code="unknown_structure_version",
            detail=key,
        )
    errors = validator.validate(root, detection.spec)
    if errors:
        return FileOutcome(
            member.source_member,
            member.kind,
            "quarantined",
            filing,
            structure_key=key,
            spec=detection.spec,
            stage="C1",
            reason_code="xsd_invalid",
            detail="; ".join(errors),
        )
    return FileOutcome(
        member.source_member,
        member.kind,
        "valid",
        filing,
        structure_key=key,
        spec=detection.spec,
        data=member.data,
    )


def map_file(
    outcome: FileOutcome,
    source: StatementSource,
    config: MappingConfig,
    ingestion_run_id: str,
) -> pl.DataFrame:
    """C2 for one `valid` file. Raises `MappingError` for quarantine (stage C2)."""
    if outcome.status != "valid" or outcome.spec is None or outcome.filing is None:
        raise ValueError(f"{outcome.source_member} is not a valid statement file")
    filing = outcome.filing
    parsed = parse_statement(etree.fromstring(outcome.data, safe_parser()), outcome.spec, config)
    if (parsed.period_start, parsed.period_end) != (filing.period_start, filing.period_end):
        raise MappingError(
            "period_mismatch",
            f"statement {parsed.period_start}..{parsed.period_end}, "
            f"RDF list {filing.period_start}..{filing.period_end}",
        )
    if filing.submission_date is None:
        raise MappingError("known_from_missing", "filing_index has no submission_date")
    ctx = DocumentContext(
        krs=source.krs,
        nip=source.nip,
        regon=source.regon,
        document_ref=filing.document_ref,
        source_document_hash=source.sha256,
        source_member=outcome.source_member,
        known_from=filing.submission_date,
        ingestion_run_id=ingestion_run_id,
    )
    return to_frame(parsed, ctx)
