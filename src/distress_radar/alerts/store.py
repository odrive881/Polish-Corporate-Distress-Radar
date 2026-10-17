"""The Postgres `alerts` table (AGENT_SPEC §5; plan 0018 step D) and the reads its rules need.

Plain idempotent DDL (ADR 0006). AGENT_SPEC §5's columns, plus `rule_version`, `detail` (the
values that fired the alert) and `known_from` (when the facts it rests on were known, never after
`as_of_date`). Rows are inserted `ON CONFLICT DO NOTHING` on the deterministic `alert_id`, so a
rerun adds nothing and an acknowledgement is never undone. Every row names a company: local only.
"""

# polars's signatures reference types pyright cannot resolve; scoped to this module.
# pyright: reportUnknownMemberType=false

from __future__ import annotations

from collections.abc import Iterable, Sequence
from datetime import datetime
from typing import LiteralString

import polars as pl
from psycopg import Connection

from distress_radar.alerts.rules import (
    SCORES_SCHEMA,
    SEARCHES_SCHEMA,
    STATEMENTS_SCHEMA,
    Alert,
    detail_json,
)

SCHEMA_DDL: tuple[LiteralString, ...] = (
    """
    CREATE TABLE IF NOT EXISTS alerts (
        alert_id             char(64) PRIMARY KEY,
        krs                  char(10) NOT NULL,
        as_of_date           date NOT NULL,
        alert_type           text NOT NULL CHECK (alert_type IN (
            'threshold_crossed', 'score_jump', 'tripwire_triggered', 'going_concern_flagged',
            'filing_overdue')),
        severity             text NOT NULL CHECK (severity IN ('high', 'medium', 'low')),
        triggering_score_id  text,
        rule_version         text NOT NULL,
        detail               jsonb NOT NULL,
        known_from           date NOT NULL CHECK (known_from <= as_of_date),
        created_at           timestamptz NOT NULL,
        acknowledged_at      timestamptz CHECK (acknowledged_at >= created_at),
        CHECK ((triggering_score_id IS NOT NULL)
               = (alert_type IN ('threshold_crossed', 'score_jump')))
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS alerts_open
        ON alerts (as_of_date DESC) WHERE acknowledged_at IS NULL
    """,
)


def ensure_schema(conn: Connection) -> None:
    for statement in SCHEMA_DDL:
        conn.execute(statement)


def insert_alerts(conn: Connection, alerts: Sequence[Alert], created_at: datetime) -> int:
    """Insert the alerts not stored yet; returns how many were new."""
    inserted = 0
    for a in alerts:
        inserted += conn.execute(
            """
            INSERT INTO alerts (alert_id, krs, as_of_date, alert_type, severity,
                                triggering_score_id, rule_version, detail, known_from, created_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s, %s)
            ON CONFLICT DO NOTHING
            """,
            (
                a.alert_id,
                a.krs,
                a.as_of_date,
                a.alert_type,
                a.severity,
                a.triggering_score_id,
                a.rule_version,
                detail_json(a),
                a.known_from,
                created_at,
            ),
        ).rowcount
    return inserted


def acknowledge(conn: Connection, alert_ids: Iterable[str], at: datetime) -> int:
    """Mark alerts acknowledged; an acknowledged one keeps its first time."""
    ids = sorted(set(alert_ids))
    return conn.execute(
        "UPDATE alerts SET acknowledged_at = %s WHERE alert_id = ANY(%s) AND acknowledged_at IS NULL",
        (at, ids),
    ).rowcount


def read_scores(conn: Connection) -> pl.DataFrame:
    """Every stored score (`SCORES_SCHEMA`); rows not scored carry a null probability."""
    return pl.DataFrame(
        conn.execute(
            """
            SELECT trim(krs), as_of_date, horizon_months, probability, model_name, model_version
            FROM scores_history
            """
        ).fetchall(),
        schema=SCORES_SCHEMA,
        orient="row",
    )


def read_searches(conn: Connection) -> pl.DataFrame:
    return pl.DataFrame(
        conn.execute("SELECT trim(krs), searched_at, complete FROM rdf_listed_entities").fetchall(),
        schema=SEARCHES_SCHEMA,
        orient="row",
    )


def read_statements(conn: Connection, download_codes: Iterable[str]) -> pl.DataFrame:
    """Listed statements of a downloaded type, not deleted, with their RDF submission date."""
    return pl.DataFrame(
        conn.execute(
            """
            SELECT trim(krs), period_end, submission_date FROM filing_index
            WHERE status IS DISTINCT FROM 'USUNIETY' AND deleted_on IS NULL
              AND rdf_type_code = ANY(%s) AND submission_date IS NOT NULL
            """,
            (sorted(download_codes),),
        ).fetchall(),
        schema=STATEMENTS_SCHEMA,
        orient="row",
    )
