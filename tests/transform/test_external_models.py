"""SQLMesh external boundary (plan 0007 step B; `transform/config.py`).

The `ext` views are declared twice: as `before_all` statements in the config,
and with their columns in `transform/external_models.yaml`, which is what
SQLMesh type-checks the models against. These tests keep both in step with the
Polars schemas the Dagster assets actually write, so a column added on the
Python side cannot silently vanish from, or mistype in, the SQL side.
"""

import importlib.util
from pathlib import Path
from types import ModuleType
from typing import Any

import polars as pl
import yaml

from distress_radar.parsing.accounting_identities import (
    IDENTITY_CHECK_RESULTS_SCHEMA,
    RESTATEMENT_SCHEMA,
)
from distress_radar.parsing.legal_events import LEGAL_EVENTS_SCHEMA
from distress_radar.parsing.mapping_engine import CANONICAL_COLUMNS

TRANSFORM = Path(__file__).parent.parent.parent / "transform"
PARQUET_SCHEMAS: dict[str, dict[str, pl.DataType | type[pl.DataType]]] = {
    "financial_statements_canonical": CANONICAL_COLUMNS,
    "restatement_events": RESTATEMENT_SCHEMA,
    "identity_check_results": IDENTITY_CHECK_RESULTS_SCHEMA,
    "legal_events": LEGAL_EVENTS_SCHEMA,
}


def _config() -> ModuleType:
    spec = importlib.util.spec_from_file_location("transform_config", TRANSFORM / "config.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _declared() -> dict[str, dict[str, str]]:
    models: list[dict[str, Any]] = yaml.safe_load((TRANSFORM / "external_models.yaml").read_text())
    return {m["name"].removeprefix("ext."): m["columns"] for m in models}


def _sql_type(dtype: pl.DataType | type[pl.DataType]) -> str:
    if isinstance(dtype, pl.Decimal):
        return f"DECIMAL({dtype.precision}, {dtype.scale})"
    return {pl.String: "TEXT", pl.Int32: "INT", pl.Date: "DATE"}[dtype]  # type: ignore[index]


def test_every_external_model_has_a_view_and_every_view_a_model() -> None:
    config = _config()
    views = {*config.PARQUET_DATASETS, *config.POSTGRES_TABLES}
    assert views == set(_declared())
    statements = "\n".join(config.config.before_all)
    for name in views:
        assert f"VIEW ext.{name} AS" in statements


def test_parquet_columns_match_the_schemas_the_assets_write() -> None:
    declared = _declared()
    for name, schema in PARQUET_SCHEMAS.items():
        expected = {column: _sql_type(dtype) for column, dtype in schema.items()}
        assert declared[name] == expected, name
        assert list(declared[name]) == list(schema), f"{name}: column order"


def test_the_test_gateway_needs_no_services() -> None:
    """`make check` runs `sqlmesh test` on this gateway with nothing running."""
    gateway = _config().config.gateways["test"]
    for connection in (gateway.connection, gateway.state_connection, gateway.test_connection):
        assert connection.type_ == "duckdb"
        assert not connection.catalogs or all(
            not isinstance(v, str) or v == ":memory:" for v in connection.catalogs.values()
        )
