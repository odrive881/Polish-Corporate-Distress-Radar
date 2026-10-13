"""Scoring the latest month-end with the served model (plan 0016 step C, owner decisions 2, 5 and 7).

**Who is scored** (decision 2), at the latest month-end in `feature_store`, of every entity with a
feature row there:
- an entity the population rule excludes (acquisition incomplete, `population.py`) is not scored,
  with its rule's reason: to a model, a company whose filings were never fetched looks like one
  that never filed;
- an entity the label rules leave off the grid at that month-end is not scored, reason
  `not_at_risk`: it is already in a proceeding or deregistered (AGENT_SPEC §4.6), the rows no model
  was trained on. The grid is read from the latest frozen label set of the served version's label
  version, which knows the most recent proceedings;
- every other entity is scored, or kept with "inputs incomplete" where the served model needs an
  input the row lacks (the regression's complete cases), never imputed.

**What every row carries** (decision 7): the served version (name, version, alias, MLflow run,
training eve); the scoring code's commit; the `feature_store` snapshot scored; the label version
and set the model was trained on; the feature set; the population's hash. The feature store must
hold the feature set the model was trained on, by version and hash, or nothing is scored.

Rows go to Postgres `scores_history` (`scores_store.py`), whose key makes a rerun add nothing.
"""

# polars's signatures reference types pyright cannot resolve; scoped to this module.
# pyright: reportUnknownMemberType=false

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime
from typing import cast

import polars as pl
from psycopg import Connection

from distress_radar.labels import LABEL_SETS_DDL
from distress_radar.models.population import Population
from distress_radar.models.serving import Scorer, ServedVersion

NOT_AT_RISK = "not_at_risk"


def latest_month_end(features: pl.DataFrame) -> date:
    """The latest `as_of_date` in `feature_store`."""
    if features.is_empty():
        raise ValueError("feature_store holds no rows")
    return cast(date, features.get_column("as_of_date").max())


def at_risk(labels: pl.DataFrame, as_of_date: date) -> frozenset[str]:
    """The entities a label set has a row for at `as_of_date` (labelled or censored); refused when
    it has none there at all, which means the set's grid ends earlier than the features'."""
    entities = frozenset(
        labels.filter(pl.col("as_of_date") == as_of_date).get_column("krs").unique().to_list()
    )
    if not entities:
        raise ValueError(
            f"the label set has no row at {as_of_date}: rebuild the labels up to the features' "
            "latest month-end"
        )
    return entities


def latest_label_set(conn: Connection, label_version: str) -> str:
    """The hash of the latest frozen label set of a label version: latest cutoff, then latest
    frozen."""
    conn.execute(LABEL_SETS_DDL)
    row = conn.execute(
        """
        SELECT label_set_hash FROM label_sets WHERE label_version = %s
        ORDER BY cutoff_date DESC, created_at DESC, label_set_hash LIMIT 1
        """,
        (label_version,),
    ).fetchone()
    if row is None:
        raise LookupError(f"no frozen label set of {label_version}")
    return str(row[0])


@dataclass(frozen=True)
class MonthEndScores:
    as_of_date: date
    scores: pl.DataFrame  # `serving.SCORE_SCHEMA`, one row per entity scored or incomplete
    not_scored: dict[str, str]  # krs -> reason: the population's, or `not_at_risk`

    def reasons(self) -> dict[str, int]:
        """Entities per reason, sorted by reason; "inputs incomplete" among them."""
        counts: dict[str, int] = {}
        for reason in [
            *self.not_scored.values(),
            *self.scores.get_column("not_scored_reason").drop_nulls().to_list(),
        ]:
            counts[reason] = counts.get(reason, 0) + 1
        return dict(sorted(counts.items()))


def score_month_end(
    features: pl.DataFrame,
    as_of_date: date,
    population: Population,
    risk_set: frozenset[str],
    scorer: Scorer,
) -> MonthEndScores:
    """Decision 2's rule over every entity with a feature row at `as_of_date`."""
    rows = features.filter(pl.col("as_of_date") == as_of_date).sort("krs")
    not_scored: dict[str, str] = {}
    for krs in rows.get_column("krs").to_list():
        if krs in population.excluded:
            not_scored[krs] = population.excluded[krs]
        elif krs not in population.included:
            raise ValueError(f"{krs} has features but the population rule never saw it")
        elif krs not in risk_set:
            not_scored[krs] = NOT_AT_RISK
    scored = rows.filter(~pl.col("krs").is_in(list(not_scored)))
    return MonthEndScores(as_of_date, scorer.score(scored), not_scored)


@dataclass(frozen=True)
class ScoreContext:
    """Decision 7's metadata, the same on every row of one scoring run and horizon."""

    served: ServedVersion
    code_commit: str
    data_snapshot_hash: str
    feature_set_version: str
    feature_set_hash: str
    population_hash: str
    scored_at: datetime

    def __post_init__(self) -> None:
        tags = self.served.tags
        trained = (tags.get("feature_set_version"), tags.get("feature_set_hash"))
        if trained != (self.feature_set_version, self.feature_set_hash):
            raise ValueError(
                f"{self.served.name} v{self.served.version} was trained on feature set "
                f"{trained[0]} ({trained[1]}); feature_store holds {self.feature_set_version} "
                f"({self.feature_set_hash})"
            )
        if self.scored_at.tzinfo is None:
            raise ValueError("scored_at must be timezone-aware")


def feature_set_identity(features: pl.DataFrame) -> tuple[str, str]:
    """The one feature set version and hash a loaded `feature_store` holds."""
    pairs = features.select("feature_set_version", "feature_set_hash").unique()
    if pairs.height != 1:
        raise ValueError(f"feature_store holds {pairs.height} feature sets")
    version, digest = pairs.row(0)
    return str(version), str(digest)


def history_rows(month_end: MonthEndScores, context: ScoreContext) -> list[dict[str, object]]:
    """`scores_history` rows (column name to value, `top_drivers` as JSON text)."""
    served = context.served
    if month_end.as_of_date <= served.training_eve:
        raise ValueError(
            f"{month_end.as_of_date} is not after {served.name} v{served.version}'s training eve "
            f"({served.training_eve}): its score there would be in-sample"
        )
    fixed: Mapping[str, object] = {
        "horizon_months": served.horizon_months,
        "model_name": served.name,
        "model_version": served.version,
        "model_alias": served.alias,
        "mlflow_run_id": served.run_id,
        "training_eve": served.training_eve,
        "code_commit": context.code_commit,
        "data_snapshot_hash": context.data_snapshot_hash,
        "label_version": served.tags["label_version"],
        "label_set_hash": served.tags["label_set_hash"],
        "feature_set_version": context.feature_set_version,
        "feature_set_hash": context.feature_set_hash,
        "population_hash": context.population_hash,
        "scored_at": context.scored_at,
    }
    return [
        {
            "krs": row["krs"],
            "as_of_date": row["as_of_date"],
            "probability": row["probability"],
            "not_scored_reason": row["not_scored_reason"],
            "top_drivers": row["top_drivers"],
            **fixed,
        }
        for row in month_end.scores.iter_rows(named=True)
    ]
