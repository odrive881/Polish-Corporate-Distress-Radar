"""The backtest report: generated Markdown tables (plan 0012 step E, owner decision 8).

Every table opens with the seed caveat and its event count, generated rather than written once,
since a harness that looks finished invites reading its numbers. A cell below `min_events` has the
verdict `n/a (<k events)` and no numbers; a cell whose model could not be fitted says why. The
two runs sit on adjacent rows. Nothing time-dependent is written, so the same inputs give the same bytes.
"""

# polars's signatures reference types pyright cannot resolve; scoped to this module.
# pyright: reportUnknownMemberType=false

from __future__ import annotations

import os
from pathlib import Path

import polars as pl

from distress_radar.models.backtest import POOLED, RUNS, BacktestResult
from distress_radar.models.evaluation import METRICS

REPORTS = Path("reports") / "backtest"
_METRIC_TITLES = {
    "brier": "Brier",
    "log_loss": "log loss",
    "auc": "AUC",
    "top_decile_precision": "top-decile precision",
}
_RUN_TITLES = {"main": "all rows", "no_regime": "2020-21 regime rows left out"}


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


def _caveat(result: BacktestResult, horizon: int) -> str:
    k = result.config.min_events
    return (
        f"_Seed output, not evidence: {result.events[horizon]} distinct events at {horizon} "
        f"months among {result.entities} entities; cells below {k} events print n/a._"
    )


def render_report(result: BacktestResult, code_commit: str) -> str:
    config = result.config
    k = config.min_events
    level = round(config.bootstrap.level * 100)
    lines = [
        f"# Backtest report: {config.backtest}",
        "",
        (
            f"> **Seed output, not evidence.** {result.entities} hand-picked entities, "
            f"{result.distress_entities} of them ever in distress; "
            + ", ".join(f"{n} distinct events at {h} months" for h, n in result.events.items())
            + ". The seed was chosen with distress hints, so every probability below is "
            "calibrated on a base rate the population does not have, and no number here is a "
            f"performance claim. A cell with fewer than {k} distinct training or test events "
            f"prints n/a. Intervals are {level}% percentile intervals over "
            f"{config.bootstrap.replicates} resamples of entities."
        ),
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
        lines += ["", "### Reliability, pooled", "", caveat, ""]
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
    return "\n".join(lines) + "\n"


def write_report(text: str, warehouse_dir: Path, name: str) -> Path:
    """Replace `reports/backtest/<name>.md` under the warehouse, atomically."""
    target = warehouse_dir / REPORTS / f"{name}.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    staging = target.with_suffix(".md.tmp")
    staging.write_bytes(text.encode("utf-8"))
    os.replace(staging, target)
    return target
