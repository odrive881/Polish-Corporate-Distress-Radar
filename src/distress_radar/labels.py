"""Outcome label parameters (plan 0008 step B); the label-set freeze joins them in step G.

`config/labels/<label_version>.yaml` holds the engineering choices behind `outcome_labels`:
horizons, the `as_of_date` grid, precedence, the cutoff policy. The file name is the version,
so a changed value is a new file and a new `label_version`, never an edit in place.
"""

from __future__ import annotations

import calendar
from datetime import date
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, model_validator

from distress_radar.parsing.canonical_schema import CONFIG_DIR
from distress_radar.parsing.legal_taxonomy import EVENT_OUTCOME_CLASSES, EventOutcomeClass


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class AsOfGrid(_Frozen):
    frequency: Literal["month_end"]
    start: date

    @model_validator(mode="after")
    def _month_end(self) -> AsOfGrid:
        last = calendar.monthrange(self.start.year, self.start.month)[1]
        if self.start.day != last:
            raise ValueError(f"as_of_grid.start {self.start} is not a month end")
        return self


class LabelConfig(_Frozen):
    label_version: str
    horizons_months: tuple[Literal[12, 24], ...]
    as_of_grid: AsOfGrid
    precedence: tuple[EventOutcomeClass, ...]
    cutoff: Literal["earliest_last_complete_fetch"]
    exclude_in_proceeding: bool
    undated_event: Literal["on_or_before_known_from"]

    @model_validator(mode="after")
    def _consistent(self) -> LabelConfig:
        if not self.horizons_months or len(set(self.horizons_months)) != len(self.horizons_months):
            raise ValueError(f"horizons_months must be non-empty and distinct: {self.horizons_months}")
        if sorted(self.precedence) != sorted(EVENT_OUTCOME_CLASSES):
            raise ValueError(
                f"precedence must rank each of {list(EVENT_OUTCOME_CLASSES)} once: {self.precedence}"
            )
        return self


def load_label_config(
    label_version: str = "outcome_labels_v1", config_dir: Path = CONFIG_DIR
) -> LabelConfig:
    path = config_dir / "labels" / f"{label_version}.yaml"
    config = LabelConfig.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))
    if config.label_version != path.stem:
        raise ValueError(f"{path}: label_version must match the file name")
    return config
