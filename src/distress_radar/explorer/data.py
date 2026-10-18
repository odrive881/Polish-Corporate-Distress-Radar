"""What the explorer shows, as frames (plan 0016 step E, decisions 3 and 8).

The Streamlit pages in `app/` only lay these out; everything here is testable without Streamlit.

- **Sources:** the served versions (from the registry, as the API resolves them), and from Postgres
  `entity_master`, `scores_history` and `scoring_exclusions` limited to the served versions,
  `filing_index` and `watchlists`; from the warehouse `legal_events` and `model_predictions`. The
  canonical facts are read per company, and a stored document only when asked for.
- **Score history (decision 3):** out-of-time only. For each month-end the backtest scored, the
  prediction from the fold whose test year holds it (the served version's backtest, its `main` run,
  its model and horizon); after that, the served version's own scores. Scoring past months with
  the served model would show in-sample fits as a track record.
- **Peers (decision 8):** the same PKD division within the scored population, as distributions,
  never a ranking of named companies.

Every frame names companies: local only (AGENT_SPEC §6K).
"""

# polars's signatures reference types pyright cannot resolve; scoped to this module.
# pyright: reportUnknownMemberType=false

from __future__ import annotations

import json
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any, LiteralString

import polars as pl
import psycopg
from mlflow import MlflowClient
from psycopg import Connection

from distress_radar.acquisition.raw_store import ObjectStore, S3ObjectStore, raw_key
from distress_radar.alerts import store as alerts_store
from distress_radar.api.db import alerts as read_alerts
from distress_radar.api.db import replace_watchlist
from distress_radar.models.backtest import PREDICTIONS
from distress_radar.models.drivers import driver_movement
from distress_radar.models.serving import ServedVersion, resolve
from distress_radar.models.splits import load_backtest_config
from distress_radar.settings import Settings
from distress_radar.warehouse import read_dataset

type Schema = dict[str, pl.DataType | type[pl.DataType]]

MAIN_RUN = "main"  # the backtest run the served versions are registered from
OUT_OF_TIME = "out-of-time backtest"
SERVED = "served model"

ENTITY_SCHEMA: Schema = {
    "krs": pl.String,
    "name": pl.String,
    "nip": pl.String,
    "regon": pl.String,
    "status": pl.String,
    "pkd_predominant": pl.String,
}
SCORE_SCHEMA: Schema = {
    "krs": pl.String,
    "as_of_date": pl.Date,
    "horizon_months": pl.Int32,
    "probability": pl.Float64,
    "not_scored_reason": pl.String,
    "missing_inputs": pl.List(pl.String),
    "top_drivers": pl.String,
    "model_name": pl.String,
    "model_version": pl.String,
    "scored_at": pl.Datetime("us", "UTC"),
    # Plan 0021: the score's 90% interval (estimation uncertainty only), null without one.
    "probability_low": pl.Float64,
    "probability_high": pl.Float64,
}
EXCLUSION_SCHEMA: Schema = {
    "krs": pl.String,
    "as_of_date": pl.Date,
    "horizon_months": pl.Int32,
    "reason": pl.String,
    "model_name": pl.String,
    "model_version": pl.String,
}
FILING_SCHEMA: Schema = {
    "krs": pl.String,
    "document_ref": pl.String,
    "rdf_type_name": pl.String,
    "period_start": pl.Date,
    "period_end": pl.Date,
    "submission_date": pl.Date,
    "is_correction": pl.Boolean,
    "sha256": pl.String,
}
WATCHLIST_SCHEMA: Schema = {
    "name": pl.String,
    "krs": pl.String,
    "added_at": pl.Datetime("us", "UTC"),
}
PREDICTION_COLUMNS = ("krs", "as_of_date", "horizon_months", "test_year", "probability")
EVENT_COLUMNS = ("krs", "event_type", "outcome_class", "event_date", "known_from", "source")
FACT_COLUMNS = (
    "fiscal_year",
    "period_end",
    "statement_type",
    "line_item",
    "column",
    "value",
    "quality_grade",
    "known_from",
    "structure_version",
    "source_document_hash",
    "source_member",
    "source_element_path",
)


@dataclass(frozen=True)
class Sources:
    served: Mapping[int, ServedVersion]
    entities: pl.DataFrame  # ENTITY_SCHEMA
    scores: pl.DataFrame  # SCORE_SCHEMA, served versions only
    exclusions: pl.DataFrame  # EXCLUSION_SCHEMA, served versions only
    filings: pl.DataFrame  # FILING_SCHEMA, non-deleted rows
    watchlists: pl.DataFrame  # WATCHLIST_SCHEMA
    predictions: pl.DataFrame  # PREDICTION_COLUMNS, the served versions' backtest rows
    legal_events: pl.DataFrame  # EVENT_COLUMNS
    canonical_dir: Path
    connect: Callable[[], Connection]
    store: ObjectStore | None


def _frame(
    conn: Connection, query: LiteralString, params: tuple[object, ...], schema: Schema
) -> pl.DataFrame:
    rows = conn.execute(query, params).fetchall()
    frame = pl.DataFrame(rows, schema=list(schema), orient="row")
    return frame.with_columns(
        pl.col(c).cast(t).str.strip_chars_end() if t == pl.String else pl.col(c).cast(t)
        for c, t in schema.items()
    )


def served_pairs(served: Mapping[int, ServedVersion]) -> list[str]:
    return [f"{v.name}:{v.version}" for v in served.values()]


def served_predictions(
    predictions: pl.DataFrame, served: Mapping[int, ServedVersion]
) -> pl.DataFrame:
    """The backtest rows of each served version: its backtest, the main run, its model, its horizon."""
    parts = [
        predictions.filter(
            (pl.col("backtest") == v.tags.get("backtest"))
            & (pl.col("run") == MAIN_RUN)
            & (pl.col("model") == v.model)
            & (pl.col("horizon_months") == horizon)
        )
        for horizon, v in served.items()
    ]
    return (
        pl.concat(parts).select(PREDICTION_COLUMNS)
        if parts
        else predictions.select(PREDICTION_COLUMNS)
    )


def load_sources(settings: Settings | None = None) -> Sources:
    """Everything the explorer reads, from the configured stores."""
    settings = settings or Settings()
    config = load_backtest_config(settings.backtest_version)
    client = MlflowClient(tracking_uri=settings.mlflow_tracking_uri)
    served = {h: resolve(client, h) for h in config.horizons}
    conninfo = settings.postgres_conninfo
    pairs = served_pairs(served)
    with psycopg.connect(conninfo) as conn:
        entities = _frame(
            conn,
            "SELECT krs, name, nip, regon, status, pkd_predominant FROM entity_master",
            (),
            ENTITY_SCHEMA,
        )
        has_intervals = conn.execute("SELECT to_regclass('score_intervals') IS NOT NULL").fetchone()
        scores = _frame(
            conn,
            """
            SELECT s.krs, s.as_of_date, s.horizon_months, s.probability, s.not_scored_reason,
                   s.missing_inputs, s.top_drivers::text, s.model_name, s.model_version,
                   s.scored_at, i.probability_low, i.probability_high
            FROM scores_history s
            LEFT JOIN score_intervals i USING (krs, as_of_date, horizon_months, model_version)
            WHERE s.model_name || ':' || s.model_version = ANY(%s)
            """
            if has_intervals and has_intervals[0]
            else """
            SELECT krs, as_of_date, horizon_months, probability, not_scored_reason,
                   missing_inputs, top_drivers::text, model_name, model_version, scored_at,
                   NULL::double precision AS probability_low,
                   NULL::double precision AS probability_high
            FROM scores_history WHERE model_name || ':' || model_version = ANY(%s)
            """,
            (pairs,),
            SCORE_SCHEMA,
        )
        exclusions = _frame(
            conn,
            """
            SELECT krs, as_of_date, horizon_months, reason, model_name, model_version
            FROM scoring_exclusions WHERE model_name || ':' || model_version = ANY(%s)
            """,
            (pairs,),
            EXCLUSION_SCHEMA,
        )
        filings = _frame(
            conn,
            """
            SELECT krs, document_ref, rdf_type_name, period_start, period_end, submission_date,
                   is_correction, sha256
            FROM filing_index WHERE status IS DISTINCT FROM 'USUNIETY' AND deleted_on IS NULL
            """,
            (),
            FILING_SCHEMA,
        )
        watchlists = _frame(
            conn, "SELECT name, krs, added_at FROM watchlists", (), WATCHLIST_SCHEMA
        )
    store = S3ObjectStore.from_endpoint(
        settings.minio_endpoint,
        settings.minio_access_key,
        settings.minio_secret_key.get_secret_value(),
        settings.minio_bucket,
    )
    return Sources(
        served=served,
        entities=entities,
        scores=scores,
        exclusions=exclusions,
        filings=filings,
        watchlists=watchlists,
        predictions=served_predictions(read_dataset(settings.warehouse_dir, PREDICTIONS), served),
        legal_events=read_dataset(settings.warehouse_dir, "legal_events").select(EVENT_COLUMNS),
        canonical_dir=settings.warehouse_dir / "financial_statements_canonical",
        connect=lambda: psycopg.connect(conninfo),
        store=store,
    )


def latest_month_end(sources: Sources) -> date | None:
    days = sources.scores.get_column("as_of_date")
    return None if days.is_empty() else days.max()  # type: ignore[return-value]


def month_end_summary(sources: Sources) -> pl.DataFrame:
    """At the latest month-end, per horizon: scored, inputs incomplete, and each exclusion reason."""
    day = latest_month_end(sources)
    scores = sources.scores.filter(pl.col("as_of_date") == day).select(
        "horizon_months",
        pl.when(pl.col("probability").is_not_null())
        .then(pl.lit("scored"))
        .otherwise(pl.col("not_scored_reason"))
        .alias("status"),
    )
    excluded = sources.exclusions.filter(pl.col("as_of_date") == day).select(
        "horizon_months", pl.col("reason").alias("status")
    )
    return (
        pl.concat([scores, excluded])
        .group_by("horizon_months", "status")
        .len("entities")
        .sort("horizon_months", "entities", descending=[False, True])
    )


def current_status(sources: Sources, krs: str) -> pl.DataFrame:
    """Per horizon, the newer of the latest score row and the latest exclusion."""
    scores = sources.scores.filter(pl.col("krs") == krs).select(
        "horizon_months",
        "as_of_date",
        "probability",
        pl.col("not_scored_reason").alias("reason"),
        "missing_inputs",
        "top_drivers",
        "model_version",
        "probability_low",
        "probability_high",
    )
    excluded = sources.exclusions.filter(pl.col("krs") == krs).select(
        "horizon_months",
        "as_of_date",
        pl.lit(None, pl.Float64).alias("probability"),
        "reason",
        pl.lit(None, pl.List(pl.String)).alias("missing_inputs"),
        pl.lit(None, pl.String).alias("top_drivers"),
        "model_version",
    )
    both = pl.concat([scores, excluded.with_columns(_excluded=pl.lit(True))], how="diagonal")
    return (
        both.with_columns(pl.col("_excluded").fill_null(False))
        .sort("horizon_months", "as_of_date", "_excluded")
        .group_by("horizon_months", maintain_order=True)
        .last()
        .drop("_excluded")
    )


def drivers_since_last(
    sources: Sources, krs: str, horizon_months: int, as_of_date: date, model_version: str
) -> list[dict[str, Any]] | None:
    """Plan 0021: how a score's drivers moved since the previous score of the same entity, horizon
    and model version; None for the first score of a version."""
    series = sources.scores.filter(
        (pl.col("krs") == krs)
        & (pl.col("horizon_months") == horizon_months)
        & (pl.col("model_version") == model_version)
        & (pl.col("as_of_date") <= as_of_date)
        & pl.col("probability").is_not_null()
    ).sort("as_of_date")
    if series.height < 2:
        return None
    previous, current = series.tail(2).get_column("top_drivers").to_list()
    return driver_movement(current, previous)


def score_history(sources: Sources, krs: str) -> pl.DataFrame:
    """Decision 3: every month-end's out-of-time score. From the backtest, each fold's prediction
    (a model trained only on labels settled before its test year); from the served versions, their
    stored scores, which are all after their training eve (`scores_history` refuses any other).
    On a month-end both hold, the served score is kept. No past month is scored with the served
    model, which would show in-sample fits as a track record. Columns: `as_of_date`,
    `horizon_months`, `probability`, `source`."""
    backtest = sources.predictions.filter(pl.col("krs") == krs).select(
        "as_of_date",
        pl.col("horizon_months").cast(pl.Int32),
        "probability",
        (pl.lit(f"{OUT_OF_TIME}, fold ") + pl.col("test_year").cast(pl.String)).alias("source"),
        pl.lit(1).alias("_rank"),
    )
    served = sources.scores.filter(
        (pl.col("krs") == krs) & pl.col("probability").is_not_null()
    ).select(
        "as_of_date",
        pl.col("horizon_months").cast(pl.Int32),
        "probability",
        (pl.lit(f"{SERVED} v") + pl.col("model_version")).alias("source"),
        pl.lit(0).alias("_rank"),
    )
    return (
        pl.concat([served, backtest])
        .sort("horizon_months", "as_of_date", "_rank")
        .unique(["horizon_months", "as_of_date"], keep="first", maintain_order=True)
        .drop("_rank")
    )


def history_marks(sources: Sources, krs: str) -> pl.DataFrame:
    """Dated marks for the history chart: each statement filing and each legal event with an
    outcome class. Columns: `date`, `kind`, `label`."""
    filings = sources.filings.filter(
        (pl.col("krs") == krs) & pl.col("submission_date").is_not_null()
    ).select(
        pl.col("submission_date").alias("date"),
        pl.lit("filing").alias("kind"),
        (
            pl.col("rdf_type_name")
            + pl.lit(" for ")
            + pl.col("period_end").cast(pl.String)
            + pl.when(pl.col("is_correction")).then(pl.lit(" (correction)")).otherwise(pl.lit(""))
        ).alias("label"),
    )
    events = sources.legal_events.filter(
        (pl.col("krs") == krs) & pl.col("outcome_class").is_not_null()
    ).select(
        pl.col("event_date").alias("date"),
        pl.lit("legal event").alias("kind"),
        (pl.col("event_type") + pl.lit(" (") + pl.col("outcome_class") + pl.lit(")")).alias(
            "label"
        ),
    )
    return pl.concat([filings, events]).drop_nulls("date").sort("date")


def statement_facts(sources: Sources, krs: str) -> pl.DataFrame:
    """A company's canonical facts, each with the document and element it was read from."""
    if not sources.canonical_dir.exists():
        return pl.DataFrame(schema=dict.fromkeys(FACT_COLUMNS, pl.String))
    return (
        pl.scan_parquet(str(sources.canonical_dir / "**" / "*.parquet"))
        .filter(pl.col("krs") == krs)
        .select(FACT_COLUMNS)
        .sort("period_end", "statement_type", "line_item", "column")
        .collect()
    )


def stored_document(sources: Sources, sha256: str) -> bytes:
    """The stored bytes of a document (as redacted at acquisition, ADR 0009)."""
    if sources.store is None:
        raise LookupError("no raw store configured")
    return sources.store.get(raw_key(sha256))


def extension(content: bytes) -> str:
    """A file extension for stored bytes, from their first bytes: the store keeps no file name
    (ADR 0009 second addendum)."""
    if content.startswith(b"PK"):
        return ".zip"
    if content.startswith(b"%PDF"):
        return ".pdf"
    if content.lstrip().startswith(b"<"):
        return ".xml"
    return ".bin"


def division(pkd: str | None) -> str | None:
    """The PKD division: the code's first two digits (`43.99.Z` → `43`)."""
    return None if not pkd else pkd.replace(".", "")[:2]


def peer_distribution(sources: Sources, horizon: int) -> pl.DataFrame:
    """At the latest month-end, the scored companies' probabilities by PKD division: count and
    quartiles. A distribution, never a list of companies."""
    day = latest_month_end(sources)
    scored = (
        sources.scores.filter(
            (pl.col("as_of_date") == day)
            & (pl.col("horizon_months") == horizon)
            & pl.col("probability").is_not_null()
        )
        .join(sources.entities.select("krs", "pkd_predominant"), on="krs", how="left")
        .with_columns(
            pl.col("pkd_predominant")
            .map_elements(division, return_dtype=pl.String)
            .fill_null("unknown")
            .alias("division")
        )
    )
    return (
        scored.group_by("division")
        .agg(
            pl.len().alias("companies"),
            pl.col("probability").quantile(0.25).alias("q1"),
            pl.col("probability").median().alias("median"),
            pl.col("probability").quantile(0.75).alias("q3"),
        )
        .sort("division")
    )


def peer_percentile(sources: Sources, krs: str, horizon: int) -> float | None:
    """Where a company's latest probability falls among its division's at that month-end (0–100),
    or None when it was not scored."""
    day = latest_month_end(sources)
    pkd = sources.entities.filter(pl.col("krs") == krs).get_column("pkd_predominant").to_list()
    own_division = division(pkd[0]) if pkd else None
    scored = sources.scores.filter(
        (pl.col("as_of_date") == day)
        & (pl.col("horizon_months") == horizon)
        & pl.col("probability").is_not_null()
    ).join(sources.entities.select("krs", "pkd_predominant"), on="krs", how="left")
    peers = scored.filter(
        pl.col("pkd_predominant").map_elements(division, return_dtype=pl.String) == own_division
    )
    own = peers.filter(pl.col("krs") == krs).get_column("probability").to_list()
    if not own or own_division is None:
        return None
    probabilities = peers.get_column("probability")
    return 100.0 * float((probabilities <= own[0]).sum()) / len(probabilities)


def save_watchlist(sources: Sources, name: str, krs: Iterable[str]) -> None:
    """Set a watchlist's members (`api.db.replace_watchlist`: members kept keep `added_at`)."""
    with sources.connect() as conn:
        replace_watchlist(conn, name, krs, datetime.now(UTC))


# --- Alerts (plan 0018 step E) ---------------------------------------------------------------

ALERT_COLUMNS: Schema = {
    "alert_id": pl.String,
    "krs": pl.String,
    "company": pl.String,
    "on_watchlist": pl.Boolean,
    "as_of_date": pl.Date,
    "alert_type": pl.String,
    "severity": pl.String,
    "what": pl.String,
    "acknowledged_at": pl.Datetime("us", "UTC"),
}
FEATURE_LABELS = {
    "art233_triggered": "Art. 233 KSH tripwire: the loss exceeds the share of capital it names",
    "negative_equity": "negative equity",
    "going_concern_threat": "the statement reports a threat to going concern",
    "going_concern_basis_abandoned": "the statement is not prepared on a going-concern basis",
    "going_concern_in_notes": "the notes report a going-concern uncertainty",
    "modified_opinion": "the auditor's opinion is modified",
}


def describe(alert_type: str, detail: Mapping[str, Any]) -> str:
    """One line saying what fired an alert, from its stored `detail`."""
    if alert_type in ("threshold_crossed", "score_jump"):
        line = (
            f"{detail['horizon_months']}-month probability {100 * detail['probability']:.1f}%, "
            f"from {100 * detail['previous_probability']:.1f}% at {detail['previous_as_of_date']}"
        )
        if alert_type == "threshold_crossed":
            line += f": crossed {100 * detail['level']:.0f}%"
        return f"{line} ({detail['model']})"
    if alert_type in ("tripwire_triggered", "going_concern_flagged"):
        feature = str(detail["feature"])
        return f"{FEATURE_LABELS.get(feature, feature)} (known from {detail['known_from']})"
    if alert_type == "filing_overdue":
        return (
            f"no statement for the year ending {detail['expected_period_end']} (due "
            f"{detail['deadline']}) at the RDF search of {str(detail['searched_at'])[:10]}"
        )
    return alert_type


def load_alerts(sources: Sources, *, acknowledged: bool, limit: int = 2_000) -> pl.DataFrame:
    """The alerts (`ALERT_COLUMNS`), in the API's order: watchlist companies first, then by
    severity and the latest month-end."""
    names = dict(sources.entities.select("krs", "name").iter_rows())
    with sources.connect() as conn:
        alerts_store.ensure_schema(conn)
        conn.commit()
        rows = read_alerts(conn, acknowledged=acknowledged, krs=None, watchlist=None, limit=limit)
    return pl.DataFrame(
        [
            {
                "alert_id": r["alert_id"],
                "krs": r["krs"],
                "company": names.get(r["krs"]),
                "on_watchlist": r["on_watchlist"],
                "as_of_date": r["as_of_date"],
                "alert_type": r["alert_type"],
                "severity": r["severity"],
                "what": describe(r["alert_type"], r["detail"]),
                "acknowledged_at": r["acknowledged_at"],
            }
            for r in rows
        ],
        schema=ALERT_COLUMNS,
    )


def acknowledge_alerts(sources: Sources, alert_ids: Iterable[str]) -> int:
    """Acknowledge alerts now; one already acknowledged keeps its first time."""
    with sources.connect() as conn:
        done = alerts_store.acknowledge(conn, alert_ids, datetime.now(UTC))
        conn.commit()
        return done


# --- Drift reports (plan 0018 step F) --------------------------------------------------------


def drift_reports(sources: Sources) -> list[tuple[dict[str, Any], Path]]:
    """Each drift report's summary and its HTML file, newest month-end first; local files only
    (`WAREHOUSE_DIR/reports/drift/`, never published)."""
    directory = sources.canonical_dir.parent / "reports" / "drift"
    found: list[tuple[dict[str, Any], Path]] = []
    for summary in sorted(directory.glob("*.json"), reverse=True):
        html = summary.with_suffix(".html")
        if html.exists():
            found.append((json.loads(summary.read_text(encoding="utf-8")), html))
    return found
