"""A4 MSiG client (plan 0008 step E), against a mock transport. Every person here is made up."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest
from tenacity import wait_none

from distress_radar.acquisition.base import SourcePolicy, build_source_client, in_memory_limiter
from distress_radar.acquisition.msig_client import (
    MSIG_EXTRACTION_VERSION,
    MsigResult,
    chapter_code,
    extraction_key,
    fetch_entity_notices,
    load_vocabulary,
    reduce_notice,
)
from distress_radar.acquisition.raw_store import InMemoryObjectStore, raw_key, sidecar_key
from distress_radar.acquisition.redaction import RedactionError

POLICY = SourcePolicy(name="msig_test", requests_per_minute=10_000, max_attempts=2)
KRS = "0000000042"
NOW = datetime(2026, 9, 23, 9, 0, tzinfo=UTC)
VOCABULARY = load_vocabulary()


def notice(notice_id: int = 1, *, krs: str = KRS, body: str | None = None) -> dict[str, Any]:
    return {
        "id": notice_id,
        "idNext": notice_id + 1,
        "idPrevious": notice_id - 1,
        "krs": krs,
        "entityName": "BUDOWLANKA PRZYKŁADOWA SPÓŁKA Z OGRANICZONĄ ODPOWIEDZIALNOŚCIĄ w Przykładowie.",
        "numberOfNotice": "6756",
        "page": 26,
        "monitorNumber": "38/2017",
        "dateOfPublication": "2017-02-23T00:00:00",
        "chapterName": "III. OGŁOSZENIA WYMAGANE PRZEZ PRAWO UPADŁOŚCIOWE/9. Inne",
        "signatureOfCase": "VIII GU 45/17.",
        "textInPosition": (
            "Poz. 6756. BUDOWLANKA PRZYKŁADOWA SPÓŁKA Z OGRANICZONĄ ODPOWIEDZIALNOŚCIĄ w Przykładowie. "
            "KRS 0000000042. SĄD REJONOWY W PRZYKŁADOWIE, wpis do rejestru: 12 grudnia 2001 r. "
            "Sygn. akt VIII GU 45/17."
        ),
        "textInBody": body
        if body is not None
        else (
            "Sąd Rejonowy w Przykładowie obwieszcza, że w sprawie z wniosku o ogłoszenie upadłości "
            "postanowieniem z 9 lutego 2017 r. zabezpieczył majątek dłużnika przez ustanowienie "
            "tymczasowego nadzorcy sądowego w osobie Zenobiusza Przykładowskiego. Posiedzenie "
            "wyznaczono na 5.06.2019, a zgromadzenie wierzycieli na 10 PAŹDZIERNIKA 2019. "
            "Pismo z 31.02.2019 r. W sprawie KI1L/GU/43/2025 bez zmian."
        ),
    }


def _record(**kwargs: Any) -> dict[str, Any]:
    return json.loads(reduce_notice(notice(**kwargs), VOCABULARY))


def test_chapter_codes() -> None:
    assert (
        chapter_code("III. PRAWO UPADŁOŚCIOWE I NAPRAWCZE/1. Postanowienie o ogłoszeniu upadłości")
        == "III/1"
    )
    assert (
        chapter_code("III. OGŁOSZENIA WYMAGANE PRZEZ PRAWO UPADŁOŚCIOWE /3. Ogłoszenie") == "III/3"
    )
    assert chapter_code("IX. OGŁOSZENIA WYMAGANE PRZEZ PRAWO RESTRUKTURYZACYJNE") == "IX"
    assert (
        chapter_code("I. OGŁOSZENIA WYMAGANE PRZEZ KODEKS SPÓŁEK HANDLOWYCH/2. Spółki z o.o.")
        == "I/2"
    )
    assert chapter_code("Inne") is None


def test_the_record_holds_no_text_and_no_names() -> None:
    data = reduce_notice(notice(), VOCABULARY).decode("utf-8")
    for fragment in (
        "Zenobiusz",
        "Przykładowski",
        "textInBody",
        "textInPosition",
        "idNext",
        "obwieszcza, że",
    ):
        assert fragment not in data


def test_structured_fields_are_kept() -> None:
    kept = _record()["notice"]
    assert kept["id"] == 1 and kept["krs"] == KRS and kept["monitorNumber"] == "38/2017"
    assert kept["chapterName"].endswith("/9. Inne")


def test_dates_in_every_format_with_their_context() -> None:
    dates = _record()["extracted"]["dates"]
    assert [(d["date"], d["in"]) for d in dates] == [
        ("2001-12-12", "position"),
        ("2017-02-09", "body"),
        ("2019-06-05", "body"),
        ("2019-10-10", "body"),
    ]  # 31.02.2019 is no calendar date and is skipped
    assert dates[0]["terms_before"] == ["wpis do rejestru"]
    assert {"postanowieni", "upadłoś"} <= set(dates[1]["terms_before"])


def test_signatures_from_the_field_and_the_text() -> None:
    assert _record()["extracted"]["signatures"] == ["VIII GU 45/17", "KI1L/GU/43/2025"]


def test_terms_and_versions() -> None:
    extracted = _record()["extracted"]
    assert {"zabezpiecz", "tymczasow", "nadzorc", "zgromadzeni"} <= set(extracted["terms"])
    assert extracted["extraction_version"] == MSIG_EXTRACTION_VERSION
    assert extracted["vocabulary_sha256"] == VOCABULARY.sha256
    assert extracted["chapter_code"] == "III/9"


def test_deterministic() -> None:
    assert reduce_notice(notice(), VOCABULARY) == reduce_notice(notice(), VOCABULARY)


def test_fails_closed_on_a_pesel_in_a_kept_field() -> None:
    bad = notice()
    bad["numberOfNotice"] = "90010112345"
    with pytest.raises(RedactionError, match="11-digit"):
        reduce_notice(bad, VOCABULARY)


def test_the_extraction_key_changes_with_the_vocabulary() -> None:
    edited = VOCABULARY.model_copy(update={"sha256": "0" * 64})
    assert extraction_key(edited) != extraction_key(VOCABULARY)
    assert extraction_key(VOCABULARY).startswith(f"{MSIG_EXTRACTION_VERSION}+")


def test_vocabulary_terms_are_generic_lowercase() -> None:
    assert all(t == t.lower() for t in VOCABULARY.terms)
    assert len(set(VOCABULARY.terms)) == len(VOCABULARY.terms)


# --- fetching ------------------------------------------------------------------------------------


def _search_page(ids: list[int]) -> dict[str, Any]:
    return {
        "countPages": 100,
        "page": 1,
        "list": [
            {"id": i, "monitorNumber": "38/2017", "entityName": "X", "signatureOfCase": None}
            for i in ids
        ],
    }


def _run(
    tmp_path: Path,
    routes: dict[str, Any],
    *,
    known: dict[int, str] | None = None,
    store: InMemoryObjectStore | None = None,
) -> tuple[MsigResult, list[httpx.Request], InMemoryObjectStore]:
    calls: list[httpx.Request] = []
    target = store or InMemoryObjectStore()

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        path = request.url.path.rsplit("/", 1)[-1]
        key = path if path != "Search" else f"Search:{request.url.params['page']}"
        key = key if path != "Detalis" else f"Detalis:{request.url.params['Id']}"
        return httpx.Response(200, json=routes[key])

    async def run() -> MsigResult:
        client = build_source_client(
            POLICY,
            cache_dir=tmp_path / "cache",
            limiter=in_memory_limiter(POLICY),
            transport=httpx.MockTransport(handler),
            wait=wait_none(),
        )
        async with client:
            return await fetch_entity_notices(
                KRS,
                client=client,
                store=target,
                vocabulary=VOCABULARY,
                ingestion_run_id="run-1",
                known=known or {},
                today=date(2026, 9, 23),
                now=lambda: NOW,
            )

    return asyncio.run(run()), calls, target


ROUTES: dict[str, Any] = {
    "SearchCount": 2,
    "Search:1": _search_page([1, 2]),
    "Search:2": _search_page([3]),
    "Detalis:1": notice(1),
    "Detalis:2": notice(2),
    "Detalis:3": notice(3, krs="0000000999"),
}


def test_pages_are_stored_as_received_and_notices_reduced(tmp_path: Path) -> None:
    result, calls, store = _run(tmp_path, ROUTES)

    assert calls[0].url.params["from"] == "2001-01-01" and calls[0].url.params["to"] == "2026-09-23"
    assert [n.notice_id for n in result.notices] == [1, 2]
    assert {n.extraction_version for n in result.notices} == {extraction_key(VOCABULARY)}
    stored_page = store.get(raw_key(result.raw_fetches[0].sha256))
    assert json.loads(stored_page) == ROUTES["Search:1"]
    sidecar = json.loads(store.get(sidecar_key(result.notices[0].sha256)))
    assert sidecar["redaction_version"] == extraction_key(VOCABULARY) and sidecar["received_sha256"]
    assert b"Przyk\xc5\x82adowskiego" not in store.get(raw_key(result.notices[0].sha256))
    assert result.fetch is not None and result.fetch.source == "MSiG" and result.fetch.stored_new
    assert result.fetch.sha256 == result.raw_fetches[0].sha256  # the first search page


def test_a_notice_for_another_krs_is_quarantined_not_stored(tmp_path: Path) -> None:
    result, _, _ = _run(tmp_path, ROUTES)
    [row] = result.quarantine
    assert (row.stage, row.reason_code, row.entity_key) == (
        "A4",
        "msig_krs_mismatch",
        f"{KRS}:msig:3",
    )
    assert 3 not in {n.notice_id for n in result.notices}


def test_known_notices_are_not_fetched_again(tmp_path: Path) -> None:
    first, _, store = _run(tmp_path, ROUTES)
    known = {n.notice_id: n.sha256 for n in first.notices}
    second, calls, _ = _run(tmp_path / "again", ROUTES, known=known, store=store)

    fetched = {c.url.params.get("Id") for c in calls if c.url.path.endswith("Detalis")}
    assert fetched == {"3"}  # only the one never stored
    assert second.notices == [] and second.fetch is not None and not second.fetch.stored_new
    assert first.fetch is not None and second.fetch.content_sha256 == first.fetch.content_sha256


FIXTURES = Path(__file__).parent.parent / "fixtures" / "legal" / "msig"


def test_fixture_records_match_the_current_vocabulary() -> None:
    records = sorted(FIXTURES.glob("notice_*.json"))
    assert records, "tests/fixtures/legal/msig/ has no notice records"
    for path in records:
        extracted = json.loads(path.read_text(encoding="utf-8"))["extracted"]
        assert extracted["extraction_version"] == MSIG_EXTRACTION_VERSION, path.name
        assert extracted["vocabulary_sha256"] == VOCABULARY.sha256, f"{path.name}: re-extract"
