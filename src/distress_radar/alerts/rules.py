"""The alert rules (plan 0018 step D, owner decisions 4 and 5): what changed, for whom, since when.

Five types (AGENT_SPEC §5 `alerts`), each a pure function of point-in-time inputs:

- `threshold_crossed`, `score_jump`: a series of stored scores (`scores_history`), one entity,
  horizon and model version; each score is compared with the series' previous one only.
- `tripwire_triggered`, `going_concern_flagged`: a feature turning true in `feature_store` since
  the entity's previous month-end. The store is point in time (a value at `as_of_date` uses only
  facts known by then), so an alert at a month-end never changes when later facts arrive.
- `filing_overdue`: at a complete RDF search, the next statement's deadline has passed and the
  statements submitted by then do not include it.

An alert's id is a SHA-256 of the rules version, its type, the entity and what fired it, so a
rerun yields the same ids and the store inserts nothing twice (invariant 5). The config is
versioned data (`config/alerts/alert_rules_v<n>.yaml`): thresholds are list-relative, the
owner's to set (ADR 0014).
"""

# polars's signatures reference types pyright cannot resolve; scoped to this module.
# pyright: reportUnknownMemberType=false

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Literal
from zoneinfo import ZoneInfo

import polars as pl
import yaml
from pydantic import BaseModel, ConfigDict, Field

from distress_radar.features.statutory import FilingDeadlines, add_months
from distress_radar.parsing.canonical_schema import CONFIG_DIR

AlertType = Literal[
    "threshold_crossed",
    "score_jump",
    "tripwire_triggered",
    "going_concern_flagged",
    "filing_overdue",
]
ALERT_TYPES: tuple[AlertType, ...] = (
    "threshold_crossed",
    "score_jump",
    "tripwire_triggered",
    "going_concern_flagged",
    "filing_overdue",
)
Severity = Literal["high", "medium", "low"]
SEVERITIES: tuple[Severity, ...] = ("high", "medium", "low")
DEFAULT_RULES = "alert_rules_v1"
SCORES_SCHEMA: dict[str, pl.DataType | type[pl.DataType]] = {
    "krs": pl.String,
    "as_of_date": pl.Date,
    "horizon_months": pl.Int32,
    "probability": pl.Float64,
    "model_name": pl.String,
    "model_version": pl.String,
}
SEARCHES_SCHEMA: dict[str, pl.DataType | type[pl.DataType]] = {
    "krs": pl.String,
    "searched_at": pl.Datetime("us", "UTC"),
    "complete": pl.Boolean,
}
STATEMENTS_SCHEMA: dict[str, pl.DataType | type[pl.DataType]] = {
    "krs": pl.String,
    "period_end": pl.Date,
    "submission_date": pl.Date,
}
_WARSAW = ZoneInfo("Europe/Warsaw")


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class Level(_Frozen):
    probability: float = Field(gt=0, lt=1)
    severity: Severity


class ThresholdRule(_Frozen):
    levels: tuple[Level, ...] = Field(min_length=1)


class JumpRule(_Frozen):
    min_increase: float = Field(gt=0, lt=1)
    min_ratio: float = Field(gt=1)
    severity: Severity


class FeatureRule(_Frozen):
    features: dict[str, Severity] = Field(min_length=1)


class FilingRule(_Frozen):
    severity: Severity


class AlertRules(_Frozen):
    version: str
    alerts_from: date
    horizon_months: int = Field(gt=0)
    threshold_crossed: ThresholdRule
    score_jump: JumpRule
    tripwire_triggered: FeatureRule
    going_concern_flagged: FeatureRule
    filing_overdue: FilingRule

    def feature_rules(self) -> dict[AlertType, FeatureRule]:
        return {
            "tripwire_triggered": self.tripwire_triggered,
            "going_concern_flagged": self.going_concern_flagged,
        }


def load_alert_rules(version: str = DEFAULT_RULES, config_dir: Path = CONFIG_DIR) -> AlertRules:
    """`config/alerts/<version>.yaml`, refused unless the file says its own name."""
    path = config_dir / "alerts" / f"{version}.yaml"
    rules = AlertRules.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))
    if rules.version != version:
        raise ValueError(f"{path.name} says it is {rules.version!r}")
    return rules


@dataclass(frozen=True)
class Alert:
    alert_id: str
    krs: str
    as_of_date: date
    alert_type: AlertType
    severity: Severity
    rule_version: str
    known_from: date
    triggering_score_id: str | None = None
    detail: Mapping[str, Any] = field(default_factory=dict[str, Any])


def alert_id(rule_version: str, alert_type: AlertType, krs: str, key: str) -> str:
    return hashlib.sha256(f"{rule_version}|{alert_type}|{krs}|{key}".encode("ascii")).hexdigest()


def score_id(krs: str, as_of_date: date, horizon: int, model_name: str, version: str) -> str:
    """A stored score's key (`scores_history`'s primary key, with the model's name)."""
    return f"{krs}/{as_of_date.isoformat()}/{horizon}m/{model_name}/v{version}"


def score_alerts(scores: pl.DataFrame, rules: AlertRules) -> list[Alert]:
    """`threshold_crossed` and `score_jump` over stored scores (`SCORES_SCHEMA`)."""
    series = (
        scores.filter(pl.col("horizon_months") == rules.horizon_months)
        .sort("krs", "model_name", "model_version", "as_of_date")
        .with_columns(
            pl.col("probability")
            .shift(1)
            .over("krs", "model_name", "model_version")
            .alias("previous"),
            pl.col("as_of_date")
            .shift(1)
            .over("krs", "model_name", "model_version")
            .alias("previous_as_of"),
        )
        .filter(
            (pl.col("as_of_date") >= rules.alerts_from)
            & pl.col("probability").is_not_null()
            & pl.col("previous").is_not_null()
        )
    )
    levels = sorted(rules.threshold_crossed.levels, key=lambda lv: -lv.probability)
    jump = rules.score_jump
    alerts: list[Alert] = []
    for row in series.iter_rows(named=True):
        krs, as_of, now, before = row["krs"], row["as_of_date"], row["probability"], row["previous"]
        triggering = score_id(
            krs, as_of, rules.horizon_months, row["model_name"], row["model_version"]
        )
        detail = {
            "horizon_months": rules.horizon_months,
            "model": f"{row['model_name']} v{row['model_version']}",
            "probability": round(now, 6),
            "previous_probability": round(before, 6),
            "previous_as_of_date": row["previous_as_of"].isoformat(),
        }
        crossed = next((lv for lv in levels if before < lv.probability <= now), None)
        if crossed is not None:
            alerts.append(
                Alert(
                    alert_id=alert_id(rules.version, "threshold_crossed", krs, triggering),
                    krs=krs,
                    as_of_date=as_of,
                    alert_type="threshold_crossed",
                    severity=crossed.severity,
                    rule_version=rules.version,
                    known_from=as_of,
                    triggering_score_id=triggering,
                    detail={**detail, "level": crossed.probability},
                )
            )
        if now - before >= jump.min_increase and now >= jump.min_ratio * before:
            alerts.append(
                Alert(
                    alert_id=alert_id(rules.version, "score_jump", krs, triggering),
                    krs=krs,
                    as_of_date=as_of,
                    alert_type="score_jump",
                    severity=jump.severity,
                    rule_version=rules.version,
                    known_from=as_of,
                    triggering_score_id=triggering,
                    detail=detail,
                )
            )
    return alerts


def feature_alerts(features: pl.DataFrame, rules: AlertRules) -> list[Alert]:
    """`tripwire_triggered` and `going_concern_flagged` over `feature_store`: a flag true at a
    month-end and not true (false or null) at the entity's previous one."""
    alerts: list[Alert] = []
    ordered = features.sort("krs", "as_of_date")
    for alert_type, rule in rules.feature_rules().items():
        for name, severity in sorted(rule.features.items()):
            known = f"{name}__known_from"
            changed = ordered.select(
                "krs",
                "as_of_date",
                pl.col(name).fill_null(False).alias("now"),
                pl.col(name).shift(1).over("krs").alias("before"),
                pl.col("as_of_date").shift(1).over("krs").alias("previous_as_of"),
                pl.col(known).alias("known_from"),
            ).filter(
                (pl.col("as_of_date") >= rules.alerts_from)
                & pl.col("now")
                & pl.col("previous_as_of").is_not_null()
                & ~pl.col("before").fill_null(False)
            )
            for row in changed.iter_rows(named=True):
                krs, as_of = row["krs"], row["as_of_date"]
                alerts.append(
                    Alert(
                        alert_id=alert_id(rules.version, alert_type, krs, f"{name}@{as_of}"),
                        krs=krs,
                        as_of_date=as_of,
                        alert_type=alert_type,
                        severity=severity,
                        rule_version=rules.version,
                        known_from=row["known_from"],
                        detail={
                            "feature": name,
                            "previous": row["before"],
                            "previous_as_of_date": row["previous_as_of"].isoformat(),
                            "known_from": row["known_from"].isoformat(),
                        },
                    )
                )
    return alerts


def filing_alerts(
    searches: pl.DataFrame,
    statements: pl.DataFrame,
    deadlines: FilingDeadlines,
    rules: AlertRules,
) -> list[Alert]:
    """`filing_overdue` at each complete RDF search (`SEARCHES_SCHEMA`), against the statements
    of a downloaded type, not deleted, submitted by the search's date (`STATEMENTS_SCHEMA`).

    One alert per missing period, at the first complete search that shows it missing. An
    incomplete search, or one made in an outage (never stored), judges nothing.
    """
    by_krs: dict[str, list[tuple[date, date]]] = {}
    for row in statements.iter_rows(named=True):
        by_krs.setdefault(row["krs"], []).append((row["period_end"], row["submission_date"]))
    seen: set[tuple[str, date]] = set()
    alerts: list[Alert] = []
    complete = searches.filter(pl.col("complete")).sort("krs", "searched_at")
    for row in complete.iter_rows(named=True):
        krs = row["krs"]
        searched = row["searched_at"].astimezone(_WARSAW).date()
        if searched < rules.alerts_from:
            continue
        known = [end for end, submitted in by_krs.get(krs, ()) if submitted <= searched]
        if not known:
            continue  # nothing listed: no period to expect the next one from
        latest = max(known)
        expected = add_months(latest, 12)
        due = deadlines.deadline(expected)
        if due >= searched or (krs, expected) in seen:
            continue
        seen.add((krs, expected))
        alerts.append(
            Alert(
                alert_id=alert_id(rules.version, "filing_overdue", krs, expected.isoformat()),
                krs=krs,
                as_of_date=searched,
                alert_type="filing_overdue",
                severity=rules.filing_overdue.severity,
                rule_version=rules.version,
                known_from=searched,
                detail={
                    "expected_period_end": expected.isoformat(),
                    "deadline": due.isoformat(),
                    "latest_period_end": latest.isoformat(),
                    "searched_at": row["searched_at"].isoformat(),
                },
            )
        )
    return alerts


def detail_json(alert: Alert) -> str:
    return json.dumps(dict(alert.detail), sort_keys=True, ensure_ascii=False)
