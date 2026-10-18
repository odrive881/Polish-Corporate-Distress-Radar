"""`GET /scores` and `GET /meta` (plan 0016 decision 6)."""

from __future__ import annotations

from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from psycopg import Connection

from distress_radar.api import db
from distress_radar.api.deps import Served, connection, envelope, served_versions
from distress_radar.api.schemas import Envelope, ScoreOut

router = APIRouter(tags=["scores"])


@router.get("/scores")
def scores(
    conn: Annotated[Connection, Depends(connection)],
    served: Annotated[Served, Depends(served_versions)],
    as_of_date: Annotated[date | None, Query()] = None,
    horizon_months: Annotated[int | None, Query(gt=0)] = None,
) -> Envelope[list[ScoreOut]]:
    """Every score row at a month-end (the latest scored one by default)."""
    day = as_of_date or db.latest_as_of_date(conn, served)
    if day is None:
        raise HTTPException(404, "no scores from the served versions yet: run the scoring job")
    if horizon_months is not None and horizon_months not in served:
        raise HTTPException(404, f"no served {horizon_months}-month model")
    rows = db.scores_at(conn, served, day, horizon_months)
    if not rows:
        raise HTTPException(404, f"no scores from the served versions at {day}")
    return envelope(served, [ScoreOut.from_row(r) for r in rows])


@router.get("/meta")
def meta(served: Annotated[Served, Depends(served_versions)]) -> Envelope[None]:
    """The served versions and the disclaimer, with no data."""
    return envelope(served, None)
