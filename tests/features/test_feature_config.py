"""Feature set and line-item map (plan 0010 step B)."""

from __future__ import annotations

import copy
import shutil
from pathlib import Path
from typing import Any

import pytest
import yaml
from pydantic import ValidationError

from distress_radar.features.config import (
    EventCountFeature,
    FeatureConfig,
    FeatureSet,
    LineItems,
    load_feature_set,
)
from distress_radar.parsing.canonical_schema import CONFIG_DIR, load_mapping_config

MICRO = {"jednostka_mikro_v1_2", "jednostka_mikro_v1_3"}
ALL_BODIES = {"jednostka_inna", "jednostka_mala"} | MICRO


@pytest.fixture(scope="module")
def config() -> FeatureConfig:
    return load_feature_set("feature_set_v1")


def _raw(name: str) -> dict[str, Any]:
    return yaml.safe_load((CONFIG_DIR / "features" / f"{name}.yaml").read_text("utf-8"))


def test_repository_feature_set_loads(config: FeatureConfig) -> None:
    fs = config.feature_set
    assert fs.feature_set_version == "feature_set_v1"
    assert fs.include_quarantined_statements is False  # plan 0010 owner decision 3
    assert {f.family for f in fs.features} == {
        "financial",
        "construction",
        "tripwire",
        "filing",
        "registry",
        "legal_history",
    }
    assert len(config.feature_set_hash) == 64


def test_coverage_by_form_is_what_the_plan_says(config: FeatureConfig) -> None:
    coverage = config.line_items.coverage(load_mapping_config())
    for everywhere in ("total_assets", "current_assets", "equity", "revenue", "net_result"):
        assert coverage[everywhere] == ALL_BODIES, everywhere
    # Micro: no equity breakdown and no liability split, so no Art. 233 and no liquidity.
    for full_and_small in (
        "supplementary_capital",
        "reserve_capital",
        "retained_result",
        "short_term_liabilities",
    ):
        assert not coverage[full_and_small] & MICRO, full_and_small
    # The small form's profit chain has no operating-result line.
    assert coverage["operating_result"] == {"jednostka_inna"}


def test_art_233_needs_a_line_micro_does_not_carry(config: FeatureConfig) -> None:
    rule = config.tripwires.rule("art_233")
    assert rule is not None
    coverage = config.line_items.coverage(load_mapping_config())
    assert any(not coverage[i] & MICRO for i in rule.inputs())


def test_legal_history_filters_resolve_to_the_taxonomy(config: FeatureConfig) -> None:
    by_name = {f.name: f for f in config.feature_set.features}
    petitions = by_name["bankruptcy_petitions_ever"]
    closed = by_name["bankruptcies_closed_ever"]
    assert isinstance(petitions, EventCountFeature) and isinstance(closed, EventCountFeature)
    assert petitions.matches(config.taxonomy) == {
        "bankruptcy_petition",
        "bankruptcy_petition_asset_security",
        "bankruptcy_petition_dismissed_no_assets",
    }
    assert closed.matches(config.taxonomy) == {
        "bankruptcy_proceeding_ended",
        "bankruptcy_petition_dismissed",
    }


# --- rejected feature sets -----------------------------------------------------------------------


def _invalid_set(mutate: Any, match: str) -> None:
    raw = copy.deepcopy(_raw("feature_set_v1"))
    mutate(raw)
    with pytest.raises(ValidationError, match=match):
        FeatureSet.model_validate(raw)


def _feature(raw: dict[str, Any], name: str) -> dict[str, Any]:
    return next(f for f in raw["features"] if f["name"] == name)


def test_rejects_a_duplicate_name() -> None:
    _invalid_set(lambda raw: raw["features"].append(raw["features"][0]), "duplicate feature names")  # pyright: ignore[reportUnknownLambdaType, reportUnknownMemberType]


def test_the_quarantine_switch_must_be_stated() -> None:
    _invalid_set(lambda raw: raw.pop("include_quarantined_statements"), "include_quarantined")  # pyright: ignore[reportUnknownLambdaType, reportUnknownMemberType]


def test_rejects_a_kind_its_family_cannot_supply() -> None:
    _invalid_set(
        lambda raw: _feature(raw, "current_ratio").update(family="registry"),  # pyright: ignore[reportUnknownLambdaType, reportUnknownMemberType]
        "cannot be a ratio",
    )
    _invalid_set(
        lambda raw: _feature(raw, "net_margin_volatility_3y").update(of="curators_ever"),  # pyright: ignore[reportUnknownLambdaType, reportUnknownMemberType]
        "not a ratio feature",
    )


def test_rejects_malformed_features() -> None:
    _invalid_set(
        lambda raw: _feature(raw, "missing_years_3y").pop("lookback_years"),  # pyright: ignore[reportUnknownLambdaType, reportUnknownMemberType]
        "lookback_years",
    )
    _invalid_set(
        lambda raw: _feature(raw, "curators_ever").update(stage="signal"),  # pyright: ignore[reportUnknownLambdaType, reportUnknownMemberType]
        "not both",
    )
    _invalid_set(
        lambda raw: _feature(raw, "curators_ever").pop("window_months"),  # pyright: ignore[reportUnknownLambdaType, reportUnknownMemberType]
        "window_months",
    )
    _invalid_set(
        lambda raw: _feature(raw, "roa").update(name="roa__known_from"),  # pyright: ignore[reportUnknownLambdaType, reportUnknownMemberType]
        "companion columns",
    )


def _check(mutate: Any, match: str, config: FeatureConfig) -> None:
    raw = copy.deepcopy(_raw("feature_set_v1"))
    mutate(raw)
    fs = FeatureSet.model_validate(raw)
    with pytest.raises(ValueError, match=match):
        fs.check_against(config.line_items, config.tripwires, config.taxonomy)


def test_rejects_inputs_the_config_cannot_supply(config: FeatureConfig) -> None:
    _check(
        lambda raw: _feature(raw, "roa")["numerator"].update(ebitda=1),  # pyright: ignore[reportUnknownLambdaType, reportUnknownMemberType]
        "inputs not in line_items_v1",
        config,
    )
    _check(
        lambda raw: _feature(raw, "art233_triggered").update(rule="art_397"),  # pyright: ignore[reportUnknownLambdaType, reportUnknownMemberType]
        "does not apply to sp_z_oo",
        config,
    )
    _check(
        lambda raw: _feature(raw, "art233_triggered").update(rule="art_999"),  # pyright: ignore[reportUnknownLambdaType, reportUnknownMemberType]
        "unknown KSH rule",
        config,
    )
    _check(
        lambda raw: _feature(raw, "board_changes_12m").update(event_types=["board_reshuffled"]),  # pyright: ignore[reportUnknownLambdaType, reportUnknownMemberType]
        "unknown event types",
        config,
    )
    _check(
        lambda raw: _feature(raw, "bankruptcy_petitions_ever").update(stage="exit"),  # pyright: ignore[reportUnknownLambdaType, reportUnknownMemberType]
        "matches no event type",
        config,
    )


# --- rejected line-item maps ---------------------------------------------------------------------


def test_rejects_an_unknown_chart_code() -> None:
    raw = copy.deepcopy(_raw("line_items_v1"))
    raw["inputs"]["revenue"].append("IS.COMP.ZZ")
    with pytest.raises(ValueError, match=r"not in the canonical chart \['IS.COMP.ZZ'\]"):
        LineItems.model_validate(raw).check_against(load_mapping_config())


def test_rejects_an_input_a_statement_carries_twice() -> None:
    # Operating result and net result both sit in the full comparative income statement.
    raw = copy.deepcopy(_raw("line_items_v1"))
    raw["inputs"]["operating_result"].append("IS.COMP.L")
    raw["inputs"]["net_result"].remove("IS.COMP.L")
    with pytest.raises(ValueError, match="jednostka_inna/RZiS/.* all for operating_result"):
        LineItems.model_validate(raw).check_against(load_mapping_config())


def test_rejects_mixed_statements_and_shared_codes() -> None:
    raw = copy.deepcopy(_raw("line_items_v1"))
    raw["inputs"]["revenue"].append("BS.ASSETS.B")
    with pytest.raises(ValueError, match="codes named by two inputs"):
        LineItems.model_validate(raw)
    raw["inputs"]["current_assets"] = ["BS.ASSETS.A"]
    with pytest.raises(ValueError, match="mixes statement types"):
        LineItems.model_validate(raw).check_against(load_mapping_config())


# --- versions and the hash -----------------------------------------------------------------------


def _copy_config(tmp_path: Path) -> Path:
    shutil.copytree(CONFIG_DIR, tmp_path / "config")
    return tmp_path / "config"


def test_the_version_is_the_file_name(tmp_path: Path) -> None:
    config_dir = _copy_config(tmp_path)
    (config_dir / "features" / "feature_set_v1.yaml").rename(
        config_dir / "features" / "feature_set_v9.yaml"
    )
    with pytest.raises(ValueError, match="must match the file name"):
        load_feature_set("feature_set_v9", config_dir)


def test_the_hash_moves_with_any_config_the_set_reads(
    tmp_path: Path, config: FeatureConfig
) -> None:
    config_dir = _copy_config(tmp_path)
    assert load_feature_set("feature_set_v1", config_dir).feature_set_hash == (
        config.feature_set_hash
    )
    tripwires = config_dir / "statutory" / "ksh_tripwires.yaml"
    tripwires.write_text(tripwires.read_text("utf-8") + "\n# edited\n", "utf-8")
    assert load_feature_set("feature_set_v1", config_dir).feature_set_hash != (
        config.feature_set_hash
    )


def test_an_experimental_set_can_include_quarantined_statements(tmp_path: Path) -> None:
    config_dir = _copy_config(tmp_path)
    raw = _raw("feature_set_v1")
    raw.update(feature_set_version="feature_set_v1q", include_quarantined_statements=True)
    (config_dir / "features" / "feature_set_v1q.yaml").write_text(yaml.safe_dump(raw), "utf-8")
    experimental = load_feature_set("feature_set_v1q", config_dir)
    assert experimental.feature_set.include_quarantined_statements is True
    assert experimental.feature_set_hash != load_feature_set("feature_set_v1").feature_set_hash
