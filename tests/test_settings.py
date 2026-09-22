"""`dq_mart` suppression threshold setting (plan 0007 decision 9)."""

import pydantic
import pytest

from distress_radar.settings import Settings


def test_suppression_ships_switched_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DQ_MART_MIN_CELL_ENTITIES", raising=False)
    assert Settings(_env_file=None).dq_mart_min_cell_entities is None  # type: ignore[call-arg]


def test_threshold_is_read_from_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DQ_MART_MIN_CELL_ENTITIES", "5")
    assert Settings(_env_file=None).dq_mart_min_cell_entities == 5  # type: ignore[call-arg]


def test_a_threshold_that_suppresses_nothing_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    """0 would look configured while suppressing nothing: off is spelled empty, not 0."""
    monkeypatch.setenv("DQ_MART_MIN_CELL_ENTITIES", "0")
    with pytest.raises(pydantic.ValidationError):
        Settings(_env_file=None)  # type: ignore[call-arg]
