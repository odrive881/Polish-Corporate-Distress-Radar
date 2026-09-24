"""Typed views of `config/features/` (plan 0010 step B): the line-item map and the feature set.

`load_feature_set(version)` loads a feature set with everything it references and cross-checks
them, so a bad edit fails at load time rather than as a wrong or silently null feature:
- every chart code the line-item map names exists, and no statement body carries two codes of one
  input in one statement and variant section, so each input has one value per statement;
- every feature's inputs are ones its family can supply: line items for ratios, a KSH rule that
  applies to the feature set's legal form, event types the procedure taxonomy defines;
- names are unique, and a volatility names a ratio feature;
- `include_quarantined_statements` is stated, never defaulted (plan 0010 owner decision 3).

`feature_set_hash` hashes the files that shape the output, like a structure spec hash.
"""

from __future__ import annotations

import hashlib
from collections import Counter
from pathlib import Path
from typing import Annotated, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from distress_radar.features.statutory import (
    FilingDeadlines,
    KshTripwires,
    LegalForm,
    load_filing_deadlines,
    load_ksh_tripwires,
)
from distress_radar.parsing.canonical_schema import CONFIG_DIR, MappingConfig, load_mapping_config
from distress_radar.parsing.legal_taxonomy import (
    EventOutcomeClass,
    ProcedureTaxonomy,
    Stage,
    load_procedure_taxonomy,
)
from distress_radar.parsing.mapping_engine import VARIANT_SECTIONS

Family = Literal["financial", "construction", "tripwire", "filing", "registry", "legal_history"]
FilingMetric = Literal[
    "days_to_file_latest",
    "missing_years",
    "late_filings",
    "corrections",
    "latest_filed_as_pdf",
    "statements_quarantined",
    "restating_filings",
    "restatement_max_to_assets",
]
# Metrics counted over fiscal years, which take a lookback; the rest describe a whole history.
_LOOKBACK_METRICS: frozenset[str] = frozenset({"missing_years", "late_filings"})

_NAME = r"^[a-z][a-z0-9_]*[a-z0-9]$"

# The families that can supply each kind's inputs.
KIND_FAMILIES: dict[str, tuple[Family, ...]] = {
    "ratio": ("financial", "construction"),
    "growth": ("financial",),
    "volatility": ("financial",),
    "tripwire": ("tripwire",),
    "below_zero": ("tripwire",),
    "filing": ("filing",),
    "event_count": ("registry", "legal_history"),
}


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


# --- line_items_*.yaml ---------------------------------------------------------------------------


class LineItems(_Frozen):
    version: str
    inputs: dict[str, tuple[str, ...]]
    flows: tuple[str, ...]  # measured over the period (plan 0010 owner decision 7)

    @model_validator(mode="after")
    def _non_empty(self) -> LineItems:
        empty = [name for name, codes in self.inputs.items() if not codes]
        if empty:
            raise ValueError(f"inputs with no codes: {empty}")
        unknown = sorted(set(self.flows) - set(self.inputs))
        if unknown:
            raise ValueError(f"flows that are not inputs: {unknown}")
        dupes = [
            c for c, n in Counter(c for cs in self.inputs.values() for c in cs).items() if n > 1
        ]
        if dupes:
            raise ValueError(f"codes named by two inputs: {dupes}")
        return self

    def check_against(self, mapping: MappingConfig) -> None:
        """Every code is in the chart; no statement section carries two codes of one input."""
        chart = mapping.chart.by_code()
        unknown = sorted(c for cs in self.inputs.values() for c in cs if c not in chart)
        if unknown:
            raise ValueError(f"{self.version}: codes not in the canonical chart {unknown}")
        for name, codes in self.inputs.items():
            types = {chart[c].statement_type for c in codes}
            if len(types) > 1:
                raise ValueError(f"{self.version}: {name} mixes statement types {sorted(types)}")
            if (types == {"income_statement"}) != (name in self.flows):
                raise ValueError(
                    f"{self.version}: {name} must be a flow exactly when it is an income-statement "
                    "input"
                )
            for body in mapping.bodies.values():
                for statement, items in body.statements.items():
                    by_section: dict[str, list[str]] = {}
                    variants = VARIANT_SECTIONS.get(statement, ())
                    for item in items:
                        if item.code in codes:
                            top = item.parts[0]
                            by_section.setdefault(top if top in variants else "", []).append(
                                item.code
                            )
                    for section, found in by_section.items():
                        if len(found) > 1:
                            raise ValueError(
                                f"{self.version}: {body.body}/{statement}/{section or '-'} carries "
                                f"{found}, all for {name}"
                            )

    def coverage(self, mapping: MappingConfig) -> dict[str, set[str]]:
        """input → the bodies that can carry it."""
        return {
            name: {b.body for b in mapping.bodies.values() if b.codes() & set(codes)}
            for name, codes in self.inputs.items()
        }


def load_line_items(version: str, config_dir: Path = CONFIG_DIR) -> LineItems:
    path = config_dir / "features" / f"{version}.yaml"
    items = LineItems.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))
    if items.version != path.stem:
        raise ValueError(f"{path}: `version: {items.version}` must match the file name")
    return items


# --- feature_set_*.yaml --------------------------------------------------------------------------


class _Feature(_Frozen):
    name: str = Field(pattern=_NAME)
    family: Family


class RatioFeature(_Feature):
    kind: Literal["ratio"]
    numerator: dict[str, Literal[1, -1]] = Field(min_length=1)
    denominator: dict[str, Literal[1, -1]] = Field(min_length=1)
    denominator_rule: Literal["nonzero", "positive"] = "nonzero"
    scale: int = Field(default=1, ge=1)

    def inputs(self) -> set[str]:
        return set(self.numerator) | set(self.denominator)


class GrowthFeature(_Feature):
    kind: Literal["growth"]
    input: str
    years: int = Field(ge=1, le=10)

    def inputs(self) -> set[str]:
        return {self.input}


class VolatilityFeature(_Feature):
    kind: Literal["volatility"]
    of: str
    years: int = Field(ge=2, le=10)

    def inputs(self) -> set[str]:
        return set()


class TripwireFeature(_Feature):
    kind: Literal["tripwire"]
    rule: str

    def inputs(self) -> set[str]:
        return set()


class BelowZeroFeature(_Feature):
    kind: Literal["below_zero"]
    input: str

    def inputs(self) -> set[str]:
        return {self.input}


class FilingFeature(_Feature):
    kind: Literal["filing"]
    metric: FilingMetric
    lookback_years: int | None = Field(default=None, ge=1, le=20)

    @model_validator(mode="after")
    def _lookback(self) -> FilingFeature:
        if (self.metric in _LOOKBACK_METRICS) != (self.lookback_years is not None):
            raise ValueError(
                f"{self.name}: `lookback_years` is required for {sorted(_LOOKBACK_METRICS)} "
                "and not allowed for other metrics"
            )
        return self

    def inputs(self) -> set[str]:
        return set()


class EventCountFeature(_Feature):
    kind: Literal["event_count"]
    # Either named event types, or a filter on the taxonomy's stage and classes.
    event_types: tuple[str, ...] = ()
    stage: Stage | None = None
    outcome_class: EventOutcomeClass | None = None
    ends: EventOutcomeClass | None = None
    window_months: int | None = Field(ge=1, le=240)  # None: ever; stated, never defaulted

    @model_validator(mode="after")
    def _one_selector(self) -> EventCountFeature:
        by_filter = (
            self.stage is not None or self.outcome_class is not None or self.ends is not None
        )
        if bool(self.event_types) == by_filter:
            raise ValueError(f"{self.name}: give `event_types` or a stage/class filter, not both")
        return self

    def inputs(self) -> set[str]:
        return set()

    def matches(self, taxonomy: ProcedureTaxonomy) -> set[str]:
        """The event types this feature counts."""
        if self.event_types:
            return set(self.event_types)
        return {
            e.event_type
            for e in taxonomy.event_types
            if (self.stage is None or e.stage == self.stage)
            and (self.outcome_class is None or e.outcome_class == self.outcome_class)
            and (self.ends is None or self.ends in e.ends)
        }


Feature = Annotated[
    RatioFeature
    | GrowthFeature
    | VolatilityFeature
    | TripwireFeature
    | BelowZeroFeature
    | FilingFeature
    | EventCountFeature,
    Field(discriminator="kind"),
]


class PeriodBand(_Frozen):
    min: int = Field(ge=1)
    max: int = Field(ge=1)

    @model_validator(mode="after")
    def _ordered(self) -> PeriodBand:
        if self.max < self.min:
            raise ValueError("period_length_days: max below min")
        return self

    def covers(self, days: int | None) -> bool:
        return days is not None and self.min <= days <= self.max


class FeatureSet(_Frozen):
    feature_set_version: str
    line_items: str
    legal_form: LegalForm
    include_quarantined_statements: bool  # required: the owner's switch is always explicit
    period_length_days: PeriodBand  # plan 0010 owner decision 7
    lag_tolerance_days: int = Field(ge=0, le=183)
    features: tuple[Feature, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def _consistent(self) -> FeatureSet:
        names = [f.name for f in self.features]
        dupes = [n for n, k in Counter(names).items() if k > 1]
        if dupes:
            raise ValueError(f"duplicate feature names: {dupes}")
        clashing = [n for n in names if n.endswith("__known_from")]
        if clashing:
            raise ValueError(f"names reserved for companion columns: {clashing}")
        ratios = {f.name for f in self.features if isinstance(f, RatioFeature)}
        for f in self.features:
            if f.family not in KIND_FAMILIES[f.kind]:
                raise ValueError(f"{f.name}: a {f.family} feature cannot be a {f.kind}")
            if isinstance(f, VolatilityFeature) and f.of not in ratios:
                raise ValueError(f"{f.name}: `of: {f.of}` is not a ratio feature")
        return self

    def check_against(
        self,
        line_items: LineItems,
        tripwires: KshTripwires,
        taxonomy: ProcedureTaxonomy,
    ) -> None:
        """Every input a feature names can be supplied by the config its family reads."""
        if line_items.version != self.line_items:
            raise ValueError(f"loaded {line_items.version}, but the set names {self.line_items}")
        known_types = {e.event_type for e in taxonomy.event_types}
        flows = set(line_items.flows)
        for f in self.features:
            missing = f.inputs() - set(line_items.inputs)
            if missing:
                raise ValueError(f"{f.name}: inputs not in {line_items.version}: {sorted(missing)}")
            if isinstance(f, RatioFeature):
                for side, terms in (("numerator", f.numerator), ("denominator", f.denominator)):
                    if len({name in flows for name in terms}) > 1:
                        # A sum of a flow and a stock has no length to judge it by.
                        raise ValueError(f"{f.name}: the {side} mixes flows and stocks")
            if isinstance(f, TripwireFeature):
                rule = tripwires.rule(f.rule)
                if rule is None:
                    raise ValueError(f"{f.name}: unknown KSH rule {f.rule}")
                if self.legal_form not in rule.legal_forms:
                    raise ValueError(f"{f.name}: {f.rule} does not apply to {self.legal_form}")
                missing = rule.inputs() - set(line_items.inputs)
                if missing:
                    raise ValueError(
                        f"{f.name}: {f.rule} reads inputs not in {line_items.version}: "
                        f"{sorted(missing)}"
                    )
            if isinstance(f, EventCountFeature):
                unknown = set(f.event_types) - known_types
                if unknown:
                    raise ValueError(f"{f.name}: unknown event types {sorted(unknown)}")
                if not f.matches(taxonomy):
                    raise ValueError(f"{f.name}: the filter matches no event type")


class FeatureConfig(_Frozen):
    """A feature set with everything it references, cross-checked."""

    feature_set: FeatureSet
    line_items: LineItems
    tripwires: KshTripwires
    deadlines: FilingDeadlines
    taxonomy: ProcedureTaxonomy
    feature_set_hash: str


# The files that shape a build's output besides the feature set and its line-item map.
_STATUTORY_FILES = ("ksh_tripwires.yaml", "filing_deadlines.yaml", "procedure_taxonomy.yaml")


def feature_set_hash(feature_set: FeatureSet, config_dir: Path = CONFIG_DIR) -> str:
    """SHA-256 over the feature set, its line-item map and the statutory files it reads."""
    paths = [
        config_dir / "features" / f"{feature_set.feature_set_version}.yaml",
        config_dir / "features" / f"{feature_set.line_items}.yaml",
        *(config_dir / "statutory" / name for name in _STATUTORY_FILES),
    ]
    digest = hashlib.sha256()
    for path in paths:
        digest.update(path.name.encode())
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def load_feature_set(version: str, config_dir: Path = CONFIG_DIR) -> FeatureConfig:
    path = config_dir / "features" / f"{version}.yaml"
    feature_set = FeatureSet.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))
    if feature_set.feature_set_version != path.stem:
        raise ValueError(
            f"{path}: `feature_set_version: {feature_set.feature_set_version}` must match the "
            "file name"
        )
    line_items = load_line_items(feature_set.line_items, config_dir)
    line_items.check_against(load_mapping_config(config_dir))
    tripwires = load_ksh_tripwires(config_dir)
    taxonomy = load_procedure_taxonomy(config_dir)
    feature_set.check_against(line_items, tripwires, taxonomy)
    return FeatureConfig(
        feature_set=feature_set,
        line_items=line_items,
        tripwires=tripwires,
        deadlines=load_filing_deadlines(config_dir),
        taxonomy=taxonomy,
        feature_set_hash=feature_set_hash(feature_set, config_dir),
    )


# --- what a feature is ---------------------------------------------------------------------------

_BOOLEAN_METRICS: frozenset[str] = frozenset({"latest_filed_as_pdf"})
_COUNT_METRICS: frozenset[str] = frozenset(
    {
        "days_to_file_latest",
        "missing_years",
        "late_filings",
        "corrections",
        "statements_quarantined",
        "restating_filings",
    }
)


def feature_dtype(feature: Feature) -> Literal["boolean", "count", "float"]:
    """How a feature's value is stored in `feature_store`."""
    if isinstance(feature, TripwireFeature | BelowZeroFeature):
        return "boolean"
    if isinstance(feature, FilingFeature):
        if feature.metric in _BOOLEAN_METRICS:
            return "boolean"
        return "count" if feature.metric in _COUNT_METRICS else "float"
    return "count" if isinstance(feature, EventCountFeature) else "float"


def length_sensitive(feature: Feature, line_items: LineItems) -> bool:
    """Whether a feature depends on a period's length (plan 0010 owner decision 7)."""
    flows = set(line_items.flows)
    if isinstance(feature, RatioFeature):
        return (next(iter(feature.numerator)) in flows) != (
            next(iter(feature.denominator)) in flows
        )
    if isinstance(feature, GrowthFeature):
        return feature.input in flows
    return False
