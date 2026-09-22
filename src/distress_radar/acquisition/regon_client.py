"""A2 — GUS REGON BIR1 identity validation (AGENT_SPEC.md §6A).

Flow per KRS: `Zaloguj` → `sid` HTTP header → `DaneSzukajPodmioty(Krs=…)` →
`DanePobierzPelnyRaport` (`BIR12OsPrawna`, `BIR12OsPrawnaPkd`) → `Wyloguj` on
close. Session acquisition and refresh are internal: BIR1 signals an expired
or missing session with an *empty* result (not a fault), confirmed by
`GetValue("StatusSesji") == "0"`; that triggers exactly one re-login.

Binding notes (verified against the live test WSDL, 2026-09-14): the WSDL
declares WS-Addressing itself, so zeep must NOT be given `WsAddressingPlugin`
(duplicate `To` header fault). Results are XML documents embedded as strings.
The `BIR12*` report names return PKD codes with `praw_pkdWersja`, which is how
PKD codes carry their classification version.

**Raw-first, with one precedence exception.** Each XML payload returned by
BIR1 is written with `put_raw` before lxml parses it into Pydantic models. The
raw unit is the embedded XML string (UTF-8), not the SOAP envelope.
**Invariant 6 (legal entities only) takes precedence over raw-first:** the
search result's `Typ` is inspected in memory *before any raw write*. If it
contains a natural person (`F`/`LF`), nothing from it is persisted — no bytes,
no name — only a quarantine row keyed by the KRS.

Segment checks never drop an entity: failures become quarantine rows with
reason codes (`not_found`, `natural_person`, `ambiguous_match`, `krs_mismatch`,
`legal_form_mismatch`, `pkd_missing`, `pkd_version_unknown`,
`pkd_section_mismatch`). `deregistered` is a status, not a rejection —
deregistered entities matter for labels.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import Any, Literal, Protocol, Self, cast

import requests
from lxml import etree
from pyrate_limiter import Limiter
from tenacity import Retrying, retry_if_exception_type, stop_after_attempt, wait_exponential_jitter
from tenacity.wait import WaitBaseT

from distress_radar.acquisition.base import (
    PermanentSourceError,
    SourcePolicy,
    TransientSourceError,
)
from distress_radar.acquisition.models import (
    NATURAL_PERSON_TYPES,
    Bir1LegalEntity,
    Bir1PkdCode,
    Bir1SearchHit,
    EntityMasterRow,
    QuarantineRecord,
    RawFetchRecord,
    ReconciliationRecord,
    SegmentSpec,
)
from distress_radar.acquisition.raw_store import ObjectStore, RawDocumentMeta, put_raw

logger = logging.getLogger(__name__)

SOURCE = "gus_bir1"
STAGE = "A2"
REPORT_LEGAL_PERSON = "BIR12OsPrawna"
REPORT_LEGAL_PERSON_PKD = "BIR12OsPrawnaPkd"
BIR1_NOT_FOUND_ERROR_CODE = "4"


@dataclass(frozen=True)
class Bir1Endpoint:
    wsdl: str
    service_url: str


ENDPOINTS: dict[Literal["test", "prod"], Bir1Endpoint] = {
    "test": Bir1Endpoint(
        wsdl="https://wyszukiwarkaregontest.stat.gov.pl/wsBIR/wsdl/UslugaBIRzewnPubl-ver11-test.wsdl",
        service_url="https://wyszukiwarkaregontest.stat.gov.pl/wsBIR/UslugaBIRzewnPubl.svc",
    ),
    "prod": Bir1Endpoint(
        wsdl="https://wyszukiwarkaregon.stat.gov.pl/wsBIR/wsdl/UslugaBIRzewnPubl-ver11-prod.wsdl",
        service_url="https://wyszukiwarkaregon.stat.gov.pl/wsBIR/UslugaBIRzewnPubl.svc",
    ),
}
_SERVICE_BINDING = "{http://tempuri.org/}e3"


# --- Service ---------------------------------------------------------------------------------


class Bir1Service(Protocol):
    """What `resolve_entity` needs from BIR1. Each call returns the embedded XML string."""

    @property
    def service_url(self) -> str: ...

    def search_by_krs(self, krs: str) -> str: ...

    def full_report(self, regon: str, report_name: str) -> str: ...


class SoapOperations(Protocol):
    """The zeep service proxy surface used by `ZeepBir1Service` (fakeable in tests)."""

    def Zaloguj(self, pKluczUzytkownika: str) -> str | None: ...

    def Wyloguj(self, pIdentyfikatorSesji: str) -> bool | None: ...

    def GetValue(self, pNazwaParametru: str) -> str | None: ...

    def DaneSzukajPodmioty(self, pParametryWyszukiwania: dict[str, str]) -> str | None: ...

    def DanePobierzPelnyRaport(self, pRegon: str, pNazwaRaportu: str) -> str | None: ...


def bir1_policy(requests_per_minute: int) -> SourcePolicy:
    return SourcePolicy(name=SOURCE, requests_per_minute=requests_per_minute)


class ZeepBir1Service:
    """Session-managing BIR1 client. Use as a context manager so `Wyloguj` runs."""

    def __init__(
        self,
        endpoint: Bir1Endpoint,
        api_key: str,
        *,
        limiter: Limiter,
        policy: SourcePolicy,
        soap: SoapOperations | None = None,
        session: requests.Session | None = None,
        wait: WaitBaseT | None = None,
    ) -> None:
        self._endpoint = endpoint
        self._api_key = api_key
        self._limiter = limiter
        self._policy = policy
        self._session = session if session is not None else requests.Session()
        self._soap = soap
        self._sid: str | None = None
        self._wait: WaitBaseT = wait if wait is not None else wait_exponential_jitter(1, 30)

    @property
    def service_url(self) -> str:
        return self._endpoint.service_url

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def close(self) -> None:
        if self._sid is not None and self._soap is not None:
            sid = self._sid
            self._sid = None
            try:
                self._call(lambda soap: soap.Wyloguj(sid))
            except (TransientSourceError, PermanentSourceError) as exc:
                logger.warning("BIR1 Wyloguj failed (session will expire server-side): %s", exc)
        self._session.headers.pop("sid", None)

    def search_by_krs(self, krs: str) -> str:
        return self._with_session(lambda soap: soap.DaneSzukajPodmioty({"Krs": krs}))

    def full_report(self, regon: str, report_name: str) -> str:
        return self._with_session(lambda soap: soap.DanePobierzPelnyRaport(regon, report_name))

    # -- internals --

    def _operations(self) -> SoapOperations:
        if self._soap is None:
            self._soap = self._call_raw(self._build_soap)
        return self._soap

    def _build_soap(self) -> SoapOperations:
        import zeep
        from zeep.transports import Transport

        transport = Transport(
            session=self._session,
            timeout=int(self._policy.timeout_seconds),
            operation_timeout=self._policy.timeout_seconds,
        )
        client = zeep.Client(self._endpoint.wsdl, transport=transport)
        service: Any = client.create_service(_SERVICE_BINDING, self._endpoint.service_url)  # pyright: ignore[reportUnknownMemberType]
        return cast(SoapOperations, service)

    def _login(self) -> None:
        sid = self._call(lambda soap: soap.Zaloguj(self._api_key))
        if not sid:
            raise PermanentSourceError("BIR1 Zaloguj returned an empty session id (bad key?)")
        self._sid = sid
        self._session.headers["sid"] = sid

    def _with_session(self, operation: Callable[[SoapOperations], str | None]) -> str:
        if self._sid is None:
            self._login()
        result = self._call(operation)
        if result:
            return result
        # Empty result: BIR1's signal for an expired/missing session. Re-login once.
        status = self._call(lambda soap: soap.GetValue("StatusSesji"))
        if status == "1":
            code = self._call(lambda soap: soap.GetValue("KomunikatKod"))
            raise PermanentSourceError(f"BIR1 returned an empty result (KomunikatKod={code})")
        logger.info("BIR1 session expired (StatusSesji=%s); logging in again", status)
        self._login()
        result = self._call(operation)
        if not result:
            raise PermanentSourceError("BIR1 returned an empty result after re-login")
        return result

    def _call[T](self, operation: Callable[[SoapOperations], T]) -> T:
        return self._call_raw(lambda: operation(self._operations()))

    def _call_raw[T](self, thunk: Callable[[], T]) -> T:
        for attempt in Retrying(
            retry=retry_if_exception_type(TransientSourceError),
            stop=stop_after_attempt(self._policy.max_attempts),
            wait=self._wait,
            reraise=True,
        ):
            with attempt:
                self._limiter.try_acquire(SOURCE)
                return _classify_soap_errors(thunk)
        raise AssertionError("unreachable: Retrying reraises")  # pragma: no cover


def _classify_soap_errors[T](thunk: Callable[[], T]) -> T:
    from zeep.exceptions import Fault, TransportError, XMLSyntaxError

    try:
        return thunk()
    except (requests.ConnectionError, requests.Timeout) as exc:
        raise TransientSourceError(f"BIR1 network error: {exc}") from exc
    except TransportError as exc:
        status = exc.status_code
        if status == 429 or status >= 500:
            raise TransientSourceError(f"BIR1 HTTP {status}") from exc
        raise PermanentSourceError(f"BIR1 HTTP {status}") from exc
    except (Fault, XMLSyntaxError) as exc:
        raise PermanentSourceError(f"BIR1 SOAP error: {exc}") from exc


# --- Parsing ---------------------------------------------------------------------------------

_PARSER = etree.XMLParser(resolve_entities=False, no_network=True)
_Element = etree._Element  # pyright: ignore[reportPrivateUsage]  # lxml exposes no public alias


def _parse_rows(xml: bytes) -> list[_Element]:
    root = etree.fromstring(xml, parser=_PARSER)
    return list(root.iterfind("dane"))


def _text(element: _Element, tag: str) -> str | None:
    child = element.find(tag)
    if child is None or child.text is None:
        return None
    value = child.text.strip()
    return value or None


def _date(element: _Element, tag: str) -> date | None:
    value = _text(element, tag)
    return date.fromisoformat(value) if value is not None else None


@dataclass(frozen=True)
class SearchResult:
    not_found: bool
    hits: list[Bir1SearchHit]
    error_message: str | None = None


def parse_search_result(xml: bytes) -> SearchResult:
    rows = _parse_rows(xml)
    if rows and _text(rows[0], "ErrorCode") is not None:
        code = _text(rows[0], "ErrorCode")
        message = _text(rows[0], "ErrorMessageEn")
        if code == BIR1_NOT_FOUND_ERROR_CODE:
            return SearchResult(not_found=True, hits=[], error_message=message)
        raise PermanentSourceError(f"BIR1 search error {code}: {message}")
    hits = [
        Bir1SearchHit(
            regon=_text(row, "Regon") or "",
            typ=_text(row, "Typ") or "",
            silos_id=_text(row, "SilosID"),
        )
        for row in rows
    ]
    return SearchResult(not_found=not hits, hits=hits)


def parse_legal_entity(report_xml: bytes, pkd_xml: bytes) -> Bir1LegalEntity:
    rows = _parse_rows(report_xml)
    if len(rows) != 1 or _text(rows[0], "ErrorCode") is not None:
        raise PermanentSourceError(f"unexpected {REPORT_LEGAL_PERSON} payload ({len(rows)} rows)")
    row = rows[0]
    regon = _text(row, "praw_regon9")
    name = _text(row, "praw_nazwa")
    form = _text(row, "praw_szczegolnaFormaPrawna_Symbol")
    if regon is None or name is None or form is None:
        raise PermanentSourceError(f"{REPORT_LEGAL_PERSON} missing regon/name/legal form")

    pkd_codes: list[Bir1PkdCode] = []
    for pkd_row in _parse_rows(pkd_xml):
        if _text(pkd_row, "ErrorCode") is not None:
            continue  # BIR1 answers "no data" for entities without registered PKD
        code = _text(pkd_row, "praw_pkdKod")
        version = _text(pkd_row, "praw_pkdWersja")
        if code is None or version is None:
            raise PermanentSourceError(f"{REPORT_LEGAL_PERSON_PKD} row missing code/version")
        pkd_codes.append(
            Bir1PkdCode(
                code=code,
                version=version,
                predominant=_text(pkd_row, "praw_pkdPrzewazajace") == "1",
            )
        )

    return Bir1LegalEntity(
        regon=regon,
        nip=_text(row, "praw_nip"),
        name=name,
        krs=_text(row, "praw_numerWRejestrzeEwidencji"),
        legal_form_code=form,
        legal_form_name=_text(row, "praw_szczegolnaFormaPrawna_Nazwa"),
        start_date=_date(row, "praw_dataRozpoczeciaDzialalnosci"),
        suspension_date=_date(row, "praw_dataZawieszeniaDzialalnosci"),
        resumption_date=_date(row, "praw_dataWznowieniaDzialalnosci"),
        end_date=_date(row, "praw_dataZakonczeniaDzialalnosci"),
        regon_removal_date=_date(row, "praw_dataSkresleniaZRegon"),
        bankruptcy_order_date=_date(row, "praw_dataOrzeczeniaOUpadlosci"),
        pkd_codes=pkd_codes,
    )


# --- Resolution ------------------------------------------------------------------------------


@dataclass
class A2Result:
    """Everything one KRS lookup produced, ready for the B2 manifest writer."""

    krs: str
    raw_fetches: list[RawFetchRecord] = field(default_factory=list[RawFetchRecord])
    entity: EntityMasterRow | None = None
    reconciliations: list[ReconciliationRecord] = field(default_factory=list[ReconciliationRecord])
    quarantine: list[QuarantineRecord] = field(default_factory=list[QuarantineRecord])


def segment_violations(entity: Bir1LegalEntity, segment: SegmentSpec) -> list[tuple[str, str]]:
    """`(reason_code, detail)` for each segment rule `entity` fails."""
    violations: list[tuple[str, str]] = []
    expected_form = segment.legal_form.bir1_szczegolna_forma_prawna_symbol
    if entity.legal_form_code != expected_form:
        violations.append(
            (
                "legal_form_mismatch",
                (
                    f"szczegolnaFormaPrawna {entity.legal_form_code} ({entity.legal_form_name}); "
                    f"segment requires {expected_form} ({segment.legal_form.label})"
                ),
            )
        )

    candidates = (
        [p for p in entity.pkd_codes if p.predominant]
        if segment.pkd_match == "predominant"
        else list(entity.pkd_codes)
    )
    if not candidates:
        violations.append(("pkd_missing", f"no {segment.pkd_match} PKD code reported"))
        return violations

    unknown = sorted({p.version for p in candidates} - segment.pkd_section_divisions.keys())
    if unknown and len(unknown) == len({p.version for p in candidates}):
        violations.append(
            (
                "pkd_version_unknown",
                f"PKD version(s) {unknown} not mapped in segment {segment.segment}",
            )
        )
        return violations

    def in_section(p: Bir1PkdCode) -> bool:
        return p.division in segment.pkd_section_divisions.get(p.version, [])

    if not any(in_section(p) for p in candidates):
        codes = ", ".join(f"{p.code} (PKD {p.version})" for p in candidates)
        violations.append(
            (
                "pkd_section_mismatch",
                f"{segment.pkd_match} PKD {codes} outside section {segment.pkd_section}",
            )
        )
    return violations


def resolve_entity(
    krs: str,
    *,
    service: Bir1Service,
    store: ObjectStore,
    segment: SegmentSpec,
    ingestion_run_id: str,
    regon_hint: str | None = None,
    nip_hint: str | None = None,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    parse: Callable[[bytes, bytes], Bir1LegalEntity] = parse_legal_entity,
) -> A2Result:
    """Look up one KRS in BIR1 and classify it into entity_master or quarantine."""
    result = A2Result(krs=krs)

    def quarantine(reason: str, detail: str, source_hash: str | None) -> A2Result:
        result.quarantine.append(
            QuarantineRecord(
                stage=STAGE,
                entity_key=krs,
                reason_code=reason,
                detail=detail,
                source_document_hash=source_hash,
                ingestion_run_id=ingestion_run_id,
                created_at=clock(),
                krs=krs,
                document_ref=None,
            )
        )
        return result

    def store_raw(xml: str, operation: str, fetched_at: datetime) -> tuple[str, bytes]:
        data = xml.encode("utf-8")
        meta = RawDocumentMeta(
            source=SOURCE,
            source_url=f"{service.service_url}#{operation}",
            content_type="application/xml; charset=utf-8",
            fetched_at=fetched_at,
            http_headers={},
            ingestion_run_id=ingestion_run_id,
        )
        digest = put_raw(store, data, meta)
        result.raw_fetches.append(RawFetchRecord(sha256=digest, byte_size=len(data), meta=meta))
        return digest, data

    # 1. Search. Inspected in memory first: invariant 6 precedes raw-first.
    search_xml = service.search_by_krs(krs)
    searched_at = clock()
    search = parse_search_result(search_xml.encode("utf-8"))
    if any(hit.typ in NATURAL_PERSON_TYPES for hit in search.hits):
        return quarantine(
            "natural_person",
            "BIR1 search returned a natural-person record; nothing persisted (invariant 6)",
            None,
        )

    search_hash, _ = store_raw(search_xml, f"DaneSzukajPodmioty?Krs={krs}", searched_at)
    if search.not_found:
        return quarantine("not_found", search.error_message or "no BIR1 match", search_hash)

    legal_persons = [hit for hit in search.hits if hit.typ == "P"]
    if len(legal_persons) != 1:
        types = ", ".join(sorted(hit.typ for hit in search.hits))
        return quarantine(
            "not_found" if not legal_persons else "ambiguous_match",
            f"{len(legal_persons)} legal-person (Typ=P) hits among [{types}]",
            search_hash,
        )
    regon = legal_persons[0].regon

    # 2. Reports: raw write, then parse.
    report_xml = service.full_report(regon, REPORT_LEGAL_PERSON)
    report_hash, report_bytes = store_raw(
        report_xml,
        f"DanePobierzPelnyRaport?pRegon={regon}&pNazwaRaportu={REPORT_LEGAL_PERSON}",
        clock(),
    )
    pkd_xml = service.full_report(regon, REPORT_LEGAL_PERSON_PKD)
    pkd_fetched_at = clock()
    pkd_hash, pkd_bytes = store_raw(
        pkd_xml,
        f"DanePobierzPelnyRaport?pRegon={regon}&pNazwaRaportu={REPORT_LEGAL_PERSON_PKD}",
        pkd_fetched_at,
    )
    entity = parse(report_bytes, pkd_bytes)

    # 3. Identity and segment checks.
    if entity.krs is not None and entity.krs != krs:
        return quarantine(
            "krs_mismatch", f"BIR1 report registry number {entity.krs} != {krs}", report_hash
        )
    for reason, detail in segment_violations(entity, segment):
        quarantine(reason, detail, report_hash if reason == "legal_form_mismatch" else pkd_hash)
    if result.quarantine:
        return result

    # 4. Seed vs BIR1 disagreements: BIR1 (the statistical register) takes precedence.
    for field_name, seed_value, bir1_value in (
        ("regon", regon_hint, entity.regon),
        ("nip", nip_hint, entity.nip),
    ):
        if seed_value is not None and seed_value != bir1_value:
            result.reconciliations.append(
                ReconciliationRecord(
                    krs=krs,
                    field=field_name,
                    seed_value=seed_value,
                    bir1_value=bir1_value,
                    resolution="bir1_precedence",
                    source_document_hash=report_hash,
                    ingestion_run_id=ingestion_run_id,
                    created_at=clock(),
                )
            )

    predominant = entity.predominant_pkd
    result.entity = EntityMasterRow(
        krs=krs,
        nip=entity.nip,
        regon=entity.regon,
        name=entity.name,
        legal_form_code=entity.legal_form_code,
        status=entity.status,
        pkd_codes=entity.pkd_codes,
        pkd_predominant=predominant.code if predominant is not None else None,
        source_document_hash=report_hash,
        pkd_source_document_hash=pkd_hash,
        known_from=pkd_fetched_at.astimezone(UTC).date(),
        ingestion_run_id=ingestion_run_id,
    )
    return result
