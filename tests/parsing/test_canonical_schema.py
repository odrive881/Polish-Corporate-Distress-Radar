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
    # One net-result line per income-statement layout. `IS.MIKRO.G` is the same
    # figure presented for UoR art. 3(1a)(2) units and deliberately has no role:
    # a filing carrying both F and G would otherwise make `_role_value` raise.
    assert chart.codes_with_role("net_result") == {
        "IS.COMP.L",
        "IS.CALC.O",
        "IS.COMP.MALA.J",
        "IS.CALC.MALA.L",
        "IS.MIKRO.F",
    }
    assert chart.codes_with_role("cash_opening") == {"CF.IND.F", "CF.DIR.F"}
    # The micro form declares no balance-sheet net-result line, so `profit_ties`
    # is skipped there rather than failing (ADR 0005 second addendum).
    assert chart.codes_with_role("net_result_balance_sheet") == {"BS.EQUITY_LIABILITIES.A.VI"}


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


def test_spec_hash_changes_with_any_body_the_spec_can_reach(tmp_path: Path) -> None:
    """Including a body reached only through a statement alternative.

    A small filing may carry the full-form statements (plan 0005 step D), so
    `jednostka_inna` is part of how `small-2018-v1-2` maps a document. If its
    hash did not move, those files would keep their `first_ingestion_run_id`
    and `parsed_documents` row after a mapping change (`parsing/manifest.py`).
    """
    config_dir = _copy_config(tmp_path)
    before = load_mapping_config(config_dir).spec_hashes
    body = config_dir / "mappings" / "structures" / "bodies" / "jednostka_inna.yaml"
    body.write_text(body.read_text(encoding="utf-8") + "\n# edited\n", encoding="utf-8")
    after = load_mapping_config(config_dir).spec_hashes
    assert after["full-2018-v1-2"] != before["full-2018-v1-2"]
    assert after["small-2018-v1-2"] != before["small-2018-v1-2"]
    # A spec that cannot reach the edited body is unaffected: micro filings
    # always carry the micro statements, so nothing about them changed.
    assert after["micro-2018-v1-2"] == before["micro-2018-v1-2"]


def test_every_chart_code_is_used(mapping_config: MappingConfig) -> None:
    """A code no body maps and no spec overrides to is vocabulary nothing can emit.

    Plan 0005 step C added 46 codes; this is what keeps an abandoned one from
    sitting in the chart looking like a mapped line.
    """
    used = set[str]().union(*(body.codes() for body in mapping_config.bodies.values()))
    used |= {
        code for spec in mapping_config.specs.values() for code in spec.code_overrides.values()
    }
    assert set(mapping_config.chart.by_code()) - used == set()
