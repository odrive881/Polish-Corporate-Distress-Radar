"""The modelling dataset (plan 0012 step C)."""

# polars's signatures reference types pyright cannot resolve; scoped to this module.
# pyright: reportUnknownMemberType=false

from __future__ import annotations

from datetime import date
from pathlib import Path

import polars as pl
import pytest
from tests.models.frames import features, labels

from distress_radar.labels import freeze_label_set
from distress_radar.models.dataset import TARGET, load_label_set, modelling_dataset

DISTRESS = ("bankruptcy", "restructuring", "liquidation", "silent_exit")
A, B = "0000000001", "0000000002"
J, F, M = date(2021, 1, 31), date(2021, 2, 28), date(2021, 3, 31)


def _features() -> pl.DataFrame:
    return features([(A, J, 0.1), (A, F, None), (A, M, -0.2), (B, J, 0.3), (B, F, 0.3)])


def _labels() -> pl.DataFrame:
    return labels(
        [
            (A, J, 12, "alive", None, False),
            (A, F, 12, "bankruptcy", date(2021, 12, 1), False),
            (A, M, 12, None, None, False),  # censored
            (B, J, 12, "silent_exit", date(2021, 10, 5), True),
            (B, J, 24, "silent_exit", date(2021, 10, 5), True),
        ]
    )


def test_one_horizon_with_its_target_and_the_censored_rows_counted() -> None:
    dataset = modelling_dataset(_features(), _labels(), 12, DISTRESS)
    assert dataset.censored_rows == 1
    rows = dataset.frame.select("krs", "as_of_date", TARGET).rows()
    assert rows == [(A, J, False), (A, F, True), (B, J, True)]
    assert (dataset.feature_set_version, dataset.horizon_months) == ("feature_set_vt", 12)


def test_feature_nulls_reach_the_dataset_as_nulls() -> None:
    """Invariant 4: nothing is imputed on the way to a model."""
    frame = modelling_dataset(_features(), _labels(), 12, DISTRESS).frame
    assert frame.filter((pl.col("krs") == A) & (pl.col("as_of_date") == F))["roa"].to_list() == [
        None
    ]


def test_the_regime_flag_is_carried_but_is_not_a_feature() -> None:
    """Owner decision 9: it describes the label window, so no model may read it."""
    dataset = modelling_dataset(_features(), _labels(), 12, DISTRESS)
    assert "regime_flag" in dataset.frame.columns
    assert dataset.feature_names() == ["roa"]


def test_a_labelled_row_without_features_is_an_error_not_a_drop() -> None:
    with pytest.raises(ValueError, match="1 labelled rows have no feature row"):
        modelling_dataset(_features().filter(pl.col("krs") == A), _labels(), 12, DISTRESS)


def test_an_unknown_outcome_class_is_refused() -> None:
    with pytest.raises(ValueError, match="neither alive nor distress"):
        modelling_dataset(_features(), _labels(), 12, ("bankruptcy",))


def test_two_feature_sets_are_refused() -> None:
    mixed = pl.concat(
        [_features(), _features().with_columns(pl.lit("other").alias("feature_set_version"))]
    )
    with pytest.raises(ValueError, match="one feature set"):
        modelling_dataset(mixed, _labels(), 12, DISTRESS)


def test_a_declaration_without_a_decision_date_is_dated_by_its_entry() -> None:
    """Plan 0008: `event_date` null, the label dated by `event_known_from`; still a positive."""
    undated = _labels().with_columns(
        pl.when(pl.col("krs") == B).then(None).otherwise(pl.col("event_date")).alias("event_date")
    )
    frame = modelling_dataset(_features(), undated, 12, DISTRESS).frame
    row = frame.filter(pl.col("krs") == B).row(0, named=True)
    assert row[TARGET] and row["event_date"] is None and row["event_known_from"] is not None


def test_a_distress_row_with_no_date_at_all_breaks_the_contract() -> None:
    broken = _labels().with_columns(
        pl.when(pl.col("krs") == B).then(None).otherwise(pl.col(c)).alias(c)
        for c in ("event_date", "event_known_from")
    )
    with pytest.raises(Exception, match="a_distress_row_is_dated"):
        modelling_dataset(_features(), broken, 12, DISTRESS)


def test_a_frozen_set_is_loaded_by_hash_and_checked(tmp_path: Path) -> None:
    frozen = freeze_label_set(_labels(), tmp_path)
    loaded = load_label_set(tmp_path, frozen.label_set_hash)
    assert loaded.height == 5
    # Tampered rows no longer hash to the set's name.
    path = tmp_path / frozen.path
    loaded.with_columns(pl.lit("alive").alias("outcome_class")).write_parquet(path)
    with pytest.raises(ValueError, match="no longer hash"):
        load_label_set(tmp_path, frozen.label_set_hash)
    with pytest.raises(FileNotFoundError):
        load_label_set(tmp_path, "0" * 64)
