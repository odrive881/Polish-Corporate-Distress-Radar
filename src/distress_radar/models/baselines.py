"""Fixed-coefficient classical models: Altman's Z'' and Polish discriminant functions (plan 0012).

A model's coefficients come from its publication, into `config/models/<version>.yaml`, with the
citation, the page they were read from and the hash of the document read: they change by
literature, not engineering (plan 0012 owner decision 4). `load_classical_model` checks a model
against the feature set it will score, so a model naming a ratio the set cannot supply fails at
load time rather than as a null score.
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from distress_radar.features.config import FeatureSet, RatioFeature
from distress_radar.parsing.canonical_schema import CONFIG_DIR

_NAME = r"^[a-z][a-z0-9_]*[a-z0-9]$"


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class Term(_Frozen):
    feature: str = Field(pattern=_NAME)
    coefficient: Decimal
    published_as: str = Field(min_length=1)  # the variable as the source defines it


class Zone(_Frozen):
    """A published score band, (lower, upper]; None is unbounded."""

    label: str = Field(pattern=_NAME)
    lower: Decimal | None
    upper: Decimal | None

    def contains(self, score: Decimal) -> bool:
        return (self.lower is None or score > self.lower) and (
            self.upper is None or score <= self.upper
        )


class Source(_Frozen):
    citation: str = Field(min_length=1)
    locator: str = Field(min_length=1)  # where in the document the coefficients are printed
    url: str = Field(min_length=1)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")  # the document the values were read from
    estimation_sample: str = Field(min_length=1)
    worked_example: str = Field(min_length=1)


class ClassicalModel(_Frozen):
    model: str = Field(pattern=_NAME)
    name: str = Field(min_length=1)
    higher_is_safer: bool  # stated, never defaulted: the sign convention differs by model
    intercept: Decimal
    terms: tuple[Term, ...] = Field(min_length=1)
    zones: tuple[Zone, ...]
    source: Source

    @model_validator(mode="after")
    def _consistent(self) -> ClassicalModel:
        features = [t.feature for t in self.terms]
        if len(set(features)) != len(features):
            raise ValueError(f"{self.model}: a feature appears in two terms")
        if not self.zones:
            return self
        # Zones tile the line: the first unbounded below, each starting where the last ended.
        if self.zones[0].lower is not None or self.zones[-1].upper is not None:
            raise ValueError(f"{self.model}: zones must cover every score")
        for below, above in zip(self.zones, self.zones[1:], strict=False):
            if below.upper is None or below.upper != above.lower:
                raise ValueError(f"{self.model}: zones {below.label} and {above.label} do not meet")
        return self

    def check_against(self, feature_set: FeatureSet) -> None:
        """Every term reads a ratio feature of the set."""
        ratios = {f.name for f in feature_set.features if isinstance(f, RatioFeature)}
        missing = sorted(t.feature for t in self.terms if t.feature not in ratios)
        if missing:
            raise ValueError(
                f"{self.model}: not ratio features of {feature_set.feature_set_version}: {missing}"
            )

    def zone(self, score: Decimal) -> str | None:
        """The published zone a score falls in; None when the source publishes none."""
        return next((z.label for z in self.zones if z.contains(score)), None)


def load_classical_model(
    version: str, feature_set: FeatureSet, config_dir: Path = CONFIG_DIR
) -> ClassicalModel:
    path = config_dir / "models" / f"{version}.yaml"
    model = ClassicalModel.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))
    if model.model != path.stem:
        raise ValueError(f"{path}: `model: {model.model}` must match the file name")
    model.check_against(feature_set)
    return model
