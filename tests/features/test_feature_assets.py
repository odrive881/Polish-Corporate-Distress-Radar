"""The Dagster `features` group and job (plan 0010 step G)."""

import dagster as dg
from dagster_defs.assets.features import COVERAGE_CHECK, LEAKAGE_CHECK
from dagster_defs.definitions import defs

# Resources that reach the network; the features job must need none of them.
NETWORK_RESOURCES = {"bir1", "rdf_browser", "krs_api", "msig_api"}


def test_the_store_carries_a_blocking_leakage_check_and_a_coverage_report() -> None:
    graph = defs.resolve_asset_graph()
    checks = {
        key.name: graph.get_check_spec(key)
        for key in graph.asset_check_keys
        if key.asset_key.to_user_string() == "feature_store"
    }
    assert set(checks) == {LEAKAGE_CHECK, COVERAGE_CHECK}
    assert checks[LEAKAGE_CHECK].blocking
    assert not checks[COVERAGE_CHECK].blocking


def test_the_features_job_rebuilds_its_inputs_then_the_store_offline() -> None:
    job = defs.resolve_job_def("features")
    selected = {key.to_user_string() for key in job.asset_layer.selected_asset_keys}
    assert selected == {
        "financial_statements_canonical",
        "restatement_events",
        "legal_events",
        "feature_store",
    }
    graph = defs.resolve_asset_graph()
    needed: set[str] = set()
    for key in job.asset_layer.selected_asset_keys:
        needed |= set(graph.get(key).assets_def.required_resource_keys)
    assert not needed & NETWORK_RESOURCES


def test_the_store_is_downstream_of_what_it_reads() -> None:
    node = defs.resolve_asset_graph().get(dg.AssetKey("feature_store"))
    parents = {key.to_user_string() for key in node.parent_keys}
    assert parents == {"financial_statements_canonical", "restatement_events", "legal_events"}
