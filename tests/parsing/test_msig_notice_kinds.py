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


def test_an_additional_rule_is_never_the_kind_and_must_be_an_event() -> None:
    kinds = load_notice_kinds()
    assert all(not r.additional for r in kinds.rules if r.kind == "remedial_proceedings_opened")
    extracted = {"chapter_code": "IX", "terms": ["oddal", "wniosek o ogłoszenie upadłości"]}
    assert kinds.classify(extracted) is None  # additional rules never classify on their own
    assert [r.kind for r in kinds.additional(extracted)] == [
        "bankruptcy_petition_dismissed_by_restructuring"
    ]
    with pytest.raises(ValidationError, match="additional rule must be an event"):
        NoticeKinds.model_validate(
            {
                "version": 1,
                "rules": [{"kind": "x", "chapters": ["IX"], "event": False, "additional": True}],
            }
        )
