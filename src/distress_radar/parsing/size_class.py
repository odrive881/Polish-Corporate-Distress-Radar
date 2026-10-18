"""`entity_size_class_history`: the statutory size class per fiscal year (AGENT_SPEC §4.4, §5).

Plan 0020. The class is computed from the filed figures, never from a registry and never from the
form the entity chose to file (§4.4). Three criteria, each tested on the fiscal year and the year
before it: total assets at the year's end, net revenue from sales of goods and products, and
average employment. The thresholds are dated config (`config/statutory/size_thresholds.yaml`),
and which filed lines give each criterion is config too (`config/mappings/size_class_inputs_v1.yaml`).

**Exact where decidable** (owner decision 1). Employment is filed only from wariant 2, and the
statute's revenue is one line only on the newest calculation forms, so a criterion is often known
only within bounds. Each criterion is placed in a *region* between the regime's three thresholds,
and an unknown one in every region its bounds allow. A class is given only when every combination
of those regions gives the same class; otherwise the year is `undetermined`, with the reason. So
a decided class is never one a missing figure could change. The years are tested independently of
each other's unknowns, which can leave a year `undetermined` that a joint test would decide, but
never decides one wrongly.

**Point in time** (§4.7). For each date on which a filing of the entity became known, every fiscal
year is classified from the filings known by then; a row is written for a year whenever its result
changes, with that date as `known_from`. A correction filed later is a new row, and the earlier one
stays. A year's class can depend on the year before's (the carry-over from FY2024), so a filing of
one year can add a row for the next.
"""

# polars's signatures reference types pyright cannot resolve; scoped to this module.
# pyright: reportUnknownMemberType=false

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field, replace
from datetime import date
from decimal import Decimal
from itertools import pairwise, product
from pathlib import Path
from typing import Literal

import polars as pl
import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from distress_radar.parsing.canonical_schema import CONFIG_DIR, MappingConfig

DATASET = "entity_size_class_history"

SizeClass = Literal["micro", "small", "medium", "large"]
CLASSES: tuple[SizeClass, ...] = ("micro", "small", "medium", "large")
UNDETERMINED = "undetermined"
Basis = Literal["statutory", "by_analogy"]
# Why a year has no class, in the order they are tested (plan 0020 decision 6).
REASONS = (
    "no_regime",  # the fiscal year starts before the first configured regime
    "no_prior_side",  # one year on each side of the top boundary, no earlier status to keep, and
    # nothing else unknown: the law defines neither medium nor large for that case
    "inputs_missing",  # neither employment nor exact revenue alone would decide it
    "employment_decides",  # it would be decided if employment were filed
    "revenue_bounds",  # it would be decided if revenue were exact
)

VALUE_DTYPE = pl.Decimal(precision=20, scale=2)
EMPLOYMENT_DTYPE = pl.Decimal(precision=12, scale=2)
SCHEMA: dict[str, pl.DataType | type[pl.DataType]] = {
    "krs": pl.String,
    "fiscal_year": pl.Int32,
    "period_start": pl.Date,
    "period_end": pl.Date,
    "size_class": pl.String,
    "size_class_basis": pl.String,
    "undetermined_reason": pl.String,
    "regime": pl.String,
    "first_year": pl.Boolean,
    "balance_sheet_total": VALUE_DTYPE,
    "net_revenue": VALUE_DTYPE,
    "net_revenue_low": VALUE_DTYPE,
    "net_revenue_high": VALUE_DTYPE,
    "revenue_rule": pl.String,
    "average_employment": EMPLOYMENT_DTYPE,
    "prior_balance_sheet_total": VALUE_DTYPE,
    "prior_net_revenue_low": VALUE_DTYPE,
    "prior_net_revenue_high": VALUE_DTYPE,
    "prior_revenue_rule": pl.String,
    "prior_average_employment": EMPLOYMENT_DTYPE,
    "threshold_config_version": pl.String,
    "inputs_config_version": pl.String,
    "known_from": pl.Date,
    "document_ref": pl.String,
    "source_document_hash": pl.String,
    "source_member": pl.String,
    "ingestion_run_id": pl.String,
}
SORT = ["krs", "fiscal_year", "known_from"]

_EMPLOYMENT_ITEM = "average_employment"
_REGISTERED = "registered"


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


# --- size_thresholds.yaml ------------------------------------------------------------------------


class Boundary(_Frozen):
    class_below: Literal["micro", "small", "medium"]
    basis: Basis
    total_assets: Decimal = Field(gt=0)
    net_revenue: Decimal = Field(gt=0)
    average_employment: Decimal = Field(gt=0)


class YearRange(_Frozen):
    from_: date = Field(alias="from")
    to: date | None = None  # inclusive; None while in force

    model_config = ConfigDict(frozen=True, extra="forbid", populate_by_name=True)

    def covers(self, day: date) -> bool:
        return self.from_ <= day and (self.to is None or day <= self.to)


class Regime(_Frozen):
    id: str
    fiscal_years_starting: YearRange
    carry_over: bool
    citation: str = Field(min_length=1)
    boundaries: tuple[Boundary, Boundary, Boundary]

    @model_validator(mode="after")
    def _ordered(self) -> Regime:
        if tuple(b.class_below for b in self.boundaries) != CLASSES[:3]:
            raise ValueError(f"{self.id}: boundaries must be micro, small, medium, in that order")
        for low, high in pairwise(self.boundaries):
            for criterion in ("total_assets", "net_revenue", "average_employment"):
                if getattr(low, criterion) >= getattr(high, criterion):
                    raise ValueError(f"{self.id}: {criterion} thresholds must increase")
        r = self.fiscal_years_starting
        if r.to is not None and r.to < r.from_:
            raise ValueError(f"{self.id}: `to` before `from`")
        return self

    def thresholds(self, criterion: str) -> tuple[Decimal, Decimal, Decimal]:
        a, b, c = (getattr(x, criterion) for x in self.boundaries)
        return (a, b, c)


class SizeThresholds(_Frozen):
    version: str
    regimes: tuple[Regime, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def _contiguous(self) -> SizeThresholds:
        dupes = [i for i, n in Counter(r.id for r in self.regimes).items() if n > 1]
        if dupes:
            raise ValueError(f"duplicate regime ids: {dupes}")
        ordered = sorted(self.regimes, key=lambda r: r.fiscal_years_starting.from_)
        for earlier, later in pairwise(ordered):
            end = earlier.fiscal_years_starting.to
            if end is None:
                raise ValueError(f"{earlier.id} is open-ended but {later.id} follows it")
            if date.fromordinal(end.toordinal() + 1) != later.fiscal_years_starting.from_:
                raise ValueError(f"{earlier.id} and {later.id} leave a gap or overlap")
        return self

    def regime_for(self, period_start: date) -> Regime | None:
        """The regime for a fiscal year starting on `period_start`; None before the first one."""
        return next((r for r in self.regimes if r.fiscal_years_starting.covers(period_start)), None)


def load_size_thresholds(config_dir: Path = CONFIG_DIR) -> SizeThresholds:
    path = config_dir / "statutory" / "size_thresholds.yaml"
    return SizeThresholds.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))


# --- size_class_inputs_v<n>.yaml -----------------------------------------------------------------

Term = tuple[Literal[1, -1], str]


class RevenueRule(_Frozen):
    id: str
    when: tuple[str, ...] = Field(min_length=1)
    low: tuple[Term, ...]
    high: tuple[Term, ...] | None

    @model_validator(mode="after")
    def _terms_are_required(self) -> RevenueRule:
        terms = {c for _, c in self.low} | {c for _, c in self.high or ()}
        missing = terms - set(self.when)
        if missing:
            raise ValueError(f"{self.id}: a bound uses codes not in `when` {sorted(missing)}")
        if any(sign < 0 for sign, _ in self.low):
            raise ValueError(f"{self.id}: a lower bound adds lines, never subtracts them")
        return self


class SizeClassInputs(_Frozen):
    version: str
    total_assets: str
    revenue_rules: tuple[RevenueRule, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def _unique(self) -> SizeClassInputs:
        dupes = [i for i, n in Counter(r.id for r in self.revenue_rules).items() if n > 1]
        if dupes:
            raise ValueError(f"duplicate revenue rule ids: {dupes}")
        return self

    def codes(self) -> set[str]:
        return {self.total_assets} | {c for r in self.revenue_rules for c in r.when}


def carried_code_sets(mapping: MappingConfig) -> list[set[str]]:
    """The codes each statement body can carry under each structure spec, overrides applied."""
    sets: list[set[str]] = []
    for spec in mapping.specs.values():
        for body in sorted(spec.bodies_used):
            codes = mapping.bodies[body].codes()
            sets.append({spec.code_overrides.get(c, c) for c in codes})
    return sets


def load_size_class_inputs(
    mapping: MappingConfig, config_dir: Path = CONFIG_DIR, version: str = "size_class_inputs_v1"
) -> SizeClassInputs:
    """The inputs config, refused if a code is not in the chart or a rule's codes are never
    carried together by one body: such a rule could never apply."""
    path = config_dir / "mappings" / f"{version}.yaml"
    inputs = SizeClassInputs.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))
    if inputs.version != version:
        raise ValueError(f"{path.name}: version must be {version}")
    unknown = inputs.codes() - set(mapping.chart.by_code())
    if unknown:
        raise ValueError(f"{path.name}: codes not in the chart {sorted(unknown)}")
    carried = carried_code_sets(mapping)
    if not all(inputs.total_assets in s for s in carried):
        raise ValueError(f"{path.name}: {inputs.total_assets} is not on every body")
    never = [r.id for r in inputs.revenue_rules if not any(set(r.when) <= s for s in carried)]
    if never:
        raise ValueError(f"{path.name}: rules no body can satisfy {never}")
    return inputs


# --- bounds and regions --------------------------------------------------------------------------


@dataclass(frozen=True)
class Bounds:
    """A criterion's value, known exactly (`low == high`), within bounds, or not at all
    (`low` 0, `high` None: unbounded above). Every criterion is non-negative."""

    low: Decimal = Decimal(0)
    high: Decimal | None = None
    rule: str | None = None  # the revenue rule that gave them

    @classmethod
    def exact(cls, value: Decimal) -> Bounds:
        return cls(value, value)

    @property
    def value(self) -> Decimal | None:
        return self.low if self.high is not None and self.low == self.high else None

    def regions(self, thresholds: tuple[Decimal, Decimal, Decimal]) -> frozenset[int]:
        """The regions the value can lie in: 0 at or below the first threshold, 1 above it and
        at or below the second, 2 below or at the third, 3 above all three."""
        found: set[int] = set()
        edges: list[Decimal | None] = [None, *thresholds]
        for region, (above, at_most) in enumerate(zip(edges, [*thresholds, None], strict=True)):
            # (above, at_most] meets [low, high]?
            if above is not None and self.high is not None and self.high <= above:
                continue
            if at_most is not None and self.low > at_most:
                continue
            found.add(region)
        return frozenset(found)


def revenue_bounds(codes: Mapping[str, Decimal], inputs: SizeClassInputs) -> Bounds:
    """The first rule whose codes the statement carries gives the bounds; none gives none."""
    for rule in inputs.revenue_rules:
        if all(c in codes for c in rule.when):
            low = sum((sign * codes[c] for sign, c in rule.low), Decimal(0))
            high = (
                None
                if rule.high is None
                else sum((sign * codes[c] for sign, c in rule.high), Decimal(0))
            )
            if high is not None and high < low:
                # The lines contradict the reasoning behind the rule (a negative line where only
                # a non-negative one was expected): bound nothing rather than something false.
                return Bounds(rule=rule.id)
            return Bounds(max(low, Decimal(0)), high, rule.id)
    return Bounds()


@dataclass(frozen=True)
class YearFigures:
    """What one year contributes to a test: its three criteria."""

    total_assets: Bounds
    revenue: Bounds
    employment: Bounds

    def levels(
        self, regime: Regime, employment: int | None = None, revenue: int | None = None
    ) -> frozenset[int]:
        """The boundaries the year can be within, as a level: the first boundary (0 micro, 1
        small, 2 medium) at which at most one criterion exceeds its threshold, or 3 above all.
        `employment` and `revenue` pin that criterion to one region (for the reasons)."""
        assets = self.total_assets.regions(regime.thresholds("total_assets"))
        rev = self.revenue.regions(regime.thresholds("net_revenue"))
        emp = self.employment.regions(regime.thresholds("average_employment"))
        if revenue is not None:
            rev = rev & {revenue}
        if employment is not None:
            emp = emp & {employment}
        found: set[int] = set()
        for regions in product(assets, rev, emp):
            # within boundary k when two criteria lie in a region <= k
            found.add(next((k for k in range(3) if sum(r <= k for r in regions) >= 2), 3))
        return frozenset(found)


# --- the test ------------------------------------------------------------------------------------

GAP = "gap"  # one year on each side of the top boundary, and no status to keep: the law is silent


def _side(level_now: int, level_before: int | None, k: int, prior: SizeClass | None) -> str:
    """`below`, `above` or GAP at boundary k. `level_before` None is the first year of activity,
    tested on itself; `prior` is the class carried from the year before, if the regime carries."""
    within_now = level_now <= k
    if level_before is None:
        return "below" if within_now else "above"
    within_before = level_before <= k
    if within_now == within_before:
        return "below" if within_now else "above"
    if prior is not None:
        return "below" if CLASSES.index(prior) <= k else "above"
    return GAP if k == 2 else "above"


def _class(level_now: int, level_before: int | None, prior: SizeClass | None) -> str:
    for k in range(3):
        side = _side(level_now, level_before, k, prior)
        if side == GAP:
            return GAP
        if side == "below":
            return CLASSES[k]
    return "large"


def outcomes(
    now: frozenset[int],
    before: frozenset[int] | None,
    priors: frozenset[SizeClass | None],
) -> frozenset[str]:
    """Every class the combinations of possible levels and carried classes give."""
    befores: Iterable[int | None] = [None] if before is None else sorted(before)
    return frozenset(
        _class(n, b, p) for n, b, p in product(sorted(now), befores, sorted(priors, key=str))
    )


@dataclass(frozen=True)
class Result:
    size_class: str  # one of CLASSES, or UNDETERMINED
    reason: str | None = None
    basis: Basis | None = None
    # The classes still possible, for the next year's carry-over when this one is undetermined.
    possible: frozenset[str] = field(default_factory=frozenset[str])


def classify_year(
    regime: Regime | None,
    now: YearFigures,
    before: YearFigures | None,
    priors: frozenset[SizeClass | None],
) -> Result:
    """One fiscal year. `before` None is the first year of activity. `priors` holds the classes
    the year before could have under the same carrying regime, or `{None}` when nothing carries."""
    if regime is None:
        return Result(UNDETERMINED, "no_regime")
    found = outcomes(now.levels(regime), None if before is None else before.levels(regime), priors)
    if len(found) == 1 and GAP not in found:
        decided = next(iter(found))
        analogy = decided in ("medium", "large") and regime.boundaries[2].basis == "by_analogy"
        return Result(decided, None, "by_analogy" if analogy else "statutory", found)
    return Result(UNDETERMINED, _reason(regime, now, before, priors, found), None, found)


def _decided_when_pinned(
    regime: Regime,
    now: YearFigures,
    before: YearFigures | None,
    priors: frozenset[SizeClass | None],
    criterion: Literal["employment", "revenue"],
) -> bool:
    """Whether the class would be decided were that criterion known in both years."""
    years = [now] if before is None else [now, before]
    pins = [
        sorted(getattr(y, criterion).regions(regime.thresholds(_CRITERION[criterion])))
        for y in years
    ]
    for chosen in product(*pins):
        levels = [
            y.levels(regime, employment=r)
            if criterion == "employment"
            else y.levels(regime, revenue=r)
            for y, r in zip(years, chosen, strict=True)
        ]
        found = outcomes(levels[0], levels[1] if before is not None else None, priors)
        if len(found) != 1 or GAP in found:
            return False
    return True


_CRITERION = {"employment": "average_employment", "revenue": "net_revenue"}


def _reason(
    regime: Regime,
    now: YearFigures,
    before: YearFigures | None,
    priors: frozenset[SizeClass | None],
    found: frozenset[str],
) -> str:
    if found == frozenset({GAP}):
        return "no_prior_side"
    if _decided_when_pinned(regime, now, before, priors, "employment"):
        return "employment_decides"
    if _decided_when_pinned(regime, now, before, priors, "revenue"):
        return "revenue_bounds"
    return "inputs_missing"


# --- assembling the years from the datasets ------------------------------------------------------


@dataclass(frozen=True)
class Filing:
    """One statement of one fiscal year, with the criteria it gives for that year and the one
    before (plan 0020 decision 3)."""

    krs: str
    fiscal_year: int
    period_start: date
    period_end: date
    known_from: date
    document_ref: str
    source_document_hash: str
    source_member: str
    ingestion_run_id: str
    current: YearFigures
    prior: YearFigures  # its employment is never filed: taken from the year before's own filing

    @property
    def order(self) -> tuple[date, str, str]:
        return (self.known_from, self.source_document_hash, self.source_member)


def _figures(
    codes: Mapping[str, Decimal], inputs: SizeClassInputs, employment: Decimal | None
) -> YearFigures:
    assets = codes.get(inputs.total_assets)
    return YearFigures(
        Bounds() if assets is None else Bounds.exact(assets),
        revenue_bounds(codes, inputs),
        Bounds() if employment is None else Bounds.exact(employment),
    )


_FILING_KEY = ["source_document_hash", "source_member"]


def filings(
    canonical: pl.DataFrame, disclosures: pl.DataFrame, inputs: SizeClassInputs
) -> list[Filing]:
    """The statements graded `pass` or `warn` (plan 0020 decision 4), each with its own year's
    criteria and the year before's, from `prior_year_restated` where filed, else `prior_year`."""
    facts = canonical.filter(
        (pl.col("quality_grade") != "quarantined") & pl.col("line_item").is_in(inputs.codes())
    )
    heads = (
        canonical.filter(pl.col("quality_grade") != "quarantined")
        .group_by(_FILING_KEY)
        .agg(
            pl.col("krs").first(),
            pl.col("fiscal_year").first(),
            pl.col("period_start").first(),
            pl.col("period_end").first(),
            pl.col("known_from").first(),
            pl.col("document_ref").first(),
            pl.col("ingestion_run_id").first(),
        )
    )
    employment = {
        (r["source_document_hash"], r["source_member"]): r["value_number"]
        for r in disclosures.filter(
            (pl.col("item") == _EMPLOYMENT_ITEM) & pl.col("value_number").is_not_null()
        ).iter_rows(named=True)
    }
    by_filing: dict[tuple[str, str], dict[str, dict[str, Decimal]]] = {}
    for r in facts.iter_rows(named=True):
        if r["value"] is None:
            continue
        columns = by_filing.setdefault((r["source_document_hash"], r["source_member"]), {})
        columns.setdefault(r["column"], {})[r["line_item"]] = r["value"]
    result: list[Filing] = []
    for h in heads.sort(_FILING_KEY).iter_rows(named=True):
        key = (h["source_document_hash"], h["source_member"])
        columns = by_filing.get(key, {})
        prior = {**columns.get("prior_year", {}), **columns.get("prior_year_restated", {})}
        result.append(
            Filing(
                krs=h["krs"],
                fiscal_year=int(h["fiscal_year"]),
                period_start=h["period_start"],
                period_end=h["period_end"],
                known_from=h["known_from"],
                document_ref=h["document_ref"],
                source_document_hash=h["source_document_hash"],
                source_member=h["source_member"],
                ingestion_run_id=h["ingestion_run_id"],
                current=_figures(columns.get("current_year", {}), inputs, employment.get(key)),
                prior=_figures(prior, inputs, None),
            )
        )
    return result


def registrations(legal_events: pl.DataFrame) -> dict[str, tuple[date, date]]:
    """Each entity's KRS registration: (event date, known from). Without a date it cannot place
    the first year of activity, and is left out."""
    found: dict[str, tuple[date, date]] = {}
    rows = legal_events.filter(
        (pl.col("event_type") == _REGISTERED) & pl.col("event_date").is_not_null()
    ).sort(["krs", "event_date", "known_from"])
    for r in rows.iter_rows(named=True):
        found.setdefault(r["krs"], (r["event_date"], r["known_from"]))
    return found


def _row(
    filing: Filing,
    regime: Regime | None,
    result: Result,
    first_year: bool,
    prior: YearFigures,
    known_from: date,
    versions: tuple[str, str],
) -> dict[str, object]:
    now = filing.current
    return {
        "krs": filing.krs,
        "fiscal_year": filing.fiscal_year,
        "period_start": filing.period_start,
        "period_end": filing.period_end,
        "size_class": result.size_class,
        "size_class_basis": result.basis,
        "undetermined_reason": result.reason,
        "regime": None if regime is None else regime.id,
        "first_year": first_year,
        "balance_sheet_total": now.total_assets.value,
        "net_revenue": now.revenue.value,
        "net_revenue_low": now.revenue.low if now.revenue.rule is not None else None,
        "net_revenue_high": now.revenue.high,
        "revenue_rule": now.revenue.rule,
        "average_employment": now.employment.value,
        "prior_balance_sheet_total": None if first_year else prior.total_assets.value,
        "prior_net_revenue_low": (
            prior.revenue.low if not first_year and prior.revenue.rule is not None else None
        ),
        "prior_net_revenue_high": None if first_year else prior.revenue.high,
        "prior_revenue_rule": None if first_year else prior.revenue.rule,
        "prior_average_employment": None if first_year else prior.employment.value,
        "threshold_config_version": versions[0],
        "inputs_config_version": versions[1],
        "known_from": known_from,
        "document_ref": filing.document_ref,
        "source_document_hash": filing.source_document_hash,
        "source_member": filing.source_member,
        "ingestion_run_id": filing.ingestion_run_id,
    }


def _entity_rows(
    entity: list[Filing],
    thresholds: SizeThresholds,
    registration: tuple[date, date] | None,
    versions: tuple[str, str],
) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    last: dict[int, dict[str, object]] = {}
    for day in sorted({f.known_from for f in entity}):
        latest: dict[int, Filing] = {}
        for f in entity:
            if f.known_from <= day and (
                f.fiscal_year not in latest or f.order > latest[f.fiscal_year].order
            ):
                latest[f.fiscal_year] = f
        results: dict[int, tuple[Regime | None, Result]] = {}
        for year in sorted(latest):
            filing = latest[year]
            regime = thresholds.regime_for(filing.period_start)
            first_year = (
                registration is not None
                and registration[1] <= day
                and filing.period_start <= registration[0] <= filing.period_end
            )
            earlier = latest.get(year - 1)
            # The year before's employment comes from its own filing (no form carries it).
            employment = earlier.current.employment if earlier is not None else Bounds()
            prior = replace(filing.prior, employment=employment)
            carried = results.get(year - 1)
            priors: frozenset[SizeClass | None] = frozenset({None})
            if (
                regime is not None
                and regime.carry_over
                and carried is not None
                and carried[0] is not None
                and carried[0].id == regime.id
            ):
                possible = carried[1].possible - {GAP}
                if carried[1].possible and GAP not in carried[1].possible:
                    priors = frozenset(c for c in CLASSES if c in possible)
                elif possible:
                    priors = frozenset({None, *(c for c in CLASSES if c in possible)})
            result = classify_year(regime, filing.current, None if first_year else prior, priors)
            results[year] = (regime, result)
            row = _row(filing, regime, result, first_year, prior, day, versions)
            content = {k: v for k, v in row.items() if k != "known_from"}
            if last.get(year) != content:
                last[year] = content
                rows.append(row)
    return rows


def entity_size_class_history(
    canonical: pl.DataFrame,
    disclosures: pl.DataFrame,
    legal_events: pl.DataFrame,
    thresholds: SizeThresholds,
    inputs: SizeClassInputs,
) -> pl.DataFrame:
    """The dataset: one row per entity, fiscal year and date its class (or why it has none)
    changed."""
    by_entity: dict[str, list[Filing]] = {}
    for f in filings(canonical, disclosures, inputs):
        by_entity.setdefault(f.krs, []).append(f)
    registered = registrations(legal_events)
    versions = (thresholds.version, inputs.version)
    rows = [
        row
        for krs in sorted(by_entity)
        for row in _entity_rows(by_entity[krs], thresholds, registered.get(krs), versions)
    ]
    return pl.DataFrame(rows, schema=SCHEMA).sort(SORT)
