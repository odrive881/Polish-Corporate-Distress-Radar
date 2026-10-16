"""`GET|PUT|DELETE /watchlists/{name}` (plan 0016 decision 6)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path
from psycopg import Connection

from distress_radar.api import db
from distress_radar.api.deps import Served, connection, envelope, served_versions
from distress_radar.api.routes.companies import latest_by_horizon
from distress_radar.api.schemas import Envelope, WatchlistIn, WatchlistMember, WatchlistOut

router = APIRouter(tags=["watchlists"])
NamePath = Annotated[str, Path(min_length=1, max_length=100)]


def _read(conn: Connection, served: Served, name: str) -> WatchlistOut:
    members = db.watchlist(conn, name)
    krs = [k for k, _ in members]
    scores = db.latest_scores(conn, served, krs)
    exclusions = db.latest_exclusions(conn, served, krs)
    out: list[WatchlistMember] = []
    for member, added_at in members:
        own_scores, own_exclusions = latest_by_horizon(
            [r for r in scores if r["krs"] == member],
            [r for r in exclusions if r["krs"] == member],
        )
        out.append(
            WatchlistMember(
                krs=member, added_at=added_at, scores=own_scores, exclusions=own_exclusions
            )
        )
    return WatchlistOut(name=name, members=out)


@router.get("/watchlists/{name}")
def get_watchlist(
    name: NamePath,
    conn: Annotated[Connection, Depends(connection)],
    served: Annotated[Served, Depends(served_versions)],
) -> Envelope[WatchlistOut]:
    found = _read(conn, served, name)
    if not found.members:
        raise HTTPException(404, f"no watchlist {name!r}")
    return envelope(served, found)


@router.put("/watchlists/{name}")
def put_watchlist(
    name: NamePath,
    body: WatchlistIn,
    conn: Annotated[Connection, Depends(connection)],
    served: Annotated[Served, Depends(served_versions)],
) -> Envelope[WatchlistOut]:
    """Make the list exactly the companies given; an empty list deletes it."""
    db.replace_watchlist(conn, name, body.krs, datetime.now(UTC))
    return envelope(served, _read(conn, served, name))


@router.delete("/watchlists/{name}", status_code=204)
def delete_watchlist(name: NamePath, conn: Annotated[Connection, Depends(connection)]) -> None:
    if db.delete_watchlist(conn, name) == 0:
        raise HTTPException(404, f"no watchlist {name!r}")
