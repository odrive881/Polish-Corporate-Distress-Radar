"""`GET /companies/{krs}` and `GET /companies/{krs}/scores` (plan 0016 decision 6)."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Path
from psycopg import Connection

from distress_radar.api import db
from distress_radar.api.deps import Served, connection, envelope, served_versions
from distress_radar.api.schemas import CompanyOut, Envelope, ExclusionOut, ScoreOut

router = APIRouter(tags=["companies"])
KrsPath = Annotated[str, Path(pattern=r"^\d{10}$")]


def latest_by_horizon(
    scores: Iterable[dict[str, Any]], exclusions: Iterable[dict[str, Any]]
) -> tuple[list[ScoreOut], list[ExclusionOut]]:
    """Per horizon, whichever is newer: the score row or the exclusion. A company scored at a
    later month-end is no longer excluded, and one excluded later (it entered a proceeding) no
    longer has a current score."""
    kept_scores = {r["horizon_months"]: r for r in scores}
    kept_exclusions: dict[int, dict[str, Any]] = {}
    for row in exclusions:
        score = kept_scores.get(row["horizon_months"])
        if score is None or row["as_of_date"] > score["as_of_date"]:
            kept_scores.pop(row["horizon_months"], None)
            kept_exclusions[row["horizon_months"]] = row
    return (
        [ScoreOut.from_row(kept_scores[h]) for h in sorted(kept_scores)],
        [ExclusionOut(**kept_exclusions[h]) for h in sorted(kept_exclusions)],
    )


@router.get("/companies/{krs}")
def company(
    krs: KrsPath,
    conn: Annotated[Connection, Depends(connection)],
    served: Annotated[Served, Depends(served_versions)],
) -> Envelope[CompanyOut]:
    scores, exclusions = latest_by_horizon(
        db.latest_scores(conn, served, [krs]), db.latest_exclusions(conn, served, [krs])
    )
    if not scores and not exclusions:
        raise HTTPException(404, f"{krs} was not seen by the served versions' scoring runs")
    return envelope(served, CompanyOut(krs=krs, scores=scores, exclusions=exclusions))


@router.get("/companies/{krs}/scores")
def company_scores(
    krs: KrsPath,
    conn: Annotated[Connection, Depends(connection)],
    served: Annotated[Served, Depends(served_versions)],
) -> Envelope[list[ScoreOut]]:
    return envelope(served, [ScoreOut.from_row(r) for r in db.score_history(conn, served, krs)])
