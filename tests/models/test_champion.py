"""The champion rule (plan 0015 step F, owner decision 7), on synthetic scored rows."""

# polars's signatures reference types pyright cannot resolve; scoped to this module.
# pyright: reportUnknownMemberType=false

from __future__ import annotations

from collections.abc import Callable
from datetime import date

import numpy as np
import polars as pl
import pytest

from distress_radar.models.champion import ChampionVerdict, decide, pair, paired_brier_interval
from distress_radar.models.dataset import WEIGHT
from distress_radar.models.evaluation import STRATUM
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


# --- weighted (plan 0019 steps E and F) ------------------------------------------------------------

WEIGHTED = RULE.model_copy(update={"weighted": True})


def _drawn(predictions: pl.DataFrame, weight: object, stratum: object) -> pl.DataFrame:
    return predictions.with_columns(
        pl.Series(WEIGHT, weight, dtype=pl.Float64)
        if isinstance(weight, list)
        else pl.lit(weight, pl.Float64).alias(WEIGHT),
        pl.Series(STRATUM, stratum)
        if isinstance(stratum, list)
        else pl.lit(stratum).alias(STRATUM),
    )


def _weighted(
    challenger: dict[str, Scores], weight: object = 1.0, stratum: object = "A", events: int = 60
) -> ChampionVerdict:
    predictions, _ = _decide(challenger, events=events)
    drawn = _drawn(predictions, weight, stratum)
    speaking = drawn.select("run", "horizon_months", "model", "test_year").unique()
    return decide(drawn, speaking, (12,), WEIGHTED, BOOT)


def test_an_unweighted_verdict_is_provisional() -> None:
    _, verdict = _decide({"main": sharp, "no_regime": sharp})
    assert verdict.basis == "unweighted" and verdict.provisional


def test_equal_weights_in_one_stratum_judge_as_the_unweighted_rule_but_finally() -> None:
    _, plain = _decide({"main": sharp, "no_regime": sharp})
    weighted = _weighted({"main": sharp, "no_regime": sharp}, weight=3.0)
    assert weighted.basis == "weighted" and not weighted.provisional
    assert weighted.promoted == plain.promoted == "lightgbm"
    for w, p in zip(weighted.comparisons, plain.comparisons, strict=True):
        assert w.events == p.events and w.rows == p.rows
        for field in ("brier_challenger", "brier_incumbent", "auc_challenger", "auc_incumbent"):
            assert getattr(w, field) == pytest.approx(getattr(p, field))
        assert (w.difference_low, w.difference_high) == pytest.approx(
            (p.difference_low, p.difference_high)
        )


def test_the_weighted_brier_score_is_the_weighted_average() -> None:
    weights = [float(1 + i % 5) for i in range(1200)]  # 4 runs x 300 rows, in frame order
    verdict = _weighted({"main": sharp, "no_regime": sharp}, weight=weights)
    predictions, _ = _decide({"main": sharp, "no_regime": sharp})
    drawn = _drawn(predictions, weights, "A")
    speaking = drawn.select("run", "horizon_months", "model", "test_year").unique()
    paired = pair(drawn, speaking, "lightgbm", "logistic_regression", 12, "main")
    y = paired.get_column("distress").to_numpy().astype(np.float64)
    w = paired.get_column(WEIGHT).to_numpy()
    main = next(c for c in verdict.comparisons if c.run == "main")
    expected = np.average((paired.get_column("challenger").to_numpy() - y) ** 2, weights=w)
    assert main.brier_challenger == pytest.approx(expected)


def test_the_weighted_floor_counts_events_not_weight() -> None:
    verdict = _weighted({"main": sharp, "no_regime": sharp}, weight=1000.0, events=20)
    assert verdict.promoted is None
    assert "20 events, the rule needs 50" in verdict.verdicts["lightgbm"]


def test_a_weighted_rule_without_weights_is_refused() -> None:
    predictions, _ = _decide({"main": sharp, "no_regime": sharp})
    speaking = predictions.select("run", "horizon_months", "model", "test_year").unique()
    with pytest.raises(ValueError, match="draw_weight"):
        decide(predictions, speaking, (12,), WEIGHTED, BOOT)


def test_the_unweighted_interval_is_the_plain_entity_bootstrap() -> None:
    """The stratified resampler, given one stratum, draws exactly as the loop it replaced."""
    rng = np.random.default_rng(5)
    krs = [f"{i % 40:010d}" for i in range(120)]
    y = (rng.random(120) < 0.2).astype(np.float64)
    p_c, p_i = rng.random(120), rng.random(120)
    entities = sorted(set(krs))
    rows_of = [np.flatnonzero(np.array(krs) == e) for e in entities]
    loss = (p_c - y) ** 2 - (p_i - y) ** 2
    draw_rng = np.random.default_rng(BOOT.seed)
    draws = []
    for _ in range(BOOT.replicates):
        picked = draw_rng.integers(0, len(entities), size=len(entities))
        draws.append(loss[np.concatenate([rows_of[int(j)] for j in picked])].mean())
    expected = tuple(np.quantile(draws, [0.025, 0.975]))
    assert paired_brier_interval(y, p_c, p_i, krs, BOOT) == pytest.approx(expected)


def test_strata_change_the_weighted_interval_not_the_point() -> None:
    strata = ["A" if i % 2 else "B:w_upadlosci" for i in range(300)] * 4
    one = _weighted({"main": sharp, "no_regime": sharp}, weight=2.0)
    two = _weighted({"main": sharp, "no_regime": sharp}, weight=2.0, stratum=strata)
    for a, b in zip(one.comparisons, two.comparisons, strict=True):
        assert a.brier_difference == pytest.approx(b.brier_difference)
        assert (a.difference_low, a.difference_high) != (b.difference_low, b.difference_high)
