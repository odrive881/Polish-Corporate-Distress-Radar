"""J — the read-only scores API (plan 0016 step D, decisions 6 and 7).

FastAPI over Postgres `scores_history`, `scoring_exclusions` and `watchlists`. The served versions
are resolved from the MLflow registry once, at startup (AGENT_SPEC §6J), and loaded in-process;
the app refuses to start when the latest stored scores came from another version. Nothing is
scored per request: the `scoring` job writes the scores.

Every response names companies, so the server binds to 127.0.0.1 only and has no auth: one user,
one machine (AGENT_SPEC §6K). `make api` runs it.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

import psycopg
import uvicorn
from fastapi import FastAPI

from distress_radar.alerts import store as alerts_store
from distress_radar.api.db import check_served
from distress_radar.api.deps import Connect, Served
from distress_radar.api.routes import alerts, companies, scores, watchlists
from distress_radar.errors import init_error_reporting
from distress_radar.models import scores_store
from distress_radar.models.serving import load_served
from distress_radar.models.splits import load_backtest_config
from distress_radar.settings import Settings

HOST = "127.0.0.1"
PORT = 8000


def create_app(served: Served, connect: Connect) -> FastAPI:
    """The app over the given served versions (one per horizon) and connection factory."""
    if not served:
        raise ValueError("no served version")

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncGenerator[None]:
        with connect() as conn:
            scores_store.ensure_schema(conn)
            alerts_store.ensure_schema(conn)
            conn.commit()
            check_served(conn, served)
        yield

    app = FastAPI(title="distress_radar (local)", lifespan=lifespan)
    app.state.served = served
    app.state.connect = connect
    for module in (companies, scores, watchlists, alerts):
        app.include_router(module.router)
    return app


def app_from_settings(settings: Settings | None = None) -> FastAPI:
    """The served versions of `BACKTEST_VERSION`'s horizons, loaded from the registry."""
    settings = settings or Settings()
    config = load_backtest_config(settings.backtest_version)
    served = {h: load_served(settings.mlflow_tracking_uri, h)[0] for h in config.horizons}
    conninfo = settings.postgres_conninfo
    return create_app(served, lambda: psycopg.connect(conninfo))


def main() -> None:
    init_error_reporting()  # a no-op without SENTRY_DSN (plan 0018 step H)
    uvicorn.run(app_from_settings(), host=HOST, port=PORT)


if __name__ == "__main__":
    main()
