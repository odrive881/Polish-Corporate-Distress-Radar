"""The Dagster `models` group, the `backtest` job (plan 0012 step G) and the `scoring` job (plan
0016 step C)."""

import dagster as dg
from dagster_defs.definitions import defs

# Resources that reach the network; the backtest must need none of them.
NETWORK_RESOURCES = {"bir1", "rdf_browser", "krs_api", "msig_api"}


def test_the_backtest_is_downstream_of_the_store_and_the_labels() -> None:
    node = defs.resolve_asset_graph().get(dg.AssetKey("backtest_run"))
    assert node.group_name == "models"
    assert {key.to_user_string() for key in node.parent_keys} == {"feature_store", "outcome_labels"}


def test_the_backtest_job_runs_the_backtest_alone_and_offline() -> None:
    job = defs.resolve_job_def("backtest")
    selected = job.asset_layer.selected_asset_keys
    assert {key.to_user_string() for key in selected} == {"backtest_run"}
    graph = defs.resolve_asset_graph()
    needed = set(graph.get(dg.AssetKey("backtest_run")).assets_def.required_resource_keys)
    assert not needed & NETWORK_RESOURCES


def test_nothing_schedules_the_backtest() -> None:
    """A backtest is a decision, not a refresh: it is run by hand (plan 0012 step G)."""
    schedules = list(defs.schedules or [])
    sensors = list(defs.sensors or [])
    assert all(s.job_name != "backtest" for s in schedules)
    assert all("backtest" not in (getattr(s, "job_name", None) or "") for s in sensors)


def test_the_scoring_job_runs_the_scores_alone_and_offline() -> None:
    """Plan 0016 step C: downstream of the store, the labels and the backtest's registry."""
    node = defs.resolve_asset_graph().get(dg.AssetKey("scores"))
    assert node.group_name == "models"
    assert {key.to_user_string() for key in node.parent_keys} == {
        "feature_store",
        "outcome_labels",
        "backtest_run",
    }
    job = defs.resolve_job_def("scoring")
    assert {key.to_user_string() for key in job.asset_layer.selected_asset_keys} == {"scores"}
    assert not set(node.assets_def.required_resource_keys) & NETWORK_RESOURCES


def test_nothing_schedules_the_scoring() -> None:
    """Scheduling is Phase 10 (plan 0016 decision 5)."""
    schedules = list(defs.schedules or [])
    sensors = list(defs.sensors or [])
    assert all(s.job_name != "scoring" for s in schedules)
    assert all("scoring" not in (getattr(s, "job_name", None) or "") for s in sensors)
