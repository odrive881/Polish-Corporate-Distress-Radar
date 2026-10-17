"""Error reporting (plan 0018 step H, decision 8): off without a DSN, and nothing personal sent."""

from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any

import dagster as dg
import pytest
import sentry_sdk
from dagster_defs.jobs import HOOKS
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sentry_sdk.envelope import Envelope
from sentry_sdk.transport import Transport

from distress_radar import errors
from distress_radar.settings import Settings

DSN = "https://0123456789abcdef@o1.ingest.de.sentry.io/1"
PESEL = "44051401458"
NAME = "Jan Kowalski"
KRS = "0000123456"
FILE_NAME = "sprawozdanie_kowalski_2024.xml"
SECRETS = (PESEL, NAME, KRS, FILE_NAME, "Kowalski")


class Recording(Transport):
    def __init__(self) -> None:
        super().__init__()
        self.events: list[dict[str, Any]] = []

    def capture_envelope(self, envelope: Envelope) -> None:
        for item in envelope.items:
            if item.type == "event" and item.payload.json is not None:
                self.events.append(item.payload.json)


@pytest.fixture
def transport() -> Iterator[Recording]:
    recording = Recording()
    try:
        assert errors.init_error_reporting(Settings(sentry_dsn=SecretStr(DSN)), transport=recording)
        yield recording
    finally:
        sentry_sdk.get_client().close()
        sentry_sdk.get_global_scope().set_client(None)


def _fail(record: str) -> None:
    pesel, name = PESEL, NAME  # locals a frame would otherwise carry  # noqa: F841
    raise ValueError(f"KRS {record}: {NAME} (PESEL {PESEL}) filed {FILE_NAME}")


def _sent(transport: Recording) -> str:
    (event,) = transport.events
    return json.dumps(event, ensure_ascii=False)


def test_without_a_dsn_nothing_is_installed():
    assert errors.init_error_reporting(Settings(sentry_dsn=None)) is False
    assert not sentry_sdk.get_client().is_active()
    errors.report(ValueError(KRS))  # and reporting is a quiet no-op


def test_a_dsn_outside_the_eu_region_is_refused():
    with pytest.raises(ValueError, match="EU region"):
        errors.init_error_reporting(
            Settings(sentry_dsn=SecretStr("https://abc@o1.ingest.us.sentry.io/1"))
        )
    assert not sentry_sdk.get_client().is_active()


def test_no_pesel_name_krs_or_file_name_survives(transport: Recording):
    with sentry_sdk.new_scope() as scope:
        scope.set_extra("statement", FILE_NAME)
        scope.set_extra("entity", KRS)
        scope.set_user({"username": NAME})
        sentry_sdk.add_breadcrumb(message=f"read {FILE_NAME}")
        try:
            _fail(KRS)
        except ValueError as exc:
            errors.report(exc, job="features", asset="feature_store")
    sent = _sent(transport)
    assert not [s for s in SECRETS if s in sent]
    (event,) = transport.events
    (value,) = event["exception"]["values"]
    assert "value" not in value and value["type"] == "ValueError"
    assert [f["function"] for f in value["stacktrace"]["frames"]][-1] == "_fail"
    assert all(
        set(f) <= {"function", "module", "lineno", "in_app", "filename"}
        for f in value["stacktrace"]["frames"]
    )
    assert event["tags"] == {"dagster_job": "features", "dagster_asset": "feature_store"}
    assert not {"extra", "user", "breadcrumbs", "server_name", "contexts", "request"} & set(event)


def test_a_logged_message_is_not_sent(transport: Recording):
    sentry_sdk.capture_message(f"{NAME} {KRS}")
    sentry_sdk.flush()
    assert transport.events == []


def test_a_failed_asset_reports_its_job_and_asset(transport: Recording):
    @dg.asset(name="failing_asset")
    def failing_asset() -> None:
        _fail(KRS)

    job = dg.define_asset_job("failing_job", selection=["failing_asset"], hooks=HOOKS)
    result = (
        dg.Definitions(assets=[failing_asset], jobs=[job])
        .resolve_job_def("failing_job")
        .execute_in_process(raise_on_error=False)
    )
    assert not result.success
    sent = _sent(transport)
    assert not [s for s in SECRETS if s in sent]
    assert transport.events[0]["tags"] == {
        "dagster_job": "failing_job",
        "dagster_asset": "failing_asset",
    }


def test_an_api_error_is_reported_without_its_request(transport: Recording):
    app = FastAPI()

    @app.get("/boom")
    def boom() -> None:  # pyright: ignore[reportUnusedFunction]
        _fail(KRS)

    TestClient(app, raise_server_exceptions=False).get(f"/boom?krs={KRS}&file={FILE_NAME}")
    sentry_sdk.flush()
    assert not [s for s in SECRETS if s in _sent(transport)]
