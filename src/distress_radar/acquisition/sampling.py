"""A1 — the draw of a weighted list (plan 0019 step B, decisions 0–3 and 10; ADR 0014 addendum).

A draw spec (`config/segments/<segment>_draw_v<n>.yaml`) says how a list was drawn from a recorded
frame, so that every drawn company's probability of being drawn, π, is known exactly. Two designs:

- `permutation`: pipeline code draws from an exported frame file. The frame's KRS numbers are put
  in one order by the SHA-256 of `"<seed>:<krs>"`, which no library version can change. Group A
  is the first n_A of that order; group B, per distress status c, the first n_Bc members of D_c
  not in A. π follows the addendum's formula for that dependent draw.
- `systematic`: the owner's script drew each group itself, with a fixed step and a random start
  over the frame's registration-year slices, and logged every pick (decision 10). The draw is
  not repeated: its run logs are checked against the spec, pick by pick, and π = n / N for each
  run. A and B are then independent draws, so a company carrying the distress statuses C has
  π = 1 − (1 − π_A) · Π_{c ∈ C} (1 − π_Bc). B's runs may share one log, one run per status.

π is exact (`Fraction`) here; it becomes `numeric` only when stored. A weight is a fact about how a
company was drawn, never about its outcome: nothing here reads a label.

Only KRS numbers, positions, registration years and statuses are read from a frame or a log. A
company name never leaves the line it was read from (ADR 0014, invariant 6). A malformed KRS is
quarantined under its line, never padded or repaired (invariant 4). Anything else that disagrees
with the spec refuses the whole file: a draw is either exactly what the spec says or not used.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import math
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from fractions import Fraction
from pathlib import Path
from typing import Annotated, Any, Literal, cast

import yaml
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, field_validator, model_validator

from distress_radar.acquisition.models import KRS_LENGTH, QuarantineRecord, is_krs

STAGE = "A1"
NO_STRATUM = "none"
RANDOM_GROUP = "A"
DISTRESS_GROUP = "B"
# The script prints the step rounded to six places.
_STEP_TOLERANCE = Decimal("0.000001")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class DrawRefused(ValueError):
    """A frame or run log that disagrees with its draw spec. The whole file is refused."""


# --- The draw spec (decision 0) ----------------------------------------------------------------


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


def _check_sha256(value: str) -> str:
    if not _SHA256.match(value):
        raise ValueError("must be a lowercase hex SHA-256")
    return value


class DistressStatus(_Frozen):
    """A status that defines D, as Rejestr.io labels it and as its status filter's value."""

    label: str
    filter_value: str | None = None


class FrameSpec(_Frozen):
    """The frame F every group is drawn from, as the search that defined it."""

    source: Literal["rejestr_io"]
    taken_on: date
    # The search's filters, as the run log's raw filter JSON prints them, unset ones left out.
    # The status filter (`op`) is not one of them: it is unset for F and set per stratum of D.
    filters: dict[str, str]
    size: int = Field(ge=1)


class PermutationFrame(FrameSpec):
    sha256: str
    krs_column: str
    status_column: str
    # N_Dc: how many of F carry each distress status, keyed like `distress_statuses`.
    distress_sizes: dict[str, int]

    _hash = field_validator("sha256")(_check_sha256)


class PermutationRandom(_Frozen):
    list_version: str
    n: int = Field(ge=1)


class PermutationDistress(_Frozen):
    list_version: str
    n: dict[str, int]


class PermutationSpec(_Frozen):
    design: Literal["permutation"]
    segment: str
    version: int = Field(ge=1)
    frame: PermutationFrame
    distress_statuses: dict[str, DistressStatus]
    seed: int = Field(ge=0)
    random: PermutationRandom
    distress: PermutationDistress | None = None

    @model_validator(mode="after")
    def _consistent(self) -> PermutationSpec:
        if set(self.frame.distress_sizes) != set(self.distress_statuses):
            raise ValueError("frame.distress_sizes must give N_Dc for every distress status")
        if self.random.n > self.frame.size:
            raise ValueError("random.n exceeds the frame")
        if self.distress is not None:
            if not set(self.distress.n) <= set(self.distress_statuses):
                raise ValueError("distress.n names a status not in distress_statuses")
            if any(n < 1 for n in self.distress.n.values()):
                raise ValueError("distress.n must be at least 1 per status")
        return self


class SystematicRun(_Frozen):
    """One run of the owner's script: a fixed step over sorted registration-year slices."""

    list_version: str
    log_sha256: str
    taken_on: date
    sort: str
    # The run's own slices: N per registration year, summing to the run's N.
    year_slices: dict[int, int]
    n: int = Field(ge=1)
    # As the log prints it; exact as a decimal.
    start: str
    seed: int = Field(ge=0)
    # The frame's count as this run's log printed it, when the search returned another number than
    # `frame.size` by the run's time (F drifts between runs taken hours apart). None: the log
    # prints no frame count, or prints `frame.size`.
    frame_size: int | None = Field(default=None, ge=1)

    _hash = field_validator("log_sha256")(_check_sha256)

    @property
    def size(self) -> int:
        return sum(self.year_slices.values())

    @property
    def step(self) -> Fraction:
        return Fraction(self.size, self.n)

    @property
    def start_value(self) -> Fraction:
        return Fraction(Decimal(self.start))

    @model_validator(mode="after")
    def _consistent(self) -> SystematicRun:
        try:
            Decimal(self.start)
        except InvalidOperation as exc:
            raise ValueError(f"start {self.start!r} is not a decimal") from exc
        if any(count < 0 for count in self.year_slices.values()):
            raise ValueError("year_slices: a negative count")
        if self.n > self.size:
            raise ValueError("n exceeds the run's N")
        if not 0 <= self.start_value < self.step:
            raise ValueError("start must lie in [0, step)")
        return self


class SystematicSpec(_Frozen):
    design: Literal["systematic"]
    segment: str
    version: int = Field(ge=1)
    frame: FrameSpec
    distress_statuses: dict[str, DistressStatus]
    random: SystematicRun
    # One run per distress status drawn; empty while only A is drawn (π_Bc is then 0).
    distress: dict[str, SystematicRun] = Field(default_factory=dict[str, SystematicRun])

    @model_validator(mode="after")
    def _consistent(self) -> SystematicSpec:
        if self.random.size != self.frame.size:
            raise ValueError("random.year_slices must sum to frame.size")
        for stratum in self.distress:
            if stratum not in self.distress_statuses:
                raise ValueError(f"distress run {stratum!r} is not in distress_statuses")
            if self.distress_statuses[stratum].filter_value is None:
                raise ValueError(f"distress status {stratum!r} needs its filter_value")
            run = self.distress[stratum]
            if run.size > (run.frame_size if run.frame_size is not None else self.frame.size):
                raise ValueError(f"distress run {stratum!r} is larger than the frame")
        return self


DrawSpec = Annotated[PermutationSpec | SystematicSpec, Field(discriminator="design")]
_DRAW_SPEC: TypeAdapter[PermutationSpec | SystematicSpec] = TypeAdapter(DrawSpec)


def load_draw_spec(path: Path) -> PermutationSpec | SystematicSpec:
    return _DRAW_SPEC.validate_python(yaml.safe_load(path.read_text(encoding="utf-8")))


def spec_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


# --- What a draw produces ----------------------------------------------------------------------


@dataclass(frozen=True)
class DrawnCompany:
    """One drawn company in one group. A company drawn into both groups has a row in each, with
    the same π: it is the probability of being drawn at all."""

    krs: str
    list_version: str
    group: Literal["A", "B"]
    # The distress status the company was drawn under or flagged with, or `none`.
    stratum: str
    inclusion_probability: Fraction
    # The registration-year slice it was drawn from (systematic draws only): the implicit strata.
    registration_year: int | None
    # Its place in the group's download order, from 0: the permutation's order, or for a systematic
    # run the same seeded hash order over its picks, so that whatever part of a group has been
    # downloaded at any moment is itself a random sample (the addendum's "Download order").
    draw_order: int
    # The frame file's SHA-256 (permutation) or the run log's (systematic) it was drawn from.
    source_sha256: str


@dataclass(frozen=True)
class DrawResult:
    rows: list[DrawnCompany] = field(default_factory=list[DrawnCompany])
    quarantine: list[QuarantineRecord] = field(default_factory=list[QuarantineRecord])


def _quarantine_line(
    name: str, line: int, *, ingestion_run_id: str, created_at: datetime
) -> QuarantineRecord:
    # The line number, never the offending value: it may sit next to a name.
    return QuarantineRecord(
        stage=STAGE,
        entity_key=f"{name}#{line}",
        reason_code="malformed_krs",
        detail=f"line {line}: KRS must be {KRS_LENGTH} digits, zero-padded",
        source_document_hash=None,
        ingestion_run_id=ingestion_run_id,
        created_at=created_at,
        krs=None,
        document_ref=None,
    )


# --- Permutation design ------------------------------------------------------------------------


@dataclass(frozen=True)
class Frame:
    """F's KRS numbers with their distress stratum (or `none`), and the lines quarantined."""

    strata: dict[str, str]
    quarantine: list[QuarantineRecord]


def read_frame(
    path: Path, spec: PermutationSpec, *, ingestion_run_id: str, created_at: datetime
) -> Frame:
    """Read a frame export's KRS and status columns; refuse it unless it is the spec's frame."""
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != spec.frame.sha256:
        raise DrawRefused(f"{path.name}: SHA-256 differs from the spec's frame")
    text = raw.decode("utf-8-sig")
    header_line = text.splitlines()[0] if text else ""
    delimiter = ";" if header_line.count(";") > header_line.count(",") else ","
    reader = csv.DictReader(io.StringIO(text, newline=""), delimiter=delimiter)
    columns = reader.fieldnames or []
    for column in (spec.frame.krs_column, spec.frame.status_column):
        if column not in columns:
            raise DrawRefused(f"{path.name}: no column {column!r}")

    by_label = {status.label: key for key, status in spec.distress_statuses.items()}
    strata: dict[str, str] = {}
    quarantine: list[QuarantineRecord] = []
    rows = 0
    for line, row in enumerate(reader, start=2):
        rows += 1
        krs = (row.get(spec.frame.krs_column) or "").strip()
        status = (row.get(spec.frame.status_column) or "").strip()
        if not is_krs(krs):
            quarantine.append(
                _quarantine_line(
                    path.name, line, ingestion_run_id=ingestion_run_id, created_at=created_at
                )
            )
            continue
        if krs in strata:
            raise DrawRefused(f"{path.name}: line {line} repeats a KRS number")
        strata[krs] = by_label.get(status, NO_STRATUM)

    if rows != spec.frame.size:
        raise DrawRefused(f"{path.name}: {rows} rows, the spec's N_F is {spec.frame.size}")
    for key, expected in spec.frame.distress_sizes.items():
        found = sum(1 for stratum in strata.values() if stratum == key)
        if found != expected:
            raise DrawRefused(f"{path.name}: {found} rows of {key!r}, the spec says {expected}")
    return Frame(strata=strata, quarantine=quarantine)


def _order_key(seed: int, krs: str) -> str:
    return hashlib.sha256(f"{seed}:{krs}".encode()).hexdigest()


def draw(frame: Frame, spec: PermutationSpec) -> DrawResult:
    """Draw A and B from a frame read by `read_frame` (the addendum's rule).

    The order depends on the seed and each KRS number alone, so a larger n extends a draw
    without changing its first members, and the same frame and spec give the same rows.
    """
    order = sorted(frame.strata, key=lambda krs: (_order_key(spec.seed, krs), krs))
    n_frame = spec.frame.size
    f_a = Fraction(spec.random.n, n_frame)
    group_a = order[: spec.random.n]
    in_a = set(group_a)

    # π per stratum: f_A outside D; for D_c, the addendum's formula with d_Ac drawn into A.
    pi: dict[str, Fraction] = {NO_STRATUM: f_a}
    for key in spec.distress_statuses:
        n_b = spec.distress.n.get(key, 0) if spec.distress is not None else 0
        d_a = sum(1 for krs in group_a if frame.strata[krs] == key)
        remaining = spec.frame.distress_sizes[key] - d_a
        share = Fraction(1) if remaining <= 0 else min(Fraction(1), Fraction(n_b, remaining))
        pi[key] = f_a + (1 - f_a) * share

    rows = [
        DrawnCompany(
            krs=krs,
            list_version=spec.random.list_version,
            group=RANDOM_GROUP,
            stratum=frame.strata[krs],
            inclusion_probability=pi[frame.strata[krs]],
            registration_year=None,
            draw_order=rank,
            source_sha256=spec.frame.sha256,
        )
        for rank, krs in enumerate(group_a)
    ]
    if spec.distress is not None:
        for key in spec.distress_statuses:
            members = [krs for krs in order if frame.strata[krs] == key and krs not in in_a]
            rows.extend(
                DrawnCompany(
                    krs=krs,
                    list_version=spec.distress.list_version,
                    group=DISTRESS_GROUP,
                    stratum=key,
                    inclusion_probability=pi[key],
                    registration_year=None,
                    draw_order=rank,
                    source_sha256=spec.frame.sha256,
                )
                for rank, krs in enumerate(members[: spec.distress.n.get(key, 0)])
            )
    return DrawResult(rows=rows, quarantine=list(frame.quarantine))


# --- Systematic design (decision 10) -----------------------------------------------------------


@dataclass(frozen=True)
class LogPick:
    line: int
    position: int
    year: int
    krs: str
    registration_year: int
    # Rejestr.io's distress labels on the company at the run's time; a company may carry two
    # ("W likwidacji; W upadłości"), and an unflagged one none.
    statuses: tuple[str, ...]


@dataclass(frozen=True)
class RunLog:
    """What a run log says about one run, without a single company name."""

    sha256: str
    result: str
    warnings: list[str]
    filters: dict[str, Any]
    sort: str
    total: int
    year_slices: dict[int, int]
    n: int
    step: Decimal
    start: str
    seed: int
    picks: list[LogPick]
    malformed_lines: list[int]
    # The frame's count as the log printed it at the run's time, when it prints one apart from
    # the run's own total (a distress log's filter funnel); None otherwise.
    frame_total: int | None = None


_PICK_COLUMNS = ("#", "global_pos", "year", "krs", "registration_date", "status")
# A distress log holds one run per status: a section `SAMPLING - <LABEL> (op=<filter>)` each, a
# raw filter line `Raw filter JSON [<label>]: {...}` each, and one PICKS table whose `sampled_as`
# column names the run a pick belongs to.
_RUN_SECTION = re.compile(r"^SAMPLING - .+ \(op=(\S+)\)$")
_RUN_FILTERS = re.compile(r"^\s*Raw filter JSON \[(.+)\]\s*:\s*(\{.*\})\s*$")
_FULL_RANGE = re.compile(r"full-range query returned (\d+)")
_FUNNEL_LINE = re.compile(r"^\s*(.*?)\s+(\d+)\s*$")
_YEAR_LINE = re.compile(r"^\s+(\d{4}):\s+(\d+)\s*$")


def _field(lines: list[str], label: str, name: str | None = None) -> str:
    pattern = re.compile(rf"^\s*{re.escape(label)}\s*:\s*(\S+)")
    for line in lines:
        match = pattern.match(line)
        if match:
            return match.group(1)
    raise DrawRefused(f"{name}: no {label!r} line" if name else f"no {label!r} line")


def _section(lines: list[str], title: str) -> list[str]:
    """The lines after a section title, up to the next blank line."""
    try:
        start = next(i for i, line in enumerate(lines) if line.strip() == title)
    except StopIteration:
        raise DrawRefused(f"no {title} section") from None
    body: list[str] = []
    for line in lines[start + 1 :]:
        if not line.strip():
            break
        body.append(line)
    return body


def _year_slices(lines: list[str]) -> dict[int, int]:
    slices: dict[int, int] = {}
    for line in lines:
        match = _YEAR_LINE.match(line)
        if match:
            slices[int(match.group(1))] = int(match.group(2))
    return slices


def _statuses(value: str) -> tuple[str, ...]:
    return tuple(part.strip() for part in value.split(";") if part.strip() not in ("", "-"))


@dataclass(frozen=True)
class _RunPart:
    """The part of a log that differs between a one-run log and a run of a distress log."""

    filters: dict[str, Any]
    total: int
    year_slices: dict[int, int]
    settings: list[str]
    # The `sampled_as` value of the run's picks, or None when every pick is the run's.
    label: str | None
    frame_total: int | None


def _single_run(lines: list[str]) -> _RunPart:
    filters_line = next(
        (line for line in lines if line.strip().startswith("Raw filter JSON")), None
    )
    if filters_line is None:
        raise DrawRefused("no 'Raw filter JSON' line")
    return _RunPart(
        filters=cast(dict[str, Any], json.loads(filters_line.split(":", 1)[1])),
        total=int(_field(lines, "Exact total returned for full range")),
        year_slices=_year_slices(_section(lines, "TOTALS")),
        settings=lines,
        label=None,
        frame_total=None,
    )


def _stratum_run(lines: list[str], status_filter: str | None) -> _RunPart:
    runs: dict[str, tuple[str, dict[str, Any]]] = {}
    for line in lines:
        match = _RUN_FILTERS.match(line)
        if match:
            filters = cast(dict[str, Any], json.loads(match.group(2)))
            runs[str(filters.get("op"))] = (match.group(1), filters)
    if status_filter is None or status_filter not in runs:
        raise DrawRefused(f"no run with the status filter {status_filter!r}")
    label, filters = runs[status_filter]
    title = next(
        line.strip()
        for line in lines
        if (match := _RUN_SECTION.match(line.strip())) and match.group(1) == status_filter
    )
    section = _section(lines, title)
    full_range = next((m for line in section if (m := _FULL_RANGE.search(line))), None)
    if full_range is None:
        raise DrawRefused(f"the {status_filter!r} run prints no full-range total")
    # The frame is the funnel's last count before the first status filter is added.
    funnel = _section(lines, "TOTALS AT EACH FILTERING STEP")
    frame_total: int | None = None
    for line in funnel:
        if line.strip().startswith("+ Status"):
            break
        match = _FUNNEL_LINE.match(line)
        if match:
            frame_total = int(match.group(2))
    return _RunPart(
        filters=filters,
        total=int(full_range.group(1)),
        year_slices=_year_slices(section),
        settings=section,
        label=label,
        frame_total=frame_total,
    )


def read_run_log(path: Path, status_filter: str | None = None) -> RunLog:
    """Parse the owner's script's run log. Only its settings and the picks' KRS, position, year,
    registration year and statuses are kept; every other column, a name among them, is dropped
    on the line it was read from.

    A log of one run (group A's script) is read whole. A distress log holds one run per status
    (the distress script), and is read for the run whose status filter is `status_filter` alone.
    """
    raw = path.read_bytes()
    name = path.name
    lines = raw.decode("utf-8-sig").splitlines()
    multi = any(_RUN_SECTION.match(line.strip()) for line in lines)
    try:
        part = _stratum_run(lines, status_filter) if multi else _single_run(lines)
        sort_lines = _section(lines, "SORT ORDER")
        warnings = [line.strip() for line in _section(lines, "WARNINGS")]
    except (json.JSONDecodeError, DrawRefused) as exc:
        raise DrawRefused(f"{name}: {exc}") from exc
    if not sort_lines:
        raise DrawRefused(f"{name}: SORT ORDER is empty")

    try:
        title_index = next(i for i, line in enumerate(lines) if line.startswith("PICKS"))
        header_index = title_index + 1
        header = lines[header_index].strip().split("\t")
    except (StopIteration, IndexError):
        raise DrawRefused(f"{name}: no PICKS table") from None
    columns = (*_PICK_COLUMNS, "sampled_as") if multi else _PICK_COLUMNS
    missing = [column for column in columns if column not in header]
    if missing:
        raise DrawRefused(f"{name}: PICKS lacks {missing}")
    index = {column: header.index(column) for column in columns}

    picks: list[LogPick] = []
    malformed: list[int] = []
    for number, line in enumerate(lines[header_index + 1 :], start=header_index + 2):
        if not line.strip():
            continue
        cells = line.strip().split("\t")
        if len(cells) <= index["#"] or not cells[index["#"]].isdigit():
            break
        try:
            values = {column: cells[i].strip() for column, i in index.items()}
        except IndexError:
            raise DrawRefused(f"{name}: line {number} is short of columns") from None
        del cells
        if part.label is not None and values["sampled_as"] != part.label:
            continue
        if not is_krs(values["krs"]):
            malformed.append(number)
            continue
        try:
            pick = LogPick(
                line=number,
                position=int(values["global_pos"]),
                year=int(values["year"]),
                krs=values["krs"],
                registration_year=date.fromisoformat(values["registration_date"]).year,
                statuses=_statuses(values["status"]),
            )
        except ValueError:
            raise DrawRefused(f"{name}: line {number} has a malformed position or date") from None
        picks.append(pick)

    try:
        return RunLog(
            sha256=hashlib.sha256(raw).hexdigest(),
            result=_field(lines, "Result", name),
            warnings=[w for w in warnings if w != "none"],
            filters=part.filters,
            sort=sort_lines[0].split()[0],
            total=part.total,
            year_slices=part.year_slices,
            n=int(_field(part.settings, "Companies wanted", name)),
            step=Decimal(_field(part.settings, "Step", name)),
            start=_field(part.settings, "Start", name),
            seed=int(_field(lines, "Seed", name)),
            picks=picks,
            malformed_lines=malformed,
            frame_total=part.frame_total,
        )
    except (ValueError, InvalidOperation) as exc:
        raise DrawRefused(f"{name}: {exc}") from exc


def _set_filters(filters: Mapping[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in filters.items() if value is not None}


def check_run_log(
    log: RunLog, run: SystematicRun, frame: FrameSpec, *, status_filter: str | None, name: str
) -> None:
    """Refuse a log unless it is exactly the run the spec describes, pick by pick."""

    def refuse(reason: str) -> None:
        raise DrawRefused(f"{name}: {reason}")

    if log.sha256 != run.log_sha256:
        refuse("SHA-256 differs from the spec's")
    if log.result != "OK":
        refuse(f"the run's result is {log.result!r}, not OK")
    if log.warnings:
        refuse(f"the run logged {len(log.warnings)} warning(s)")
    filters = _set_filters(log.filters)
    status = filters.pop("op", None)
    if filters != frame.filters:
        refuse("its filters differ from the frame's")
    if status != status_filter:
        refuse(f"its status filter is {status!r}, the spec's {status_filter!r}")
    # A frame count the log prints must be the spec's: N_F, or the count the spec records for this
    # run when the frame had drifted by the run's time.
    if run.frame_size is not None and log.frame_total is None:
        refuse("the spec gives a frame count for this run, the log prints none")
    frame_size = run.frame_size if run.frame_size is not None else frame.size
    if log.frame_total is not None and log.frame_total != frame_size:
        refuse(f"its frame count is {log.frame_total}, the spec's {frame_size}")
    if log.sort != run.sort:
        refuse(f"sorted {log.sort!r}, the spec says {run.sort!r}")
    if log.year_slices != run.year_slices:
        refuse("its registration-year slices differ from the spec's")
    if log.total != run.size:
        refuse(f"total {log.total}, the slices sum to {run.size}")
    if log.n != run.n or log.seed != run.seed or Decimal(log.start) != Decimal(run.start):
        refuse("its n, seed or start differ from the spec's")
    if abs(log.step - Decimal(run.size) / Decimal(run.n)) > _STEP_TOLERANCE:
        refuse(f"step {log.step} is not N / n")
    if len(log.picks) + len(log.malformed_lines) != run.n:
        refuse(f"{len(log.picks) + len(log.malformed_lines)} picks, the spec's n is {run.n}")
    if len({pick.krs for pick in log.picks}) != len(log.picks):
        refuse("a KRS number is picked twice")

    # Every pick at ⌊start + i·step⌋, in its slice's year, in log order. A malformed pick's line
    # still holds its place, so positions are checked against the line order.
    slice_end: list[tuple[int, int]] = []
    total = 0
    for year in sorted(run.year_slices):
        total += run.year_slices[year]
        slice_end.append((total, year))
    lines = sorted([pick.line for pick in log.picks] + log.malformed_lines)
    by_line = {pick.line: pick for pick in log.picks}
    for i, line in enumerate(lines):
        pick = by_line.get(line)
        if pick is None:
            continue
        expected = math.floor(run.start_value + i * run.step)
        if pick.position != expected:
            refuse(f"line {line}: position {pick.position}, the rule gives {expected}")
        year = next(year for end, year in slice_end if pick.position < end)
        if pick.year != year or pick.registration_year != year:
            refuse(f"line {line}: not in the {year} slice its position falls in")


def draw_systematic(
    spec: SystematicSpec,
    logs: Mapping[str, Path],
    *,
    ingestion_run_id: str,
    created_at: datetime,
) -> DrawResult:
    """Check each run's log against the spec and give every pick its π.

    `logs` maps `random` and each drawn distress status to its run log; the distress runs may
    share one log. A and the runs of B are independent runs, so a company has
    π = 1 − (1 − n_A/N_F) · Π (1 − n_Bc/N_Dc) over the distress statuses c it carries in any log
    that names it: one in D_bpcy and D_liq could have been drawn by either B run. A distress
    script that finds its target already taken shifts to a neighbour and logs a warning, which
    refuses the log, so an accepted log holds no shifted pick.
    """
    expected = {"random", *spec.distress}
    if set(logs) != expected:
        raise DrawRefused(f"logs must be given for exactly {sorted(expected)}")

    pi_a = Fraction(spec.random.n, spec.random.size)
    pi_b = {key: Fraction(run.n, run.size) for key, run in spec.distress.items()}

    def strata_of(pick: LogPick) -> list[str]:
        return [key for key, st in spec.distress_statuses.items() if st.label in pick.statuses]

    runs: list[tuple[str, SystematicRun, str | None]] = [("random", spec.random, None)]
    runs.extend(
        (key, run, spec.distress_statuses[key].filter_value) for key, run in spec.distress.items()
    )
    result = DrawResult()
    read: list[tuple[str, SystematicRun, RunLog]] = []
    flagged: dict[str, set[str]] = {}
    for key, run, status_filter in runs:
        path = logs[key]
        log = read_run_log(path, status_filter)
        check_run_log(log, run, spec.frame, status_filter=status_filter, name=path.name)
        result.quarantine.extend(
            _quarantine_line(
                path.name, line, ingestion_run_id=ingestion_run_id, created_at=created_at
            )
            for line in log.malformed_lines
        )
        for pick in log.picks:
            strata = strata_of(pick)
            if key != "random" and key not in strata:
                raise DrawRefused(
                    f"{path.name}: line {pick.line} is not {spec.distress_statuses[key].label!r}"
                )
            flagged.setdefault(pick.krs, set()).update(strata)
        read.append((key, run, log))

    def pi(krs: str) -> Fraction:
        missed = 1 - pi_a
        for stratum in flagged[krs]:
            missed *= 1 - pi_b.get(stratum, Fraction(0))
        return 1 - missed

    for key, run, log in read:
        ranks = {
            krs: rank
            for rank, krs in enumerate(
                sorted((p.krs for p in log.picks), key=lambda krs: (_order_key(run.seed, krs), krs))
            )
        }
        for pick in log.picks:
            if key == "random":
                group: Literal["A", "B"] = RANDOM_GROUP
                stratum = next(iter(strata_of(pick)), NO_STRATUM)
            else:
                group = DISTRESS_GROUP
                stratum = key
            result.rows.append(
                DrawnCompany(
                    krs=pick.krs,
                    list_version=run.list_version,
                    group=group,
                    stratum=stratum,
                    inclusion_probability=pi(pick.krs),
                    registration_year=pick.year,
                    draw_order=ranks[pick.krs],
                    source_sha256=log.sha256,
                )
            )
    return result


def draw_from_spec(
    spec: PermutationSpec | SystematicSpec,
    *,
    frame: Path | None,
    logs: Mapping[str, Path],
    ingestion_run_id: str,
    created_at: datetime,
) -> DrawResult:
    """The draw a spec describes, from its frame file (permutation) or its run logs (systematic)."""
    if isinstance(spec, PermutationSpec):
        if frame is None or logs:
            raise DrawRefused("a permutation draw takes a frame file and no run logs")
        read = read_frame(frame, spec, ingestion_run_id=ingestion_run_id, created_at=created_at)
        return draw(read, spec)
    if frame is not None:
        raise DrawRefused("a systematic draw takes run logs, not a frame file")
    return draw_systematic(spec, logs, ingestion_run_id=ingestion_run_id, created_at=created_at)
