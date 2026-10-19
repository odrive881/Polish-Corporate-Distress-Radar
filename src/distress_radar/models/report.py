"""The backtest report: generated Markdown tables (plan 0012 step E, owner decision 8).

Every table opens with the seed caveat and its event count, generated rather than written once,
since a harness that looks finished invites reading its numbers. A cell below `min_events` has the
verdict `n/a (<k events)` and no numbers; a cell whose model could not be fitted says why. The
runs sit on adjacent rows. With generation 3 (plan 0015 step F): its inner year per fold, each
fold's feature importance (never a company or a row), the survival models' metrics, and the
champion rule's verdict. A weighted backtest (plan 0019 step D) says which draw it rests on, and
gives each pooled cell weighted (the headline), for the random group alone, and unweighted. Nothing
time-dependent is written, so the same inputs give the same bytes.
"""

# polars's signatures reference types pyright cannot resolve; scoped to this module.
# pyright: reportUnknownMemberType=false

from __future__ import annotations

import os
from pathlib import Path

import polars as pl

from distress_radar.models.backtest import BASES, POOLED, RUNS, BacktestResult
from distress_radar.models.evaluation import METRICS
from distress_radar.models.explain import TOP, top_features

REPORTS = Path("reports") / "backtest"
_METRIC_TITLES = {
    "brier": "Brier",
    "log_loss": "log loss",
    "auc": "AUC",
    "top_decile_precision": "top-decile precision",
}
_RUN_TITLES = {
    "main": "all rows",
    "no_regime": "2020-21 regime rows left out",
    "entity_disjoint": "`main`'s models, scored only on entities no model of the fold trained on",
}
_BASIS_TITLES = {
    "weighted": "weighted, both groups (the headline)",
    "a_alone": "random group alone, unweighted (self-weighting: the check)",
    "unweighted": "both groups, unweighted (the sample's, not the segment's)",
}


def _number(value: float | None) -> str:
    return "–" if value is None else f"{value:.3f}"


def _verdict(row: dict[str, object], min_events: int) -> str:
    """Why a cell has numbers, or why not."""
    if row["evaluable"]:
        return "scored"
    if not row["fitted"]:
        return f"n/a ({row['note'] or 'not fitted'})"
    return f"n/a (<{min_events} events)"


def _metric(row: dict[str, object], name: str) -> str:
    if not row["evaluable"]:
        return "n/a"
    value, low, high = row[name], row[f"{name}_low"], row[f"{name}_high"]
    if not isinstance(value, float):
        return "n/a (undefined)"
    if not (isinstance(low, float) and isinstance(high, float)):
        return _number(value)
    return f"{_number(value)} [{_number(low)}–{_number(high)}]"


def _table(header: list[str], rows: list[list[str]]) -> list[str]:
    return [
        "| " + " | ".join(header) + " |",
        "|" + "|".join("---" for _ in header) + "|",
        *("| " + " | ".join(row) + " |" for row in rows),
    ]


def _by_list(counts: dict[str, int]) -> str:
    return " (" + ", ".join(f"{name} {n}" for name, n in counts.items()) + ")" if counts else ""


def _caveat(result: BacktestResult, horizon: int) -> str:
    k = result.config.min_events
    if result.weights is not None:
        return (
            f"_Weighted to the draw: {result.events[horizon]} distinct events at {horizon} "
            f"months among {result.entities} drawn entities; cells below {k} events print n/a._"
        )
    kind = "Purposive lists" if result.lists else "Seed output"
    return (
        f"_{kind}, not evidence: {result.events[horizon]} distinct events at {horizon} "
        f"months{_by_list(result.events_by_list.get(horizon, {}))} among {result.entities} "
        f"entities; cells below {k} events print n/a._"
    )


def _opening(result: BacktestResult) -> str:
    config = result.config
    k = config.min_events
    level = round(config.bootstrap.level * 100)
    counts = ", ".join(f"{n} distinct events at {h} months" for h, n in result.events.items())
    weights = config.weights
    if weights is not None:
        groups = f"`{weights.random}`" + (f" and `{weights.distress}`" if weights.distress else "")
        return (
            f"> **Weighted to a recorded draw.** {result.entities} entities drawn from one frame "
            f"({groups}, draw spec `{weights.draw_spec_sha256}`), "
            f"{result.distress_entities} of them ever in distress; {counts}. Every fit and every "
            "cell weights a row by its entity's 1/π, so the metrics speak for the frame the draw "
            "was taken from; a pooled cell is also given for the random group alone and "
            "unweighted. The survival models' time-dependent metrics, and the champion rule, read "
            f"the rows unweighted. A cell with fewer than {k} distinct training or test events "
            f"prints n/a. Intervals are {level}% percentile intervals over "
            f"{config.bootstrap.replicates} resamples of entities within each draw stratum."
        )
    if result.lists:
        who = (
            f"> **Purposive lists, not evidence.** {result.entities} entities"
            f"{_by_list(result.lists)}, {result.distress_entities} of them ever in distress; "
            f"{counts}. Each list was chosen with distress status in mind (the seed by distress "
            "hints, a hand-built list along axes that include it; ADR 0014), so every probability "
            "below is calibrated on a base rate the population does not have, and no number here "
            "is a performance claim."
        )
    else:
        who = (
            f"> **Seed output, not evidence.** {result.entities} hand-picked entities, "
            f"{result.distress_entities} of them ever in distress; {counts}. The seed was chosen "
            "with distress hints, so every probability below is calibrated on a base rate the "
            "population does not have, and no number here is a performance claim."
        )
    return (
        f"{who} A cell with fewer than {k} distinct training or test events prints n/a. "
        f"Intervals are {level}% percentile intervals over {config.bootstrap.replicates} "
        "resamples of entities."
    )


def _gbm_folds(result: BacktestResult, horizon: int) -> list[str]:
    """Generation 3's inner year per fold: tuned or defaults, calibrated or raw, and why."""
    if result.gbm_folds is None:
        return []
    rows = result.gbm_folds.filter(pl.col("horizon_months") == horizon).sort("test_year", "run")
    return [
        "",
        "### Generation 3 per fold",
        "",
        (
            "Tuning and calibration read only the inner validation year, the training rows' last "
            "settled year; below their gates the defaults and the raw score stand."
        ),
        "",
        *_table(
            ["test year", "run", "inner year", "inner events", "settings", "calibration"],
            [
                [
                    str(r["test_year"]),
                    str(r["run"]),
                    "–" if r["inner_year"] is None else str(r["inner_year"]),
                    str(r["inner_events"]),
                    str(r["tuning"]),
                    str(r["calibration"]),
                ]
                for r in rows.iter_rows(named=True)
            ],
        ),
    ]


def _importance(result: BacktestResult, horizon: int, caveat: str) -> list[str]:
    """Each fold's top features by mean absolute SHAP value; no company, no row."""
    if result.importance is None:
        return []
    table = result.importance.filter(pl.col("horizon_months") == horizon)
    lines = [
        "",
        "### Feature importance (generation 3)",
        "",
        caveat,
        "",
        (
            f"_Mean absolute SHAP value over each fold's test rows, in log-odds of the raw score; "
            f"the top {TOP} features, the rest summed. With this few events a ranking is an "
            "anecdote, not a finding._"
        ),
    ]
    for run in RUNS[:2]:
        folds = table.filter(pl.col("run") == run)
        years = sorted(folds.get_column("test_year").unique().to_list())
        if not years:
            continue
        columns = [
            top_features(folds.filter(pl.col("test_year") == year)).iter_rows() for year in years
        ]
        ranked = [list(c) for c in columns]
        depth = max(len(c) for c in ranked)
        lines += ["", f"`{run}`:", ""]
        lines += _table(
            ["rank", *(str(y) for y in years)],
            [
                [
                    str(i + 1) if i < TOP else "rest",
                    *(f"{c[i][0]} {c[i][1]:.3f}" if i < len(c) else "" for c in ranked),
                ]
                for i in range(depth)
            ],
        )
    return lines


def _survival(result: BacktestResult) -> list[str]:
    """The survival models' time-dependent metrics per run and test year."""
    if result.survival is None:
        return []
    k = result.config.min_events
    rows = result.survival.sort("model", "test_year", "run")
    return [
        "",
        "## Survival",
        "",
        (
            f"_Uno's concordance (IPCW) and the integrated Brier score of the survival models on "
            f"each test year's records, censored ones included, with the censoring distribution "
            f"from the training records; below {k} events, n/a. Their 12- and 24-month "
            "probabilities are scored in the horizon tables above._"
        ),
        "",
        *_table(
            [
                "model",
                "test year",
                "run",
                "train records",
                "train events",
                "test records",
                "test events",
                "verdict",
                "concordance (IPCW)",
                "integrated Brier",
            ],
            [
                [
                    str(r["model"]),
                    str(r["test_year"]),
                    str(r["run"]),
                    str(r["train_records"]),
                    str(r["train_events"]),
                    str(r["test_records"]),
                    str(r["test_events"]),
                    "scored" if r["evaluable"] else f"n/a ({r['note']})",
                    _number(r["concordance_ipcw"]) if r["evaluable"] else "n/a",
                    _number(r["integrated_brier"]) if r["evaluable"] else "n/a",
                ]
                for r in rows.iter_rows(named=True)
            ],
        ),
    ]


def _champion(result: BacktestResult) -> list[str]:
    """The champion rule's comparisons and verdict."""
    verdict = result.champion
    rule = result.config.champion
    if verdict is None or rule is None:
        return []
    level = round(result.config.bootstrap.level * 100)
    if verdict.promoted is not None:
        outcome = f"**`{verdict.promoted}` is promoted over `{verdict.incumbent}`.**"
    else:
        outcome = f"**Nothing is promoted: `{verdict.incumbent}` stays the champion.**"
    lines = [
        "",
        "## Champion rule",
        "",
        (
            f"A challenger replaces `{verdict.incumbent}` only if, at each horizon and in each of "
            f"{', '.join(f'`{r}`' for r in rule.runs)}, its pooled Brier score is lower with the "
            f"{level}% paired entity-bootstrap interval of the difference wholly below zero, its "
            f"AUC is at most {rule.auc_margin} lower, and the paired rows hold at least "
            f"{rule.min_promotion_events} distinct events. Paired rows: the test rows both models "
            f"scored, in the folds where both trained on {result.config.min_events}+ events."
        ),
        "",
        outcome,
        "",
        *_table(
            ["challenger", "verdict"],
            [[f"`{c}`", v] for c, v in verdict.verdicts.items()],
        ),
        "",
    ]
    lines += _table(
        [
            "challenger",
            "horizon",
            "run",
            "paired rows",
            "events",
            "Brier, challenger",
            "Brier, incumbent",
            "difference",
            "AUC, challenger",
            "AUC, incumbent",
            "outcome",
        ],
        [
            [
                f"`{c.challenger}`",
                str(c.horizon_months),
                c.run,
                str(c.rows),
                str(c.events),
                _number(c.brier_challenger),
                _number(c.brier_incumbent),
                (
                    f"{_number(c.brier_difference)} "
                    f"[{_number(c.difference_low)}–{_number(c.difference_high)}]"
                    if c.evaluable
                    else "n/a"
                ),
                _number(c.auc_challenger),
                _number(c.auc_incumbent),
                "passes" if c.passes else ("n/a: " if not c.evaluable else "fails: ") + c.reason,
            ]
            for c in verdict.comparisons
        ],
    )
    return lines


def _population(result: BacktestResult) -> list[str]:
    """The population rule's exclusions (plan 0015 decision 10), when the config names one."""
    population = result.population
    if population is None or result.config.population is None:
        return []
    return [
        "",
        "## Population",
        "",
        (
            f"Only entities whose acquisition is complete are modelled "
            f"(`{result.config.population.rule}`, entities `{population.entities_hash}`): "
            f"{len(population.included)} included, {len(population.excluded)} left out, "
            f"{result.excluded_distress_entities} of them ever labelled distress. A left-out "
            "entity's filings are not all in yet, so its nulls would describe the downloads, "
            "not the company."
        ),
        "",
        *_table(
            ["left out because", "entities"],
            [[f"`{reason}`", str(n)] for reason, n in population.reasons().items()],
        ),
    ]


def _bases(result: BacktestResult, horizon: int, caveat: str) -> list[str]:
    """A weighted backtest's pooled cells on every basis (plan 0019 step D)."""
    if result.basis_cells is None:
        return []
    k = result.config.min_events
    headline = result.cells.filter(
        (pl.col("horizon_months") == horizon) & (pl.col("test_year") == POOLED)
    ).select(pl.lit("weighted").alias("basis"), pl.all())
    others = result.basis_cells.filter(pl.col("horizon_months") == horizon)
    order = {b: i for i, b in enumerate(("weighted", *BASES))}
    rows = pl.concat([headline, others]).sort(
        "model", "run", pl.col("basis").replace_strict(order, return_dtype=pl.Int32)
    )
    return [
        "",
        "### Pooled, by basis",
        "",
        caveat,
        "",
        "; ".join(f"`{b}`: {_BASIS_TITLES[b]}" for b in order) + ".",
        "",
        *_table(
            [
                "model",
                "run",
                "basis",
                "scored rows",
                "test events",
                "verdict",
                *(_METRIC_TITLES[m] for m in METRICS),
            ],
            [
                [
                    str(row["model"]),
                    str(row["run"]),
                    f"`{row['basis']}`",
                    str(row["scored_rows"]),
                    str(row["test_events"]),
                    _verdict(row, k),
                    *(_metric(row, m) for m in METRICS),
                ]
                for row in rows.iter_rows(named=True)
            ],
        ),
    ]


def render_report(result: BacktestResult, code_commit: str) -> str:
    config = result.config
    k = config.min_events
    lines = [
        f"# Backtest report: {config.backtest}",
        "",
        _opening(result),
        "",
        *_table(
            ["input", "value"],
            [
                ["code commit", f"`{code_commit}`"],
                ["feature set", f"{result.feature_set_version} (`{result.feature_set_hash}`)"],
                ["label set", f"{result.label_version} (`{result.label_set_hash}`)"],
                ["backtest config", f"`config/models/{config.backtest}.yaml`"],
                [
                    "training labels",
                    (
                        "as settled on the eve of the test year: a distress event public by then; "
                        f"`alive` windows ending on or after {result.timing.krz_launch} settled "
                        f"{result.timing.alive_lag_months} months later"
                    ),
                ],
                ["runs", "; ".join(f"`{r}`: {_RUN_TITLES[r]}" for r in RUNS)],
            ],
        ),
        *_population(result),
    ]
    for horizon in config.horizons:
        caveat = _caveat(result, horizon)
        lines += ["", f"## {horizon}-month horizon", "", "### Metrics", "", caveat, ""]
        lines += [
            (
                f"A pooled row gathers the test rows of the folds whose model trained on at least "
                f"{k} events; its train rows are their sum, its train events the smallest fold's."
            ),
            "",
        ]
        cells = result.cells.filter(pl.col("horizon_months") == horizon).sort(
            "model", pl.col("test_year").replace(POOLED, 9999), "run"
        )
        lines += _table(
            [
                "model",
                "test year",
                "run",
                "train rows",
                "train events",
                "scored rows",
                "test events",
                "verdict",
                *(_METRIC_TITLES[m] for m in METRICS),
            ],
            [
                [
                    str(row["model"]),
                    "pooled" if row["test_year"] == POOLED else str(row["test_year"]),
                    str(row["run"]),
                    str(row["train_rows"]),
                    str(row["train_events"]),
                    str(row["scored_rows"]),
                    str(row["test_events"]),
                    _verdict(row, k),
                    *(_metric(row, m) for m in METRICS),
                ]
                for row in cells.iter_rows(named=True)
            ],
        )
        lines += _bases(result, horizon, caveat)
        lines += ["", "### Reliability, pooled", "", caveat, ""]
        if result.weights is not None:
            lines += ["Mean predicted and observed are weighted; rows are counts.", ""]
        speaking = cells.filter((pl.col("test_year") == POOLED) & pl.col("evaluable"))
        bins = (
            result.reliability.join(
                speaking.select("run", "horizon_months", "model"),
                on=["run", "horizon_months", "model"],
            ).sort("model", "run", "bin_low")
            if not result.reliability.is_empty()
            else result.reliability
        )
        if bins.is_empty():
            lines.append(f"No pooled cell reaches {k} events, so no reliability table is shown.")
        else:
            lines += _table(
                [
                    "model",
                    "run",
                    "probability",
                    "rows",
                    "distress rows",
                    "mean predicted",
                    "observed",
                ],
                [
                    [
                        str(r["model"]),
                        str(r["run"]),
                        f"{r['bin_low']:.1f}–{r['bin_high']:.1f}",
                        str(r["rows"]),
                        str(r["distress_rows"]),
                        _number(r["mean_predicted"]),
                        _number(r["observed_rate"]),
                    ]
                    for r in bins.iter_rows(named=True)
                ],
            )
        lines += ["", "### Folds", "", caveat, ""]
        folds = result.folds.filter(pl.col("horizon_months") == horizon).sort("test_year", "run")
        lines += _table(
            [
                "test year",
                "run",
                "train rows",
                "unsettled, left out",
                "train events",
                "test rows",
                "test events",
                "test events by class",
                f"fold clears {k} events",
            ],
            [
                [
                    str(r["test_year"]),
                    str(r["run"]),
                    str(r["train_rows"]),
                    str(r["train_rows_unsettled"]),
                    str(r["train_events"]),
                    str(r["test_rows"]),
                    str(r["test_events"]),
                    ", ".join(
                        f"{c} {r[f'test_events_{c}']}"
                        for c in config.distress_classes
                        if r[f"test_events_{c}"]
                    )
                    or "–",
                    "yes" if r["evaluable"] else "no",
                ]
                for r in folds.iter_rows(named=True)
            ],
        )
        lines += _gbm_folds(result, horizon)
        lines += _importance(result, horizon, caveat)
    lines += _survival(result)
    lines += _champion(result)
    return "\n".join(lines) + "\n"


def write_report(text: str, warehouse_dir: Path, name: str) -> Path:
    """Replace `reports/backtest/<name>.md` under the warehouse, atomically."""
    target = warehouse_dir / REPORTS / f"{name}.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    staging = target.with_suffix(".md.tmp")
    staging.write_bytes(text.encode("utf-8"))
    os.replace(staging, target)
    return target
