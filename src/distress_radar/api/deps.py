"""What every route depends on: a Postgres connection, the served versions, the envelope."""

from __future__ import annotations

from collections.abc import Callable, Iterator, Mapping
from typing import cast

from fastapi import Request
from psycopg import Connection

from distress_radar.api.schemas import Envelope, Meta, ServedOut
from distress_radar.models.serving import ServedVersion

type Served = Mapping[int, ServedVersion]
type Connect = Callable[[], Connection]


def connection(request: Request) -> Iterator[Connection]:
    """One connection per request, closed after it."""
    connect = cast("Connect", request.app.state.connect)
    with connect() as conn:
        yield conn


def served_versions(request: Request) -> Served:
    return cast("Served", request.app.state.served)


def served_out(served: Served) -> list[ServedOut]:
    return [
        ServedOut(
            horizon_months=horizon,
            model_name=v.name,
            model_version=v.version,
            model_alias=v.alias,
            model=v.model,
            mlflow_run_id=v.run_id,
            training_eve=v.training_eve,
            code_commit=v.tags["code_commit"],
            data_snapshot_hash=v.tags["data_snapshot_hash"],
            label_version=v.tags["label_version"],
            label_set_hash=v.tags["label_set_hash"],
            feature_set_version=v.tags["feature_set_version"],
            feature_set_hash=v.tags["feature_set_hash"],
        )
        for horizon, v in sorted(served.items())
    ]


def envelope[T](served: Served, data: T) -> Envelope[T]:
    return Envelope[T](meta=Meta(served=served_out(served)), data=data)
