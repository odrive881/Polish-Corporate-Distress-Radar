"""Settings with a rule behind their default: `dq_mart` suppression (plan 0007 decision 9, and
the publish floor of plan 0016 decision 9) and the MLflow store (plan 0012 decision 6)."""

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


@pytest.mark.parametrize("value", [None, "1", "4"])
def test_nothing_is_published_without_a_threshold_of_at_least_five(
    monkeypatch: pytest.MonkeyPatch, value: str | None
) -> None:
    if value is None:
        monkeypatch.delenv("DQ_MART_MIN_CELL_ENTITIES", raising=False)
    else:
        monkeypatch.setenv("DQ_MART_MIN_CELL_ENTITIES", value)
    with pytest.raises(ValueError, match="at least 5"):
        Settings(_env_file=None).publish_min_cell_entities()  # type: ignore[call-arg]


def test_the_publish_threshold_is_the_configured_one(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DQ_MART_MIN_CELL_ENTITIES", "10")
    assert Settings(_env_file=None).publish_min_cell_entities() == 10  # type: ignore[call-arg]


def test_the_example_env_sets_the_recommended_threshold() -> None:
    from pathlib import Path

    example = (Path(__file__).parents[1] / ".env.example").read_text(encoding="utf-8")
    assert "\nDQ_MART_MIN_CELL_ENTITIES=10\n" in example


def test_mlflow_defaults_to_a_local_store_in_the_gitignored_data_dir(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No service to run in Phase 6, and nothing tracked lands in git (`.data/` is ignored)."""
    monkeypatch.delenv("MLFLOW_TRACKING_URI", raising=False)
    monkeypatch.delenv("MLFLOW_ARTIFACT_DIR", raising=False)
    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    assert settings.mlflow_tracking_uri == "sqlite:///.data/mlflow/mlflow.db"
    assert settings.mlflow_artifact_dir.parts[0] == ".data"
