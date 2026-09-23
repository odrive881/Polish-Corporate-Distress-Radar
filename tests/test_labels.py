"""Outcome label parameters (plan 0008 step B)."""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

import pytest
import yaml
from pydantic import ValidationError

from distress_radar.labels import LabelConfig, load_label_config
from distress_radar.parsing.canonical_schema import CONFIG_DIR


def _raw() -> dict[str, Any]:
    return yaml.safe_load((CONFIG_DIR / "labels" / "outcome_labels_v1.yaml").read_text("utf-8"))


def test_repository_label_config_loads() -> None:
    config = load_label_config()
    assert config.label_version == "outcome_labels_v1"
    assert config.horizons_months == (12, 24)
    assert config.precedence[0] == "bankruptcy"


@pytest.mark.parametrize(
    ("key", "value", "match"),
    [
        ("horizons_months", [12, 36], "horizons_months"),
        ("horizons_months", [12, 12], "distinct"),
        ("precedence", ["bankruptcy", "restructuring", "liquidation"], "precedence"),
        ("precedence", ["bankruptcy", "restructuring", "liquidation", "alive"], "precedence"),
        ("as_of_grid", {"frequency": "month_end", "start": "2012-01-30"}, "month end"),
        ("cutoff", "latest_fetch", "cutoff"),
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
