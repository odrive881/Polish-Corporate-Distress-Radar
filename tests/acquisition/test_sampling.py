"""Plan 0019 step B: the draw, on synthetic frames and run logs only (no real list is committed)."""

from __future__ import annotations

import hashlib
import json
import math
from datetime import UTC, datetime
from fractions import Fraction
from pathlib import Path
from typing import Any

import pytest
import yaml

from distress_radar.acquisition.sampling import (
    DrawRefused,
    PermutationSpec,
    SystematicSpec,
    draw,
    draw_from_spec,
    draw_systematic,
    load_draw_spec,
    read_frame,
    read_run_log,
)

NOW = datetime(2026, 10, 19, 12, 0, tzinfo=UTC)
FILTERS = {"lf": "szoo", "ind": "F", "rd": "2001-01-01:2022-12-31"}
STATUSES = {
    "w_upadlosci": {"label": "W upadłości", "filter_value": "bankrupt"},
    "w_likwidacji": {"label": "W likwidacji", "filter_value": "liquidation"},
}
# A name that must never leave the reader, in every synthetic file.
NAME = "PRZEDSIĘBIORSTWO TESTOWE"


def _krs(i: int) -> str:
    return f"{i:010d}"


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _spec(tmp_path: Path, body: dict[str, Any]) -> PermutationSpec | SystematicSpec:
    path = tmp_path / "draw.yaml"
    path.write_text(yaml.safe_dump(body, allow_unicode=True), encoding="utf-8")
    return load_draw_spec(path)


# --- Permutation design ------------------------------------------------------------------------


def _frame_file(tmp_path: Path, size: int, distress: dict[int, str], extra: str = "") -> Path:
    lines = ["krs,company_name,status"]
    for i in range(1, size + 1):
        lines.append(f"{_krs(i)},{NAME} {i},{distress.get(i, '')}")
    path = tmp_path / "frame.csv"
    path.write_text("\n".join(lines) + "\n" + extra, encoding="utf-8")
    return path


# 100 companies; 1–10 bankrupt, 11–15 in liquidation.
DISTRESS = {i: "W upadłości" for i in range(1, 11)} | {i: "W likwidacji" for i in range(11, 16)}


def _permutation_body(
    frame: Path, *, n_a: int = 20, seed: int = 7, size: int = 100
) -> dict[str, Any]:
    return {
        "design": "permutation",
        "segment": "construction_sme_v1",
        "version": 1,
        "frame": {
            "source": "rejestr_io",
            "taken_on": "2026-10-19",
            "filters": FILTERS,
            "size": size,
            "sha256": _sha(frame),
            "krs_column": "krs",
            "status_column": "status",
            "distress_sizes": {"w_upadlosci": 10, "w_likwidacji": 5},
        },
        "distress_statuses": STATUSES,
        "seed": seed,
        "random": {"list_version": "rejestr_io_v3_random", "n": n_a},
        "distress": {
            "list_version": "rejestr_io_v3_distress",
            "n": {"w_upadlosci": 4, "w_likwidacji": 2},
        },
    }


def _permutation(tmp_path: Path, **kwargs: Any):
    frame_path = _frame_file(tmp_path, 100, DISTRESS)
    spec = _spec(tmp_path, _permutation_body(frame_path, **kwargs))
    assert isinstance(spec, PermutationSpec)
    frame = read_frame(frame_path, spec, ingestion_run_id="run-1", created_at=NOW)
    return spec, frame


def test_permutation_draw_is_reproducible(tmp_path: Path):
    spec, frame = _permutation(tmp_path)
    assert draw(frame, spec) == draw(frame, spec)


def test_another_seed_draws_other_companies(tmp_path: Path):
    spec, frame = _permutation(tmp_path)
    other = spec.model_copy(update={"seed": 8})
    assert [r.krs for r in draw(frame, spec).rows] != [r.krs for r in draw(frame, other).rows]


def test_a_larger_n_extends_the_draw(tmp_path: Path):
    spec, frame = _permutation(tmp_path)
    larger = spec.model_copy(update={"random": spec.random.model_copy(update={"n": 40})})
    small = [r.krs for r in draw(frame, spec).rows if r.group == "A"]
    large = [r.krs for r in draw(frame, larger).rows if r.group == "A"]
    assert large[:20] == small


def test_groups_do_not_overlap_and_d_members_in_a_stay_in_a(tmp_path: Path):
    spec, frame = _permutation(tmp_path, n_a=60)
    rows = draw(frame, spec).rows
    a = {r.krs for r in rows if r.group == "A"}
    b = {r.krs for r in rows if r.group == "B"}
    assert not a & b
    assert any(r.stratum != "none" for r in rows if r.group == "A")
    assert all(r.stratum != "none" for r in rows if r.group == "B")


def test_permutation_pi_matches_a_hand_computation(tmp_path: Path):
    spec, frame = _permutation(tmp_path)
    rows = draw(frame, spec).rows
    group_a = [r for r in rows if r.group == "A"]
    f_a = Fraction(20, 100)
    d_a = {key: sum(1 for r in group_a if r.stratum == key) for key in STATUSES}
    expected = {
        "none": f_a,
        "w_upadlosci": f_a + (1 - f_a) * Fraction(4, 10 - d_a["w_upadlosci"]),
        "w_likwidacji": f_a + (1 - f_a) * min(Fraction(1), Fraction(2, 5 - d_a["w_likwidacji"])),
    }
    for row in rows:
        assert row.inclusion_probability == expected[row.stratum]


def test_frame_with_another_hash_is_refused(tmp_path: Path):
    frame_path = _frame_file(tmp_path, 100, DISTRESS)
    body = _permutation_body(frame_path)
    body["frame"]["sha256"] = "0" * 64
    spec = _spec(tmp_path, body)
    assert isinstance(spec, PermutationSpec)
    with pytest.raises(DrawRefused, match="SHA-256"):
        read_frame(frame_path, spec, ingestion_run_id="run-1", created_at=NOW)


@pytest.mark.parametrize(
    ("field", "value", "match"),
    [("size", 99, "N_F"), ("distress_sizes", {"w_upadlosci": 9, "w_likwidacji": 5}, "w_upadlosci")],
)
def test_frame_counts_must_match_the_spec(tmp_path: Path, field: str, value: Any, match: str):
    frame_path = _frame_file(tmp_path, 100, DISTRESS)
    body = _permutation_body(frame_path)
    body["frame"][field] = value
    if field == "size":
        body["random"]["n"] = 20
    spec = _spec(tmp_path, body)
    assert isinstance(spec, PermutationSpec)
    with pytest.raises(DrawRefused, match=match):
        read_frame(frame_path, spec, ingestion_run_id="run-1", created_at=NOW)


def test_malformed_frame_krs_is_quarantined_by_line_and_names_are_dropped(tmp_path: Path):
    frame_path = _frame_file(tmp_path, 99, DISTRESS, extra=f"12345,{NAME} X,\n")
    spec = _spec(tmp_path, _permutation_body(frame_path))
    assert isinstance(spec, PermutationSpec)
    frame = read_frame(frame_path, spec, ingestion_run_id="run-1", created_at=NOW)
    result = draw(frame, spec)
    assert [(q.entity_key, q.reason_code, q.krs) for q in result.quarantine] == [
        ("frame.csv#101", "malformed_krs", None)
    ]
    assert "0000012345" not in frame.strata
    assert NAME not in repr(result) and NAME not in repr(frame)


# --- Systematic design -------------------------------------------------------------------------

SLICES = {2001: 120, 2002: 200, 2003: 480}
D_SLICES = {2001: 10, 2002: 15, 2003: 25}


def _positions(size: int, n: int, start: str) -> list[int]:
    step = Fraction(size, n)
    return [math.floor(Fraction(start) + i * step) for i in range(n)]


def _year_of(position: int, slices: dict[int, int]) -> int:
    end = 0
    for year in sorted(slices):
        end += slices[year]
        if position < end:
            return year
    raise AssertionError(position)


def _log_text(
    *,
    slices: dict[int, int],
    n: int,
    start: str,
    seed: int,
    op: str | None,
    statuses: dict[int, str] | None = None,
    krs_base: int = 1_000,
    positions: list[int] | None = None,
    result: str = "OK",
    warnings: str = "none",
) -> str:
    size = sum(slices.values())
    raw = {"op": op, "lf": "szoo", "ind": "F", "loc": None, "rd": FILTERS["rd"], "cs": None}
    lines = [
        "REJESTR.IO SAMPLE RUN LOG",
        "=" * 60,
        "Search started : 2026-10-19 14:03:57 (Europe/Warsaw)",
        f"Result         : {result}",
        "",
        "FILTERS",
        f"  Raw filter JSON: {json.dumps(raw)}",
        "",
        "SORT ORDER",
        "  rd:desc (Data rejestracji: od najnowszej); applied inside each registration-year slice",
        "",
        "TOTALS",
        f"  Exact total returned for full range: {size}",
        f"  Sum of year slices (sampling frame): {size}",
        *(f"    {year}: {count}" for year, count in sorted(slices.items())),
        "",
        "SAMPLING",
        f"  Companies wanted : {n}",
        f"  Step             : {Fraction(size, n).numerator / Fraction(size, n).denominator:.6f}",
        f"  Start            : {start}  (random)",
        f"  Seed             : {seed}  (put in SEED setting to reproduce this run)",
        "",
        "WARNINGS",
        f"  {warnings}",
        "",
        "PICKS (in list order)",
        "  #\tglobal_pos\tyear\tpage\toffset\tkrs\tregistration_date\tremoved\tstatus\tcompany_name",
    ]
    for i, position in enumerate(positions or _positions(size, n, start)):
        year = _year_of(position, slices)
        status = (statuses or {}).get(i, "-")
        lines.append(
            f"  {i + 1}\t{position}\t{year}\t1\t0\t{_krs(krs_base + i)}\t{year}-06-30\tfalse"
            f"\t{status}\t{NAME} {i}"
        )
    return "\r\n".join(lines) + "\r\n"


def _write_log(tmp_path: Path, name: str, text: str) -> Path:
    path = tmp_path / name
    path.write_text(text, encoding="utf-8-sig")
    return path


def _run(path: Path, *, slices: dict[int, int], n: int, start: str, seed: int, version: str):
    return {
        "list_version": version,
        "log_sha256": _sha(path),
        "taken_on": "2026-10-19",
        "sort": "rd:desc",
        "year_slices": slices,
        "n": n,
        "start": start,
        "seed": seed,
    }


def _systematic_body(a_log: Path, b_log: Path | None = None) -> dict[str, Any]:
    body: dict[str, Any] = {
        "design": "systematic",
        "segment": "construction_sme_v1",
        "version": 1,
        "frame": {
            "source": "rejestr_io",
            "taken_on": "2026-10-19",
            "filters": FILTERS,
            "size": 800,
        },
        "distress_statuses": STATUSES,
        "random": _run(a_log, slices=SLICES, n=40, start="3.25", seed=11, version="v3_random"),
    }
    if b_log is not None:
        body["distress"] = {
            "w_upadlosci": _run(
                b_log, slices=D_SLICES, n=10, start="1.5", seed=12, version="v3_distress"
            )
        }
    return body


def _a_log(tmp_path: Path, **kwargs: Any) -> Path:
    text_kwargs: dict[str, Any] = {
        "slices": SLICES,
        "n": 40,
        "start": "3.25",
        "seed": 11,
        "op": None,
        "statuses": {0: "W upadłości", 5: "W likwidacji", 7: "W zawieszeniu"},
    }
    text_kwargs.update(kwargs)
    return _write_log(tmp_path, "a.txt", _log_text(**text_kwargs))


def _b_log(tmp_path: Path, **kwargs: Any) -> Path:
    text_kwargs: dict[str, Any] = {
        "slices": D_SLICES,
        "n": 10,
        "start": "1.5",
        "seed": 12,
        "op": "bankrupt",
        "statuses": dict.fromkeys(range(10), "W upadłości"),
        # The first B pick is also A's first pick (KRS 1000): two independent runs may overlap.
        "krs_base": 1_000,
    }
    text_kwargs.update(kwargs)
    return _write_log(tmp_path, "b.txt", _log_text(**text_kwargs))


def _systematic(tmp_path: Path, a_log: Path, b_log: Path | None = None) -> SystematicSpec:
    spec = _spec(tmp_path, _systematic_body(a_log, b_log))
    assert isinstance(spec, SystematicSpec)
    return spec


def _logs(a_log: Path, b_log: Path | None = None) -> dict[str, Path]:
    return {"random": a_log} | ({"w_upadlosci": b_log} if b_log else {})


def test_systematic_log_gives_every_pick_n_over_n(tmp_path: Path):
    a_log = _a_log(tmp_path)
    spec = _systematic(tmp_path, a_log)
    result = draw_systematic(spec, _logs(a_log), ingestion_run_id="run-1", created_at=NOW)
    assert len(result.rows) == 40 and result.quarantine == []
    # Without a B run, π_B is 0: every pick has π = 40/800, flagged or not.
    assert {r.inclusion_probability for r in result.rows} == {Fraction(1, 20)}
    strata = [r.stratum for r in result.rows]
    assert strata[0] == "w_upadlosci" and strata[5] == "w_likwidacji"
    assert strata[7] == "none"  # suspension is not distress
    assert [r.registration_year for r in result.rows][:3] == [2001, 2001, 2001]


def test_systematic_pi_for_two_independent_runs(tmp_path: Path):
    a_log, b_log = _a_log(tmp_path), _b_log(tmp_path)
    spec = _systematic(tmp_path, a_log, b_log)
    rows = draw_systematic(spec, _logs(a_log, b_log), ingestion_run_id="run-1", created_at=NOW).rows
    pi_a, pi_b = Fraction(40, 800), Fraction(10, 50)
    union = 1 - (1 - pi_a) * (1 - pi_b)
    # A company is in D_c if any log flags it c: B's picks are A's first ten, flagged in B alone.
    in_b = {r.krs for r in rows if r.group == "B"}
    for row in rows:
        expected = union if row.stratum == "w_upadlosci" or row.krs in in_b else pi_a
        assert row.inclusion_probability == expected
    # A company in both runs has a row in each, with the same π.
    both = [r for r in rows if r.krs == _krs(1_000)]
    assert {r.group for r in both} == {"A", "B"}
    assert len({r.inclusion_probability for r in both}) == 1


def test_systematic_draw_is_reproducible(tmp_path: Path):
    a_log = _a_log(tmp_path)
    spec = _systematic(tmp_path, a_log)
    first = draw_systematic(spec, _logs(a_log), ingestion_run_id="run-1", created_at=NOW)
    again = draw_systematic(spec, _logs(a_log), ingestion_run_id="run-1", created_at=NOW)
    assert first == again


def test_no_name_leaves_the_log_reader(tmp_path: Path):
    a_log = _a_log(tmp_path)
    assert NAME not in repr(read_run_log(a_log))
    spec = _systematic(tmp_path, a_log)
    assert NAME not in repr(
        draw_systematic(spec, _logs(a_log), ingestion_run_id="run-1", created_at=NOW)
    )


def _refused(tmp_path: Path, a_log: Path, match: str, body_change: Any = None) -> None:
    body = _systematic_body(a_log)
    if body_change is not None:
        body_change(body)
    spec = _spec(tmp_path, body)
    assert isinstance(spec, SystematicSpec)
    with pytest.raises(DrawRefused, match=match):
        draw_systematic(spec, _logs(a_log), ingestion_run_id="run-1", created_at=NOW)


def test_a_pick_off_the_rule_is_refused(tmp_path: Path):
    positions = _positions(800, 40, "3.25")
    positions[10] += 1
    _refused(tmp_path, _a_log(tmp_path, positions=positions), "line .*: position")


def test_a_log_of_another_hash_is_refused(tmp_path: Path):
    a_log = _a_log(tmp_path)

    def change(body: dict[str, Any]) -> None:
        body["random"]["log_sha256"] = "0" * 64

    _refused(tmp_path, a_log, "SHA-256", change)


def test_other_slices_are_refused(tmp_path: Path):
    a_log = _a_log(tmp_path, slices={2001: 121, 2002: 199, 2003: 480})
    _refused(tmp_path, a_log, "slices|position|year")


def test_other_filters_are_refused(tmp_path: Path):
    a_log = _a_log(tmp_path)

    def change(body: dict[str, Any]) -> None:
        body["frame"]["filters"] = {**FILTERS, "rd": "2001-01-01:2021-12-31"}

    _refused(tmp_path, a_log, "filters", change)


@pytest.mark.parametrize(
    ("kwargs", "match"),
    [({"result": "ERROR"}, "result"), ({"warnings": "page 3 retried"}, "warning")],
)
def test_a_failed_or_warned_run_is_refused(tmp_path: Path, kwargs: dict[str, str], match: str):
    _refused(tmp_path, _a_log(tmp_path, **kwargs), match)


def test_a_distress_run_without_its_status_filter_is_refused(tmp_path: Path):
    a_log, b_log = _a_log(tmp_path), _b_log(tmp_path, op=None)
    spec = _systematic(tmp_path, a_log, b_log)
    with pytest.raises(DrawRefused, match="status filter"):
        draw_systematic(spec, _logs(a_log, b_log), ingestion_run_id="run-1", created_at=NOW)


def test_a_distress_pick_of_another_status_is_refused(tmp_path: Path):
    statuses = dict.fromkeys(range(10), "W upadłości") | {3: "W likwidacji"}
    a_log, b_log = _a_log(tmp_path), _b_log(tmp_path, statuses=statuses)
    spec = _systematic(tmp_path, a_log, b_log)
    with pytest.raises(DrawRefused, match="is not 'W upadłości'"):
        draw_systematic(spec, _logs(a_log, b_log), ingestion_run_id="run-1", created_at=NOW)


def test_malformed_log_krs_is_quarantined_not_padded(tmp_path: Path):
    text = _log_text(slices=SLICES, n=40, start="3.25", seed=11, op=None)
    text = text.replace(f"\t{_krs(1_004)}\t", "\t1004\t")
    a_log = _write_log(tmp_path, "a.txt", text)
    spec = _systematic(tmp_path, a_log)
    result = draw_systematic(spec, _logs(a_log), ingestion_run_id="run-1", created_at=NOW)
    assert len(result.rows) == 39
    assert _krs(1_004) not in {r.krs for r in result.rows}
    assert [(q.reason_code, q.krs) for q in result.quarantine] == [("malformed_krs", None)]
    key = result.quarantine[0].entity_key
    assert key.startswith("a.txt#") and NAME not in key


def test_spec_refuses_a_start_outside_the_step(tmp_path: Path):
    body = _systematic_body(_a_log(tmp_path))
    body["random"]["start"] = "20"
    with pytest.raises(ValueError, match="start"):
        _spec(tmp_path, body)


def test_spec_refuses_unknown_keys(tmp_path: Path):
    body = _systematic_body(_a_log(tmp_path))
    body["frame"]["company_names"] = "kept"
    with pytest.raises(ValueError, match="company_names"):
        _spec(tmp_path, body)


def test_each_group_has_a_download_order_that_is_a_permutation(tmp_path: Path):
    a_log = _a_log(tmp_path)
    spec = _systematic(tmp_path, a_log)
    rows = draw_systematic(spec, _logs(a_log), ingestion_run_id="run-1", created_at=NOW).rows
    assert sorted(r.draw_order for r in rows) == list(range(40))
    # Not the log's order, which runs through the registration years.
    assert [r.draw_order for r in rows] != list(range(40))
    assert {r.source_sha256 for r in rows} == {_sha(a_log)}

    other = tmp_path / "permutation"
    other.mkdir()
    pspec, frame = _permutation(other)
    prow = draw(frame, pspec).rows
    assert [r.draw_order for r in prow if r.group == "A"] == list(range(20))


def test_draw_from_spec_takes_the_designs_own_source(tmp_path: Path):
    a_log = _a_log(tmp_path)
    spec = _systematic(tmp_path, a_log)
    with pytest.raises(DrawRefused, match="run logs"):
        draw_from_spec(spec, frame=a_log, logs={}, ingestion_run_id="run-1", created_at=NOW)
    result = draw_from_spec(
        spec, frame=None, logs=_logs(a_log), ingestion_run_id="run-1", created_at=NOW
    )
    assert len(result.rows) == 40


# --- A distress log with one run per status (the owner's distress script) ----------------------

L_SLICES = {2001: 6, 2002: 10, 2003: 14}


def _distress_log_text(
    *,
    frame_total: int = 800,
    statuses: dict[tuple[str, int], str] | None = None,
    warnings: str = "none",
) -> str:
    """Two runs in one log, laid out as the distress script writes it: W upadłości (n=10 of
    D_SLICES, start 1.5) then W likwidacji (n=5 of L_SLICES, start 2.25), KRS numbers from 2000."""
    runs = [
        ("W upadłości", "bankrupt", D_SLICES, 10, "1.5"),
        ("W likwidacji", "liquidation", L_SLICES, 5, "2.25"),
    ]
    raw = {"lf": "szoo", "ind": "F", "loc": None, "rd": FILTERS["rd"], "cs": None}
    lines = [
        "REJESTR.IO DISTRESSED SAMPLE RUN LOG",
        "=" * 60,
        "Search started : 2026-10-19 17:21:39 (Europe/Warsaw)",
        "Result         : OK",
        "",
        "FILTERS (common to all statuses)",
        *(
            f"  Raw filter JSON [{label}]: {json.dumps({'op': op, **raw})}"
            for label, op, *_ in runs
        ),
        "",
        "SORT ORDER",
        "  rd:desc (Data rejestracji: od najnowszej); applied inside each registration-year slice",
        "",
        "TOTALS AT EACH FILTERING STEP",
        "  Spółka z o.o.                                        845663",
        "  + Budownictwo                                        91712",
        f"  + Data rejestracji {FILTERS['rd']}             {frame_total}",
        *(
            f"  + Status {label} (op={op})                       {sum(slices.values())}"
            for label, op, slices, *_ in runs
        ),
        "    (companies with both statuses)                     2",
        "",
    ]
    for label, op, slices, n, start in runs:
        size = sum(slices.values())
        lines += [
            f"SAMPLING - {label.upper()} (op={op})",
            f"  Total sampled from : {size}  (full-range query returned {size})",
            f"  Companies wanted   : {n}   collected: {n}",
            f"  Step               : {float(Fraction(size, n)):.6f}  (= {size} / {n})",
            f"  Start              : {start}  (random, 0 <= start < step)",
            "  Totals per registration year:",
            *(f"    {year}: {count}" for year, count in sorted(slices.items())),
            "",
        ]
    lines += [
        "RUN",
        "  Seed         : 12  (put in SEED setting to reproduce this run)",
        "",
        "WARNINGS",
        f"  {warnings}",
        "",
        "PICKS (in list order)",
        (
            "  sampled_as\t#\tglobal_pos\tyear\tpage\toffset\tkrs\tregistration_date\tremoved"
            "\tstatus\tcompany_name"
        ),
    ]
    krs = 2_000
    for label, _, slices, n, start in runs:
        for i, position in enumerate(_positions(sum(slices.values()), n, start)):
            year = _year_of(position, slices)
            status = (statuses or {}).get((label, i), label)
            lines.append(
                f"  {label}\t{i + 1}\t{position}\t{year}\t1\t{position}\t{_krs(krs)}"
                f"\t{year}-06-30\tfalse\t{status}\t{NAME} {krs}"
            )
            krs += 1
    return "\r\n".join(lines) + "\r\n"


def _distress_body(a_log: Path, d_log: Path, **frame_sizes: int) -> dict[str, Any]:
    body = _systematic_body(a_log)
    runs = {
        "w_upadlosci": _run(d_log, slices=D_SLICES, n=10, start="1.5", seed=12, version="v3_d"),
        "w_likwidacji": _run(d_log, slices=L_SLICES, n=5, start="2.25", seed=12, version="v3_d"),
    }
    for key, size in frame_sizes.items():
        runs[key]["frame_size"] = size
    body["distress"] = runs
    return body


def _distress_draw(tmp_path: Path, text: str, **frame_sizes: int):
    a_log = _a_log(tmp_path)
    d_log = _write_log(tmp_path, "d.txt", text)
    spec = _spec(tmp_path, _distress_body(a_log, d_log, **frame_sizes))
    assert isinstance(spec, SystematicSpec)
    logs = {"random": a_log, "w_upadlosci": d_log, "w_likwidacji": d_log}
    return draw_systematic(spec, logs, ingestion_run_id="run-1", created_at=NOW)


def test_a_distress_log_is_read_one_run_per_status(tmp_path: Path):
    d_log = _write_log(tmp_path, "d.txt", _distress_log_text())
    bankrupt = read_run_log(d_log, "bankrupt")
    liquidation = read_run_log(d_log, "liquidation")
    assert (bankrupt.n, bankrupt.total, bankrupt.year_slices) == (10, 50, D_SLICES)
    assert (liquidation.n, liquidation.total, liquidation.year_slices) == (5, 30, L_SLICES)
    assert [p.krs for p in liquidation.picks] == [_krs(2_010 + i) for i in range(5)]
    assert bankrupt.frame_total == liquidation.frame_total == 800
    assert NAME not in repr(bankrupt) + repr(liquidation)
    with pytest.raises(DrawRefused, match="no run with the status filter"):
        read_run_log(d_log, "suspended")


def test_a_distress_log_gives_each_run_its_pi(tmp_path: Path):
    rows = _distress_draw(tmp_path, _distress_log_text()).rows
    pi_a = Fraction(40, 800)
    b = [r for r in rows if r.group == "B"]
    assert len(b) == 15 and {r.list_version for r in b} == {"v3_d"}
    by_stratum = {r.stratum: r.inclusion_probability for r in b}
    assert by_stratum == {
        "w_upadlosci": 1 - (1 - pi_a) * (1 - Fraction(10, 50)),
        "w_likwidacji": 1 - (1 - pi_a) * (1 - Fraction(5, 30)),
    }
    assert sorted(r.draw_order for r in b if r.stratum == "w_likwidacji") == list(range(5))


def test_a_pick_with_both_statuses_counts_both_runs(tmp_path: Path):
    both = "W likwidacji; W upadłości"
    rows = _distress_draw(tmp_path, _distress_log_text(statuses={("W upadłości", 3): both})).rows
    pi_a = Fraction(40, 800)
    row = next(r for r in rows if r.krs == _krs(2_003))
    assert row.stratum == "w_upadlosci"
    assert row.inclusion_probability == 1 - (1 - pi_a) * (1 - Fraction(10, 50)) * (
        1 - Fraction(5, 30)
    )


def test_a_drifted_frame_count_must_be_in_the_spec(tmp_path: Path):
    text = _distress_log_text(frame_total=802)
    with pytest.raises(DrawRefused, match="frame count is 802"):
        _distress_draw(tmp_path, text)
    rows = _distress_draw(tmp_path, text, w_upadlosci=802, w_likwidacji=802).rows
    assert len(rows) == 55


def test_a_warned_distress_log_is_refused(tmp_path: Path):
    text = _distress_log_text(warnings="W likwidacji pick #3: target already taken/missing")
    with pytest.raises(DrawRefused, match="warning"):
        _distress_draw(tmp_path, text)
