"""SQLMesh project config (plan 0007 step B, ADR 0010).

DuckDB executes; SQLMesh's own state (snapshots, plans, environments) lives in
the Postgres that already holds the manifest. Paths and credentials come from
`distress_radar.settings.Settings`, so `WAREHOUSE_DIR` and the Postgres
connection have one source of truth shared with the Python side.

The external boundary is the DuckDB schema `ext`: one view per upstream
dataset, recreated by `before_all` at the start of every `plan` and `run`.
Parquet written by Dagster is read in place under `WAREHOUSE_DIR` (ADR 0008);
Postgres tables are read through DuckDB's `postgres` extension, attached
read-only as the catalog `manifest`. Models only ever select from `ext.*`,
whose columns are declared in `external_models.yaml`.

The `test` gateway is what `make transform-test` uses: in-memory DuckDB for
both execution and state, no Postgres, no warehouse. `before_all` does not run
for unit tests, so the `ext` tables there are the tests' own fixtures, and
`make check` keeps needing no running services.
"""

from __future__ import annotations

from sqlmesh.core.config import (
    Config,
    DuckDBConnectionConfig,
    GatewayConfig,
    ModelDefaultsConfig,
    PostgresConnectionConfig,
)
from sqlmesh.core.config.connection import DuckDBAttachOptions

from distress_radar.settings import Settings

# Parquet datasets under WAREHOUSE_DIR, written by `dagster_defs/assets/parsing.py`.
PARQUET_DATASETS = (
    "financial_statements_canonical",
    "restatement_events",
    "identity_check_results",
)
# Postgres manifest tables (ADR 0006).
POSTGRES_TABLES = (
    "quarantine_events",
    "parsed_documents",
)

settings = Settings()
warehouse = settings.warehouse_dir.resolve()


def _parquet_view(name: str) -> str:
    # Partition columns are stored inside the files (`warehouse.py`), so Hive
    # parsing stays off; `**` also matches an empty dataset's root file.
    glob = (warehouse / name / "**" / "*.parquet").as_posix()
    return (
        f"CREATE OR REPLACE VIEW ext.{name} AS "
        f"SELECT * FROM read_parquet('{glob}', hive_partitioning = false)"
    )


def _postgres_view(name: str) -> str:
    return f"CREATE OR REPLACE VIEW ext.{name} AS SELECT * FROM manifest.public.{name}"


config = Config(
    project="distress_radar",
    default_gateway="local",
    gateways={
        "local": GatewayConfig(
            connection=DuckDBConnectionConfig(
                catalogs={
                    "transform": (warehouse / "transform.duckdb").as_posix(),
                    "manifest": DuckDBAttachOptions(
                        type="postgres", path=settings.postgres_conninfo, read_only=True
                    ),
                },
                # The `postgres` extension is installed by `make transform-setup`,
                # never downloaded implicitly in the middle of a run.
                connector_config={"autoinstall_known_extensions": False},
            ),
            state_connection=PostgresConnectionConfig(
                host=settings.postgres_host,
                port=settings.postgres_port,
                user=settings.postgres_user,
                password=settings.postgres_password.get_secret_value(),
                database=settings.postgres_db,
            ),
            state_schema="sqlmesh",
        ),
        "test": GatewayConfig(
            connection=DuckDBConnectionConfig(),
            state_connection=DuckDBConnectionConfig(),
            test_connection=DuckDBConnectionConfig(),
        ),
    },
    model_defaults=ModelDefaultsConfig(dialect="duckdb"),
    before_all=[
        "CREATE SCHEMA IF NOT EXISTS ext",
        *(_parquet_view(name) for name in PARQUET_DATASETS),
        *(_postgres_view(name) for name in POSTGRES_TABLES),
    ],
)
