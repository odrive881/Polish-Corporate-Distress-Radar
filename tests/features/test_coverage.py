"""Feature coverage by family and form (plan 0010 step G)."""

# polars's signatures reference types pyright cannot resolve; scoped to this module.
# pyright: reportUnknownMemberType=false

from __future__ import annotations

from datetime import date

import polars as pl
import pytest

from distress_radar.features import feature_definitions as fd
from distress_radar.features.asof_assembly import assemble
from distress_radar.features.config import FeatureConfig, load_feature_set
from distress_radar.features.coverage import FORMS_SCHEMA, coverage, latest_forms
from distress_radar.features.panel import PANEL_SCHEMA
from distress_radar.parsing.mapping_engine import CANONICAL_COLUMNS

KRS = "0000000001"


@pytest.fixture(scope="module")
def config() -> FeatureConfig:
    return load_feature_set("feature_set_v1")


def _version(ref: str, period_end: date, known_from: date, kind: str) -> dict[str, object]:
    return {
        "krs": KRS,
        "period_end": period_end,
        "known_from": known_from,
        "input": "total_assets",
        "source_kind": kind,
        "document_ref": ref,
    }


def _canonical(*refs: tuple[str, str]) -> pl.DataFrame:
    return pl.DataFrame(
        [{"document_ref": ref, "structure_version": version} for ref, version in refs],
        schema={k: CANONICAL_COLUMNS[k] for k in ("document_ref", "structure_version")},
    )


def _grid(*days: date) -> pl.DataFrame:
    return pl.DataFrame([(KRS, d) for d in days], schema=fd.GRID_SCHEMA, orient="row")


def test_a_rows_form_is_its_latest_known_statements() -> None:
    panel = pl.DataFrame(
        [
            _version("m21", date(2021, 12, 31), date(2022, 6, 1), "filed"),
            _version("f22", date(2022, 12, 31), date(2023, 6, 1), "filed"),
            _version("f22", date(2022, 12, 31), date(2023, 8, 1), "withdrawn"),
        ],
        schema={k: PANEL_SCHEMA[k] for k in _version("x", date.min, date.min, "x")},
    )
    canonical = _canonical(("m21", "micro-2018-v1-2"), ("f22", "full-2018-v1-2"))
    days = [date(2022, 5, 31), date(2022, 6, 30), date(2023, 6, 30), date(2023, 8, 31)]
    forms = latest_forms(_grid(*days), panel, canonical)
    assert forms.schema == pl.Schema(FORMS_SCHEMA)
    # Nothing known yet; micro; the full 2022 statement; withdrawn, so micro 2021 again.
    assert forms.get_column("form").to_list() == ["none", "micro", "full", "micro"]


def test_coverage_counts_non_null_values_per_family_and_form(config: FeatureConfig) -> None:
    grid = _grid(date(2022, 6, 30), date(2022, 7, 31))
    values = pl.DataFrame(
        [
            (KRS, date(2022, 7, 31), "current_ratio", 2.0, date(2022, 7, 1)),
            (KRS, date(2022, 7, 31), "negative_equity", 0.0, date(2022, 7, 1)),
        ],
        schema=fd.FEATURE_VALUES_SCHEMA,
        orient="row",
    )
    forms = pl.DataFrame(
        [(KRS, date(2022, 6, 30), "none"), (KRS, date(2022, 7, 31), "full")],
        schema=FORMS_SCHEMA,
        orient="row",
    )
    shares = coverage(assemble(grid, values, config), forms, config)
    tripwire = shares.filter(pl.col("family") == "tripwire").sort("form")
    assert tripwire.select("form", "rows", "values", "non_null").rows() == [
        ("full", 1, 2, 1),
        ("none", 1, 2, 0),
    ]
    financial = shares.filter((pl.col("family") == "financial") & (pl.col("form") == "full"))
    n = sum(f.family == "financial" for f in config.feature_set.features)
    assert financial.row(0, named=True)["share"] == pytest.approx(1 / n)
    assert set(shares.get_column("family").to_list()) == set(fd.FAMILIES)
