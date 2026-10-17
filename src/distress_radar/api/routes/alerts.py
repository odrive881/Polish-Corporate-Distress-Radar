"""`GET /alerts` (plan 0018 step E, decision 5): the alerts, read-only.

Acknowledging is the explorer's (its Alerts page); this route only reads. Like every route it is
served on 127.0.0.1 only: an alert names a company.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from psycopg import Connection

from distress_radar.api import db
from distress_radar.api.deps import Served, connection, envelope, served_versions
from distress_radar.api.schemas import AlertOut, Envelope, Krs

router = APIRouter(tags=["alerts"])


@router.get("/alerts")
def alerts(
    conn: Annotated[Connection, Depends(connection)],
    served: Annotated[Served, Depends(served_versions)],
    acknowledged: Annotated[bool, Query()] = False,
    krs: Annotated[Krs | None, Query()] = None,
    watchlist: Annotated[str | None, Query(min_length=1, max_length=100)] = None,
    limit: Annotated[int, Query(gt=0, le=10_000)] = 500,
) -> Envelope[list[AlertOut]]:
    """The unacknowledged alerts (`acknowledged=true`: the acknowledged ones), companies on a
    watchlist first, then by severity and the latest month-end."""
    rows = db.alerts(conn, acknowledged=acknowledged, krs=krs, watchlist=watchlist, limit=limit)
    return envelope(served, [AlertOut(**r) for r in rows])
