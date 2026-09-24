"""Feature families (plan 0010 step D; AGENT_SPEC §6H): one function per family.

Each family takes the grid (`krs`, `as_of_date`) and returns long rows
(`krs`, `as_of_date`, `feature`, `value`, `known_from`), one per non-null feature value, where
`known_from` is the latest `known_from` of every fact the value was built from (plan 0010
decision 2). A feature with no row is null; step E pivots the rows into `feature_store`.

**Nothing after `as_of_date` is read.** The financial families are computed once per entity at
each date its known figures change (a *snapshot*), from the panel versions known by then, and
a DuckDB `ASOF JOIN` gives each grid row the latest snapshot at or before its `as_of_date`. The
join is on the snapshot, never per feature, so a feature that became null never shows an older
value. The filing and event families change when a deadline passes or a window slides, not
only when data arrives, so they are evaluated per grid row from the facts known by then.

**Counts and their dates.** A count of zero is a value, not a missing one: it is known once the
source has been seen for the entity. Its `known_from` is then the latest fact seen from that source
(a statement filing, or a legal event such as the registration), and it is null only when no fact
from the source is known yet.

**Events are counted by a key that no later fact can change**: the event type and its date (the
decision date, or `known_from` when there is none). `dedup_group_id` is not used: it is computed
over every row, so a notice published later could merge two groups an observer saw as two.
"""

# polars's and duckdb's signatures reference types pyright cannot resolve; scoped to this module.
# pyright: reportUnknownMemberType=false

from __future__ import annotations

import statistics
from bisect import bisect_right
from collections.abc import Callable, Iterable, Iterator, Mapping
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from fractions import Fraction

import duckdb
import polars as pl

from distress_radar.features.config import (
    BelowZeroFeature,
    EventCountFeature,
    Family,
    Feature,
    FeatureConfig,
    FilingFeature,
    GrowthFeature,
    RatioFeature,
    TripwireFeature,
    VolatilityFeature,
    length_sensitive,
)
from distress_radar.features.panel import TOTAL_ASSETS
from distress_radar.features.statutory import add_months

GRID_SCHEMA: dict[str, pl.DataType | type[pl.DataType]] = {
    "krs": pl.String,
    "as_of_date": pl.Date,
}
FEATURE_VALUES_SCHEMA: dict[str, pl.DataType | type[pl.DataType]] = {
    "krs": pl.String,
    "as_of_date": pl.Date,
    "feature": pl.String,
    "value": pl.Float64,
    "known_from": pl.Date,
}
FEATURE_VALUES_SORT_KEY = ["krs", "as_of_date", "feature"]

# One row per statement filing: its parts (pre-2018 filings came as separate PDF documents) are
# one filing. Built by `statement_filings` from `filing_index`, `parsed_documents` and the grades.
STATEMENT_FILINGS_SCHEMA: dict[str, pl.DataType | type[pl.DataType]] = {
    "krs": pl.String,
    "period_start": pl.Date,
    "period_end": pl.Date,
    "known_from": pl.Date,
    "deleted_on": pl.Date,
    "is_correction": pl.Boolean,
    "is_pdf": pl.Boolean,
    "is_quarantined": pl.Boolean,
}

# `filing_index` columns `statement_filings` reads, and `parsed_documents` ones.
FILING_INDEX_COLUMNS = [
    "krs",
    "document_ref",
    "rdf_type_code",
    "period_start",
    "period_end",
    "submission_date",
    "deleted_on",
    "is_correction",
    "file_name",
]
PARSE_STATUS_COLUMNS = ["krs", "document_ref", "status"]


@dataclass(frozen=True)
class FeatureInputs:
    panel: pl.DataFrame  # features.panel.PANEL_SCHEMA
    filings: pl.DataFrame  # STATEMENT_FILINGS_SCHEMA
    restatements: pl.DataFrame  # parsing.accounting_identities.RESTATEMENT_SCHEMA
    legal_events: pl.DataFrame  # parsing.legal_events.LEGAL_EVENTS_SCHEMA


Row = tuple[str, date, str, float, date]


def _frame(rows: Iterable[Row]) -> pl.DataFrame:
    return pl.DataFrame(list(rows), schema=FEATURE_VALUES_SCHEMA, orient="row").sort(
        FEATURE_VALUES_SORT_KEY
    )


def _features(config: FeatureConfig, family: Family) -> list[Feature]:
    return [f for f in config.feature_set.features if f.family == family]


def _grid_by_entity(grid: pl.DataFrame) -> dict[str, list[date]]:
    out: dict[str, list[date]] = {}
    for krs, as_of in grid.select("krs", "as_of_date").unique().sort(["krs", "as_of_date"]).rows():
        out.setdefault(krs, []).append(as_of)
    return out


# --- statement filings ---------------------------------------------------------------------------


def statement_filings(
    filing_index: pl.DataFrame,
    parse_status: pl.DataFrame,
    canonical: pl.DataFrame,
    statement_type_codes: Iterable[str],
) -> pl.DataFrame:
    """Statement filings with what the filing features need (`STATEMENT_FILINGS_SCHEMA`).

    `statement_type_codes`: the RDF type codes `config/mappings/rdf_document_types.yaml` marks
    `canonical: statement`. A filing with no submission date (a row never expanded) cannot be
    dated, so it is not known at any `as_of_date` and is left out.
    """
    status = parse_status.group_by(["krs", "document_ref"]).agg(
        (pl.col("status") == "needs_pdf_tier").any().alias("_pdf_tier"),
        (pl.col("status") == "quarantined").any().alias("_parse_quarantined"),
    )
    grades = canonical.group_by(["krs", "document_ref"]).agg(
        (pl.col("quality_grade") == "quarantined").any().alias("_graded_quarantined")
    )
    documents = (
        filing_index.select(FILING_INDEX_COLUMNS)
        .filter(
            pl.col("rdf_type_code").is_in(list(statement_type_codes))
            & pl.col("submission_date").is_not_null()
        )
        .join(status, on=["krs", "document_ref"], how="left")
        .join(grades, on=["krs", "document_ref"], how="left")
        .with_columns(
            (
                pl.col("_pdf_tier").fill_null(False)
                | pl.col("file_name").str.to_lowercase().str.ends_with(".pdf").fill_null(False)
            ).alias("_is_pdf"),
            (
                pl.col("_parse_quarantined").fill_null(False)
                | pl.col("_graded_quarantined").fill_null(False)
            ).alias("_is_quarantined"),
        )
    )
    return (
        documents.group_by(
            ["krs", "period_start", "period_end", "submission_date", "is_correction"]
        )
        .agg(
            # Present while any part is: deleted only once every part is.
            pl.when(pl.col("deleted_on").is_null().any())
            .then(None)
            .otherwise(pl.col("deleted_on").max())
            .alias("deleted_on"),
            pl.col("_is_pdf").all().alias("is_pdf"),
            pl.col("_is_quarantined").any().alias("is_quarantined"),
        )
        .select(
            pl.col("krs"),
            pl.col("period_start"),
            pl.col("period_end"),
            pl.col("submission_date").alias("known_from"),
            pl.col("deleted_on").cast(pl.Date),
            pl.col("is_correction").fill_null(False),
            pl.col("is_pdf"),
            pl.col("is_quarantined"),
        )
        .sort(["krs", "period_end", "known_from", "is_correction"])
    )


# --- the panel, as snapshots ---------------------------------------------------------------------


@dataclass(frozen=True)
class _Version:
    period_end: date
    period_start: date | None
    known_from: date
    values: dict[str, Decimal | None]

    def days(self) -> int | None:
        if self.period_start is None:
            return None
        return (self.period_end - self.period_start).days + 1


def _versions(panel: pl.DataFrame) -> dict[str, list[_Version]]:
    """Each entity's panel versions, in `known_from` order."""
    grouped: dict[tuple[str, date, date], tuple[date | None, dict[str, Decimal | None]]] = {}
    for row in panel.sort(["krs", "known_from", "period_end", "input"]).iter_rows(named=True):
        key = (row["krs"], row["known_from"], row["period_end"])
        _, values = grouped.setdefault(key, (row["period_start"], {}))
        values[row["input"]] = row["value"]
    out: dict[str, list[_Version]] = {}
    for (krs, known_from, period_end), (start, values) in grouped.items():
        out.setdefault(krs, []).append(_Version(period_end, start, known_from, values))
    return out


class _Snapshot:
    """What was known of an entity's statements on one day: each period's latest version."""

    def __init__(self, versions: list[_Version], day: date, config: FeatureConfig) -> None:
        self.periods: dict[date, _Version] = {}
        for v in versions:  # in known_from order, so a later version replaces an earlier one
            if v.known_from <= day:
                self.periods[v.period_end] = v
        self.config = config
        self.band = config.feature_set.period_length_days
        self.tolerance = config.feature_set.lag_tolerance_days

    def latest(self) -> _Version | None:
        return self.periods[max(self.periods)] if self.periods else None

    def back(self, v: _Version, years: int) -> _Version | None:
        """The period that ended `years` years before `v`, within the lag tolerance."""
        target = add_months(v.period_end, -12 * years)
        near = [
            p
            for end, p in self.periods.items()
            if abs((end - target).days) <= self.tolerance and end < v.period_end
        ]
        return min(
            near, key=lambda p: (abs((p.period_end - target).days), p.period_end), default=None
        )

    def in_band(self, v: _Version) -> bool:
        return self.band.covers(v.days())


def _sum(terms: Mapping[str, int], values: Mapping[str, Decimal | None]) -> Decimal | None:
    total = Decimal(0)
    for name, sign in terms.items():
        value = values.get(name)
        if value is None:
            return None
        total += sign * value
    return total


def _ratio(f: RatioFeature, v: _Version, snap: _Snapshot, sensitive: bool) -> float | None:
    if sensitive and not snap.in_band(v):
        return None
    numerator = _sum(f.numerator, v.values)
    denominator = _sum(f.denominator, v.values)
    if numerator is None or denominator is None or denominator == 0:
        return None
    if f.denominator_rule == "positive" and denominator < 0:
        return None
    return float(numerator / denominator * f.scale)


def _financial_value(
    f: Feature, snap: _Snapshot, ratios: dict[str, RatioFeature]
) -> tuple[float, date] | None:
    """One feature from one snapshot, with the latest `known_from` it read."""
    latest = snap.latest()
    if latest is None:
        return None
    line_items = snap.config.line_items
    sensitive = length_sensitive(f, line_items)
    if isinstance(f, RatioFeature):
        value = _ratio(f, latest, snap, sensitive)
        return None if value is None else (value, latest.known_from)
    if isinstance(f, GrowthFeature):
        base = snap.back(latest, f.years)
        if base is None or (sensitive and not (snap.in_band(latest) and snap.in_band(base))):
            return None
        now, then = latest.values.get(f.input), base.values.get(f.input)
        if now is None or then is None or then <= 0:
            return None
        return float(now / then - 1), max(latest.known_from, base.known_from)
    if isinstance(f, VolatilityFeature):
        ratio = ratios[f.of]
        chain = [latest]
        while len(chain) < f.years:
            earlier = snap.back(chain[-1], 1)
            if earlier is None:
                return None
            chain.append(earlier)
        values = [_ratio(ratio, p, snap, length_sensitive(ratio, line_items)) for p in chain]
        if any(v is None for v in values):
            return None
        return statistics.stdev(v for v in values if v is not None), max(
            p.known_from for p in chain
        )
    if isinstance(f, TripwireFeature):
        return _tripwire(f, latest, snap.config)
    if isinstance(f, BelowZeroFeature):
        value = latest.values.get(f.input)
        return None if value is None else (float(value < 0), latest.known_from)
    raise TypeError(f"{f.name}: a {f.kind} feature is not a financial one")


def _tripwire(f: TripwireFeature, v: _Version, config: FeatureConfig) -> tuple[float, date] | None:
    """KSH loss test (AGENT_SPEC §4.5), exact: triggered only when the loss is strictly greater."""
    rule = config.tripwires.resolve(f.rule, config.feature_set.legal_form, v.period_end)
    if rule is None:
        return None
    if any(v.values.get(name) is None for name in rule.inputs()):
        return None
    result = sum((Fraction(v.values[name] or 0) for name in rule.losses), Fraction(0))
    loss = -result if result < 0 else Fraction(0)
    threshold = sum(
        (c * Fraction(v.values[name] or 0) for name, c in rule.coefficients().items()),
        Fraction(0),
    )
    return float(loss > threshold), v.known_from


def _asof(grid: pl.DataFrame, snapshots: pl.DataFrame) -> pl.DataFrame:
    """For each grid row, the latest snapshot at or before its `as_of_date` (DuckDB ASOF JOIN)."""
    con = duckdb.connect()
    try:
        con.register("grid", grid.select("krs", "as_of_date").unique())
        con.register("snapshots", snapshots)
        return con.sql(
            """
            SELECT g.krs, g.as_of_date, s.snapshot_date
            FROM grid g ASOF JOIN snapshots s
              ON g.krs = s.krs AND g.as_of_date >= s.snapshot_date
            """
        ).pl()
    finally:
        con.close()


def _panel_family(
    family: Family, grid: pl.DataFrame, panel: pl.DataFrame, config: FeatureConfig
) -> pl.DataFrame:
    features = _features(config, family)
    ratios = {f.name: f for f in config.feature_set.features if isinstance(f, RatioFeature)}
    snapshots: list[tuple[str, date]] = []
    by_snapshot: list[Row] = []
    for krs, versions in _versions(panel).items():
        for day in sorted({v.known_from for v in versions}):
            snapshots.append((krs, day))
            snap = _Snapshot(versions, day, config)
            for f in features:
                hit = _financial_value(f, snap, ratios)
                if hit is not None:
                    by_snapshot.append((krs, day, f.name, hit[0], hit[1]))
    snapshot_days = pl.DataFrame(
        snapshots, schema={"krs": pl.String, "snapshot_date": pl.Date}, orient="row"
    )
    if grid.is_empty() or snapshot_days.is_empty():
        return _frame([])
    matched = _asof(grid, snapshot_days)
    values = pl.DataFrame(
        by_snapshot,
        schema={
            "krs": pl.String,
            "snapshot_date": pl.Date,
            "feature": pl.String,
            "value": pl.Float64,
            "known_from": pl.Date,
        },
        orient="row",
    )
    return (
        matched.join(values, on=["krs", "snapshot_date"], how="inner")
        .select(FEATURE_VALUES_SCHEMA.keys())
        .cast(FEATURE_VALUES_SCHEMA)  # pyright: ignore[reportArgumentType]
        .sort(FEATURE_VALUES_SORT_KEY)
    )


def financial(grid: pl.DataFrame, inputs: FeatureInputs, config: FeatureConfig) -> pl.DataFrame:
    """Ratios, growth and volatility from the panel (plan 0010 decision 4)."""
    return _panel_family("financial", grid, inputs.panel, config)


def construction(grid: pl.DataFrame, inputs: FeatureInputs, config: FeatureConfig) -> pl.DataFrame:
    """Receivable and liability days, prepayments to assets."""
    return _panel_family("construction", grid, inputs.panel, config)


def tripwire(grid: pl.DataFrame, inputs: FeatureInputs, config: FeatureConfig) -> pl.DataFrame:
    """KSH Art. 233 and negative equity (AGENT_SPEC §4.5)."""
    return _panel_family("tripwire", grid, inputs.panel, config)


# --- filing behaviour ----------------------------------------------------------------------------


@dataclass(frozen=True)
class _Filing:
    period_start: date
    period_end: date
    known_from: date
    deleted_on: date | None
    is_correction: bool
    is_pdf: bool
    is_quarantined: bool

    def known_on(self, day: date) -> bool:
        return self.known_from <= day and (self.deleted_on is None or day < self.deleted_on)


@dataclass(frozen=True)
class _Restatement:
    document_ref: str
    period_end: date
    known_from: date
    difference: Decimal


class _FilingHistory:
    """One entity's statement filings and restatements, asked about one day at a time."""

    def __init__(
        self,
        filings: list[_Filing],
        restatements: list[_Restatement],
        versions: list[_Version],
        config: FeatureConfig,
    ) -> None:
        self.filings = filings
        self.restatements = restatements
        self.versions = versions
        self.config = config
        self.tolerance = config.feature_set.lag_tolerance_days

    def _year_ends(self, known: list[_Filing], day: date, lookback: int) -> list[date]:
        """The `lookback` latest year ends, on the latest period's calendar, due by `day`.

        Only from the first filing known: RDF holds no earlier filings for most entities
        (paper, before 2018), so an earlier gap says nothing about the entity.
        """
        anchor = max(f.period_end for f in known)
        first = min(f.period_end for f in known)
        deadlines = self.config.deadlines
        ends: list[date] = []
        k = 0
        # Forward to the last year end already due, then back until enough or the first filing.
        while deadlines.deadline(add_months(anchor, 12 * (k + 1))) < day:
            k += 1
        while len(ends) < lookback:
            end = add_months(anchor, 12 * k)
            if end < first:
                break
            if deadlines.deadline(end) < day:
                ends.append(end)
            k -= 1
        return ends

    def _covering(self, known: list[_Filing], year_end: date) -> list[_Filing]:
        """Filings for a period that ends in the 12 months to `year_end` (within the tolerance)."""
        start = add_months(year_end, -12)
        return [
            f
            for f in known
            if (f.period_end - start).days > self.tolerance
            and (f.period_end - year_end).days <= self.tolerance
        ]

    def value(self, f: FilingFeature, day: date) -> tuple[float, date] | None:
        known = [x for x in self.filings if x.known_on(day)]
        if not known:
            return None
        evidence = max(x.known_from for x in known)
        metric = f.metric
        if metric == "days_to_file_latest":
            latest_end = max(x.period_end for x in known)
            first = min(
                (x for x in known if x.period_end == latest_end), key=lambda x: x.known_from
            )
            return float((first.known_from - latest_end).days), first.known_from
        if metric in ("missing_years", "late_filings"):
            ends = self._year_ends(known, day, f.lookback_years or 0)
            count = 0
            for end in ends:
                covering = self._covering(known, end)
                if metric == "missing_years":
                    count += not covering
                elif covering:
                    period_end = max(x.period_end for x in covering)
                    first = min(x.known_from for x in covering if x.period_end == period_end)
                    count += first > self.config.deadlines.deadline(period_end)
            return float(count), evidence
        if metric == "corrections":
            return float(sum(x.is_correction for x in known)), evidence
        if metric == "statements_quarantined":
            return float(sum(x.is_quarantined for x in known)), evidence
        if metric == "latest_filed_as_pdf":
            latest = max(known, key=lambda x: (x.period_end, x.known_from, x.is_correction))
            return float(latest.is_pdf), latest.known_from
        restating = [r for r in self.restatements if r.known_from <= day]
        if metric == "restating_filings":
            return float(len({r.document_ref for r in restating})), evidence
        if metric == "restatement_max_to_assets":
            if not restating:
                return 0.0, evidence
            last = max(restating, key=lambda r: (r.known_from, r.document_ref))
            lines = [r for r in restating if r.document_ref == last.document_ref]
            snap = _Snapshot(self.versions, day, self.config)
            restated = snap.periods.get(last.period_end)
            assets = restated.values.get(TOTAL_ASSETS) if restated else None
            if restated is None or assets is None or assets <= 0:
                return None
            largest = max(abs(r.difference) for r in lines)
            return float(largest / assets), max(last.known_from, restated.known_from)
        raise ValueError(f"{f.name}: unknown filing metric {metric}")


def filing(grid: pl.DataFrame, inputs: FeatureInputs, config: FeatureConfig) -> pl.DataFrame:
    """Filing behaviour, from statement filings and restatements (plan 0010 decision 4)."""
    features = [f for f in _features(config, "filing") if isinstance(f, FilingFeature)]
    filings: dict[str, list[_Filing]] = {}
    for row in inputs.filings.sort(["krs", "known_from", "period_end"]).iter_rows(named=True):
        filings.setdefault(row["krs"], []).append(
            _Filing(
                period_start=row["period_start"],
                period_end=row["period_end"],
                known_from=row["known_from"],
                deleted_on=row["deleted_on"],
                is_correction=row["is_correction"],
                is_pdf=row["is_pdf"],
                is_quarantined=row["is_quarantined"],
            )
        )
    restatements: dict[str, list[_Restatement]] = {}
    for row in inputs.restatements.iter_rows(named=True):
        restatements.setdefault(row["krs"], []).append(
            _Restatement(
                document_ref=row["restating_document_ref"],
                period_end=row["period_end"],
                known_from=row["known_from"],
                difference=row["restated_value"] - row["originally_reported_value"],
            )
        )
    versions = _versions(inputs.panel)

    def rows() -> Iterator[Row]:
        for krs, days in _grid_by_entity(grid).items():
            history = _FilingHistory(
                filings.get(krs, []), restatements.get(krs, []), versions.get(krs, []), config
            )
            for day in days:
                for f in features:
                    hit = history.value(f, day)
                    if hit is not None:
                        yield (krs, day, f.name, hit[0], hit[1])

    return _frame(rows())


# --- registry dynamics and legal history ---------------------------------------------------------


class _EventHistory:
    """One entity's legal events, by a key no later fact can change."""

    def __init__(self, events: pl.DataFrame) -> None:
        first_seen: dict[tuple[str, date], date] = {}
        for row in events.iter_rows(named=True):
            key = (row["event_type"], row["event_date"] or row["known_from"])
            first_seen[key] = min(first_seen.get(key, row["known_from"]), row["known_from"])
        self.first_seen = first_seen
        self.seen = sorted(events.get_column("known_from").to_list())

    def value(self, types: set[str], window: int | None, day: date) -> tuple[float, date] | None:
        at = bisect_right(self.seen, day)
        if at == 0:
            return None  # nothing from the registry or MSiG is known yet
        start = add_months(day, -window) if window is not None else None
        counted = [
            known
            for (event_type, _), known in self.first_seen.items()
            if event_type in types and known <= day and (start is None or known > start)
        ]
        return float(len(counted)), (max(counted) if counted else self.seen[at - 1])


def _event_family(
    family: Family, grid: pl.DataFrame, events: pl.DataFrame, config: FeatureConfig
) -> pl.DataFrame:
    features = [f for f in _features(config, family) if isinstance(f, EventCountFeature)]
    types = {f.name: f.matches(config.taxonomy) for f in features}
    histories = {
        krs: _EventHistory(group)
        for (krs,), group in events.select("krs", "event_type", "event_date", "known_from")
        .sort(["krs", "known_from"])
        .group_by(["krs"], maintain_order=True)
    }

    def rows() -> Iterator[Row]:
        for krs, days in _grid_by_entity(grid).items():
            history = histories.get(krs)
            if history is None:
                continue
            for day in days:
                for f in features:
                    hit = history.value(types[f.name], f.window_months, day)
                    if hit is not None:
                        yield (krs, day, f.name, hit[0], hit[1])

    return _frame(rows())


def registry(grid: pl.DataFrame, inputs: FeatureInputs, config: FeatureConfig) -> pl.DataFrame:
    """Board, office and capital changes, arrears enforcement, curators."""
    return _event_family("registry", grid, inputs.legal_events, config)


def legal_history(grid: pl.DataFrame, inputs: FeatureInputs, config: FeatureConfig) -> pl.DataFrame:
    """Petition-stage events and closed proceedings, by class."""
    return _event_family("legal_history", grid, inputs.legal_events, config)


FAMILIES: dict[Family, Callable[[pl.DataFrame, FeatureInputs, FeatureConfig], pl.DataFrame]] = {
    "financial": financial,
    "construction": construction,
    "tripwire": tripwire,
    "filing": filing,
    "registry": registry,
    "legal_history": legal_history,
}


def compute_features(
    grid: pl.DataFrame, inputs: FeatureInputs, config: FeatureConfig
) -> pl.DataFrame:
    """Every family's values for the grid, in one long frame."""
    frames = [compute(grid, inputs, config) for compute in FAMILIES.values()]
    return pl.concat(frames).sort(FEATURE_VALUES_SORT_KEY)
