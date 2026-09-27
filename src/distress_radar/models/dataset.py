"""The modelling dataset (plan 0012 step C): `feature_store` joined to one frozen label set.

One row per labelled `(krs, as_of_date)` of a horizon, with the binary target of owner decision 1:
distress (the configured classes) against `alive`. Censored rows are counted and left out; they
belong to Phase 8's survival models. Every other labelled row must find its feature row: the
feature grid covers every labelled date (plan 0010), so a miss is a bug, raised, never dropped.

Nothing is imputed here or later (invariant 4): feature nulls reach the models as nulls.
"""

# polars's and pandera's signatures reference types pyright cannot resolve; scoped to this module.
# pyright: reportUnknownMemberType=false

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

import pandera.polars as pa
import polars as pl

from distress_radar.features.contracts import KEY_COLUMNS, KNOWN_FROM_SUFFIX
from distress_radar.labels import DATASET as LABELS_DATASET
from distress_radar.labels import label_set_hash
from distress_radar.warehouse import read_dataset

FEATURE_STORE = "feature_store"
# The label columns the dataset keeps. `regime_flag` is here for the sensitivity run's row filter
# and is never a model input (owner decision 9): it describes the label window, the future.
# `event_date` is null for a declaration with no decision date, which labels date by its
# `event_known_from` (plan 0008); `event_day` is that date either way.
LABEL_COLUMNS = [
    "outcome_class",
    "event_date",
    "event_known_from",
    "trigger_event_type",
    "regime_flag",
]
TARGET = "distress"


@dataclass(frozen=True)
class ModellingDataset:
    frame: pl.DataFrame
    horizon_months: int
    feature_set_version: str
    feature_set_hash: str
    label_set_hash: str
    censored_rows: int  # labelled rows left out as censored, counted rather than lost

    def feature_names(self) -> list[str]:
        """The feature columns, in the feature set's order; companions excluded."""
        return [c for c in self.frame.columns if f"{c}{KNOWN_FROM_SUFFIX}" in self.frame.columns]


def load_label_set(warehouse_dir: Path, digest: str) -> pl.DataFrame:
    """A frozen label set by hash, checked: rows that do not hash to their name are refused."""
    path = warehouse_dir / LABELS_DATASET / f"label_set_hash={digest}" / "part-0.parquet"
    if not path.exists():
        raise FileNotFoundError(f"label set {digest} is not frozen under {warehouse_dir}")
    labels = pl.read_parquet(path)
    if label_set_hash(labels) != digest:
        raise ValueError(f"label set {digest}: its rows no longer hash to its name")
    return labels


def load_feature_store(warehouse_dir: Path, feature_set_version: str) -> pl.DataFrame:
    """`feature_store` as built, refused unless it holds exactly the requested feature set."""
    features = read_dataset(warehouse_dir, FEATURE_STORE)
    versions = features.get_column("feature_set_version").unique().to_list()
    hashes = features.get_column("feature_set_hash").unique().to_list()
    if versions != [feature_set_version] or len(hashes) != 1:
        raise ValueError(
            f"feature_store holds {versions} (hashes {hashes}); rebuild it with "
            f"FEATURE_SET_VERSION={feature_set_version}"
        )
    return features


def modelling_dataset(
    features: pl.DataFrame,
    labels: pl.DataFrame,
    horizon_months: int,
    distress_classes: Sequence[str],
) -> ModellingDataset:
    """The rows of one horizon with their target, sorted by `(krs, as_of_date)`."""
    versions = features.get_column("feature_set_version").unique().to_list()
    hashes = features.get_column("feature_set_hash").unique().to_list()
    digests = labels.get_column("label_set_hash").unique().to_list()
    if len(versions) != 1 or len(hashes) != 1 or len(digests) != 1:
        raise ValueError("a modelling dataset reads one feature set and one label set")
    horizon = labels.filter(pl.col("horizon_months") == horizon_months)
    if horizon.is_empty():
        raise ValueError(f"the label set has no {horizon_months}-month rows")
    censored = horizon.filter(pl.col("censored"))
    labelled = horizon.filter(~pl.col("censored"))
    unknown = labelled.filter(
        (pl.col("outcome_class") != "alive") & ~pl.col("outcome_class").is_in(distress_classes)
    )
    if not unknown.is_empty():
        classes = sorted(unknown.get_column("outcome_class").unique().to_list())
        raise ValueError(f"outcome classes neither alive nor distress: {classes}")
    feature_columns = [c for c in features.columns if c not in KEY_COLUMNS]
    frame = labelled.select("krs", "as_of_date", *LABEL_COLUMNS).join(
        features.select("krs", "as_of_date", *feature_columns),
        on=["krs", "as_of_date"],
        how="left",
        validate="1:1",
    )
    unmatched = labelled.join(features, on=["krs", "as_of_date"], how="anti")
    if not unmatched.is_empty():
        raise ValueError(
            f"{unmatched.height} labelled rows have no feature row, e.g. "
            f"{unmatched.select('krs', 'as_of_date').row(0)}"
        )
    frame = frame.with_columns(pl.col("outcome_class").is_in(distress_classes).alias(TARGET)).sort(
        "krs", "as_of_date"
    )
    frame = frame.select("krs", "as_of_date", TARGET, *LABEL_COLUMNS, *feature_columns)
    dataset_contract().validate(frame.select("krs", "as_of_date", TARGET, *LABEL_COLUMNS))
    return ModellingDataset(
        frame=frame,
        horizon_months=horizon_months,
        feature_set_version=str(versions[0]),
        feature_set_hash=str(hashes[0]),
        label_set_hash=str(digests[0]),
        censored_rows=censored.height,
    )


def event_day() -> pl.Expr:
    """The date an event is labelled by: its decision date, else the date it became known."""
    return pl.coalesce("event_date", "event_known_from")


def _event_dated(data: pa.PolarsData) -> pl.LazyFrame:
    return data.lazyframe.select(~pl.col(TARGET) | event_day().is_not_null())


def _target_matches_class(data: pa.PolarsData) -> pl.LazyFrame:
    return data.lazyframe.select(pl.col(TARGET) == (pl.col("outcome_class") != "alive"))


def dataset_contract() -> pa.DataFrameSchema:
    """The key and label columns; the features are already under `feature_store`'s contract."""
    return pa.DataFrameSchema(
        {
            "krs": pa.Column(pl.String, checks=[pa.Check.str_matches(r"^[0-9]{10}$")]),
            "as_of_date": pa.Column(pl.Date),
            TARGET: pa.Column(pl.Boolean),
            "outcome_class": pa.Column(pl.String),
            "event_date": pa.Column(pl.Date, nullable=True),
            "event_known_from": pa.Column(pl.Date, nullable=True),
            "trigger_event_type": pa.Column(pl.String, nullable=True),
            "regime_flag": pa.Column(pl.Boolean),
        },
        checks=[
            pa.Check(_event_dated, name="a_distress_row_is_dated"),
            pa.Check(_target_matches_class, name="target_is_not_alive"),
        ],
        strict=True,
        ordered=True,
        unique=["krs", "as_of_date"],
        name="modelling_dataset",
    )
