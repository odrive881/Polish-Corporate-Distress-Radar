"""The API's reads and writes over Postgres (plan 0016 step D, decision 6).

Scores are read, never computed: `scores_history` and `scoring_exclusions` are what the `scoring`
job wrote. Every read is limited to the served versions, one per horizon, so a response never mixes
in a score from a version the registry no longer serves. `check_served` refuses a database whose
latest scores came from another version (decision 6), which is what a `scoring` run not yet made
after a promotion looks like.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from datetime import date, datetime
from typing import Any, LiteralString

from psycopg import Connection
from psycopg.rows import dict_row

from distress_radar.models.serving import ServedVersion

_SCORE_COLUMNS: LiteralString = """
    krs, as_of_date, horizon_months, probability, not_scored_reason, missing_inputs, top_drivers,
    model_name, model_version, model_alias, mlflow_run_id, training_eve, code_commit,
    data_snapshot_hash, label_version, label_set_hash, feature_set_version, feature_set_hash,
    population_hash, scored_at
"""
_EXCLUSION_COLUMNS: LiteralString = """
    krs, as_of_date, horizon_months, model_name, model_version, reason, population_hash,
    risk_label_set_hash, code_commit, data_snapshot_hash, scored_at
"""


def _served_pairs(served: Mapping[int, ServedVersion]) -> list[str]:
    """The served (model name, version) pairs, as `name:version` for an `= ANY` match."""
    return [f"{v.name}:{v.version}" for v in served.values()]


def check_served(conn: Connection, served: Mapping[int, ServedVersion]) -> None:
    """Refuse a database whose latest scores, for a served horizon, came from another version."""
    for horizon, version in served.items():
        rows = conn.execute(
            """
            SELECT DISTINCT model_name, model_version FROM scores_history
            WHERE horizon_months = %s
              AND as_of_date = (SELECT max(as_of_date) FROM scores_history WHERE horizon_months = %s)
            """,
            (horizon, horizon),
        ).fetchall()
        stored = {(str(name), str(v)) for name, v in rows}
        if stored and stored != {(version.name, version.version)}:
            raise RuntimeError(
                f"the latest {horizon}-month scores came from {sorted(stored)}, but the registry "
                f"serves {version.name} v{version.version}: run the scoring job first"
            )


def _rows(conn: Connection, query: LiteralString, params: Sequence[object]) -> list[dict[str, Any]]:
    with conn.cursor(row_factory=dict_row) as cursor:
        cursor.execute(query, params)
        return [_clean(row) for row in cursor.fetchall()]


def _clean(row: dict[str, Any]) -> dict[str, Any]:
    """Postgres pads `char(n)` columns; nothing here is padded on purpose."""
    return {k: v.rstrip() if isinstance(v, str) else v for k, v in row.items()}


def latest_scores(
    conn: Connection, served: Mapping[int, ServedVersion], krs: Iterable[str]
) -> list[dict[str, Any]]:
    """Each company's score row at its latest month-end, per served horizon."""
    return _rows(
        conn,
        f"""
        SELECT DISTINCT ON (krs, horizon_months) {_SCORE_COLUMNS} FROM scores_history
        WHERE krs = ANY(%s) AND model_name || ':' || model_version = ANY(%s)
        ORDER BY krs, horizon_months, as_of_date DESC
        """,
        (list(krs), _served_pairs(served)),
    )


def latest_exclusions(
    conn: Connection, served: Mapping[int, ServedVersion], krs: Iterable[str]
) -> list[dict[str, Any]]:
    """Each company's exclusion at its latest month-end, per served horizon."""
    return _rows(
        conn,
        f"""
        SELECT DISTINCT ON (krs, horizon_months) {_EXCLUSION_COLUMNS} FROM scoring_exclusions
        WHERE krs = ANY(%s) AND model_name || ':' || model_version = ANY(%s)
        ORDER BY krs, horizon_months, as_of_date DESC
        """,
        (list(krs), _served_pairs(served)),
    )


def score_history(
    conn: Connection, served: Mapping[int, ServedVersion], krs: str
) -> list[dict[str, Any]]:
    """A company's score rows from the served versions, oldest first."""
    return _rows(
        conn,
        f"""
        SELECT {_SCORE_COLUMNS} FROM scores_history
        WHERE krs = %s AND model_name || ':' || model_version = ANY(%s)
        ORDER BY as_of_date, horizon_months
        """,
        (krs, _served_pairs(served)),
    )


def latest_as_of_date(conn: Connection, served: Mapping[int, ServedVersion]) -> date | None:
    row = conn.execute(
        "SELECT max(as_of_date) FROM scores_history"
        " WHERE model_name || ':' || model_version = ANY(%s)",
        (_served_pairs(served),),
    ).fetchone()
    return None if row is None else row[0]


def scores_at(
    conn: Connection,
    served: Mapping[int, ServedVersion],
    as_of_date: date,
    horizon_months: int | None,
) -> list[dict[str, Any]]:
    """Every score row at a month-end from the served versions, by company and horizon."""
    return _rows(
        conn,
        f"""
        SELECT {_SCORE_COLUMNS} FROM scores_history
        WHERE as_of_date = %s AND model_name || ':' || model_version = ANY(%s)
          AND (%s::integer IS NULL OR horizon_months = %s::integer)
        ORDER BY krs, horizon_months
        """,
        (as_of_date, _served_pairs(served), horizon_months, horizon_months),
    )


def watchlist(conn: Connection, name: str) -> list[tuple[str, datetime]]:
    """A watchlist's members and when each was added, by KRS."""
    rows = conn.execute(
        "SELECT krs, added_at FROM watchlists WHERE name = %s ORDER BY krs", (name,)
    ).fetchall()
    return [(str(krs).rstrip(), added_at) for krs, added_at in rows]


def replace_watchlist(conn: Connection, name: str, krs: Iterable[str], now: datetime) -> None:
    """Make the list exactly `krs`: members kept keep their `added_at`."""
    members = sorted(set(krs))
    with conn.transaction():
        conn.execute(
            "DELETE FROM watchlists WHERE name = %s AND NOT (krs = ANY(%s))", (name, members)
        )
        with conn.cursor() as cursor:
            for member in members:
                cursor.execute(
                    "INSERT INTO watchlists (name, krs, added_at) VALUES (%s, %s, %s)"
                    " ON CONFLICT DO NOTHING",
                    (name, member, now),
                )


def delete_watchlist(conn: Connection, name: str) -> int:
    """Remove a watchlist; the number of members it had."""
    with conn.transaction():
        return conn.execute("DELETE FROM watchlists WHERE name = %s", (name,)).rowcount
