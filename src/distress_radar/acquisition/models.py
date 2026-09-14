"""Pydantic models for stage A boundaries: specs, source responses, manifest rows."""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from distress_radar.acquisition.raw_store import RawDocumentMeta

KRS_LENGTH = 10


def _require_aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("timestamps must be timezone-aware (UTC)")
    return value


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


# --- Segment spec (config/segments/<segment>.yaml) -------------------------------------------


class LegalFormSpec(_Frozen):
    label: str
    bir1_szczegolna_forma_prawna_symbol: str


class SegmentSpec(_Frozen):
    """Declarative universe spec. Size and history are declared, enforced from filings later."""

    segment: str
    version: int
    effective_from: date
    pkd_section: str
    pkd_match: Literal["predominant", "any"]
    # PKD division prefixes making up `pkd_section`, per classification version BIR1 reports.
    pkd_section_divisions: dict[str, list[str]]
    legal_form: LegalFormSpec
    size_classes: list[Literal["micro", "small", "medium", "large"]]
    min_history_years: int = Field(ge=1)


class SeedEntry(_Frozen):
    """One hand-picked seed row. `regon`/`nip` are optional hints checked against BIR1."""

    model_config = ConfigDict(frozen=True, extra="ignore")

    krs: object
    regon: str | None = None
    nip: str | None = None


# --- A1 --------------------------------------------------------------------------------------


class UniverseCandidate(_Frozen):
    krs: str
    discovery_source: str
    discovered_at: datetime
    ingestion_run_id: str
    regon_hint: str | None = None
    nip_hint: str | None = None

    @field_validator("krs")
    @classmethod
    def _krs_format(cls, value: str) -> str:
        if len(value) != KRS_LENGTH or not value.isdigit():
            raise ValueError(f"KRS must be {KRS_LENGTH} digits, zero-padded: {value!r}")
        return value

    _aware = field_validator("discovered_at")(_require_aware)


# --- Quarantine (Postgres landing table, ADR 0006) -------------------------------------------

QuarantineStage = Literal["A1", "A2"]


class QuarantineRecord(_Frozen):
    stage: QuarantineStage
    entity_key: str
    reason_code: str
    detail: str
    source_document_hash: str | None
    ingestion_run_id: str
    created_at: datetime

    _aware = field_validator("created_at")(_require_aware)


# --- B2 --------------------------------------------------------------------------------------


class RawFetchRecord(_Frozen):
    """A `put_raw` call's manifest footprint: one `raw_documents` + one fetch row."""

    sha256: str
    byte_size: int
    meta: RawDocumentMeta


# --- A2: GUS BIR1 ----------------------------------------------------------------------------

Bir1EntityType = Literal["P", "LP", "F", "LF"]
NATURAL_PERSON_TYPES: frozenset[str] = frozenset({"F", "LF"})


class Bir1SearchHit(_Frozen):
    regon: str
    typ: str
    silos_id: str | None


class Bir1PkdCode(_Frozen):
    code: str
    version: str
    predominant: bool

    @property
    def division(self) -> str:
        return self.code[:2]


EntityStatus = Literal["active", "suspended", "deregistered"]


class Bir1LegalEntity(_Frozen):
    """Parsed `BIR12OsPrawna` + `BIR12OsPrawnaPkd` reports for one legal person."""

    regon: str
    nip: str | None
    name: str
    krs: str | None
    legal_form_code: str
    legal_form_name: str | None
    start_date: date | None
    suspension_date: date | None
    resumption_date: date | None
    end_date: date | None
    regon_removal_date: date | None
    bankruptcy_order_date: date | None
    pkd_codes: list[Bir1PkdCode]

    @property
    def status(self) -> EntityStatus:
        if self.end_date is not None or self.regon_removal_date is not None:
            return "deregistered"
        if self.suspension_date is not None and (
            self.resumption_date is None or self.resumption_date < self.suspension_date
        ):
            return "suspended"
        return "active"

    @property
    def predominant_pkd(self) -> Bir1PkdCode | None:
        return next((p for p in self.pkd_codes if p.predominant), None)


class EntityMasterRow(_Frozen):
    krs: str
    nip: str | None
    regon: str
    name: str
    legal_form_code: str
    status: EntityStatus
    pkd_codes: list[Bir1PkdCode]
    pkd_predominant: str | None
    source_document_hash: str  # BIR12OsPrawna report
    pkd_source_document_hash: str  # BIR12OsPrawnaPkd report
    known_from: date
    ingestion_run_id: str


class ReconciliationRecord(_Frozen):
    krs: str
    field: str
    seed_value: str | None
    bir1_value: str | None
    resolution: str
    source_document_hash: str
    ingestion_run_id: str
    created_at: datetime

    _aware = field_validator("created_at")(_require_aware)
