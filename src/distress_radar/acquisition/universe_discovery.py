"""A1 — universe discovery (AGENT_SPEC.md §6A).

Phase 1 discovers nothing on the network: the universe is a hand-picked seed
list in `config/segments/<segment>_seed.yaml`. Aggregator-based discovery comes
later and will emit the same `UniverseCandidate` rows with a different
`discovery_source`.

Malformed seed entries are quarantined with a reason code, never dropped or
repaired (invariant 4). In particular a KRS YAML parsed as an integer is not
zero-padded back: an unquoted `0000123456` may have been read as octal, so the
original digits cannot be trusted.
"""

from __future__ import annotations

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
)

STAGE = "A1"


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
        if len(krs) != KRS_LENGTH or not krs.isdigit():
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
