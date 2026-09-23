"""Pydantic models for stage A boundaries: specs, source responses, manifest rows."""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from distress_radar.acquisition.raw_store import RawDocumentMeta

KRS_LENGTH = 10


def is_krs(value: object) -> bool:
    """A zero-padded, 10-digit KRS number."""
    return isinstance(value, str) and len(value) == KRS_LENGTH and value.isdigit()


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


# --- Quarantine (Postgres detection log `quarantine_events`, ADR 0006, plan 0007) ------------

QuarantineStage = Literal["A1", "A2", "A3", "A4", "C1", "C2", "E2"]


class QuarantineRecord(_Frozen):
    """One detection, appended to `quarantine_events` (never updated or deleted).

    `krs` and `document_ref` are required fields that may be None, so every
    writer states them explicitly: `krs` is None only where no valid KRS exists
    (a malformed A1 seed entry); `document_ref` is None wherever there is no
    RDF filing behind the row (A1–A3, and a C1 file with no filing row).
    """

    stage: QuarantineStage
    entity_key: str
    reason_code: str
    detail: str
    source_document_hash: str | None
    ingestion_run_id: str
    created_at: datetime
    krs: str | None
    document_ref: str | None

    _aware = field_validator("created_at")(_require_aware)

    @field_validator("krs")
    @classmethod
    def _krs_format(cls, value: str | None) -> str | None:
        if value is not None and not is_krs(value):
            raise ValueError(f"krs must be {KRS_LENGTH} digits, got {value!r}")
        return value


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


# --- A3: RDF filing index --------------------------------------------------------------------


RdfDocumentStatus = Literal["NIEUSUNIETY", "USUNIETY"]


class RdfDocumentType(_Frozen):
    name: str
    canonical: str
    download: bool


class RdfDocumentTypes(_Frozen):
    """`config/mappings/rdf_document_types.yaml`: observed RDF types and the download scope."""

    model_config = ConfigDict(frozen=True, extra="ignore")

    version: int
    effective_from: date
    types: dict[str, RdfDocumentType]

    @property
    def download_codes(self) -> list[str]:
        return sorted(code for code, t in self.types.items() if t.download)


class FilingListEntry(_Frozen):
    """One row of an entity's RDF filing list (`dokumenty/wyszukiwanie`).

    The list carries no submission date and no fiscal year: `known_from` comes
    from the document detail, and the fiscal year is derived from the reporting
    period later (periods need not be calendar years).
    """

    krs: str
    document_ref: str  # RDF's own document id, e.g. "kQL-7bDLHvl-dIGIeLuLlQ=="
    rdf_type_code: str  # `rodzaj`; equals the detail's `rodzajDokumentu.id`
    status: RdfDocumentStatus
    period_start: date
    period_end: date
    deleted_on: date | None


class FilingDetail(_Frozen):
    """One document's RDF detail (`dokumenty/{id}` + `dokumenty/{id}/id-dokumentu-i-korekt`)."""

    document_ref: str
    rdf_type_id: str
    rdf_type_name: str
    submission_date: date  # `dataDodania` -> known_from
    prepared_date: date | None  # `dataSporzadzenia`
    is_correction: bool
    is_ifrs: bool | None  # RDF leaves it empty on pre-2018 filings
    file_name: str | None
    correction_refs: list[str]  # the document and its corrections, as RDF lists them
    # Also in the detail; the only source of these for corrections, which the list omits.
    status: RdfDocumentStatus | None = None
    period_start: date | None = None
    period_end: date | None = None
    deleted_on: date | None = None  # `dataUsunieciaDokumentuPrzezSad`


class FilingIndexRow(_Frozen):
    """A `filing_index` manifest row as first written from the list.

    Detail columns and `sha256` are filled in later (`record_a3_detail`,
    `record_a3_download`). Corrections never appear in RDF's list; their rows
    are added from the corrected document's expanded row (`correction_of`).
    """

    krs: str
    document_ref: str
    rdf_type_code: str
    status: RdfDocumentStatus
    period_start: date
    period_end: date
    deleted_on: date | None
    discovered_at: datetime
    ingestion_run_id: str

    _aware = field_validator("discovered_at")(_require_aware)


class FilingDocumentState(_Frozen):
    """Where one `filing_index` row stands.

    `rdf_type_id` / `file_name` are `None` until the detail has been fetched.
    `needs_detail` is also true for a detailed document whose corrections have
    no rows yet. `bundle` is what RDF downloads for it: the document and its
    corrections (empty until detailed). A correction (`correction_of` set) is
    downloaded through the document it corrects.
    """

    krs: str
    document_ref: str
    rdf_type_code: str
    status: RdfDocumentStatus
    rdf_type_id: str | None
    file_name: str | None
    downloaded: bool
    needs_detail: bool
    correction_of: str | None = None
    bundle: list[str] = []

    @property
    def download_ref(self) -> str:
        """The listed document whose "Pobierz dokumenty" delivers this one."""
        return self.correction_of or self.document_ref


PendingFilingDocument = FilingDocumentState  # a state still owed a detail or a download


# --- A4: legal-event sources -----------------------------------------------------------------

LegalSource = Literal["KRS", "KRZ", "MSiG"]


class LegalSourceFetch(_Frozen):
    """One successful fetch of one entity from one legal-event source (`legal_source_fetches`).

    `sha256` is the raw object holding the content: a new one, or the previous object when
    the content is unchanged. `content_sha256` is the fingerprint that decides that (for KRS,
    the redacted extract without its per-request timestamp). The latest fetch per
    `(krs, source)` sets that source's cutoff for the entity.
    """

    krs: str
    source: LegalSource
    sha256: str
    content_sha256: str
    fetched_at: datetime
    ingestion_run_id: str
    stored_new: bool

    _aware = field_validator("fetched_at")(_require_aware)
