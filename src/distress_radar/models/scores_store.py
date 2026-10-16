"""Postgres `scores_history`, `scoring_exclusions` and `watchlists` (plan 0016 step A, AGENT_SPEC §5). Same DDL
conventions as `acquisition/manifest.py` (ADR 0006): plain idempotent DDL, natural keys.

`scores_history` holds one row per company, month-end, horizon and served model version, so a
rerun of the scoring job adds nothing (decision 5, invariant 5). Beside §5's columns, every row
carries decision 7's metadata, so a score traces to its MLflow run:
- the served version: `model_name`, `model_version`, `model_alias`, its `mlflow_run_id`, and its
  `training_eve` (labels settled on that day trained it);
- the identifiers: `code_commit` (the scoring code's; the model's own is on its version),
  `data_snapshot_hash` (the `feature_store` scored), `label_version` and `label_set_hash`,
  `feature_set_version` and `feature_set_hash`, `population_hash` (null for a backtest config
  without a population rule);
- `not_scored_reason`: a row of the modelled population the served model cannot score (the
  regression's incomplete inputs) is kept with its reason and no probability, never imputed
  (decision 2), and `missing_inputs` names the inputs it lacks, in the model's input order
  (decision 12): set exactly when the reason is "inputs incomplete".

`scoring_exclusions` holds the rest of the month-end (decision 6, plan 0016 buildable item 2): one
row per entity with a feature row there that was not scored at all, keyed like `scores_history`,
with its reason: the population rule's (acquisition incomplete) or `not_at_risk` (off the label
grid: already in a proceeding or deregistered). It carries the population's hash and the label set
the risk set was read from, so the API can say why a company has no score, and from what. An
entity the served model could not score for a missing input is not here: it is a
`scores_history` row with "inputs incomplete".

Every row names a company: local only (AGENT_SPEC §6K), never published.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import LiteralString

from psycopg import Connection

SCHEMA_DDL: tuple[LiteralString, ...] = (
    """
    CREATE TABLE IF NOT EXISTS scores_history (
        krs                  char(10) NOT NULL,
        as_of_date           date NOT NULL,
        horizon_months       integer NOT NULL CHECK (horizon_months > 0),
        probability          double precision CHECK (probability BETWEEN 0 AND 1),
        not_scored_reason    text,
        model_name           text NOT NULL,
        model_version        text NOT NULL,
        model_alias          text NOT NULL,
        mlflow_run_id        text NOT NULL,
        training_eve         date NOT NULL CHECK (training_eve < as_of_date),
        code_commit          char(40) NOT NULL,
        data_snapshot_hash   char(64) NOT NULL,
        label_version        text NOT NULL,
        label_set_hash       char(64) NOT NULL,
        feature_set_version  text NOT NULL,
        feature_set_hash     char(64) NOT NULL,
        population_hash      text,
        top_drivers          jsonb,
        scored_at            timestamptz NOT NULL,
        PRIMARY KEY (krs, as_of_date, horizon_months, model_version),
        CHECK ((probability IS NULL) = (not_scored_reason IS NOT NULL)),
        CHECK (probability IS NOT NULL OR top_drivers IS NULL)
    )
    """,
    # Decision 12, added after the table first shipped.
    """
    ALTER TABLE scores_history ADD COLUMN IF NOT EXISTS missing_inputs text[]
        CHECK ((missing_inputs IS NOT NULL)
               = (not_scored_reason IS NOT DISTINCT FROM 'inputs incomplete'))
        CHECK (cardinality(missing_inputs) > 0)
    """,
    """
    CREATE INDEX IF NOT EXISTS scores_history_as_of
        ON scores_history (as_of_date, horizon_months)
    """,
    # Plan 0016 buildable item 2. The reasons are `population.REASONS` and `scoring.NOT_AT_RISK`
    # (a test holds the list to them).
    """
    CREATE TABLE IF NOT EXISTS scoring_exclusions (
        krs                  char(10) NOT NULL,
        as_of_date           date NOT NULL,
        horizon_months       integer NOT NULL CHECK (horizon_months > 0),
        model_version        text NOT NULL,
        reason               text NOT NULL CHECK (reason IN (
            'rdf_not_searched', 'rdf_not_found', 'rdf_search_incomplete',
            'no_statement_imported', 'statements_held_back', 'not_at_risk')),
        model_name           text NOT NULL,
        population_hash      char(64) NOT NULL,
        risk_label_set_hash  char(64) NOT NULL,
        code_commit          char(40) NOT NULL,
        data_snapshot_hash   char(64) NOT NULL,
        scored_at            timestamptz NOT NULL,
        PRIMARY KEY (krs, as_of_date, horizon_months, model_version)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS watchlists (
        name      text NOT NULL CHECK (name <> ''),
        krs       char(10) NOT NULL,
        added_at  timestamptz NOT NULL,
        PRIMARY KEY (name, krs)
    )
    """,
)


def ensure_schema(conn: Connection) -> None:
    for statement in SCHEMA_DDL:
        conn.execute(statement)


COLUMNS: tuple[LiteralString, ...] = (
    "krs",
    "as_of_date",
    "horizon_months",
    "probability",
    "not_scored_reason",
    "model_name",
    "model_version",
    "model_alias",
    "mlflow_run_id",
    "training_eve",
    "code_commit",
    "data_snapshot_hash",
    "label_version",
    "label_set_hash",
    "feature_set_version",
    "feature_set_hash",
    "population_hash",
    "top_drivers",
    "missing_inputs",
    "scored_at",
)


def insert_scores(conn: Connection, rows: Sequence[dict[str, object]]) -> int:
    """Insert `scores_history` rows (`top_drivers` as JSON text or None); a row whose key is
    already there is left as it was. Returns the rows inserted."""
    if not rows:
        return 0
    columns = ", ".join(COLUMNS)
    values = ", ".join("%s::jsonb" if c == "top_drivers" else "%s" for c in COLUMNS)
    inserted = 0
    with conn.cursor() as cursor:
        for row in rows:
            cursor.execute(
                f"INSERT INTO scores_history ({columns}) VALUES ({values}) ON CONFLICT DO NOTHING",
                tuple(row[c] for c in COLUMNS),
            )
            inserted += cursor.rowcount
    return inserted


EXCLUSION_COLUMNS: tuple[LiteralString, ...] = (
    "krs",
    "as_of_date",
    "horizon_months",
    "model_version",
    "reason",
    "model_name",
    "population_hash",
    "risk_label_set_hash",
    "code_commit",
    "data_snapshot_hash",
    "scored_at",
)


def insert_exclusions(conn: Connection, rows: Sequence[dict[str, object]]) -> int:
    """Insert `scoring_exclusions` rows; a row whose key is already there is left as it was.
    Returns the rows inserted."""
    if not rows:
        return 0
    columns = ", ".join(EXCLUSION_COLUMNS)
    values = ", ".join("%s" for _ in EXCLUSION_COLUMNS)
    inserted = 0
    with conn.cursor() as cursor:
        for row in rows:
            cursor.execute(
                f"INSERT INTO scoring_exclusions ({columns}) VALUES ({values})"
                " ON CONFLICT DO NOTHING",
                tuple(row[c] for c in EXCLUSION_COLUMNS),
            )
            inserted += cursor.rowcount
    return inserted
