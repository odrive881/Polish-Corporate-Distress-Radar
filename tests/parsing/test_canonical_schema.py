"""Loading and cross-checking config/mappings/ (plan 0004 step C)."""

import shutil
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from distress_radar.parsing.canonical_schema import (
    CONFIG_DIR,
    CanonicalChart,
    MappingConfig,
    StatementBody,
    load_mapping_config,
)


def test_repository_config_loads(mapping_config: MappingConfig) -> None:
    assert set(mapping_config.specs) >= {"full-2018-v1-0", "full-2018-v1-2", "full-2025-w2-v1-0"}
    assert mapping_config.bodies["jednostka_inna"].codes() <= set(mapping_config.chart.by_code())
    assert len(set(mapping_config.spec_hashes.values())) == len(mapping_config.specs)


def test_every_role_the_checks_read_is_defined(mapping_config: MappingConfig) -> None:
    chart = mapping_config.chart
    assert chart.codes_with_role("total_assets") == {"BS.ASSETS"}
    assert chart.codes_with_role("total_equity_and_liabilities") == {"BS.EQUITY_LIABILITIES"}
    assert chart.codes_with_role("net_result") == {"IS.COMP.L", "IS.CALC.O"}
    assert chart.codes_with_role("cash_opening") == {"CF.IND.F", "CF.DIR.F"}


def test_2025_narrowed_items_are_distinct_codes(mapping_config: MappingConfig) -> None:
    chart = mapping_config.chart.by_code()
    assert chart["IS.COMP.A.IV.R2025"].replaces == "IS.COMP.A.IV"
    overrides = mapping_config.specs["full-2025-w2-v1-0"].code_overrides
    assert overrides["IS.COMP.A.IV"] == "IS.COMP.A.IV.R2025"
    assert mapping_config.specs["full-2018-v1-2"].code_overrides == {}


def _chart(*codes: str) -> dict[str, object]:
    return {
        "version": 1,
        "items": [
            {"code": c, "statement_type": "balance_sheet", "variant": "n/a", "label_pl": c}
            for c in codes
        ],
    }


def test_chart_rejects_duplicate_codes() -> None:
    with pytest.raises(ValidationError, match="duplicate"):
        CanonicalChart.model_validate(_chart("BS.X", "BS.X"))


def test_chart_rejects_unknown_replaces() -> None:
    data = _chart("BS.X")
    data["items"][0]["replaces"] = "BS.Y"  # type: ignore[index]
    with pytest.raises(ValidationError, match="replaces"):
        CanonicalChart.model_validate(data)


def test_body_rejects_orphans_and_bad_formulas() -> None:
    orphan = {"body": "b", "statements": {"Bilans": [{"path": "A/B", "code": "BS.B"}]}}
    with pytest.raises(ValidationError, match="without a parent"):
        StatementBody.model_validate(orphan)
    formula = {
        "body": "b",
        "formulas": {"BS.A": [[1, "BS.MISSING"]]},
        "statements": {"Bilans": [{"path": "A", "code": "BS.A"}]},
    }
    with pytest.raises(ValidationError, match="formulas"):
        StatementBody.model_validate(formula)
    neither = {"body": "b", "statements": {"Bilans": [{"path": "A"}]}}
    with pytest.raises(ValidationError, match="exactly one"):
        StatementBody.model_validate(neither)


def _copy_config(tmp_path: Path) -> Path:
    target = tmp_path / "config"
    shutil.copytree(CONFIG_DIR / "mappings", target / "mappings")
    return target


def test_spec_with_unknown_body_code_override_is_rejected(tmp_path: Path) -> None:
    config_dir = _copy_config(tmp_path)
    path = config_dir / "mappings" / "structures" / "full-2018-v1-2.yaml"
    spec = yaml.safe_load(path.read_text(encoding="utf-8"))
    spec["code_overrides"] = {"IS.COMP.A.IV": "IS.NOT.IN.CHART"}
    path.write_text(yaml.safe_dump(spec), encoding="utf-8")
    with pytest.raises(ValidationError, match="code override"):
        load_mapping_config(config_dir)


def test_two_specs_detecting_the_same_version_are_rejected(tmp_path: Path) -> None:
    config_dir = _copy_config(tmp_path)
    structures = config_dir / "mappings" / "structures"
    spec = yaml.safe_load((structures / "full-2018-v1-2.yaml").read_text(encoding="utf-8"))
    spec["structure_version"] = "full-2018-v1-2-copy"
    (structures / "full-2018-v1-2-copy.yaml").write_text(yaml.safe_dump(spec), encoding="utf-8")
    with pytest.raises(ValidationError, match="detect the same key"):
        load_mapping_config(config_dir)


def test_spec_hash_changes_with_the_body(tmp_path: Path) -> None:
    config_dir = _copy_config(tmp_path)
    before = load_mapping_config(config_dir).spec_hashes["full-2018-v1-2"]
    body = config_dir / "mappings" / "structures" / "bodies" / "jednostka_inna.yaml"
    body.write_text(body.read_text(encoding="utf-8") + "\n# edited\n", encoding="utf-8")
    assert load_mapping_config(config_dir).spec_hashes["full-2018-v1-2"] != before
