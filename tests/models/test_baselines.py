"""The classical models' configuration (plan 0012 step B, owner decision 4)."""

from __future__ import annotations

import copy
import shutil
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
import yaml
from pydantic import ValidationError

from distress_radar.features.config import FeatureSet, load_feature_set
from distress_radar.models.baselines import ClassicalModel, load_classical_model
from distress_radar.parsing.canonical_schema import CONFIG_DIR

MODELS = ("altman_z2_2000", "poznan_2004")


@pytest.fixture(scope="module")
def feature_set() -> FeatureSet:
    return load_feature_set("feature_set_v2").feature_set


def _raw(version: str) -> dict[str, Any]:
    return yaml.safe_load((CONFIG_DIR / "models" / f"{version}.yaml").read_text("utf-8"))


def _coefficients(model: ClassicalModel) -> dict[str, Decimal]:
    return {t.feature: t.coefficient for t in model.terms}


def test_every_configured_model_loads(feature_set: FeatureSet) -> None:
    configured = (CONFIG_DIR / "models").glob("*.yaml")
    assert sorted(p.stem for p in configured if not p.stem.startswith("backtest")) == sorted(MODELS)
    for version in MODELS:
        assert load_classical_model(version, feature_set).model == version


def test_z2_holds_the_published_coefficients(feature_set: FeatureSet) -> None:
    """Altman (2000), p. 27: Z'' = 6.56 X1 + 3.26 X2 + 6.72 X3 + 1.05 X4, no constant, no zones."""
    z2 = load_classical_model("altman_z2_2000", feature_set)
    assert _coefficients(z2) == {
        "working_capital_to_assets": Decimal("6.56"),
        "retained_earnings_to_assets": Decimal("3.26"),
        "ebit_to_assets": Decimal("6.72"),
        "equity_to_liabilities": Decimal("1.05"),
    }
    assert z2.intercept == 0 and z2.higher_is_safer
    assert z2.zone(Decimal("1.5")) is None  # the source publishes no Z'' cut-offs


def test_poznan_holds_the_published_coefficients(feature_set: FeatureSet) -> None:
    """Hamrol et al. (2004), p. 38: FD = 3.562 W7 + 1.588 W16 + 4.288 W5 + 6.719 W13 - 2.368."""
    poznan = load_classical_model("poznan_2004", feature_set)
    assert _coefficients(poznan) == {
        "roa": Decimal("3.562"),
        "quick_ratio": Decimal("1.588"),
        "long_term_capital_to_assets": Decimal("4.288"),
        "sales_margin": Decimal("6.719"),
    }
    assert poznan.intercept == Decimal("-2.368") and poznan.higher_is_safer
    # "Jeżeli otrzymany wynik jest liczbą większą od zera ... do dobrych": zero itself is not sound.
    assert poznan.zone(Decimal(0)) == "at_risk"
    assert poznan.zone(Decimal("0.001")) == "sound"
    assert poznan.zone(Decimal(-5)) == "at_risk"


def test_a_model_needing_a_ratio_the_set_lacks_is_rejected() -> None:
    v1 = load_feature_set("feature_set_v1").feature_set
    with pytest.raises(ValueError, match="not ratio features of feature_set_v1"):
        load_classical_model("altman_z2_2000", v1)


def test_a_model_reading_a_non_ratio_feature_is_rejected(feature_set: FeatureSet) -> None:
    raw = copy.deepcopy(_raw("poznan_2004"))
    raw["terms"][0]["feature"] = "late_filings_3y"  # a feature, but a count, not a ratio
    with pytest.raises(ValueError, match="late_filings_3y"):
        ClassicalModel.model_validate(raw).check_against(feature_set)


def test_zones_must_cover_every_score() -> None:
    raw = copy.deepcopy(_raw("poznan_2004"))
    raw["zones"][1]["lower"] = "0.5"
    with pytest.raises(ValidationError, match="do not meet"):
        ClassicalModel.model_validate(raw)
    raw = copy.deepcopy(_raw("poznan_2004"))
    raw["zones"] = raw["zones"][:1]
    with pytest.raises(ValidationError, match="cover every score"):
        ClassicalModel.model_validate(raw)


def test_a_source_must_name_the_document_read() -> None:
    raw = copy.deepcopy(_raw("altman_z2_2000"))
    del raw["source"]["sha256"]
    with pytest.raises(ValidationError):
        ClassicalModel.model_validate(raw)


def test_the_file_name_is_the_version(tmp_path: Path, feature_set: FeatureSet) -> None:
    config_dir = tmp_path / "config"
    shutil.copytree(CONFIG_DIR / "models", config_dir / "models")
    (config_dir / "models" / "poznan_2004.yaml").rename(config_dir / "models" / "poznan_v2.yaml")
    with pytest.raises(ValueError, match="must match the file name"):
        load_classical_model("poznan_v2", feature_set, config_dir)
