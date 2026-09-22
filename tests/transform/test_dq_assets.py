"""The Dagster DQ group against the SQLMesh project (plan 0007 step F).

`dagster_defs/assets/dq.py` lists every audit statically, so its asset checks
exist before any run. These tests hold that list equal to what the project
declares (read on the in-memory `test` gateway: no services), so adding,
renaming or dropping an audit fails here rather than as an unexpected check
result in the middle of a Dagster run.
"""

from dagster_defs.assets.dq import DQ_AUDITS
from dagster_defs.definitions import defs

from distress_radar.transform_project import DQ_MODELS, RESTATED, declared_audits


def test_static_audit_list_matches_the_project() -> None:
    assert {model: list(audits) for model, audits in DQ_AUDITS.items()} == declared_audits()


def test_every_audit_is_an_asset_check_on_its_models_asset() -> None:
    graph = defs.resolve_asset_graph()
    checks = {(key.asset_key.path[-1], key.name) for key in graph.asset_check_keys}
    expected = {(DQ_MODELS[m], audit) for m, audits in DQ_AUDITS.items() for audit in audits}
    assert expected <= checks


def test_every_table_model_is_restated_and_every_restated_model_is_an_asset() -> None:
    assert set(RESTATED) <= set(DQ_MODELS)
    assert set(DQ_MODELS) - set(RESTATED) == {"staging.parsed_documents_current"}
