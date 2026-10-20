"""The API's response models (plan 0016 step D, decisions 6 and 7).

Every response is an `Envelope`: the data, the served versions that stand behind it (`meta`), and
the disclaimer (AGENT_SPEC §6K: every score display says it is a statistical estimate). Score rows
carry decision 7's identifiers as `scores_history` stores them, so any score traces to its MLflow
run.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date, datetime
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from distress_radar.models.drivers import driver_movement

DISCLAIMER = (
    "A statistical estimate from a model, not a statement of fact about the company. "
    "The model was trained on a purposive list of companies, so no probability here is a rate "
    "for the sector. A probability range covers the model's estimation uncertainty only."
)

Krs = Annotated[str, StringConstraints(pattern=r"^\d{10}$")]


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class ServedOut(_Frozen):
    """A served registered version: what scores a horizon, and what identifies it."""

    horizon_months: int
    model_name: str
    model_version: str
    model_alias: str
    model_status: str  # `champion`, `provisional champion` or `incumbent` (plan 0019 step F)
    model: str
    mlflow_run_id: str
    training_eve: date
    code_commit: str
    data_snapshot_hash: str
    label_version: str
    label_set_hash: str
    feature_set_version: str
    feature_set_hash: str


class Meta(_Frozen):
    served: list[ServedOut]


class ScoreOut(_Frozen):
    """A `scores_history` row: a probability, or "inputs incomplete" and the inputs missing."""

    krs: str
    as_of_date: date
    horizon_months: int
    probability: float | None
    not_scored_reason: str | None
    missing_inputs: list[str] | None
    top_drivers: list[dict[str, Any]] | None
    model_name: str
    model_version: str
    model_alias: str
    mlflow_run_id: str
    training_eve: date
    code_commit: str
    data_snapshot_hash: str
    label_version: str
    label_set_hash: str
    feature_set_version: str
    feature_set_hash: str
    population_hash: str | None
    scored_at: datetime
    # Plan 0021: a 90% interval for estimation uncertainty only (entity-bootstrap refits of the
    # served regression, on the lists it was trained on), or `interval_note` saying why none.
    probability_low: float | None = None
    probability_high: float | None = None
    interval_level: float | None = None
    interval_note: str | None = None
    # How the drivers moved since the previous score of the same version; None without one.
    previous_as_of_date: date | None = None
    driver_movement: list[dict[str, Any]] | None = None

    @classmethod
    def from_row(cls, row: Mapping[str, Any]) -> ScoreOut:
        """A score row as read (`api.db`), its movement computed from the previous drivers."""
        fields = {k: v for k, v in row.items() if k != "previous_drivers"}
        fields["driver_movement"] = driver_movement(
            row.get("top_drivers"), row.get("previous_drivers")
        )
        return cls(**fields)


class ExclusionOut(_Frozen):
    """A `scoring_exclusions` row: why an entity was not scored at all."""

    krs: str
    as_of_date: date
    horizon_months: int
    model_name: str
    model_version: str
    reason: str
    population_hash: str
    risk_label_set_hash: str
    code_commit: str
    data_snapshot_hash: str
    scored_at: datetime


class CompanyOut(_Frozen):
    """A company at its latest scored month-end, per served horizon: a score row or an exclusion."""

    krs: str
    scores: list[ScoreOut]
    exclusions: list[ExclusionOut]


class WatchlistMember(_Frozen):
    krs: str
    added_at: datetime
    scores: list[ScoreOut]
    exclusions: list[ExclusionOut]


class WatchlistOut(_Frozen):
    name: str
    members: list[WatchlistMember]


class WatchlistIn(_Frozen):
    """The whole list: PUT replaces the members, keeping each kept member's `added_at`."""

    krs: list[Krs] = Field(max_length=10_000)


class AlertOut(_Frozen):
    """An alert as `alerts` stores it (plan 0018 step D); `detail` holds what fired it."""

    alert_id: str
    krs: str
    as_of_date: date
    alert_type: str
    severity: str
    triggering_score_id: str | None
    rule_version: str
    detail: dict[str, Any]
    known_from: date
    created_at: datetime
    acknowledged_at: datetime | None
    on_watchlist: bool


class Envelope[T](_Frozen):
    meta: Meta
    disclaimer: str = DISCLAIMER
    data: T
