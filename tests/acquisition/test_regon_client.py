from datetime import UTC, date, datetime
from pathlib import Path

import pytest
import requests
from tenacity import wait_none

from distress_radar.acquisition.base import PermanentSourceError, in_memory_limiter
from distress_radar.acquisition.models import Bir1LegalEntity
from distress_radar.acquisition.raw_store import InMemoryObjectStore, raw_key, sha256_hex
from distress_radar.acquisition.regon_client import (
    ENDPOINTS,
    REPORT_LEGAL_PERSON,
    REPORT_LEGAL_PERSON_PKD,
    ZeepBir1Service,
    bir1_policy,
    parse_legal_entity,
    resolve_entity,
)
from distress_radar.acquisition.universe_discovery import load_segment

REPO_ROOT = Path(__file__).resolve().parents[2]
BIR1 = REPO_ROOT / "tests" / "fixtures" / "bir1"
SEGMENT = load_segment(REPO_ROOT / "config" / "segments" / "construction_sme_v1.yaml")
NOW = datetime(2026, 9, 14, 12, 0, tzinfo=UTC)

CASES = {
    "0000163893": "legal_entity",
    "0000028301": "wrong_pkd",
    "0000040289": "legal_form_mismatch",
    "9999999999": "not_found",
    "0000000000": "natural_person",
}


def _fixture(name: str) -> str:
    return (BIR1 / f"{name}.xml").read_text(encoding="utf-8")


class FixtureBir1Service:
    """Serves recorded BIR1 payloads; `overrides` patches individual reports."""

    service_url = "https://bir1.test/UslugaBIRzewnPubl.svc"

    def __init__(self, overrides: dict[str, str] | None = None) -> None:
        self.calls: list[tuple[str, ...]] = []
        self._regon_to_case: dict[str, str] = {}
        self._overrides = overrides or {}

    def search_by_krs(self, krs: str) -> str:
        self.calls.append(("search", krs))
        case = CASES[krs]
        xml = _fixture(f"{case}_search")
        if "<Regon>" in xml:
            regon = xml.split("<Regon>")[1].split("</Regon>")[0]
            self._regon_to_case[regon] = case
        return xml

    def full_report(self, regon: str, report_name: str) -> str:
        self.calls.append(("report", regon, report_name))
        suffix = {REPORT_LEGAL_PERSON: "report", REPORT_LEGAL_PERSON_PKD: "pkd"}[report_name]
        key = f"{self._regon_to_case[regon]}_{suffix}"
        return self._overrides.get(key) or _fixture(key)


def _resolve(krs: str, service: FixtureBir1Service | None = None, **kwargs: object):
    store = InMemoryObjectStore()
    result = resolve_entity(
        krs,
        service=service or FixtureBir1Service(),
        store=store,
        segment=SEGMENT,
        ingestion_run_id="run-1",
        clock=lambda: NOW,
        **kwargs,  # type: ignore[arg-type]
    )
    return result, store


def test_parses_reports_into_bir1_legal_entity():
    entity = parse_legal_entity(
        _fixture("legal_entity_report").encode(), _fixture("legal_entity_pkd").encode()
    )

    assert isinstance(entity, Bir1LegalEntity)
    assert entity.regon == "430036025"
    assert entity.nip == "7160004884"
    assert entity.krs == "0000163893"
    assert entity.name.startswith("PRZEDSIĘBIORSTWO BUDOWLANE MARBUD")
    assert entity.legal_form_code == "117"
    assert entity.start_date == date(1991, 10, 8)
    assert entity.end_date is None
    assert entity.status == "active"
    assert entity.predominant_pkd is not None
    assert entity.predominant_pkd.code == "4120Z"
    assert {p.version for p in entity.pkd_codes} == {"2007"}
    assert sum(p.predominant for p in entity.pkd_codes) == 1


def test_valid_legal_entity_becomes_entity_master_row():
    result, store = _resolve("0000163893")

    assert result.quarantine == []
    assert result.entity is not None
    row = result.entity
    assert (row.krs, row.regon, row.nip, row.legal_form_code) == (
        "0000163893",
        "430036025",
        "7160004884",
        "117",
    )
    assert row.status == "active"
    assert row.pkd_predominant == "4120Z"
    assert row.known_from == NOW.date()
    assert row.ingestion_run_id == "run-1"
    # lineage: search + two reports stored, entity points at the report hashes
    assert len(result.raw_fetches) == 3
    report_bytes = _fixture("legal_entity_report").encode("utf-8")
    assert row.source_document_hash == sha256_hex(report_bytes)
    assert store.get(raw_key(row.source_document_hash)) == report_bytes
    assert store.exists(raw_key(row.pkd_source_document_hash))


def test_raw_write_happens_before_parse():
    store_at_parse: list[set[str]] = []
    store = InMemoryObjectStore()

    def recording_parse(report: bytes, pkd: bytes) -> Bir1LegalEntity:
        store_at_parse.append(set(store.objects))
        assert store.objects[raw_key(sha256_hex(report))] == report
        assert store.objects[raw_key(sha256_hex(pkd))] == pkd
        return parse_legal_entity(report, pkd)

    resolve_entity(
        "0000163893",
        service=FixtureBir1Service(),
        store=store,
        segment=SEGMENT,
        ingestion_run_id="run-1",
        clock=lambda: NOW,
        parse=recording_parse,
    )

    assert len(store_at_parse) == 1
    assert len(store_at_parse[0]) == 6  # 3 objects + 3 sidecars already written


def test_natural_person_writes_no_raw_object_and_only_a_quarantine_row():
    result, store = _resolve("0000000000")

    assert store.objects == {}
    assert result.raw_fetches == []
    assert result.entity is None
    assert result.reconciliations == []
    assert len(result.quarantine) == 1
    row = result.quarantine[0]
    assert (row.stage, row.entity_key, row.reason_code) == ("A2", "0000000000", "natural_person")
    assert row.source_document_hash is None
    assert "SYNTHETIC" not in row.detail  # no name persisted


def test_not_found_is_quarantined_with_lineage():
    result, store = _resolve("9999999999")

    assert result.entity is None
    assert [q.reason_code for q in result.quarantine] == ["not_found"]
    search_hash = sha256_hex(_fixture("not_found_search").encode())
    assert result.quarantine[0].source_document_hash == search_hash
    assert store.exists(raw_key(search_hash))


def test_wrong_pkd_is_quarantined():
    result, _ = _resolve("0000028301")

    assert result.entity is None
    assert [q.reason_code for q in result.quarantine] == ["pkd_section_mismatch"]
    assert "2511Z" in result.quarantine[0].detail
    assert len(result.raw_fetches) == 3  # legal entity: raw kept for audit


def test_legal_form_mismatch_is_quarantined():
    result, _ = _resolve("0000040289")

    assert result.entity is None
    assert [q.reason_code for q in result.quarantine] == ["legal_form_mismatch"]
    assert "116" in result.quarantine[0].detail


def test_deregistered_is_recorded_not_rejected():
    report = _fixture("legal_entity_report").replace(
        "<praw_dataZakonczeniaDzialalnosci />",
        "<praw_dataZakonczeniaDzialalnosci>2024-03-01</praw_dataZakonczeniaDzialalnosci>",
    )
    service = FixtureBir1Service(overrides={"legal_entity_report": report})

    result, _ = _resolve("0000163893", service)

    assert result.quarantine == []
    assert result.entity is not None
    assert result.entity.status == "deregistered"


def test_seed_hint_disagreement_is_logged_with_bir1_precedence():
    result, _ = _resolve("0000163893", regon_hint="999999999", nip_hint="7160004884")

    assert result.entity is not None
    assert result.entity.regon == "430036025"
    assert [
        (r.field, r.seed_value, r.bir1_value, r.resolution) for r in result.reconciliations
    ] == [("regon", "999999999", "430036025", "bir1_precedence")]


def test_resolution_is_idempotent():
    first, store = _resolve("0000163893")
    objects = dict(store.objects)

    second = resolve_entity(
        "0000163893",
        service=FixtureBir1Service(),
        store=store,
        segment=SEGMENT,
        ingestion_run_id="run-1",
        clock=lambda: NOW,
    )

    assert store.objects == objects
    assert second.entity == first.entity
    assert second.raw_fetches == first.raw_fetches


# --- ZeepBir1Service session handling (fake SOAP proxy, no network) ---------------------------


class FakeSoap:
    def __init__(self, search_results: list[object]) -> None:
        self.search_results = list(search_results)
        self.logins = 0
        self.logouts: list[str] = []
        self.session_status = "1"

    def Zaloguj(self, pKluczUzytkownika: str) -> str:
        self.logins += 1
        self.session_status = "1"
        return f"sid-{self.logins}"

    def Wyloguj(self, pIdentyfikatorSesji: str) -> bool:
        self.logouts.append(pIdentyfikatorSesji)
        return True

    def GetValue(self, pNazwaParametru: str) -> str:
        return {"StatusSesji": self.session_status, "KomunikatKod": "7"}[pNazwaParametru]

    def DaneSzukajPodmioty(self, pParametryWyszukiwania: dict[str, str]) -> str | None:
        outcome = self.search_results.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        if outcome is None:
            self.session_status = "0"
        return outcome  # type: ignore[return-value]

    def DanePobierzPelnyRaport(self, pRegon: str, pNazwaRaportu: str) -> str:
        raise NotImplementedError


def _service(soap: FakeSoap, session: requests.Session | None = None) -> ZeepBir1Service:
    policy = bir1_policy(requests_per_minute=10_000)
    return ZeepBir1Service(
        ENDPOINTS["test"],
        "key",
        limiter=in_memory_limiter(policy),
        policy=policy,
        soap=soap,  # type: ignore[arg-type]
        session=session,
        wait=wait_none(),
    )


def test_expired_session_triggers_one_relogin():
    soap = FakeSoap([None, "<root/>"])
    session = requests.Session()

    with _service(soap, session) as service:
        assert service.search_by_krs("0000163893") == "<root/>"
        assert session.headers["sid"] == "sid-2"

    assert soap.logins == 2
    assert soap.logouts == ["sid-2"]
    assert "sid" not in session.headers


def test_empty_result_after_relogin_is_permanent():
    soap = FakeSoap([None, None])

    with pytest.raises(PermanentSourceError):
        _service(soap).search_by_krs("0000163893")
    assert soap.logins == 2


def test_transient_network_errors_retry_then_succeed():
    soap = FakeSoap([requests.ConnectionError("reset"), requests.Timeout("slow"), "<root/>"])

    assert _service(soap).search_by_krs("0000163893") == "<root/>"
    assert soap.logins == 1
