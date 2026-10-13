"""Scoring the latest month-end (plan 0016 step C): who is scored, why the rest are not, and the
metadata every row carries."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime

import polars as pl
import pytest

from distress_radar.models.population import Population
from distress_radar.models.scoring import (
    NOT_AT_RISK,
    MonthEndScores,
    ScoreContext,
    at_risk,
    feature_set_identity,
    history_rows,
    latest_month_end,
    score_month_end,
)
from distress_radar.models.serving import INPUTS_INCOMPLETE, Scorer, ServedVersion, load_scorer

DAY = date(2026, 9, 30)
NOW = datetime(2026, 10, 13, 12, 0, tzinfo=UTC)
FS_VERSION, FS_HASH = "feature_set_v6", "d" * 64


def _scorer() -> Scorer:
    body = {
        "kind": "rank_logistic",
        "horizon_months": 12,
        "inputs": ["roa"],
        "coefficients": [-2.0],
        "intercept": 0.0,
        "rank_reference": [[0.1, 0.2, 0.3, 0.4]],
    }
    return load_scorer({"model.json": json.dumps(body).encode()})


def _features() -> pl.DataFrame:
    rows = [
        ("0000000001", DAY, 0.15),  # scored
        ("0000000002", DAY, None),  # inputs incomplete
        ("0000000003", DAY, 0.25),  # population: not searched
        ("0000000004", DAY, 0.35),  # in a proceeding: off the label grid
        ("0000000001", date(2026, 8, 31), 0.2),  # an earlier month-end: not scored now
    ]
    return pl.DataFrame(
        [
            {
                "krs": k,
                "as_of_date": d,
                "feature_set_version": FS_VERSION,
                "feature_set_hash": FS_HASH,
                "roa": v,
                "roa__known_from": None if v is None else date(2026, 6, 1),
            }
            for k, d, v in rows
        ],
        schema={
            "krs": pl.String,
            "as_of_date": pl.Date,
            "feature_set_version": pl.String,
            "feature_set_hash": pl.String,
            "roa": pl.Float64,
            "roa__known_from": pl.Date,
        },
    )


POPULATION = Population(
    included=frozenset({"0000000001", "0000000002", "0000000004"}),
    excluded={"0000000003": "rdf_not_searched"},
)
RISK_SET = frozenset({"0000000001", "0000000002", "0000000003"})


def _served(test_year: int = 2025, **tags: str) -> ServedVersion:
    return ServedVersion(
        name="distress_radar_12m",
        version="3",
        alias="incumbent",
        run_id="f" * 32,
        model="logistic_regression",
        horizon_months=12,
        test_year=test_year,
        tags={
            "label_version": "label_v2",
            "label_set_hash": "c" * 64,
            "feature_set_version": FS_VERSION,
            "feature_set_hash": FS_HASH,
            **tags,
        },
    )


def _context(served: ServedVersion | None = None) -> ScoreContext:
    return ScoreContext(
        served=served or _served(),
        code_commit="a" * 40,
        data_snapshot_hash="b" * 64,
        feature_set_version=FS_VERSION,
        feature_set_hash=FS_HASH,
        population_hash="e" * 64,
        scored_at=NOW,
    )


def _month_end() -> MonthEndScores:
    features = _features()
    return score_month_end(features, latest_month_end(features), POPULATION, RISK_SET, _scorer())


def test_only_the_population_at_risk_is_scored_and_the_rest_say_why() -> None:
    month_end = _month_end()
    assert month_end.as_of_date == DAY
    assert month_end.not_scored == {"0000000003": "rdf_not_searched", "0000000004": NOT_AT_RISK}
    scores = month_end.scores
    assert scores.get_column("krs").to_list() == ["0000000001", "0000000002"]
    assert scores.get_column("not_scored_reason").to_list() == [None, INPUTS_INCOMPLETE]
    assert scores.get_column("probability").null_count() == 1
    assert month_end.reasons() == {
        INPUTS_INCOMPLETE: 1,
        NOT_AT_RISK: 1,
        "rdf_not_searched": 1,
    }


def test_an_entity_the_population_rule_never_saw_is_an_error() -> None:
    features = _features()
    with pytest.raises(ValueError, match="never saw"):
        score_month_end(
            features, DAY, Population(frozenset(), {}), RISK_SET | {"0000000004"}, _scorer()
        )


def test_a_label_set_with_no_row_at_the_month_end_is_refused() -> None:
    labels = pl.DataFrame(
        {"krs": ["0000000001"], "as_of_date": [date(2026, 5, 31)]},
        schema={"krs": pl.String, "as_of_date": pl.Date},
    )
    with pytest.raises(ValueError, match="rebuild the labels"):
        at_risk(labels, DAY)
    assert at_risk(labels, date(2026, 5, 31)) == frozenset({"0000000001"})


def test_every_row_carries_the_metadata() -> None:
    rows = history_rows(_month_end(), _context())
    assert [r["krs"] for r in rows] == ["0000000001", "0000000002"]
    for row in rows:
        assert row["training_eve"] == date(2024, 12, 31)
        assert row["model_version"] == "3" and row["model_alias"] == "incumbent"
        assert row["mlflow_run_id"] == "f" * 32
        assert row["label_set_hash"] == "c" * 64
        assert row["feature_set_hash"] == FS_HASH
        assert row["population_hash"] == "e" * 64
        assert row["scored_at"] == NOW
        assert all(value is not None for key, value in row.items() if key not in _NULLABLE)
    assert rows[0]["top_drivers"] is not None and rows[1]["top_drivers"] is None


_NULLABLE = {"probability", "not_scored_reason", "top_drivers"}


def test_a_month_end_on_or_before_the_training_eve_is_refused() -> None:
    with pytest.raises(ValueError, match="in-sample"):
        history_rows(_month_end(), _context(_served(test_year=2027)))


def test_a_model_trained_on_another_feature_set_is_refused() -> None:
    with pytest.raises(ValueError, match="trained on feature set"):
        _context(_served(feature_set_hash="9" * 64))


def test_a_naive_timestamp_is_refused() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        ScoreContext(
            served=_served(),
            code_commit="a" * 40,
            data_snapshot_hash="b" * 64,
            feature_set_version=FS_VERSION,
            feature_set_hash=FS_HASH,
            population_hash="e" * 64,
            scored_at=datetime(2026, 10, 13),  # noqa: DTZ001
        )


def test_the_feature_store_holds_one_feature_set() -> None:
    assert feature_set_identity(_features()) == (FS_VERSION, FS_HASH)
    mixed = pl.concat(
        [_features(), _features().with_columns(pl.lit("x" * 64).alias("feature_set_hash"))]
    )
    with pytest.raises(ValueError, match="2 feature sets"):
        feature_set_identity(mixed)
