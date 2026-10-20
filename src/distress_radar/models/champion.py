"""The champion rule (plan 0015 step F, owner decision 7).

A challenger is promoted over the incumbent (generation 2's regression) only if, at each horizon and
in each of the rule's runs (`main` and `no_regime`):

- its pooled Brier score is lower, with the percentile interval of the **paired** difference
  (challenger less incumbent, the same entity draws for both) wholly below zero;
- its AUC is at most `auc_margin` lower;
- and the comparison rests on at least `min_promotion_events` distinct events.

The comparison is paired on rows: the test rows both models scored, in the folds where both may speak
for a pool (fitted on at least `min_events` training events, as the backtest's pooled cells). A
comparison short of the event floor is not evaluable, and a challenger with one is never promoted.
Of several challengers that pass, the one with the lowest mean `main` Brier score over the horizons
is promoted (ties by name). The challengers are generation 3's results: `lightgbm` (calibrated, the
model's result; its raw score is a diagnostic) and the two survival models.

Promotion here is a verdict; setting the MLflow alias `champion` on the registered model is the
registry's (step G), and only on a verdict that promotes.

Weighted (plan 0019 steps E and F, decisions 6–8): with `weighted`, the paired rows carry their
entity's `draw_weight` and `draw_stratum`; both Brier scores, their difference and both AUCs are
weighted, and the paired bootstrap resamples entities within each stratum, each keeping its size.
The event floor still counts distinct events, unweighted: a weight adds no evidence. A verdict
records its `basis`, and one reached unweighted is **provisional**: every list it can read so far
is purposive, so only a weighted run's verdict is final.
"""

# polars's signatures reference types pyright cannot resolve; scoped to this module.
# pyright: reportUnknownMemberType=false

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Literal

import numpy as np
import numpy.typing as npt
import polars as pl

from distress_radar.models.dataset import TARGET, WEIGHT
from distress_radar.models.evaluation import STRATUM, auc, brier, entity_strata, resample_entities
from distress_radar.models.splits import BootstrapConfig, ChampionConfig, events

CHALLENGERS = ("lightgbm", "survival_lightgbm", "survival_forest")
ROW_KEYS = ["run", "horizon_months", "test_year", "krs", "as_of_date"]
FOLD_KEYS = ["run", "horizon_months", "model", "test_year"]
Array = npt.NDArray[np.float64]
Basis = Literal["unweighted", "weighted"]


@dataclass(frozen=True)
class Comparison:
    """A challenger against the incumbent at one horizon and run, on their paired rows."""

    challenger: str
    horizon_months: int
    run: str
    rows: int
    events: int
    evaluable: bool
    brier_challenger: float | None = None
    brier_incumbent: float | None = None
    brier_difference: float | None = None  # challenger less incumbent
    difference_low: float | None = None
    difference_high: float | None = None
    auc_challenger: float | None = None
    auc_incumbent: float | None = None
    passes: bool = False
    reason: str = ""  # why it does not pass, or why it cannot be judged


@dataclass(frozen=True)
class ChampionVerdict:
    incumbent: str
    promoted: str | None  # the challenger promoted, or None
    verdicts: dict[str, str]  # per challenger: "passes", "fails: …" or "not evaluable: …"
    comparisons: list[Comparison]
    basis: Basis = "unweighted"

    @property
    def champion(self) -> str:
        return self.promoted or self.incumbent

    @property
    def provisional(self) -> bool:
        """Reached unweighted, over purposive lists: a weighted run decides (decision 8)."""
        return self.basis == "unweighted"


def paired_brier_interval(
    y: Array,
    challenger: Array,
    incumbent: Array,
    krs: list[str],
    config: BootstrapConfig,
    weight: Array | None = None,
    strata: list[list[str]] | None = None,
) -> tuple[float, float]:
    """The percentile interval of the Brier difference over entity resamples, both models scored on
    each draw's rows. With `weight`, the difference is weighted; with `strata`, entities are
    resampled within each (decision 6). Without either, the plain entity bootstrap."""
    keys = np.array(krs)
    rows_of = {e: np.flatnonzero(keys == e) for e in sorted(set(krs))}
    loss = (challenger - y) ** 2 - (incumbent - y) ** 2
    groups = strata if strata is not None else [sorted(rows_of)]
    rng = np.random.default_rng(config.seed)
    draws = np.empty(config.replicates)
    for i in range(config.replicates):
        rows = np.concatenate([rows_of[e] for e in resample_entities(groups, rng)])
        draws[i] = (
            loss[rows].mean() if weight is None else np.average(loss[rows], weights=weight[rows])
        )
    tail = (1 - config.level) / 2
    low, high = np.quantile(draws, [tail, 1 - tail])
    return float(low), float(high)


def compare(
    paired: pl.DataFrame,
    challenger: str,
    horizon: int,
    run: str,
    config: ChampionConfig,
    bootstrap: BootstrapConfig,
) -> Comparison:
    """`paired`: one row per scored test row, with `TARGET`, the label columns `events` reads,
    `challenger` and `incumbent` probabilities, and, for a weighted rule, `draw_weight` and
    `draw_stratum`."""
    found = events(paired).height  # distinct events, unweighted (decision 7)
    base = Comparison(challenger, horizon, run, paired.height, found, evaluable=False)
    if found < config.min_promotion_events:
        return replace(
            base,
            reason=f"{found} events, the rule needs {config.min_promotion_events}",
        )
    y = paired.get_column(TARGET).to_numpy().astype(np.float64)
    p_c = paired.get_column("challenger").to_numpy().astype(np.float64)
    p_i = paired.get_column("incumbent").to_numpy().astype(np.float64)
    labels = y.astype(np.bool_)
    w: Array | None = None
    strata: list[list[str]] | None = None
    if config.weighted:
        missing = [c for c in (WEIGHT, STRATUM) if c not in paired.columns]
        if missing:
            raise ValueError(f"a weighted champion rule needs the paired rows' {missing}")
        w = paired.get_column(WEIGHT).to_numpy().astype(np.float64)
        strata = entity_strata(paired)
    b_c, b_i = brier(labels, p_c, w), brier(labels, p_i, w)
    low, high = paired_brier_interval(
        y, p_c, p_i, paired.get_column("krs").to_list(), bootstrap, w, strata
    )
    a_c, a_i = auc(labels, p_c, w), auc(labels, p_i, w)
    reasons: list[str] = []
    if not high < 0:
        reasons.append("the Brier difference's interval reaches zero")
    if a_c is None or a_i is None:
        reasons.append("AUC undefined")
    elif a_c < a_i - config.auc_margin:
        reasons.append(f"AUC lower by more than {config.auc_margin}")
    return replace(
        base,
        evaluable=True,
        brier_challenger=b_c,
        brier_incumbent=b_i,
        brier_difference=b_c - b_i,
        difference_low=low,
        difference_high=high,
        auc_challenger=a_c,
        auc_incumbent=a_i,
        passes=not reasons,
        reason="; ".join(reasons),
    )


def pair(
    predictions: pl.DataFrame,
    speaking: pl.DataFrame,
    challenger: str,
    incumbent: str,
    horizon: int,
    run: str,
) -> pl.DataFrame:
    """The rows both models scored in the folds where both may speak for a pool, with their weight
    and stratum when the predictions carry them."""
    folds = (
        speaking.filter(
            (pl.col("run") == run)
            & (pl.col("horizon_months") == horizon)
            & pl.col("model").is_in([challenger, incumbent])
        )
        .group_by("test_year")
        .agg(pl.col("model").n_unique().alias("models"))
        .filter(pl.col("models") == 2)
        .select("test_year")
    )
    scoped = predictions.filter(
        (pl.col("run") == run) & (pl.col("horizon_months") == horizon)
    ).join(folds, on="test_year")
    labels = ["outcome_class", "event_date", "event_known_from", "trigger_event_type", TARGET]
    drawn = [c for c in (WEIGHT, STRATUM) if c in predictions.columns]
    mine = scoped.filter(pl.col("model") == challenger).select(
        *ROW_KEYS, *labels, *drawn, pl.col("probability").alias("challenger")
    )
    theirs = scoped.filter(pl.col("model") == incumbent).select(
        *ROW_KEYS, pl.col("probability").alias("incumbent")
    )
    return mine.join(theirs, on=ROW_KEYS, how="inner", validate="1:1").sort(ROW_KEYS)


def decide(
    predictions: pl.DataFrame,
    speaking: pl.DataFrame,
    horizons: tuple[int, ...],
    config: ChampionConfig,
    bootstrap: BootstrapConfig,
) -> ChampionVerdict:
    """The rule over a backtest's scored rows. `speaking`: the folds (`FOLD_KEYS`) whose model may
    speak for a pool. Challengers the backtest did not run are left out."""
    present = set(predictions.get_column("model").unique().to_list())
    comparisons: list[Comparison] = []
    verdicts: dict[str, str] = {}
    passing: list[tuple[float, str]] = []
    for challenger in (c for c in CHALLENGERS if c in present):
        mine = [
            compare(
                pair(predictions, speaking, challenger, config.incumbent, horizon, run),
                challenger,
                horizon,
                run,
                config,
                bootstrap,
            )
            for horizon in horizons
            for run in config.runs
        ]
        comparisons += mine
        short = [c for c in mine if not c.evaluable]
        failed = [c for c in mine if c.evaluable and not c.passes]
        if short:
            verdicts[challenger] = "not evaluable: " + "; ".join(
                f"{c.horizon_months} months `{c.run}`: {c.reason}" for c in short
            )
        elif failed:
            verdicts[challenger] = "fails: " + "; ".join(
                f"{c.horizon_months} months `{c.run}`: {c.reason}" for c in failed
            )
        else:
            verdicts[challenger] = "passes"
            main = [
                c.brier_challenger
                for c in mine
                if c.run == "main" and c.brier_challenger is not None
            ]
            passing.append((float(np.mean(main)) if main else float("inf"), challenger))
    promoted = min(passing)[1] if passing else None
    basis: Basis = "weighted" if config.weighted else "unweighted"
    return ChampionVerdict(config.incumbent, promoted, verdicts, comparisons, basis)
