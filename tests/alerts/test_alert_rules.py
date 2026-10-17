"""The alert rules (plan 0018 step D, decision 4), on hand-built frames."""

from __future__ import annotations

from datetime import UTC, date, datetime

import polars as pl

from distress_radar.alerts.rules import (
    SCORES_SCHEMA,
    SEARCHES_SCHEMA,
    STATEMENTS_SCHEMA,
    feature_alerts,
    filing_alerts,
    load_alert_rules,
    score_alerts,
)
from distress_radar.features.statutory import load_filing_deadlines

RULES = load_alert_rules()
KRS = "0000000001"
M0, M1, M2, M3 = date(2026, 9, 30), date(2026, 10, 31), date(2026, 11, 30), date(2026, 12, 31)


def _scores(*rows: tuple[date, float | None, str]) -> pl.DataFrame:
    return pl.DataFrame(
        [
            {
                "krs": KRS,
                "as_of_date": d,
                "horizon_months": 12,
                "probability": p,
                "model_name": "distress_radar_12m",
                "model_version": v,
            }
            for d, p, v in rows
        ],
        schema=SCORES_SCHEMA,
    )


def _features(*rows: tuple[date, bool | None, bool | None]) -> pl.DataFrame:
    """Two flags at once: `art233_triggered` and `going_concern_threat`."""
    return pl.DataFrame(
        [
            {
                "krs": KRS,
                "as_of_date": d,
                "art233_triggered": a,
                "art233_triggered__known_from": None if a is None else date(2026, 7, 1),
                "going_concern_threat": g,
                "going_concern_threat__known_from": None if g is None else date(2026, 7, 1),
                "negative_equity": None,
                "negative_equity__known_from": None,
                "going_concern_basis_abandoned": None,
                "going_concern_basis_abandoned__known_from": None,
                "going_concern_in_notes": None,
                "going_concern_in_notes__known_from": None,
                "modified_opinion": None,
                "modified_opinion__known_from": None,
            }
            for d, a, g in rows
        ],
        schema_overrides={
            "art233_triggered__known_from": pl.Date,
            "going_concern_threat__known_from": pl.Date,
            "negative_equity": pl.Boolean,
            "negative_equity__known_from": pl.Date,
            "going_concern_basis_abandoned": pl.Boolean,
            "going_concern_basis_abandoned__known_from": pl.Date,
            "going_concern_in_notes": pl.Boolean,
            "going_concern_in_notes__known_from": pl.Date,
            "modified_opinion": pl.Boolean,
            "modified_opinion__known_from": pl.Date,
        },
    )


def test_a_crossing_fires_once_and_a_first_score_never():
    alerts = score_alerts(
        _scores((M0, 0.30, "5"), (M1, 0.08, "5"), (M2, 0.12, "5"), (M3, 0.14, "5")), RULES
    )
    # 0.30 is a first score: nothing. 0.08 → 0.12 crosses 0.10 once; 0.14 stays above it.
    assert [(a.as_of_date, a.alert_type, a.severity) for a in alerts] == [
        (M2, "threshold_crossed", "medium")
    ]
    assert alerts[0].triggering_score_id == f"{KRS}/2026-11-30/12m/distress_radar_12m/v5"


def test_crossing_two_levels_at_once_gives_the_higher():
    (alert, jump) = score_alerts(_scores((M0, 0.05, "5"), (M1, 0.40, "5")), RULES)
    assert (alert.alert_type, alert.severity, alert.detail["level"]) == (
        "threshold_crossed",
        "high",
        0.25,
    )
    assert jump.alert_type == "score_jump"


def test_a_jump_needs_both_its_increase_and_its_ratio():
    assert score_alerts(_scores((M0, 0.02, "5"), (M1, 0.09, "5")), RULES) == []  # ×4.5, +0.07
    assert score_alerts(_scores((M0, 0.40, "5"), (M1, 0.55, "5")), RULES) == []  # +0.15, ×1.4
    (jump,) = score_alerts(_scores((M0, 0.15, "5"), (M1, 0.31, "5")), RULES)[1:]
    assert jump.alert_type == "score_jump"


def test_a_new_model_version_is_a_new_series_and_no_jump():
    assert score_alerts(_scores((M0, 0.02, "5"), (M1, 0.60, "6")), RULES) == []


def test_a_score_with_incomplete_inputs_breaks_the_series():
    assert score_alerts(_scores((M0, 0.05, "5"), (M1, None, "5"), (M2, 0.40, "5")), RULES) == []


def test_a_flag_fires_on_turning_true_only():
    alerts = feature_alerts(
        _features(
            (date(2026, 8, 31), None, None),  # before alerts_from: history, never alerted
            (M0, False, None),
            (M1, True, True),  # both turn true: one alert each, from false and from null
            (M2, True, True),  # still true: nothing
            (M3, False, True),
        ),
        RULES,
    )
    assert sorted((a.alert_type, a.detail["feature"], a.as_of_date) for a in alerts) == [
        ("going_concern_flagged", "going_concern_threat", M1),
        ("tripwire_triggered", "art233_triggered", M1),
    ]
    assert all(a.known_from == date(2026, 7, 1) <= a.as_of_date for a in alerts)


def test_an_entitys_first_month_end_alerts_nothing():
    assert feature_alerts(_features((M1, True, True)), RULES) == []


def test_an_alert_at_a_month_end_is_unchanged_by_later_facts():
    """Point in time (invariant 1): later month-ends add alerts, never change earlier ones."""
    early = _features((M0, False, False), (M1, True, False))
    later = pl.concat([early, _features((M2, True, True), (M3, False, True))])
    before = {a.alert_id: a for a in feature_alerts(early, RULES)}
    after = {a.alert_id: a for a in feature_alerts(later, RULES)}
    assert before and all(after[i] == a for i, a in before.items())
    assert {a.as_of_date for i, a in after.items() if i not in before} == {M2}


def _searches(*rows: tuple[str, bool]) -> pl.DataFrame:
    return pl.DataFrame(
        [
            {
                "krs": KRS,
                "searched_at": datetime.fromisoformat(f"{t}T08:00:00").replace(tzinfo=UTC),
                "complete": c,
            }
            for t, c in rows
        ],
        schema=SEARCHES_SCHEMA,
    )


def _statements(*rows: tuple[date, date]) -> pl.DataFrame:
    return pl.DataFrame(
        [{"krs": KRS, "period_end": p, "submission_date": s} for p, s in rows],
        schema=STATEMENTS_SCHEMA,
    )


def test_a_filing_is_overdue_only_at_a_complete_search_after_its_deadline():
    statements = _statements((date(2024, 12, 31), date(2025, 7, 1)))  # FY2025 due 2026-07-15
    deadlines = load_filing_deadlines()

    incomplete = filing_alerts(_searches(("2026-10-01", False)), statements, deadlines, RULES)
    assert incomplete == []  # an incomplete search judges nothing

    alerts = filing_alerts(
        _searches(("2026-10-01", True), ("2026-10-10", True)), statements, deadlines, RULES
    )
    (alert,) = alerts  # one alert per missing period, at the first search showing it
    assert (alert.as_of_date, alert.known_from) == (date(2026, 10, 1), date(2026, 10, 1))
    assert alert.detail["expected_period_end"] == "2025-12-31"
    assert alert.detail["deadline"] == "2026-07-15"

    filed = _statements(
        (date(2024, 12, 31), date(2025, 7, 1)), (date(2025, 12, 31), date(2026, 7, 10))
    )
    assert filing_alerts(_searches(("2026-10-01", True)), filed, deadlines, RULES) == []


def test_a_filer_that_stopped_cycles_ago_is_alerted_by_v1_only():
    """Owner, 2026-10-17 (`alert_rules_v2`): only the latest period due is judged."""
    stopped = _statements((date(2022, 12, 31), date(2023, 7, 1)))  # FY2023 and FY2024 also missing
    searches = _searches(("2026-10-01", True))
    deadlines = load_filing_deadlines()
    assert filing_alerts(searches, stopped, deadlines, RULES) == []
    (old,) = filing_alerts(searches, stopped, deadlines, load_alert_rules("alert_rules_v1"))
    assert old.detail["expected_period_end"] == "2023-12-31"


def test_v2_changes_only_the_look_back():
    v1, v2 = load_alert_rules("alert_rules_v1"), RULES
    assert (v2.version, v2.filing_overdue.look_back_cycles, v1.filing_overdue.look_back_cycles) == (
        "alert_rules_v2",
        1,
        None,
    )
    skip = {"version", "filing_overdue"}
    assert v1.model_dump(exclude=skip) == v2.model_dump(exclude=skip)


def test_a_statement_submitted_after_the_search_is_not_seen_by_it():
    statements = _statements(
        (date(2024, 12, 31), date(2025, 7, 1)), (date(2025, 12, 31), date(2026, 10, 5))
    )
    (alert,) = filing_alerts(
        _searches(("2026-10-01", True), ("2026-10-10", True)),
        statements,
        load_filing_deadlines(),
        RULES,
    )
    assert alert.as_of_date == date(2026, 10, 1)


def test_reruns_give_the_same_ids():
    scores = _scores((M0, 0.05, "5"), (M1, 0.40, "5"))
    assert [a.alert_id for a in score_alerts(scores, RULES)] == [
        a.alert_id for a in score_alerts(scores, RULES)
    ]
