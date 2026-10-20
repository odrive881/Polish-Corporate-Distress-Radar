"""Loading a registered model version for scoring (plan 0016 step B, owner decisions 1, 2 and 4).

**Which version serves** (decision 1): per horizon, the registered model `distress_radar_<h>m`'s
version under the alias `champion`, else under `incumbent`; neither is an error, never a guess. The
version serves as registered: the model the backtest evaluated, never a refit. Its fold year is
part of every score's metadata: it was trained on the labels settled on that year's eve.

**Provisional or final** (plan 0019 step F, decision 8): a version's basis is its
`champion_basis` tag; a version registered before the tag existed came from an unweighted verdict.
A champion is final only from a weighted one; `status` says which, in one place for every reader.

**A scorer per kind**, rebuilt from the version's `model/` files (`model.json`, and the booster's
text or the forest's pickle), so that a loaded version reproduces the probabilities its fold
printed:
- `rank_logistic`: each input through the training fold's rank transform, then the regression.
  Complete cases only (decision 2): a row missing an input is "not scored: inputs incomplete",
  never imputed, and names the inputs it lacks (`missing_inputs`, in the model's input order);
- `lightgbm_binary`: the booster's probability, or its raw score through the Platt map; nulls are
  inputs, as in training;
- `discrete_time_hazard_lightgbm` and `random_survival_forest`: one minus the survival at the
  horizon.

**`top_drivers`** (decision 4): each input's exact contribution to the row's log-odds, the top
`TOP_DRIVERS` by absolute value, each with the input's value and its `known_from`. For the
regression, the coefficient times the row's rank less the training mean rank (the exact SHAP value
of a linear model with independent inputs); for LightGBM, TreeSHAP on the raw score (`explain.py`),
before the Platt map, which is monotone. The survival kinds have no contribution defined yet (plan
0015 explains only the binary booster): their scores carry no drivers.

Everything here is company-level output: local only (AGENT_SPEC §6K), never published.
"""

# polars's, mlflow's, LightGBM's and scikit-survival's signatures reference types pyright cannot
# resolve; scoped to this module.
# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false
# pyright: reportUnknownArgumentType=false, reportMissingTypeStubs=false

from __future__ import annotations

import json
import pickle
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import cast

import lightgbm as lgb
import numpy as np
import polars as pl
from mlflow.tracking import MlflowClient
from sksurv.ensemble import RandomSurvivalForest

from distress_radar.features.contracts import KNOWN_FROM_SUFFIX
from distress_radar.models.classical import Array, RankTransform
from distress_radar.models.explain import shap_values
from distress_radar.models.gbm import PlattMap, feature_matrix
from distress_radar.models.registry import (
    BASIS_TAG,
    CHAMPION_ALIAS,
    INCUMBENT_ALIAS,
    registered_name,
)
from distress_radar.models.survival import forest_survival, hazard_survival

SERVING_ALIASES = (CHAMPION_ALIAS, INCUMBENT_ALIAS)  # in order of preference
TOP_DRIVERS = 5
INPUTS_INCOMPLETE = "inputs incomplete"
SCORE_SCHEMA: dict[str, pl.DataType | type[pl.DataType]] = {
    "krs": pl.String,
    "as_of_date": pl.Date,
    "probability": pl.Float64,  # null exactly when `not_scored_reason` is set
    "not_scored_reason": pl.String,
    "top_drivers": pl.String,  # JSON; null when not scored, or for a kind without contributions
    "missing_inputs": pl.List(pl.String),  # in input order; null unless inputs incomplete
}


class NoServedModelError(LookupError):
    """No registered version under any serving alias for a horizon."""


@dataclass(frozen=True)
class ServedVersion:
    """The registered version that serves a horizon, and what identifies it."""

    name: str
    version: str
    alias: str
    run_id: str
    model: str  # the backtest's model name, e.g. `logistic_regression`
    horizon_months: int
    test_year: int  # its fold: trained on labels settled on this year's eve
    tags: dict[str, str]  # the version's tags: the four identifiers among them

    @property
    def training_eve(self) -> date:
        return date(self.test_year - 1, 12, 31)

    @property
    def basis(self) -> str:
        """The champion rule's basis for the backtest that registered it."""
        return self.tags.get(BASIS_TAG, "unweighted")

    @property
    def provisional(self) -> bool:
        """A champion promoted on an unweighted run; an incumbent is neither."""
        return self.alias == CHAMPION_ALIAS and self.basis != "weighted"

    @property
    def status(self) -> str:
        """`champion`, `provisional champion` or `incumbent`."""
        if self.alias != CHAMPION_ALIAS:
            return self.alias
        return "provisional champion" if self.provisional else "champion"


def resolve(client: MlflowClient, horizon: int) -> ServedVersion:
    """The version under `champion`, else `incumbent`, of the horizon's registered model."""
    name = registered_name(horizon)
    found = client.search_registered_models(filter_string=f"name = '{name}'")
    aliases: dict[str, str] = dict(found[0].aliases) if found else {}
    for alias in SERVING_ALIASES:
        if alias in aliases:
            version = client.get_model_version(name, aliases[alias])
            tags = {str(k): str(v) for k, v in version.tags.items()}
            return ServedVersion(
                name=name,
                version=str(version.version),
                alias=alias,
                run_id=str(version.run_id),
                model=tags["model"],
                horizon_months=horizon,
                test_year=int(tags["test_year"]),
                tags=tags,
            )
    raise NoServedModelError(
        f"{name} has no version under {' or '.join(SERVING_ALIASES)}: run the backtest first"
    )


def model_files(client: MlflowClient, served: ServedVersion) -> dict[str, bytes]:
    """The version's `model/` files, by name."""
    with TemporaryDirectory() as workdir:
        local = Path(client.download_artifacts(served.run_id, "model", workdir))
        return {p.name: p.read_bytes() for p in sorted(local.iterdir()) if p.is_file()}


# A row's contributions to its log-odds, per input, and the base they add to.
Contributions = Callable[[Array], tuple[Array, Array]]


@dataclass(frozen=True)
class Scorer:
    """One registered version's scoring: P(distress within `horizon_months`) per row."""

    kind: str
    horizon_months: int
    inputs: tuple[str, ...]
    complete_cases: bool  # a row missing an input is not scored (the regression)
    probability: Callable[[Array], Array]
    contributions: Contributions | None  # None: this kind has no drivers

    def score(self, rows: pl.DataFrame) -> pl.DataFrame:
        """SCORE_SCHEMA, one row per row of `rows` (which holds `krs`, `as_of_date`, the inputs
        and their `*__known_from` companions), in the same order."""
        inputs = list(self.inputs)
        missing = [c for c in inputs if c not in rows.columns]
        if missing:
            raise ValueError(f"the rows lack the model's inputs: {', '.join(missing[:5])}")
        x = feature_matrix(rows, inputs)
        scorable = (
            ~np.isnan(x).any(axis=1) if self.complete_cases else np.ones(rows.height, dtype=bool)
        )
        lacking: list[list[str] | None] = [
            None if s else [name for name, gap in zip(inputs, holes, strict=True) if gap]
            for s, holes in zip(scorable, np.isnan(x), strict=True)
        ]
        probability = np.full(rows.height, np.nan)
        drivers: list[str | None] = [None] * rows.height
        if scorable.any():
            probability[scorable] = self.probability(x[scorable])
            if self.contributions is not None:
                values, _ = self.contributions(x[scorable])
                at = np.flatnonzero(scorable)
                kept = rows.filter(pl.Series(scorable))
                for i, row in enumerate(kept.iter_rows(named=True)):
                    drivers[int(at[i])] = _top_drivers(inputs, values[i], row)
        return pl.DataFrame(
            {
                "krs": rows.get_column("krs"),
                "as_of_date": rows.get_column("as_of_date"),
                "probability": pl.Series(probability, dtype=pl.Float64).fill_nan(None),
                "not_scored_reason": [None if s else INPUTS_INCOMPLETE for s in scorable],
                "top_drivers": drivers,
                "missing_inputs": lacking,
            },
            schema=SCORE_SCHEMA,
        )


def _json_value(value: object) -> object:
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, float) and np.isnan(value):
        return None
    return value


def _top_drivers(inputs: list[str], values: Array, row: dict[str, object]) -> str:
    """The `TOP_DRIVERS` largest contributions by absolute value (ties by name), as JSON."""
    order = sorted(range(len(inputs)), key=lambda j: (-abs(float(values[j])), inputs[j]))
    return json.dumps(
        [
            {
                "feature": inputs[j],
                "value": _json_value(row[inputs[j]]),
                "known_from": _json_value(row.get(f"{inputs[j]}{KNOWN_FROM_SUFFIX}")),
                "contribution": float(values[j]),
            }
            for j in order[:TOP_DRIVERS]
        ],
        sort_keys=True,
    )


def _sigmoid(z: Array) -> Array:
    return 1.0 / (1.0 + np.exp(-z))


def _rank_logistic(body: Mapping[str, object]) -> Scorer:
    ranks = RankTransform(
        tuple(
            np.asarray(v, dtype=np.float64) for v in cast(list[list[float]], body["rank_reference"])
        )
    )
    coefficients = np.asarray(body["coefficients"], dtype=np.float64)
    intercept = float(cast(float, body["intercept"]))
    # The training rows' mean rank per input: the point each contribution is measured from.
    mean_rank = np.array(
        [
            (
                (np.searchsorted(v, v, side="left") + np.searchsorted(v, v, side="right"))
                / (2.0 * v.size)
            ).mean()
            for v in ranks.sorted_columns
        ]
    )

    def contributions(x: Array) -> tuple[Array, Array]:
        values = coefficients * (ranks.transform(x) - mean_rank)
        base = np.full(x.shape[0], intercept + float(coefficients @ mean_rank))
        return values, base

    def probability(x: Array) -> Array:
        values, base = contributions(x)
        return _sigmoid(base + values.sum(axis=1))

    return Scorer(
        kind="rank_logistic",
        horizon_months=int(cast(int, body["horizon_months"])),
        inputs=tuple(cast(list[str], body["inputs"])),
        complete_cases=True,
        probability=probability,
        contributions=contributions,
    )


def _lightgbm_binary(body: Mapping[str, object], files: Mapping[str, bytes]) -> Scorer:
    booster = lgb.Booster(model_str=files["model.txt"].decode())
    platt_body = cast(dict[str, float] | None, body["platt"])
    platt = (
        PlattMap(float(platt_body["slope"]), float(platt_body["intercept"]))
        if platt_body is not None
        else None
    )

    def probability(x: Array) -> Array:
        if platt is None:
            return np.asarray(booster.predict(x), dtype=np.float64)
        return platt.apply(np.asarray(booster.predict(x, raw_score=True), dtype=np.float64))

    def contributions(x: Array) -> tuple[Array, Array]:
        values, base = shap_values(booster, x)
        return values, np.full(x.shape[0], base)

    return Scorer(
        kind="lightgbm_binary",
        horizon_months=int(cast(int, body["horizon_months"])),
        inputs=tuple(cast(list[str], body["inputs"])),
        complete_cases=False,
        probability=probability,
        contributions=contributions,
    )


def _survival(body: Mapping[str, object], files: Mapping[str, bytes]) -> Scorer:
    horizon = int(cast(int, body["horizon_months"]))
    max_months = int(cast(int, body["max_months"]))
    if not 1 <= horizon <= max_months:
        raise ValueError(f"horizon {horizon} is outside the model's 1 to {max_months} months")
    kind = str(body["kind"])
    curves: Callable[[Array], Array]
    if kind == "discrete_time_hazard_lightgbm":
        booster = lgb.Booster(model_str=files["model.txt"].decode())
        curves = lambda x: hazard_survival(booster, x, max_months)
    else:
        forest = cast(RandomSurvivalForest, pickle.loads(files["model.pkl"]))  # our own registry
        curves = lambda x: forest_survival(forest, x, max_months)
    return Scorer(
        kind=kind,
        horizon_months=horizon,
        inputs=tuple(cast(list[str], body["inputs"])),
        complete_cases=False,
        probability=lambda x: 1.0 - curves(x)[:, horizon - 1],
        contributions=None,
    )


def load_scorer(files: Mapping[str, bytes]) -> Scorer:
    """The scorer a version's `model/` files describe."""
    body = cast(dict[str, object], json.loads(files["model.json"]))
    kind = body.get("kind")
    if kind == "rank_logistic":
        return _rank_logistic(body)
    if kind == "lightgbm_binary":
        return _lightgbm_binary(body, files)
    if kind in ("discrete_time_hazard_lightgbm", "random_survival_forest"):
        return _survival(body, files)
    raise ValueError(f"no scorer for model kind {kind!r}")


def load_served(tracking_uri: str, horizon: int) -> tuple[ServedVersion, Scorer]:
    """The served version of a horizon and its scorer, checked to be that horizon's."""
    client = MlflowClient(tracking_uri=tracking_uri)
    served = resolve(client, horizon)
    scorer = load_scorer(model_files(client, served))
    if scorer.horizon_months != horizon:
        raise ValueError(
            f"{served.name} v{served.version} holds a {scorer.horizon_months}-month model"
        )
    return served, scorer
