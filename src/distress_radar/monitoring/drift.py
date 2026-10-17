"""L — input drift of the served model, per horizon (plan 0018 step F, decision 6).

The reference is what the served version was trained on: its fold's training rows, rebuilt from
the version's own label set (`label_set_hash` tag) and test year with the backtest's split. The
current data is the latest scored month-end's rows. Only the model's inputs are compared, raw
(before any fold-fitted ranking), nulls included: a model that scores complete cases only would
otherwise hide its missing inputs.

At about 240 current rows a drift test flags little and proves less (plan 0018, constraint 4):
the report is read, never a gate. It is written locally only (`WAREHOUSE_DIR/reports/drift/`),
never to the public site: Evidently's charts draw per-row distributions whose tails can single
out a company (decision 6).

Evidently is imported only after its telemetry is switched off (plan 0018 step A: three
variables, none of which its report path needed, set anyway). Its HTML draws fresh widget ids on
every render; `normalise_ids` numbers them by first appearance, so the same inputs give the same
bytes (invariant 5).
"""

# polars's signatures reference types pyright cannot resolve, and Evidently ships no stubs; scoped
# to this module.
# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false
# pyright: reportUnknownArgumentType=false, reportMissingTypeStubs=false

from __future__ import annotations

import json
import os
import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

import polars as pl

from distress_radar.models.dataset import modelling_dataset
from distress_radar.models.splits import LabelTiming, split

TELEMETRY_OFF = {
    "DO_NOT_TRACK": "1",
    "ITERATIVE_DO_NOT_TRACK": "1",
    "EVIDENTLY_DISABLE_TELEMETRY": "1",
}
DRIFT_DIR = Path("reports") / "drift"
_ID = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
    r"|(?<![0-9a-zA-Z])[0-9a-f]{32}(?![0-9a-zA-Z])"
)


def telemetry_off() -> None:
    """Set the variables that switch Evidently's telemetry off; never unset by this module."""
    for name, value in TELEMETRY_OFF.items():
        os.environ[name] = value


def normalise_ids(html: str) -> str:
    """Renumber Evidently's random widget ids by first appearance, keeping each one's length."""
    seen: dict[str, str] = {}

    def _number(match: re.Match[str]) -> str:
        found = match.group(0)
        if found not in seen:
            seen[found] = f"{len(seen):0{len(found)}d}"
        return seen[found]

    return _ID.sub(_number, html)


@dataclass(frozen=True)
class DriftResult:
    horizon_months: int
    model: str  # `<name> v<version>`
    as_of_date: date
    reference_rows: int
    current_rows: int
    population_matches_pin: bool | None  # None: the backtest had no population rule
    columns: dict[str, dict[str, Any]]  # per input: method, statistic, drifted
    html: str

    def summary(self) -> dict[str, Any]:
        return {
            "horizon_months": self.horizon_months,
            "model": self.model,
            "as_of_date": self.as_of_date.isoformat(),
            "reference_rows": self.reference_rows,
            "current_rows": self.current_rows,
            "population_matches_pin": self.population_matches_pin,
            "columns": self.columns,
        }


def reference_frame(
    features: pl.DataFrame,
    labels: pl.DataFrame,
    *,
    horizon_months: int,
    test_year: int,
    distress_classes: Sequence[str],
    entities: frozenset[str] | None,
    timing: LabelTiming,
) -> pl.DataFrame:
    """The served version's fold's training rows, as the backtest split them: the label set's
    rows (of `entities` only, when the backtest had a population), whose window closed before
    `test_year` and whose label was settled on its eve, with their features."""
    if entities is not None:
        labels = labels.filter(pl.col("krs").is_in(sorted(entities)))
    dataset = modelling_dataset(features, labels, horizon_months, distress_classes)
    return split(dataset.frame, horizon_months, test_year, timing).train


def current_frame(features: pl.DataFrame, scored: frozenset[str], as_of_date: date) -> pl.DataFrame:
    """The latest scored month-end's feature rows of the entities the served version scored there
    (scored or with inputs incomplete: the missing inputs are part of what drifts)."""
    return features.filter(
        (pl.col("as_of_date") == as_of_date) & pl.col("krs").is_in(sorted(scored))
    )


def drift_report(
    reference: pl.DataFrame,
    current: pl.DataFrame,
    inputs: Sequence[str],
    *,
    horizon_months: int,
    model: str,
    as_of_date: date,
    population_matches_pin: bool | None = None,
) -> DriftResult:
    """Evidently's data-drift preset over `inputs`, current against reference."""
    telemetry_off()
    # Imported here, not at the top: its telemetry reads the environment at import time.
    from evidently import DataDefinition, Dataset, Report
    from evidently.presets import DataDriftPreset

    columns = list(inputs)
    if not columns:
        raise ValueError("no inputs to compare")
    if reference.is_empty() or current.is_empty():
        raise ValueError(
            f"reference {reference.height} rows, current {current.height}: nothing to compare"
        )
    definition = DataDefinition(numerical_columns=columns)

    def to_dataset(frame: pl.DataFrame) -> Any:
        numbers = frame.select(columns).cast(dict.fromkeys(columns, pl.Float64))
        return Dataset.from_pandas(numbers.to_pandas(), data_definition=definition)

    snapshot = Report([DataDriftPreset(columns=columns)]).run(
        to_dataset(current), to_dataset(reference)
    )
    found: dict[str, dict[str, Any]] = {}
    for metric in snapshot.dict()["metrics"]:
        config = metric.get("config", {})
        column = config.get("column")
        if column in columns and isinstance(metric.get("value"), int | float):
            threshold = config.get("threshold")
            method = str(config.get("method", ""))
            value = float(metric["value"])
            found[column] = {
                "method": method,
                "statistic": round(value, 6),
                "threshold": threshold,
                # A p-value drifts below its threshold; a distance drifts above it.
                "drifted": None
                if threshold is None
                else (value < threshold if "p_value" in method else value >= threshold),
            }
    return DriftResult(
        horizon_months=horizon_months,
        model=model,
        as_of_date=as_of_date,
        reference_rows=reference.height,
        current_rows=current.height,
        population_matches_pin=population_matches_pin,
        columns=found,
        html=normalise_ids(snapshot.get_html_str(as_iframe=False)),
    )


def write_drift(result: DriftResult, warehouse_dir: Path) -> tuple[Path, Path]:
    """`<as_of>_<h>m.html` and its `.json` summary under `WAREHOUSE_DIR/reports/drift/`."""
    directory = warehouse_dir / DRIFT_DIR
    directory.mkdir(parents=True, exist_ok=True)
    stem = f"{result.as_of_date.isoformat()}_{result.horizon_months}m"
    html = directory / f"{stem}.html"
    summary = directory / f"{stem}.json"
    html.write_text(result.html, encoding="utf-8")
    summary.write_text(
        json.dumps(result.summary(), indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return html, summary
