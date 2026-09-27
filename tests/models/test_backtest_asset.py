"""The Dagster `models` group and `backtest` job (plan 0012 step G)."""

import dagster as dg
from dagster_defs.definitions import defs

# Resources that reach the network; the backtest must need none of them.
NETWORK_RESOURCES = {"bir1", "rdf_browser", "krs_api", "msig_api"}


def test_the_backtest_is_downstream_of_the_store_and_the_labels() -> None:
    node = defs.resolve_asset_graph().get(dg.AssetKey("backtest"))
    assert node.group_name == "models"
    assert {key.to_user_string() for key in node.parent_keys} == {"feature_store", "outcome_labels"}


def test_the_backtest_job_runs_the_backtest_alone_and_offline() -> None:
    job = defs.resolve_job_def("backtest")
    selected = job.asset_layer.selected_asset_keys
    assert {key.to_user_string() for key in selected} == {"backtest"}
    graph = defs.resolve_asset_graph()
    needed = set(graph.get(dg.AssetKey("backtest")).assets_def.required_resource_keys)
    assert not needed & NETWORK_RESOURCES


def test_nothing_schedules_the_backtest() -> None:
    """A backtest is a decision, not a refresh: it is run by hand (plan 0012 step G)."""
    schedules = list(defs.schedules or [])
    sensors = list(defs.sensors or [])
    assert all(s.job_name != "backtest" for s in schedules)
    assert all("backtest" not in (getattr(s, "job_name", None) or "") for s in sensors)
