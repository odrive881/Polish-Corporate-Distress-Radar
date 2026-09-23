"""Outcome labels: parameters (plan 0008 step B) and the label-set freeze (step G)."""

from __future__ import annotations

import shutil
from datetime import date
from pathlib import Path
from typing import Any

import polars as pl
import pytest
import yaml
from pydantic import ValidationError

from distress_radar.labels import (
    OUTCOME_LABELS_SCHEMA,
    LabelConfig,
    freeze_label_set,
    label_set_hash,
    load_label_config,
)
from distress_radar.parsing.canonical_schema import CONFIG_DIR
from distress_radar.settings import Settings


def _raw() -> dict[str, Any]:
    return yaml.safe_load((CONFIG_DIR / "labels" / "outcome_labels_v1.yaml").read_text("utf-8"))


def test_repository_label_configs_load() -> None:
    v1 = load_label_config("outcome_labels_v1")
    assert v1.label_version == "outcome_labels_v1"
    assert v1.horizons_months == (12, 24)
    assert v1.precedence[0] == "bankruptcy"
    # Version 1 predates the lag allowance and petition expiry: both off.
    assert (v1.alive_lag_months, v1.petition_expiry_months) == (0, None)


def test_version_2_adds_only_the_lag_allowance_and_petition_expiry() -> None:
    v1 = load_label_config("outcome_labels_v1")
    v2 = load_label_config("outcome_labels_v2")
    assert (v2.alive_lag_months, v2.petition_expiry_months) == (12, 24)
    changed = {k for k, v in v2.model_dump().items() if v != v1.model_dump()[k]}
    assert changed == {"label_version", "alive_lag_months", "petition_expiry_months"}


def test_the_default_label_version_is_2() -> None:
    assert Settings().label_version == "outcome_labels_v2"


@pytest.mark.parametrize(
    ("key", "value", "match"),
    [
        ("horizons_months", [12, 36], "horizons_months"),
        ("horizons_months", [12, 12], "distinct"),
        ("precedence", ["bankruptcy", "restructuring", "liquidation"], "precedence"),
        ("precedence", ["bankruptcy", "restructuring", "liquidation", "alive"], "precedence"),
        ("as_of_grid", {"frequency": "month_end", "start": "2012-01-30"}, "month end"),
        ("cutoff", "latest_fetch", "cutoff"),
        ("alive_lag_months", -1, "alive_lag_months"),
        ("petition_expiry_months", 0, "petition_expiry_months"),
    ],
)
def test_rejects_bad_values(key: str, value: object, match: str) -> None:
    raw = _raw()
    raw[key] = value
    with pytest.raises(ValidationError, match=match):
        LabelConfig.model_validate(raw)


def test_version_must_match_the_file_name(tmp_path: Path) -> None:
    (tmp_path / "labels").mkdir()
    shutil.copy(CONFIG_DIR / "labels" / "outcome_labels_v1.yaml", tmp_path / "labels" / "v9.yaml")
    with pytest.raises(ValueError, match="file name"):
        load_label_config("v9", config_dir=tmp_path)


# --- freezing (plan 0008 step G) -----------------------------------------------------------------


def _labels(**overrides: object) -> pl.DataFrame:
    row: dict[str, object] = {
        "krs": "0000000042",
        "as_of_date": date(2020, 1, 31),
        "horizon_months": 12,
        "outcome_class": "bankruptcy",
        "censored": False,
        "event_date": date(2020, 6, 1),
        "event_known_from": date(2020, 6, 10),
        "trigger_event_type": "bankruptcy_declared",
        "proceeding_id": "IX/GU/1/20",
        "proceeding_id_note": None,
        "regime_flag": True,
        "source_era": "pre_krz",
        "cutoff_date": date(2026, 9, 23),
        "label_version": "outcome_labels_v1",
    }
    other = {**row, "as_of_date": date(2020, 2, 29), "outcome_class": None, "censored": True}
    return pl.DataFrame([{**row, **overrides}, other], schema=OUTCOME_LABELS_SCHEMA, orient="row")


def test_the_hash_is_of_the_rows_not_their_order() -> None:
    frame = _labels()
    assert label_set_hash(frame) == label_set_hash(frame.reverse())
    assert label_set_hash(frame) != label_set_hash(_labels(outcome_class="restructuring"))


def test_a_set_is_written_once_under_its_hash(tmp_path: Path) -> None:
    first = freeze_label_set(_labels(), tmp_path)
    assert first.written and first.path.parts[0] == "outcome_labels"
    stored = pl.read_parquet(tmp_path / first.path)
    assert stored["label_set_hash"].unique().to_list() == [first.label_set_hash]
    assert stored.drop("label_set_hash").equals(
        _labels().sort(["krs", "as_of_date", "horizon_months"])
    )
    mtime = (tmp_path / first.path).stat().st_mtime_ns

    again = freeze_label_set(_labels().reverse(), tmp_path)
    assert (again.label_set_hash, again.written) == (first.label_set_hash, False)
    assert (tmp_path / first.path).stat().st_mtime_ns == mtime  # never rewritten

    other = freeze_label_set(_labels(outcome_class="liquidation"), tmp_path)
    assert other.written and other.path != first.path


def test_a_set_has_one_label_version(tmp_path: Path) -> None:
    mixed = pl.concat([_labels(), _labels(label_version="outcome_labels_v2", krs="0000000043")])
    with pytest.raises(ValueError, match="one label_version"):
        freeze_label_set(mixed, tmp_path)
