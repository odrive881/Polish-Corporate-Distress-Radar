"""A weighted backtest (plan 0019 step D): weights, weighted metrics, the stratified bootstrap."""

# polars's signatures reference types pyright cannot resolve; scoped to this module.
# pyright: reportUnknownMemberType=false

from __future__ import annotations

from datetime import UTC, datetime
from fractions import Fraction

import numpy as np
import polars as pl
import pytest
from tests.models.frames import TIMING, panel

from distress_radar.features.config import load_feature_set
from distress_radar.models.backtest import BASES, POOLED, BacktestResult, run_backtest
from distress_radar.models.baselines import load_classical_model
from distress_radar.models.classical import fit_predict_logistic
from distress_radar.models.dataset import WEIGHT, modelling_dataset
from distress_radar.models.evaluation import (
    METRICS,
    STRATUM,
    bootstrap_intervals,
    point_metrics,
    resample_entities,
    top_decile_precision,
)
from distress_radar.models.population import (
    FILINGS_SCHEMA,
    SEARCHES_SCHEMA,
    SOURCES_SCHEMA,
    Population,
    PopulationConfig,
    classify,
    entities_hash,
)
from distress_radar.models.report import render_report
from distress_radar.models.splits import (
    BacktestConfig,
    BootstrapConfig,
    WeightsConfig,
    load_backtest_config,
    split,
)
from distress_radar.models.weights import DRAWS_SCHEMA, DrawWeights, draw_weights

SPEC = "ab" * 32
BOOT = BootstrapConfig(replicates=100, seed=7, level=0.95)
WEIGHTS = WeightsConfig(
    draw_spec_sha256=SPEC, random="v3_random", distress="v3_distress", bootstrap="stratified"
)


# --- the weights -----------------------------------------------------------------------------------


def _draws(rows: list[tuple[str, str, str, str, Fraction, int | None]]) -> pl.DataFrame:
    return pl.DataFrame(
        [(k, v, g, s, pi.numerator, pi.denominator, y) for k, v, g, s, pi, y in rows],
        schema=DRAWS_SCHEMA,
        orient="row",
    )


def test_a_weight_is_one_over_pi_and_its_stratum_follows_the_group():
    union = 1 - (1 - Fraction(1, 100)) * (1 - Fraction(1, 5))
    draws = _draws(
        [
            ("0000000001", "v3_random", "A", "none", Fraction(1, 100), 2015),
            ("0000000002", "v3_random", "A", "w_upadlosci", union, 2019),
            ("0000000002", "v3_distress", "B", "w_upadlosci", union, 2019),
            ("0000000003", "v3_distress", "B", "w_likwidacji", Fraction(1, 4), 2020),
            ("0000000004", "other_list", "A", "none", Fraction(1, 2), 2001),
        ]
    )
    weights = draw_weights(draws, WEIGHTS)
    assert weights.frame.rows() == [
        ("0000000001", 100.0, "A:2015", True),
        ("0000000002", float(1 / union), "B:w_upadlosci", True),
        ("0000000003", 4.0, "B:w_likwidacji", False),
    ]
    assert weights.members == {"0000000001", "0000000002", "0000000003"}
    assert weights.random_group == {"0000000001", "0000000002"}


def test_a_permutation_draw_has_one_random_stratum():
    draws = _draws([("0000000001", "v3_random", "A", "none", Fraction(1, 10), None)])
    config = WEIGHTS.model_copy(update={"distress": None})
    assert draw_weights(draws, config).frame.get_column(STRATUM).to_list() == ["A"]


def test_two_probabilities_for_one_company_are_refused():
    draws = _draws(
        [
            ("0000000001", "v3_random", "A", "none", Fraction(1, 10), 2015),
            ("0000000001", "v3_distress", "B", "w_upadlosci", Fraction(1, 5), 2015),
        ]
    )
    with pytest.raises(ValueError, match="different probabilities"):
        draw_weights(draws, WEIGHTS)


def test_a_missing_group_is_refused():
    draws = _draws([("0000000001", "v3_random", "A", "none", Fraction(1, 10), 2015)])
    with pytest.raises(ValueError, match="v3_distress"):
        draw_weights(draws, WEIGHTS)


def test_weights_do_not_read_labels():
    features, labels = panel(9, seed=3)
    weights = _uniform_weights(labels, odd=3.0)
    # Every label changed: every row alive, its event gone.
    flipped = labels.with_columns(
        pl.lit("alive").alias("outcome_class"),
        pl.lit(None, dtype=pl.Date).alias("event_date"),
        pl.lit(None, dtype=pl.Date).alias("event_known_from"),
        pl.lit(None, dtype=pl.String).alias("trigger_event_type"),
    )
    seen: list[pl.Series] = []
    for rows in (labels, flipped):
        joined = rows.join(weights.frame.select("krs", WEIGHT), on="krs")
        dataset = modelling_dataset(features, joined, 12, ["bankruptcy"])
        seen.append(dataset.frame.select("krs", "as_of_date", WEIGHT).to_struct())
    assert seen[0].equals(seen[1])


# --- weighted metrics and the bootstrap ------------------------------------------------------------


def _scored(seed: int, entities: int = 20) -> pl.DataFrame:
    rng = np.random.default_rng(seed)
    rows = [
        {
            "krs": f"{e:010d}",
            "distress": e % 4 == 0,
            "probability": float(rng.uniform(0.5, 0.9) if e % 4 == 0 else rng.uniform(0.1, 0.6)),
            "outcome_class": "bankruptcy" if e % 4 == 0 else "alive",
            "trigger_event_type": "bankruptcy_declared" if e % 4 == 0 else None,
        }
        for e in range(entities)
        for _ in range(3)
    ]
    return pl.DataFrame(rows).with_columns(
        pl.lit(None, dtype=pl.Date).alias("event_date"),
        pl.date(2021, 1, 1).alias("event_known_from"),
    )


def test_an_integer_weight_is_repeating_the_row_for_every_metric():
    scored = _scored(1)
    counts = [1 + i % 3 for i in range(scored.height)]
    counts[-1] += 120 - sum(counts)  # a total of 120: a tenth is a whole number of rows
    weighted = scored.with_columns(pl.Series(WEIGHT, counts, dtype=pl.Float64))
    repeated = scored.select(pl.all().repeat_by(pl.Series(counts)).explode(empty_as_null=True))
    by_weight, by_repeat = point_metrics(weighted), point_metrics(repeated)
    for name in METRICS:
        assert by_weight[name] == pytest.approx(by_repeat[name]), name


def test_equal_weights_give_the_unweighted_top_decile_on_a_whole_tenth():
    y = np.array([True, False] * 10)
    p = np.linspace(0.9, 0.1, 20)
    assert top_decile_precision(y, p, np.full(20, 3.0)) == top_decile_precision(y, p)


def test_weighted_top_decile_counts_part_of_the_boundary_row():
    y = np.array([True, False, True])
    p = np.array([0.9, 0.8, 0.1])
    # Total weight 20: the top tenth is 2, the first row's weight 1 and half the second's.
    assert top_decile_precision(y, p, np.array([1.0, 2.0, 17.0])) == pytest.approx(0.5)


def test_a_constant_weight_in_one_stratum_changes_nothing():
    scored = _scored(2)
    weighted = scored.with_columns(pl.lit(7.0).alias(WEIGHT), pl.lit("A").alias(STRATUM))
    plain, same = bootstrap_intervals(scored, BOOT), bootstrap_intervals(weighted, BOOT)
    for name in METRICS:
        assert plain[name] == pytest.approx(same[name]), name


def test_each_stratum_keeps_its_size_and_its_members():
    strata = [["a1", "a2", "a3"], ["b1"], ["c1", "c2"]]
    rng = np.random.default_rng(3)
    for _ in range(50):
        picked = resample_entities(strata, rng)
        assert [sum(p.startswith(s[0][0]) for p in picked) for s in strata] == [3, 1, 2]
        assert picked[3] == "b1"


def test_a_weighted_fit_differs_from_the_unweighted_one():
    features, labels = panel(30, seed=3)
    weights = _uniform_weights(labels, odd=5.0)
    config = load_backtest_config("backtest_v1").logistic_regression
    fits = []
    for rows in (labels, labels.join(weights.frame.select("krs", WEIGHT), on="krs")):
        dataset = modelling_dataset(features, rows, 12, ["bankruptcy"])
        fold = split(dataset.frame, 12, 2023, TIMING)
        fits.append(fit_predict_logistic(fold, config).summary.coefficients)
    assert fits[0] != fits[1]


# --- the population and the backtest ---------------------------------------------------------------


def _uniform_weights(labels: pl.DataFrame, *, odd: float, spec: str = SPEC) -> DrawWeights:
    """Every labelled entity drawn: weight 1 for even KRS numbers, `odd` for odd ones; the odd
    ones in the distress group."""
    krs = sorted(labels.get_column("krs").unique().to_list())
    frame = pl.DataFrame(
        {
            "krs": krs,
            WEIGHT: [odd if int(k) % 2 else 1.0 for k in krs],
            STRATUM: ["B:w_upadlosci" if int(k) % 2 else "A" for k in krs],
            "random_group": [not int(k) % 2 for k in krs],
        }
    )
    return DrawWeights(frame, spec)


def test_an_entity_outside_the_draw_is_left_out_first():
    searches = pl.DataFrame(
        [("a", datetime(2026, 10, 1, tzinfo=UTC), True, True)],
        schema=SEARCHES_SCHEMA,
        orient="row",
    )
    filings = pl.DataFrame(
        [("a", "18", True), ("b", "18", True)], schema=FILINGS_SCHEMA, orient="row"
    )
    population = classify(
        ["a", "b"],
        searches,
        filings,
        pl.DataFrame([], schema=SOURCES_SCHEMA),
        complete_by_capture=[],
        download_codes=["18"],
        draw=frozenset({"a"}),
    )
    assert population.included == {"a"}
    assert population.excluded == {"b": "not_in_draw"}
    assert next(iter(population.reasons())) == "not_in_draw"


def _weighted_config(population: PopulationConfig, spec: str = SPEC) -> BacktestConfig:
    return load_backtest_config("backtest_v1").model_copy(
        update={
            "test_years": (2021, 2022, 2023, 2024),
            "bootstrap": BOOT,
            "population": population,
            "weights": WEIGHTS.model_copy(update={"draw_spec_sha256": spec}),
        }
    )


def _weighted_run(odd: float) -> BacktestResult:
    features, labels = panel(30, seed=3)
    weights = _uniform_weights(labels, odd=odd)
    included = weights.members
    rule = PopulationConfig(
        rule="acquisition_complete", complete_by_capture=(), entities_hash=entities_hash(included)
    )
    population = Population(included=included, excluded={}, drawn=True)
    feature_set = load_feature_set("feature_set_v3").feature_set
    models = [load_classical_model("altman_z2_2000", feature_set)]
    return run_backtest(
        features, labels, _weighted_config(rule), TIMING, models, population, None, weights
    )


def test_a_weighted_backtest_reports_every_basis():
    result = _weighted_run(odd=4.0)
    assert result.basis_cells is not None
    assert set(result.basis_cells.get_column("basis").unique().to_list()) == set(BASES)
    pooled = result.cells.filter(
        (pl.col("test_year") == POOLED) & (pl.col("run") == "main") & pl.col("evaluable")
    )
    unweighted = result.basis_cells.filter(
        (pl.col("basis") == "unweighted") & (pl.col("run") == "main") & pl.col("evaluable")
    )
    assert not pooled.is_empty()
    joined = pooled.join(unweighted, on=["horizon_months", "model"], suffix="_u")
    assert (joined.get_column("brier") != joined.get_column("brier_u")).any()
    report = render_report(result, "0" * 40)
    assert "Weighted to a recorded draw" in report
    assert "### Pooled, by basis" in report
    assert "`a_alone`" in report and f"`{SPEC}`" in report


def test_equal_weights_give_the_unweighted_point_metrics():
    # Top-decile precision is left out: unweighted, it rounds a tenth up to a whole row.
    result = _weighted_run(odd=1.0)
    assert result.basis_cells is not None
    pooled = result.cells.filter((pl.col("test_year") == POOLED) & pl.col("evaluable"))
    unweighted = result.basis_cells.filter(pl.col("basis") == "unweighted")
    joined = pooled.join(unweighted, on=["run", "horizon_months", "model"], suffix="_u")
    assert not joined.is_empty()
    for name in (m for m in METRICS if m != "top_decile_precision"):
        for a, b in joined.select(name, f"{name}_u").iter_rows():
            assert a == pytest.approx(b), name


def test_weights_are_given_exactly_when_the_config_names_a_draw():
    features, labels = panel(9, seed=3)
    weights = _uniform_weights(labels, odd=2.0)
    config = load_backtest_config("backtest_v1")
    with pytest.raises(ValueError, match="exactly when"):
        run_backtest(features, labels, config, TIMING, [], None, None, weights)


def test_the_weights_must_be_the_pinned_draws():
    features, labels = panel(9, seed=3)
    weights = _uniform_weights(labels, odd=2.0, spec="cd" * 32)
    rule = PopulationConfig(
        rule="acquisition_complete",
        complete_by_capture=(),
        entities_hash=entities_hash(weights.members),
    )
    population = Population(included=weights.members, excluded={}, drawn=True)
    with pytest.raises(ValueError, match="pinned draw"):
        run_backtest(
            features, labels, _weighted_config(rule), TIMING, [], population, None, weights
        )


def test_a_weighted_config_needs_a_population_rule():
    with pytest.raises(ValueError, match="population rule"):
        load_backtest_config("backtest_v1").model_validate(
            {**load_backtest_config("backtest_v1").model_dump(by_alias=True), "weights": WEIGHTS}
        )
