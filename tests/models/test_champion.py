"""The champion rule (plan 0015 step F, owner decision 7), on synthetic scored rows."""

# polars's signatures reference types pyright cannot resolve; scoped to this module.
# pyright: reportUnknownMemberType=false

from __future__ import annotations

from collections.abc import Callable
from datetime import date

import numpy as np
import polars as pl

from distress_radar.models.champion import ChampionVerdict, decide, pair
from distress_radar.models.splits import BootstrapConfig, ChampionConfig

RULE = ChampionConfig(
    incumbent="logistic_regression",
    brier="paired_entity_bootstrap",
    auc_margin=0.02,
    runs=("main", "no_regime"),
    min_promotion_events=50,
)
BOOT = BootstrapConfig(replicates=200, seed=11, level=0.95)
Scores = Callable[[np.ndarray, np.random.Generator], np.ndarray]


def _rows(run: str, model: str, events: int, alive: int, scores: Scores) -> pl.DataFrame:
    """One test row per entity in 2023: `events` distressed entities, then `alive` ones."""
    rng = np.random.default_rng(len(model) * 7 + len(run))
    y = np.array([True] * events + [False] * alive)
    n = y.size
    return pl.DataFrame(
        {
            "run": [run] * n,
            "horizon_months": [12] * n,
            "model": [model] * n,
            "test_year": [2023] * n,
            "krs": [f"{i + 1:010d}" for i in range(n)],
            "as_of_date": [date(2023, 1, 31)] * n,
            "distress": y,
            "outcome_class": ["bankruptcy" if d else "alive" for d in y],
            "event_date": [date(2023, 6, 1) if d else None for d in y],
            "event_known_from": [date(2023, 6, 1) if d else None for d in y],
            "trigger_event_type": [None] * n,
            "probability": np.clip(scores(y, rng), 0.001, 0.999),
        },
        schema_overrides={"horizon_months": pl.Int32, "test_year": pl.Int32},
    )


def sharp(y: np.ndarray, rng: np.random.Generator) -> np.ndarray:
    return np.where(y, 0.8, 0.05) + rng.normal(0, 0.03, y.size)


def vague(y: np.ndarray, rng: np.random.Generator) -> np.ndarray:
    return np.where(y, 0.45, 0.35) + rng.normal(0, 0.1, y.size)


def overconfident(y: np.ndarray, rng: np.random.Generator) -> np.ndarray:
    """A perfect ranking at badly wrong levels."""
    return np.where(y, 0.9, 0.6) + rng.normal(0, 0.02, y.size)


def flat(y: np.ndarray, rng: np.random.Generator) -> np.ndarray:
    """The base rate everywhere: a good Brier score, no ranking at all."""
    return np.full(y.size, y.mean())


def _decide(
    challenger: dict[str, Scores], incumbent: Scores = vague, events: int = 60
) -> tuple[pl.DataFrame, ChampionVerdict]:
    frames = [
        _rows(run, "logistic_regression", events, 240, incumbent) for run in ("main", "no_regime")
    ]
    frames += [_rows(run, "lightgbm", events, 240, s) for run, s in challenger.items()]
    predictions = pl.concat(frames)
    speaking = predictions.select("run", "horizon_months", "model", "test_year").unique()
    return predictions, decide(predictions, speaking, (12,), RULE, BOOT)


def test_a_challenger_better_everywhere_is_promoted() -> None:
    _, verdict = _decide({"main": sharp, "no_regime": sharp})
    assert verdict.promoted == "lightgbm"
    assert verdict.champion == "lightgbm"
    assert verdict.verdicts == {"lightgbm": "passes"}
    for c in verdict.comparisons:
        assert c.passes and c.events == 60
        assert c.difference_high is not None and c.difference_high < 0


def test_an_interval_that_reaches_zero_fails() -> None:
    _, verdict = _decide({"main": vague, "no_regime": vague})
    assert verdict.promoted is None
    assert verdict.champion == "logistic_regression"
    assert verdict.verdicts["lightgbm"].startswith("fails: ")
    assert "interval reaches zero" in verdict.verdicts["lightgbm"]


def test_a_better_brier_with_a_worse_auc_fails() -> None:
    _, verdict = _decide({"main": flat, "no_regime": flat}, incumbent=overconfident)
    assert verdict.promoted is None
    main = next(c for c in verdict.comparisons if c.run == "main")
    assert main.brier_difference is not None and main.brier_difference < 0
    assert main.difference_high is not None and main.difference_high < 0
    assert "AUC lower by more than 0.02" in main.reason


def test_failing_in_no_regime_fails() -> None:
    _, verdict = _decide({"main": sharp, "no_regime": vague})
    assert verdict.promoted is None
    assert verdict.verdicts["lightgbm"] == (
        "fails: 12 months `no_regime`: the Brier difference's interval reaches zero"
    )


def test_below_the_event_floor_nothing_is_judged_or_promoted() -> None:
    _, verdict = _decide({"main": sharp, "no_regime": sharp}, events=20)
    assert verdict.promoted is None
    assert verdict.verdicts["lightgbm"].startswith("not evaluable: ")
    assert "20 events, the rule needs 50" in verdict.verdicts["lightgbm"]
    assert all(not c.evaluable and c.brier_difference is None for c in verdict.comparisons)


def test_pairs_are_the_rows_both_scored_in_folds_both_speak_for() -> None:
    predictions, _ = _decide({"main": sharp, "no_regime": sharp})
    speaking = predictions.select("run", "horizon_months", "model", "test_year").unique()
    both = pair(predictions, speaking, "lightgbm", "logistic_regression", 12, "main")
    assert both.height == 300
    # The incumbent silent in 2023 (trained on too few events): no pairs.
    silent = speaking.filter(pl.col("model") != "logistic_regression")
    assert pair(predictions, silent, "lightgbm", "logistic_regression", 12, "main").is_empty()
    # A row only the challenger scored is not paired.
    fewer = predictions.filter(
        ~((pl.col("model") == "logistic_regression") & (pl.col("krs") == "0000000001"))
    )
    assert pair(fewer, speaking, "lightgbm", "logistic_regression", 12, "main").height == 299


def test_the_verdict_is_reproducible() -> None:
    _, first = _decide({"main": sharp, "no_regime": sharp})
    _, second = _decide({"main": sharp, "no_regime": sharp})
    assert first == second
