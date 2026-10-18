"""The retry of a registry check that stopped on a maintenance page (plan 0018, 2026-10-18)."""

from __future__ import annotations

import asyncio
import functools
from datetime import date, datetime

import dagster as dg
import httpx
import pytest
from dagster_defs import sensors
from dagster_defs.sensors import (
    MAX_RETRIES,
    RETRY_NUMBER,
    RETRY_OF,
    WARSAW,
    registry_check_retry,
    retry_number,
    retry_request,
)

from distress_radar.acquisition.availability import PROBE_KRS, probe_sources

MAINTENANCE = (
    "KRS API: krs_api is unavailable: maintenance page (Przerwa techniczna); "
    "stopped after 0 of 806 entities, nothing recorded for the rest"
)
UP: dict[str, str | None] = {"KRS API": None, "MSiG": None}
MORNING = datetime(2026, 10, 18, 9, 30, tzinfo=WARSAW)


@functools.cache
def _instance(description: str | None, tags: tuple[tuple[str, str], ...]) -> dg.DagsterInstance:
    """An instance whose latest `legal_to_labels` run failed with `description` (None: succeeded).
    Cached: building one takes seconds, and no test changes it."""

    @dg.op
    def fetch() -> None:
        if description is not None:
            raise dg.Failure(description=description)

    @dg.job(name="legal_to_labels")
    def job() -> None:
        fetch()

    instance = dg.DagsterInstance.ephemeral()
    job.execute_in_process(instance=instance, raise_on_error=False, tags=dict(tags))
    return instance


def _run(description: str | None, tags: dict[str, str] | None = None) -> dg.DagsterInstance:
    return _instance(description, tuple(sorted((tags or {}).items())))


def _evaluate(
    instance: dg.DagsterInstance, monkeypatch: pytest.MonkeyPatch, probe: dict[str, str | None]
):
    calls: list[date] = []

    async def fake_probe(today: date) -> dict[str, str | None]:
        calls.append(today)
        return probe

    monkeypatch.setattr(sensors, "probe_sources", fake_probe)
    monkeypatch.setattr(sensors, "datetime", _Frozen)
    result = registry_check_retry(dg.build_sensor_context(instance=instance))
    return result, calls


class _Frozen(datetime):
    @classmethod
    def now(cls, tz=None):  # type: ignore[override]
        return MORNING.astimezone(tz)


def test_a_run_stopped_on_a_maintenance_page_is_retried_once_the_sources_answer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    instance = _run(MAINTENANCE)
    failed = instance.get_runs(limit=1)[0]
    result, calls = _evaluate(instance, monkeypatch, UP)
    assert isinstance(result, dg.RunRequest)
    assert result.run_key == f"retry_{failed.run_id}"
    assert result.tags == {RETRY_OF: failed.run_id, RETRY_NUMBER: "1"}
    assert calls == [MORNING.date()]


def test_not_while_a_source_is_still_down(monkeypatch: pytest.MonkeyPatch) -> None:
    result, _ = _evaluate(
        _run(MAINTENANCE), monkeypatch, {"KRS API": None, "MSiG": "maintenance page"}
    )
    assert isinstance(result, dg.SkipReason)
    assert "MSiG" in (result.skip_message or "")


def test_another_failure_is_never_retried_and_nothing_is_probed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result, calls = _evaluate(_run("krs_extracts failed for 3 entities"), monkeypatch, UP)
    assert isinstance(result, dg.SkipReason)
    assert calls == []


def test_a_successful_run_is_not_retried(monkeypatch: pytest.MonkeyPatch) -> None:
    result, calls = _evaluate(_run(None), monkeypatch, UP)
    assert isinstance(result, dg.SkipReason)
    assert calls == []


def test_retries_stop_after_the_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    instance = _run(MAINTENANCE, {RETRY_OF: "night", RETRY_NUMBER: str(MAX_RETRIES)})
    result, calls = _evaluate(instance, monkeypatch, UP)
    assert isinstance(result, dg.SkipReason)
    assert calls == []


def test_a_retry_of_a_retry_keeps_the_night_it_belongs_to() -> None:
    failed = _run(MAINTENANCE, {RETRY_OF: "night", RETRY_NUMBER: "1"}).get_runs(limit=1)[0]
    number = retry_number(failed, [MAINTENANCE], MORNING)
    assert number == 2
    request = retry_request(failed, 2, UP)
    assert isinstance(request, dg.RunRequest)
    assert request.tags == {RETRY_OF: "night", RETRY_NUMBER: "2"}


@pytest.mark.parametrize("hour", [22, 23, 0, 1])
def test_not_in_the_hours_before_the_nights_own_run(hour: int) -> None:
    failed = _run(MAINTENANCE).get_runs(limit=1)[0]
    late = MORNING.replace(hour=hour)
    assert isinstance(retry_number(failed, [MAINTENANCE], late), dg.SkipReason)


def test_the_sensor_ships_stopped_and_requests_the_registry_check() -> None:
    assert registry_check_retry.default_status == dg.DefaultSensorStatus.STOPPED
    assert registry_check_retry.job_name == "legal_to_labels"


# --- the probe -----------------------------------------------------------------------------------


def _transport(answer: httpx.Response) -> tuple[httpx.MockTransport, list[httpx.Request]]:
    seen: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return answer

    return httpx.MockTransport(handle), seen


def test_the_probe_asks_about_a_number_no_company_has() -> None:
    transport, seen = _transport(httpx.Response(404, json={"message": "not found"}))
    assert asyncio.run(probe_sources(date(2026, 10, 18), transport)) == UP
    assert len(seen) == 2
    assert all(PROBE_KRS in str(r.url) for r in seen)


def test_the_probe_reads_a_maintenance_page_as_down() -> None:
    page = httpx.Response(
        200,
        headers={"content-type": "text/html"},
        text="<html>Przerwa techniczna: trwa przerwa techniczna w dostępie do systemu</html>",
    )
    transport, _ = _transport(page)
    found = asyncio.run(probe_sources(date(2026, 10, 18), transport))
    assert set(found) == {"KRS API", "MSiG"}
    assert all(v and "maintenance" in v for v in found.values())


def test_the_probe_reads_a_server_error_as_down() -> None:
    transport, _ = _transport(httpx.Response(503))
    found = asyncio.run(probe_sources(date(2026, 10, 18), transport))
    assert found == {"KRS API": "HTTP 503", "MSiG": "HTTP 503"}
