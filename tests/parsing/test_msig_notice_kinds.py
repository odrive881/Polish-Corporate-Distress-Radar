"""MSiG notice-kind rules (plan 0008 step F): `config/mappings/msig_notice_kinds.yaml`."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from distress_radar.parsing.canonical_schema import CONFIG_DIR
from distress_radar.parsing.msig_notice_kinds import NoticeKinds, load_notice_kinds


def test_repository_rules_load_against_the_vocabulary() -> None:
    kinds = load_notice_kinds()
    assert kinds.rules[0].kind == "bankruptcy_declared"


def test_a_term_outside_the_vocabulary_is_a_load_error(tmp_path: Path) -> None:
    (tmp_path / "mappings").mkdir()
    raw = yaml.safe_load((CONFIG_DIR / "mappings" / "msig_notice_kinds.yaml").read_text("utf-8"))
    raw["rules"][0]["all"] = ["kowalski"]
    (tmp_path / "mappings" / "msig_notice_kinds.yaml").write_text(
        yaml.safe_dump(raw, allow_unicode=True)
    )
    shutil.copy(CONFIG_DIR / "mappings" / "msig_vocabulary.yaml", tmp_path / "mappings")
    with pytest.raises(ValueError, match="not in the MSiG vocabulary"):
        load_notice_kinds(tmp_path)


def test_a_procedural_kind_has_no_date() -> None:
    with pytest.raises(ValidationError, match="procedural kind has no date"):
        NoticeKinds.model_validate(
            {
                "version": 1,
                "rules": [{"kind": "x", "chapters": ["IX"], "event": False, "date": "publication"}],
            }
        )
