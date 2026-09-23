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
    if isinstance(dtype, pl.List):
        return f"{_sql_type(dtype.inner)}[]"
    return {pl.String: "TEXT", pl.Int32: "INT", pl.Date: "DATE", pl.Boolean: "BOOLEAN"}[dtype]  # type: ignore[index]


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


def test_a_dataset_not_yet_written_is_an_empty_typed_view(tmp_path: Path) -> None:
    """A fresh clone: no Parquet yet. The view must still exist, empty, with its columns."""
    import duckdb

    config = _config()
    con = duckdb.connect()
    con.execute("CREATE SCHEMA ext")
    con.execute(config._parquet_view("legal_events", root=tmp_path))  # pyright: ignore[reportPrivateUsage]
    described = {row[0]: row[1] for row in con.execute("DESCRIBE ext.legal_events").fetchall()}
    assert list(described) == list(_declared()["legal_events"])
    assert described["ends"] == "VARCHAR[]" and described["event_date"] == "DATE"
    assert con.execute("SELECT COUNT(*) FROM ext.legal_events").fetchone() == (0,)


def test_a_written_dataset_is_read_from_its_files(tmp_path: Path) -> None:
    import duckdb

    config = _config()
    (tmp_path / "legal_events" / "event_year=2020").mkdir(parents=True)
    empty = pl.DataFrame(schema=PARQUET_SCHEMAS["legal_events"])
    empty.write_parquet(tmp_path / "legal_events" / "event_year=2020" / "part-0.parquet")
    statement = config._parquet_view("legal_events", root=tmp_path)  # pyright: ignore[reportPrivateUsage]
    assert "read_parquet(" in statement
    con = duckdb.connect()
    con.execute("CREATE SCHEMA ext")
    con.execute(statement)
    assert con.execute("SELECT COUNT(*) FROM ext.legal_events").fetchone() == (0,)


def test_every_parquet_view_builds_on_an_empty_warehouse(tmp_path: Path) -> None:
    import duckdb

    config = _config()
    con = duckdb.connect()
    con.execute("CREATE SCHEMA ext")
    for name in config.PARQUET_DATASETS:
        con.execute(config._parquet_view(name, root=tmp_path))  # pyright: ignore[reportPrivateUsage]
        columns = [row[0] for row in con.execute(f"DESCRIBE ext.{name}").fetchall()]
        assert columns == list(_declared()[name]), name
