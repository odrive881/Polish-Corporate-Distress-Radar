"""A1 — universe discovery (AGENT_SPEC.md §6A).

Nothing is discovered on the network. The universe is the hand-picked seed list
in `config/segments/<segment>_seed.yaml`, plus lists of KRS numbers kept outside
the repository (`load_krs_list`, ADR 0014): the owner's Rejestr.io list, read for
its KRS numbers alone. Both emit the same `UniverseCandidate` rows, told apart by
`discovery_source`.

Malformed seed entries are quarantined with a reason code, never dropped or
repaired (invariant 4). In particular a KRS YAML parsed as an integer is not
zero-padded back: an unquoted `0000123456` may have been read as octal, so the
original digits cannot be trusted.
"""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, cast

import yaml
from pydantic import ValidationError

from distress_radar.acquisition.models import (
    KRS_LENGTH,
    QuarantineRecord,
    SeedEntry,
    SegmentSpec,
    UniverseCandidate,
    is_krs,
)

STAGE = "A1"
KRS_LIST_COLUMNS = ("krs_num", "krs")


@dataclass(frozen=True)
class SeedLoad:
    candidates: list[UniverseCandidate] = field(default_factory=list[UniverseCandidate])
    quarantine: list[QuarantineRecord] = field(default_factory=list[QuarantineRecord])


def load_segment(path: Path) -> SegmentSpec:
    return SegmentSpec.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))


def load_seed(path: Path, *, ingestion_run_id: str, discovered_at: datetime) -> SeedLoad:
    """Read a seed file into candidates; malformed entries become quarantine rows.

    Duplicate KRS numbers collapse to the first occurrence (same entity, not data loss).
    """
    document = cast(dict[str, Any], yaml.safe_load(path.read_text(encoding="utf-8")))
    discovery_source = str(document["discovery_source"])
    entries = cast(list[Any], document.get("entities") or [])

    result = SeedLoad()
    seen: set[str] = set()

    def quarantine(entity_key: str, reason_code: str, detail: str) -> None:
        result.quarantine.append(
            QuarantineRecord(
                stage=STAGE,
                entity_key=entity_key,
                reason_code=reason_code,
                detail=detail,
                source_document_hash=None,
                ingestion_run_id=ingestion_run_id,
                created_at=discovered_at,
                # Only a well-formed KRS is recorded as one; a malformed entry's
                # key is kept verbatim in `entity_key`.
                krs=entity_key if is_krs(entity_key) else None,
                document_ref=None,
            )
        )

    for index, raw in enumerate(entries):
        try:
            entry = SeedEntry.model_validate(raw)
        except ValidationError as exc:
            quarantine(f"{path.name}#{index}", "malformed_seed_entry", str(exc))
            continue

        krs = entry.krs
        if not isinstance(krs, str):
            quarantine(
                repr(krs),
                "malformed_krs",
                f"entry #{index}: KRS must be a quoted string, got {type(krs).__name__}",
            )
            continue
        if not is_krs(krs):
            quarantine(
                krs,
                "malformed_krs",
                f"entry #{index}: KRS must be {KRS_LENGTH} digits, zero-padded",
            )
            continue
        if krs in seen:
            continue
        seen.add(krs)

        result.candidates.append(
            UniverseCandidate(
                krs=krs,
                discovery_source=discovery_source,
                discovered_at=discovered_at,
                ingestion_run_id=ingestion_run_id,
                regon_hint=entry.regon,
                nip_hint=entry.nip,
            )
        )
    return result


def load_krs_list(
    path: Path, *, discovery_source: str, ingestion_run_id: str, discovered_at: datetime
) -> SeedLoad:
    """Read a CSV list's KRS column into candidates (ADR 0014); every other column is ignored.

    The list stays outside the repository and only KRS numbers enter the pipeline: its other
    columns (company names, some naming a person) are never read into a candidate or a
    quarantine row. A malformed number is quarantined under its line, never padded or repaired.
    Duplicates collapse to the first occurrence.
    """
    text = path.read_text(encoding="utf-8-sig")
    header_line = text.splitlines()[0] if text else ""
    delimiter = ";" if header_line.count(";") > header_line.count(",") else ","
    reader = csv.DictReader(io.StringIO(text, newline=""), delimiter=delimiter)
    column = next((c for c in KRS_LIST_COLUMNS if c in (reader.fieldnames or [])), None)
    if column is None:
        raise ValueError(f"{path.name}: no KRS column, expected one of {KRS_LIST_COLUMNS}")

    result = SeedLoad()
    seen: set[str] = set()
    for line, row in enumerate(reader, start=2):
        krs = (row.get(column) or "").strip()
        if not is_krs(krs):
            result.quarantine.append(
                QuarantineRecord(
                    stage=STAGE,
                    entity_key=f"{path.name}#{line}",
                    reason_code="malformed_krs",
                    detail=f"line {line}: KRS must be {KRS_LENGTH} digits, zero-padded",
                    source_document_hash=None,
                    ingestion_run_id=ingestion_run_id,
                    created_at=discovered_at,
                    krs=None,
                    document_ref=None,
                )
            )
            continue
        if krs in seen:
            continue
        seen.add(krs)
        result.candidates.append(
            UniverseCandidate(
                krs=krs,
                discovery_source=discovery_source,
                discovered_at=discovered_at,
                ingestion_run_id=ingestion_run_id,
            )
        )
    return result
