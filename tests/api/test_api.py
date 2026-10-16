"""The scores API (plan 0016 step D): every response carries the served versions and the
disclaimer, it binds to localhost, it refuses a database scored by another version, and each
route's shape. Route tests run against a live Postgres (`make dev-up`; `make test-integration`),
each in a throwaway schema."""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from datetime import UTC, date, datetime
from typing import Any

import psycopg
import pytest
from fastapi.testclient import TestClient

from distress_radar.api import main
from distress_radar.api.schemas import DISCLAIMER
from distress_radar.models import scores_store
from distress_radar.models.serving import ServedVersion
from distress_radar.settings import Settings

NOW = datetime(2026, 10, 16, 12, 0, tzinfo=UTC)
DAY = date(2026, 9, 30)
EARLIER = date(2026, 8, 31)


def _served(horizon: int, version: str = "4") -> ServedVersion:
    return ServedVersion(
        name=f"distress_radar_{horizon}m",
        version=version,
        alias="incumbent",
        run_id="f" * 32,
        model="logistic_regression",
        horizon_months=horizon,
        test_year=2025,
        tags={
            "code_commit": "1" * 40,
            "data_snapshot_hash": "2" * 64,
            "label_version": "outcome_labels_v2",
            "label_set_hash": "c" * 64,
            "feature_set_version": "feature_set_v6",
            "feature_set_hash": "d" * 64,
        },
    )


SERVED = {12: _served(12), 24: _served(24)}


def _score(krs: str, horizon: int, day: date = DAY, **changes: object) -> dict[str, object]:
    row: dict[str, object] = {
        "krs": krs,
        "as_of_date": day,
        "horizon_months": horizon,
        "probability": 0.05,
        "not_scored_reason": None,
        "model_name": f"distress_radar_{horizon}m",
        "model_version": "4",
        "model_alias": "incumbent",
        "mlflow_run_id": "f" * 32,
        "training_eve": date(2024, 12, 31),
        "code_commit": "a" * 40,
        "data_snapshot_hash": "b" * 64,
        "label_version": "outcome_labels_v2",
        "label_set_hash": "c" * 64,
        "feature_set_version": "feature_set_v6",
        "feature_set_hash": "d" * 64,
        "population_hash": "e" * 64,
        "top_drivers": '[{"feature": "roa", "contribution": 0.4}]',
        "missing_inputs": None,
        "scored_at": NOW,
    }
    row.update(changes)
    return row


def _exclusion(krs: str, horizon: int, reason: str, day: date = DAY) -> dict[str, object]:
    return {
        "krs": krs,
        "as_of_date": day,
        "horizon_months": horizon,
        "model_version": "4",
        "reason": reason,
        "model_name": f"distress_radar_{horizon}m",
        "population_hash": "e" * 64,
        "risk_label_set_hash": "c" * 64,
        "code_commit": "a" * 40,
        "data_snapshot_hash": "b" * 64,
        "scored_at": NOW,
    }


def test_the_server_binds_to_localhost_only(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, Any] = {}
    monkeypatch.setattr(main, "app_from_settings", lambda: "app")
    monkeypatch.setattr(main.uvicorn, "run", lambda app, **kw: seen.update(app=app, **kw))  # type: ignore[reportUnknownLambdaType]
    main.main()
    assert seen == {"app": "app", "host": "127.0.0.1", "port": main.PORT}


def test_meta_states_the_served_versions_and_the_disclaimer() -> None:
    # No `with`: the lifespan (which needs Postgres) does not run, and /meta reads no table.
    client = TestClient(main.create_app(SERVED, lambda: pytest.fail("no connection expected")))
    body = client.get("/meta").json()
    assert body["disclaimer"] == DISCLAIMER and body["data"] is None
    served = body["meta"]["served"]
    assert [s["horizon_months"] for s in served] == [12, 24]
    assert served[0]["model_version"] == "4" and served[0]["training_eve"] == "2024-12-31"
    assert served[0]["label_set_hash"] == "c" * 64 and served[0]["code_commit"] == "1" * 40


def test_an_app_with_nothing_served_is_refused() -> None:
    with pytest.raises(ValueError, match="no served version"):
        main.create_app({}, lambda: pytest.fail("no connection expected"))


@pytest.fixture
def schema() -> Iterator[str]:
    name = f"test_api_{uuid.uuid4().hex[:12]}"
    with psycopg.connect(Settings().postgres_conninfo, autocommit=True) as conn:
        conn.execute(f'CREATE SCHEMA "{name}"')  # type: ignore[arg-type]
        try:
            yield name
        finally:
            conn.execute(f'DROP SCHEMA "{name}" CASCADE')  # type: ignore[arg-type]


def _connect(schema: str) -> psycopg.Connection:
    return psycopg.connect(Settings().postgres_conninfo, options=f"-c search_path={schema}")


def _seed(schema: str) -> None:
    with _connect(schema) as conn:
        scores_store.ensure_schema(conn)
        scores_store.insert_scores(
            conn,
            [
                _score("0000000001", 12, EARLIER, probability=0.02),
                _score("0000000001", 12),
                _score("0000000001", 24, probability=0.09),
                _score(
                    "0000000002",
                    12,
                    probability=None,
                    not_scored_reason="inputs incomplete",
                    top_drivers=None,
                    missing_inputs=["working_capital_to_assets"],
                ),
                # A company scored before, then found in a proceeding: the exclusion is current.
                _score("0000000004", 12, EARLIER),
                # Scored by a version the registry no longer serves: never shown.
                _score("0000000005", 12, EARLIER, model_version="3"),
            ],
        )
        scores_store.insert_exclusions(
            conn,
            [
                _exclusion("0000000003", 12, "rdf_not_searched"),
                _exclusion("0000000003", 24, "rdf_not_searched"),
                _exclusion("0000000004", 12, "not_at_risk"),
            ],
        )


@pytest.fixture
def client(schema: str) -> Iterator[TestClient]:
    _seed(schema)
    with TestClient(main.create_app(SERVED, lambda: _connect(schema))) as test_client:
        yield test_client


def _envelope(response: Any) -> Any:
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["disclaimer"] == DISCLAIMER
    assert [s["model_version"] for s in body["meta"]["served"]] == ["4", "4"]
    return body["data"]


@pytest.mark.integration
def test_a_company_shows_its_latest_score_per_horizon(client: TestClient) -> None:
    data = _envelope(client.get("/companies/0000000001"))
    assert [(s["horizon_months"], s["as_of_date"], s["probability"]) for s in data["scores"]] == [
        (12, "2026-09-30", 0.05),
        (24, "2026-09-30", 0.09),
    ]
    assert data["exclusions"] == []
    row = data["scores"][0]
    assert row["top_drivers"] == [{"feature": "roa", "contribution": 0.4}]
    for key in ("mlflow_run_id", "code_commit", "data_snapshot_hash", "label_set_hash"):
        assert row[key]
    assert row["population_hash"] == "e" * 64


@pytest.mark.integration
def test_a_company_not_scored_says_why(client: TestClient) -> None:
    incomplete = _envelope(client.get("/companies/0000000002"))["scores"][0]
    assert incomplete["probability"] is None
    assert incomplete["missing_inputs"] == ["working_capital_to_assets"]
    excluded = _envelope(client.get("/companies/0000000003"))
    assert excluded["scores"] == []
    assert [(e["horizon_months"], e["reason"]) for e in excluded["exclusions"]] == [
        (12, "rdf_not_searched"),
        (24, "rdf_not_searched"),
    ]


@pytest.mark.integration
def test_a_newer_exclusion_replaces_an_older_score(client: TestClient) -> None:
    data = _envelope(client.get("/companies/0000000004"))
    assert data["scores"] == []
    assert [e["reason"] for e in data["exclusions"]] == ["not_at_risk"]


@pytest.mark.integration
def test_a_version_no_longer_served_is_never_shown(client: TestClient) -> None:
    assert client.get("/companies/0000000005").status_code == 404
    assert _envelope(client.get("/companies/0000000005/scores")) == []


@pytest.mark.integration
def test_unknown_and_malformed_companies(client: TestClient) -> None:
    assert client.get("/companies/0000009999").status_code == 404
    assert client.get("/companies/12345").status_code == 422


@pytest.mark.integration
def test_a_company_history_is_oldest_first(client: TestClient) -> None:
    data = _envelope(client.get("/companies/0000000001/scores"))
    assert [(s["as_of_date"], s["horizon_months"]) for s in data] == [
        ("2026-08-31", 12),
        ("2026-09-30", 12),
        ("2026-09-30", 24),
    ]


@pytest.mark.integration
def test_scores_at_a_month_end(client: TestClient) -> None:
    latest = _envelope(client.get("/scores"))
    assert {(s["krs"], s["horizon_months"]) for s in latest} == {
        ("0000000001", 12),
        ("0000000001", 24),
        ("0000000002", 12),
    }
    only_24 = _envelope(client.get("/scores", params={"horizon_months": 24}))
    assert [s["krs"] for s in only_24] == ["0000000001"]
    earlier = _envelope(client.get("/scores", params={"as_of_date": "2026-08-31"}))
    assert {s["krs"] for s in earlier} == {"0000000001", "0000000004"}
    assert client.get("/scores", params={"as_of_date": "2026-07-31"}).status_code == 404
    assert client.get("/scores", params={"horizon_months": 36}).status_code == 404


@pytest.mark.integration
def test_a_watchlist_is_replaced_read_and_deleted(client: TestClient) -> None:
    assert client.get("/watchlists/budowlane").status_code == 404
    put = client.put("/watchlists/budowlane", json={"krs": ["0000000001", "0000000003"]})
    members = _envelope(put)["members"]
    assert [m["krs"] for m in members] == ["0000000001", "0000000003"]
    assert len(members[0]["scores"]) == 2 and len(members[1]["exclusions"]) == 2
    added = members[0]["added_at"]

    put = client.put("/watchlists/budowlane", json={"krs": ["0000000001", "0000000002"]})
    members = _envelope(put)["members"]
    assert [m["krs"] for m in members] == ["0000000001", "0000000002"]
    assert members[0]["added_at"] == added  # kept, not re-added

    assert client.put("/watchlists/budowlane", json={"krs": ["123"]}).status_code == 422
    assert client.delete("/watchlists/budowlane").status_code == 204
    assert client.delete("/watchlists/budowlane").status_code == 404


@pytest.mark.integration
def test_a_database_scored_by_another_version_is_refused(schema: str) -> None:
    _seed(schema)
    promoted = {12: _served(12, version="5"), 24: _served(24)}
    with (
        pytest.raises(RuntimeError, match="run the scoring job first"),
        TestClient(main.create_app(promoted, lambda: _connect(schema))),
    ):
        pass


@pytest.mark.integration
def test_an_empty_database_starts_and_says_there_are_no_scores(schema: str) -> None:
    with TestClient(main.create_app(SERVED, lambda: _connect(schema))) as empty:
        assert empty.get("/scores").status_code == 404
