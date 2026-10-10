"""Purged expanding-window folds (plan 0012 step C, owner decision 2).

For a test year Y, the test rows are those with `as_of_date` in Y; the training rows are those
whose whole label window closed before 1 January Y, `as_of_date + horizon < Y-01-01`, and whose
label was settled by then: what the label set's own rules would have said on 31 December Y-1.
- A distress row trains only if its event was public by then (`event_known_from`). The registry
  enters decisions up to 21 months late, so a window can close with its event still unknown.
- An `alive` row trains only if its window ended by the alive limit the labels apply at that date:
  the date itself, moved back by `alive_lag_months` for a window ending on or after KRZ's launch
  (plan 0009). The label set knows the rows stayed alive later; the model on Y's eve could not.
A row whose window closed but whose label was not settled is left out and counted, never
relabelled: as of Y's eve it was censored. Test rows keep their final labels, the truth a model
is judged against. The same entities appear on both sides, as a panel does; an entity-disjoint
check waits for scale.

Each fold is reported with its rows, distinct events (entity and event date: one event labels
up to `horizon` consecutive month-ends), their class mix and entities, and whether it clears the
minimum-events rule. A fold that does not is kept and reported, never scored (decision 2).
"""

# polars's signatures reference types pyright cannot resolve; scoped to this module.
# pyright: reportUnknownMemberType=false

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Literal

import polars as pl
import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from distress_radar.features.config import DisclosureFlagFeature, RatioFeature, load_feature_set
from distress_radar.labels import load_label_config
from distress_radar.models.dataset import TARGET, ModellingDataset, event_day
from distress_radar.models.population import PopulationConfig
from distress_radar.parsing.canonical_schema import CONFIG_DIR
from distress_radar.parsing.legal_taxonomy import load_procedure_taxonomy


class LogisticConfig(BaseModel):
    """Owner decision 5: fixed before the first run, never tuned."""

    model_config = ConfigDict(frozen=True, extra="forbid", populate_by_name=True)

    features: tuple[str, ...] = Field(min_length=1, max_length=6)
    penalty: Literal["l2"]
    c: float = Field(alias="C", gt=0)
    class_weight: Literal["none"]

    @model_validator(mode="after")
    def _distinct(self) -> LogisticConfig:
        if len(set(self.features)) != len(self.features):
            raise ValueError("logistic_regression.features has a repeat")
        return self


class BootstrapConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    replicates: int = Field(ge=100)
    seed: int = Field(ge=0)
    level: float = Field(gt=0.5, lt=1)


class LightGbmConfig(BaseModel):
    """Plan 0015 decision 1: generation 3's defaults, fixed before the first run. The values the
    decision fixes outright (nulls kept, no class weighting, no bagging, byte-reproducible) are
    literals; the rest are the defaults decision 2's search may move, where it runs."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    num_leaves: int = Field(ge=2)
    min_data_in_leaf: int = Field(ge=1)
    learning_rate: float = Field(gt=0, le=1)
    num_iterations: int = Field(ge=1)
    lambda_l2: float = Field(ge=0)
    feature_fraction: float = Field(gt=0, le=1)
    bagging_fraction: float = Field(ge=1, le=1)  # bagging off: decision 1
    use_missing: Literal[True]
    zero_as_missing: Literal[False]
    class_weight: Literal["none"]
    deterministic: Literal[True]
    force_row_wise: Literal[True]
    num_threads: Literal[1]
    seed: int = Field(ge=0)


class SearchRange(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    type: Literal["int", "float"]
    low: float
    high: float
    log: bool = False

    @model_validator(mode="after")
    def _range(self) -> SearchRange:
        if self.low >= self.high:
            raise ValueError("a search range needs low < high")
        if self.log and self.low <= 0:
            raise ValueError("a log-scaled range needs low > 0")
        if self.type == "int" and not (self.low.is_integer() and self.high.is_integer()):
            raise ValueError("an int range needs whole bounds")
        return self


# The LightGBM parameters decision 2's search may move; every other one is fixed by decision 1.
TUNABLE = frozenset(
    {
        "num_leaves",
        "min_data_in_leaf",
        "learning_rate",
        "num_iterations",
        "lambda_l2",
        "feature_fraction",
    }
)


class TuningConfig(BaseModel):
    """Plan 0015 decision 2: Optuna inside a fold, on the inner validation year's Brier score,
    only where that year holds `min_tuning_events` distinct events."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    min_tuning_events: int = Field(ge=1)
    objective: Literal["inner_validation_brier"]
    sampler: Literal["tpe"]
    seed: int = Field(ge=0)
    trials: int = Field(ge=1)
    space: dict[str, SearchRange] = Field(min_length=1)

    @model_validator(mode="after")
    def _tunable(self) -> TuningConfig:
        unknown = sorted(set(self.space) - TUNABLE)
        if unknown:
            raise ValueError(f"tuning.space names parameters that are not tunable: {unknown}")
        return self


class CalibrationConfig(BaseModel):
    """Plan 0015 decision 3: Platt scaling fitted on the inner validation year, never the test
    year; below `min_events` events there, the raw score is kept and reported."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    method: Literal["platt"]
    fit_on: Literal["inner_validation_year"]


class SurvivalForestConfig(BaseModel):
    """Decision 4's comparison model: scikit-survival's random survival forest, which accepts
    missing values in the installed version (step A)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    n_estimators: int = Field(ge=1)
    min_samples_leaf: int = Field(ge=1)
    max_features: Literal["sqrt"]
    seed: int = Field(ge=0)
    n_jobs: Literal[1]


class SurvivalConfig(BaseModel):
    """Plan 0015 decision 4: a discrete-time hazard model in LightGBM on monthly person-period
    rows up to `max_months`, with LightGBM's defaults; scikit-survival's metrics beside it."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    period: Literal["month"]
    max_months: int = Field(ge=1)
    hazard_model: Literal["lightgbm"]
    comparison: SurvivalForestConfig
    metrics: tuple[Literal["concordance_ipcw", "integrated_brier"], ...] = Field(min_length=1)


class ChampionConfig(BaseModel):
    """Plan 0015 decision 7: a challenger beats the incumbent only if, at each horizon, its pooled
    Brier score is lower with the paired entity-bootstrap interval of the difference wholly below
    zero, its AUC is at most `auc_margin` lower, both hold in every run of `runs`, and the pooled
    cells rest on `min_promotion_events` distinct events."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    incumbent: Literal["logistic_regression"]
    brier: Literal["paired_entity_bootstrap"]
    auc_margin: float = Field(ge=0, lt=1)
    runs: tuple[Literal["main", "no_regime"], ...] = Field(min_length=1)
    min_promotion_events: int = Field(ge=1)


# Generation 3's sections (plan 0015): a config names all of them or none (before backtest_v5).
GENERATION_3 = ("lightgbm", "tuning", "calibration", "survival", "champion")


class BacktestConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    backtest: str
    feature_set_version: str
    label_set_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    horizons: tuple[int, ...] = Field(min_length=1)
    distress_classes: tuple[str, ...] = Field(min_length=1)
    test_years: tuple[int, ...] = Field(min_length=1)
    min_events: int = Field(ge=1)
    logistic_regression: LogisticConfig
    bootstrap: BootstrapConfig
    reliability_bins: int = Field(ge=2, le=20)
    # Plan 0015 owner decision 10: the entities whose acquisition is complete (`population.py`);
    # absent before backtest_v5, where every labelled entity is modelled.
    population: PopulationConfig | None = None
    lightgbm: LightGbmConfig | None = None
    tuning: TuningConfig | None = None
    calibration: CalibrationConfig | None = None
    survival: SurvivalConfig | None = None
    champion: ChampionConfig | None = None

    @model_validator(mode="after")
    def _generation_3(self) -> BacktestConfig:
        named = [s for s in GENERATION_3 if getattr(self, s) is not None]
        if named and len(named) != len(GENERATION_3):
            missing = [s for s in GENERATION_3 if s not in named]
            raise ValueError(f"generation 3 needs every one of its sections; missing: {missing}")
        if self.survival is not None and self.survival.max_months < max(self.horizons):
            raise ValueError("survival.max_months must reach the longest horizon")
        return self

    @model_validator(mode="after")
    def _ordered(self) -> BacktestConfig:
        if list(self.test_years) != sorted(set(self.test_years)):
            raise ValueError("test_years must be increasing, without repeats")
        if "alive" in self.distress_classes:
            raise ValueError("`alive` is the negative class, not a distress class")
        return self


def load_backtest_config(version: str, config_dir: Path = CONFIG_DIR) -> BacktestConfig:
    """The backtest, checked against its feature set: the regression reads ratio features and the
    statement's own going-concern flags (plan 0013 decision 8), nothing else."""
    path = config_dir / "models" / f"{version}.yaml"
    config = BacktestConfig.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))
    if config.backtest != path.stem:
        raise ValueError(f"{path}: `backtest: {config.backtest}` must match the file name")
    feature_set = load_feature_set(config.feature_set_version, config_dir).feature_set
    readable = {
        f.name for f in feature_set.features if isinstance(f, RatioFeature | DisclosureFlagFeature)
    }
    missing = sorted(set(config.logistic_regression.features) - readable)
    if missing:
        raise ValueError(
            f"{path}: not ratio or disclosure-flag features of {config.feature_set_version}: "
            f"{missing}"
        )
    return config


def window_end(horizon_months: int) -> pl.Expr:
    """The month-end a row's label window closes on."""
    return pl.col("as_of_date").dt.offset_by(f"{horizon_months}mo").dt.month_end()


@dataclass(frozen=True)
class LabelTiming:
    """The label set's rules for when a label is settled: its `alive` lag and KRZ's launch."""

    alive_lag_months: int
    krz_launch: date


def label_timing(labels: pl.DataFrame, config_dir: Path = CONFIG_DIR) -> LabelTiming:
    """The timing rules of the label version a frozen set was built with."""
    versions = labels.get_column("label_version").unique().to_list()
    if len(versions) != 1:
        raise ValueError(f"a label set has one label_version, not {versions}")
    config = load_label_config(str(versions[0]), config_dir)
    return LabelTiming(config.alive_lag_months, load_procedure_taxonomy(config_dir).krz_launch)


def settled_by(day: date, horizon_months: int, timing: LabelTiming) -> pl.Expr:
    """True where a row's label, as the label set's rules give it, was already known on `day`."""
    closes = window_end(horizon_months)
    limit = pl.lit(day, dtype=pl.Date)
    alive_limit = (
        pl.when(closes >= timing.krz_launch)
        .then(
            limit.dt.offset_by(f"-{timing.alive_lag_months}mo")
            if timing.alive_lag_months
            else limit
        )
        .otherwise(limit)
    )
    known = pl.col("event_known_from").is_not_null() & (pl.col("event_known_from") <= day)
    return pl.when(pl.col(TARGET)).then(known).otherwise(closes <= alive_limit)


@dataclass(frozen=True)
class Fold:
    horizon_months: int
    test_year: int
    train: pl.DataFrame
    test: pl.DataFrame
    train_rows_unsettled: int  # window closed, label not yet known on the eve: counted, not used


def purged_folds(
    dataset: ModellingDataset, test_years: tuple[int, ...], timing: LabelTiming
) -> list[Fold]:
    return [split(dataset.frame, dataset.horizon_months, year, timing) for year in test_years]


def split(frame: pl.DataFrame, horizon_months: int, year: int, timing: LabelTiming) -> Fold:
    """One purged fold of `frame`: train on the rows whose window closed before `year` and whose
    label was settled on its eve, test on the rows dated in `year`. Generation 3 splits a fold's
    own training rows the same way for its inner validation year (`gbm.py`)."""
    closes = window_end(horizon_months)
    closed = frame.filter(closes < date(year, 1, 1))
    train = closed.filter(settled_by(date(year - 1, 12, 31), horizon_months, timing))
    return Fold(
        horizon_months=horizon_months,
        test_year=year,
        train=train,
        test=frame.filter(pl.col("as_of_date").dt.year() == year),
        train_rows_unsettled=closed.height - train.height,
    )


def events(rows: pl.DataFrame) -> pl.DataFrame:
    """Distinct events among the positive rows: entity, class, trigger and the event's date."""
    return (
        rows.filter(pl.col(TARGET))
        .select("krs", "outcome_class", "trigger_event_type", event_day().alias("event_day"))
        .unique()
        .sort("krs", "event_day", "trigger_event_type")
    )


def fold_report(
    folds: list[Fold], distress_classes: tuple[str, ...], min_events: int
) -> pl.DataFrame:
    """One row per fold: rows (and unsettled rows left out), events by class, entities, and
    whether it is evaluable."""
    records: list[dict[str, object]] = []
    for fold in folds:
        record: dict[str, object] = {
            "horizon_months": fold.horizon_months,
            "test_year": fold.test_year,
        }
        event_counts: list[int] = []
        for side, rows in (("train", fold.train), ("test", fold.test)):
            found = events(rows)
            event_counts.append(found.height)
            record[f"{side}_rows"] = rows.height
            if side == "train":
                record["train_rows_unsettled"] = fold.train_rows_unsettled
            record[f"{side}_entities"] = rows.get_column("krs").n_unique()
            record[f"{side}_events"] = found.height
            for cls in distress_classes:
                record[f"{side}_events_{cls}"] = found.filter(pl.col("outcome_class") == cls).height
        record["evaluable"] = min(event_counts) >= min_events
        records.append(record)
    return pl.DataFrame(records)
