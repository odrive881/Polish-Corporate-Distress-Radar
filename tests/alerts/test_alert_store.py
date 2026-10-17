"""The Postgres `alerts` table (plan 0018 step D): idempotent inserts, kept acknowledgements."""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta

import psycopg
import pytest

from distress_radar.alerts import store
from distress_radar.alerts.rules import Alert, alert_id
from distress_radar.settings import Settings

T0 = datetime(2026, 10, 17, 12, 0, tzinfo=UTC)


@pytest.fixture
def conn() -> Iterator[psycopg.Connection]:
    schema = f"test_alerts_{uuid.uuid4().hex[:12]}"
    with psycopg.connect(Settings().postgres_conninfo, autocommit=True) as connection:
        connection.execute(f'CREATE SCHEMA "{schema}"')  # type: ignore[arg-type]
        connection.execute(f'SET search_path TO "{schema}"')  # type: ignore[arg-type]
        try:
            store.ensure_schema(connection)
            store.ensure_schema(connection)  # idempotent DDL (ADR 0006)
            yield connection
        finally:
            connection.execute(f'DROP SCHEMA "{schema}" CASCADE')  # type: ignore[arg-type]


def _alert(**changes: object) -> Alert:
    values: dict[str, object] = {
        "alert_id": alert_id("alert_rules_v1", "tripwire_triggered", "0000000001", "x"),
        "krs": "0000000001",
        "as_of_date": date(2026, 10, 31),
        "alert_type": "tripwire_triggered",
        "severity": "high",
        "rule_version": "alert_rules_v1",
        "known_from": date(2026, 7, 1),
        "detail": {"feature": "art233_triggered"},
    }
    return Alert(**(values | changes))  # type: ignore[arg-type]


@pytest.mark.integration
def test_a_rerun_inserts_nothing_and_an_acknowledgement_stays(conn: psycopg.Connection):
    assert store.insert_alerts(conn, [_alert()], T0) == 1
    assert store.acknowledge(conn, [_alert().alert_id], T0 + timedelta(hours=1)) == 1

    assert store.insert_alerts(conn, [_alert()], T0 + timedelta(days=1)) == 0
    assert store.acknowledge(conn, [_alert().alert_id], T0 + timedelta(days=2)) == 0
    assert conn.execute("SELECT created_at, acknowledged_at, detail FROM alerts").fetchall() == [
        (T0, T0 + timedelta(hours=1), {"feature": "art233_triggered"})
    ]


@pytest.mark.integration
def test_the_table_refuses_what_the_rules_never_produce(conn: psycopg.Connection):
    late = _alert(alert_id="b" * 64, known_from=date(2026, 11, 1))  # known after its as_of_date
    with pytest.raises(psycopg.errors.CheckViolation):
        store.insert_alerts(conn, [late], T0)
    scoreless = _alert(alert_id="c" * 64, alert_type="threshold_crossed")  # no triggering score
    with pytest.raises(psycopg.errors.CheckViolation):
        store.insert_alerts(conn, [scoreless], T0)
