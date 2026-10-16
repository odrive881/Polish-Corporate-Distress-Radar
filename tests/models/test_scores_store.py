"""`scores_history` and `watchlists` against a live Postgres (`make dev-up`; run via
`make test-integration`). Each test runs in a throwaway schema."""

import uuid
from collections.abc import Iterator
from datetime import UTC, date, datetime

import psycopg
import pytest
from psycopg.types.json import Jsonb

from distress_radar.models import scores_store
from distress_radar.settings import Settings

pytestmark = pytest.mark.integration

NOW = datetime(2026, 10, 13, 12, 0, tzinfo=UTC)
COLUMNS = (
    "krs, as_of_date, horizon_months, probability, not_scored_reason, model_name, model_version,"
    " model_alias, mlflow_run_id, training_eve, code_commit, data_snapshot_hash, label_version,"
    " label_set_hash, feature_set_version, feature_set_hash, population_hash, top_drivers,"
    " missing_inputs, scored_at"
)


@pytest.fixture
def conn() -> Iterator[psycopg.Connection]:
    schema = f"test_scores_{uuid.uuid4().hex[:12]}"
    with psycopg.connect(Settings().postgres_conninfo, autocommit=True) as connection:
        connection.execute(f'CREATE SCHEMA "{schema}"')  # type: ignore[arg-type]
        connection.execute(f'SET search_path TO "{schema}"')  # type: ignore[arg-type]
        try:
            yield connection
        finally:
            connection.execute(f'DROP SCHEMA "{schema}" CASCADE')  # type: ignore[arg-type]


def _row(**changes: object) -> dict[str, object]:
    row: dict[str, object] = {
        "krs": "0000000001",
        "as_of_date": date(2026, 9, 30),
        "horizon_months": 12,
        "probability": 0.08,
        "not_scored_reason": None,
        "model_name": "distress_radar_12m",
        "model_version": "3",
        "model_alias": "incumbent",
        "mlflow_run_id": "f" * 32,
        "training_eve": date(2024, 12, 31),
        "code_commit": "a" * 40,
        "data_snapshot_hash": "b" * 64,
        "label_version": "label_v2",
        "label_set_hash": "c" * 64,
        "feature_set_version": "feature_set_v6",
        "feature_set_hash": "d" * 64,
        "population_hash": "e" * 64,
        "top_drivers": Jsonb([{"feature": "roa", "contribution": 0.4}]),
        "missing_inputs": None,
        "scored_at": NOW,
    }
    row.update(changes)
    return row


def _insert(conn: psycopg.Connection, row: dict[str, object]) -> int:
    placeholders = ", ".join(["%s"] * len(row))
    cursor = conn.execute(
        f"INSERT INTO scores_history ({COLUMNS}) VALUES ({placeholders})"  # type: ignore[arg-type]
        " ON CONFLICT DO NOTHING",
        tuple(row.values()),
    )
    return cursor.rowcount


def test_the_schema_is_idempotent_and_a_rerun_adds_nothing(conn: psycopg.Connection) -> None:
    scores_store.ensure_schema(conn)
    scores_store.ensure_schema(conn)
    assert _insert(conn, _row()) == 1
    # The same company, month-end, horizon and version: the first row stands.
    assert _insert(conn, _row(probability=0.5, scored_at=datetime(2026, 11, 1, tzinfo=UTC))) == 0
    assert _insert(conn, _row(model_version="4")) == 1
    assert _insert(conn, _row(horizon_months=24, model_name="distress_radar_24m")) == 1
    probability = conn.execute(
        "SELECT probability FROM scores_history WHERE model_version = '3' AND horizon_months = 12"
    ).fetchone()
    assert probability == (0.08,)


INCOMPLETE = {
    "probability": None,
    "not_scored_reason": "inputs incomplete",
    "top_drivers": None,
    "missing_inputs": ["roa"],
}


def test_a_row_not_scored_has_a_reason_and_no_probability_or_drivers(
    conn: psycopg.Connection,
) -> None:
    scores_store.ensure_schema(conn)
    assert _insert(conn, _row(**INCOMPLETE)) == 1
    for bad in (
        _row(krs="0000000002", probability=None, top_drivers=None),  # no reason
        _row(krs="0000000003", **{**INCOMPLETE, "probability": 0.2}),  # a reason and a score
        _row(krs="0000000004", **{**INCOMPLETE, "top_drivers": Jsonb([])}),  # drivers
        _row(krs="0000000005", probability=1.5),
    ):
        with pytest.raises(psycopg.errors.CheckViolation):
            _insert(conn, bad)


def test_missing_inputs_are_named_exactly_when_inputs_are_incomplete(
    conn: psycopg.Connection,
) -> None:
    scores_store.ensure_schema(conn)
    for bad in (
        _row(krs="0000000002", **{**INCOMPLETE, "missing_inputs": None}),  # unnamed
        _row(krs="0000000003", **{**INCOMPLETE, "missing_inputs": []}),  # named as none
        _row(krs="0000000004", missing_inputs=["roa"]),  # on a scored row
    ):
        with pytest.raises(psycopg.errors.CheckViolation):
            _insert(conn, bad)
    assert _insert(conn, _row(krs="0000000005", **INCOMPLETE)) == 1
    stored = conn.execute(
        "SELECT missing_inputs FROM scores_history WHERE krs = '0000000005'"
    ).fetchone()
    assert stored == (["roa"],)


def test_no_score_is_dated_on_or_before_its_training_eve(conn: psycopg.Connection) -> None:
    scores_store.ensure_schema(conn)
    with pytest.raises(psycopg.errors.CheckViolation):
        _insert(conn, _row(as_of_date=date(2024, 12, 31)))


def test_a_watchlist_holds_a_company_once(conn: psycopg.Connection) -> None:
    scores_store.ensure_schema(conn)
    insert = (
        "INSERT INTO watchlists (name, krs, added_at) VALUES (%s, %s, %s) ON CONFLICT DO NOTHING"
    )
    assert conn.execute(insert, ("budowlane", "0000000001", NOW)).rowcount == 1
    assert conn.execute(insert, ("budowlane", "0000000001", NOW)).rowcount == 0
    assert conn.execute(insert, ("inne", "0000000001", NOW)).rowcount == 1
    with pytest.raises(psycopg.errors.CheckViolation):
        conn.execute(insert, ("", "0000000002", NOW))


def test_insert_scores_adds_new_rows_only(conn: psycopg.Connection) -> None:
    scores_store.ensure_schema(conn)
    rows = [
        {**_row(), "top_drivers": '[{"feature": "roa"}]'},
        {
            **_row(krs="0000000002"),
            **INCOMPLETE,
        },
    ]
    assert scores_store.insert_scores(conn, rows) == 2
    assert scores_store.insert_scores(conn, rows) == 0
    assert scores_store.insert_scores(conn, []) == 0
    drivers = conn.execute(
        "SELECT top_drivers FROM scores_history WHERE krs = '0000000001'"
    ).fetchone()
    assert drivers == ([{"feature": "roa"}],)


def test_the_latest_label_set_of_a_version_is_found(conn: psycopg.Connection) -> None:
    from distress_radar.labels import LABEL_SETS_DDL
    from distress_radar.models.scoring import latest_label_set

    conn.execute(LABEL_SETS_DDL)
    insert = (
        "INSERT INTO label_sets (label_set_hash, label_version, cutoff_date, row_count,"
        " parquet_path, created_at, ingestion_run_id) VALUES (%s, %s, %s, 1, 'p', %s, 'r')"
    )
    conn.execute(insert, ("old", "label_v2", date(2026, 9, 1), NOW))
    conn.execute(insert, ("new", "label_v2", date(2026, 10, 9), NOW))
    conn.execute(insert, ("other", "label_v3", date(2026, 11, 1), NOW))
    assert latest_label_set(conn, "label_v2") == "new"
    with pytest.raises(LookupError, match="label_v1"):
        latest_label_set(conn, "label_v1")


def _exclusion(**changes: object) -> dict[str, object]:
    row: dict[str, object] = {
        "krs": "0000000001",
        "as_of_date": date(2026, 9, 30),
        "horizon_months": 12,
        "model_version": "3",
        "reason": "not_at_risk",
        "model_name": "distress_radar_12m",
        "population_hash": "e" * 64,
        "risk_label_set_hash": "c" * 64,
        "code_commit": "a" * 40,
        "data_snapshot_hash": "b" * 64,
        "scored_at": NOW,
    }
    row.update(changes)
    return row


def test_insert_exclusions_adds_new_rows_only_and_refuses_an_unknown_reason(
    conn: psycopg.Connection,
) -> None:
    scores_store.ensure_schema(conn)
    rows = [_exclusion(), _exclusion(krs="0000000002", reason="rdf_not_searched")]
    assert scores_store.insert_exclusions(conn, rows) == 2
    assert scores_store.insert_exclusions(conn, rows) == 0
    assert scores_store.insert_exclusions(conn, [_exclusion(model_version="4")]) == 1
    assert scores_store.insert_exclusions(conn, []) == 0
    with pytest.raises(psycopg.errors.CheckViolation):
        scores_store.insert_exclusions(conn, [_exclusion(krs="0000000003", reason="other")])


def test_the_exclusion_reasons_are_the_population_rules_and_not_at_risk(
    conn: psycopg.Connection,
) -> None:
    from distress_radar.models.population import REASONS
    from distress_radar.models.scoring import NOT_AT_RISK

    scores_store.ensure_schema(conn)
    rows = [
        _exclusion(krs=f"{i:010d}", reason=reason)
        for i, reason in enumerate([*REASONS, NOT_AT_RISK])
    ]
    assert scores_store.insert_exclusions(conn, rows) == len(rows)
