"""The Dagster `labels` group against the SQLMesh project (plan 0008 step G).

As for the DQ group: the static audit list must equal what the project declares, so an audit
added, renamed or dropped fails here rather than as a surprise check result mid-run.
"""

from dagster_defs.assets.labels import LABEL_AUDITS
from dagster_defs.definitions import defs

from distress_radar.transform_project import LABEL_MODELS, LABEL_RESTATED, declared_audits


def test_static_audit_list_matches_the_project() -> None:
    declared = declared_audits(models=LABEL_MODELS)
    assert {model: list(audits) for model, audits in LABEL_AUDITS.items()} == declared


def test_every_audit_is_an_asset_check_on_its_models_asset() -> None:
    graph = defs.resolve_asset_graph()
    checks = {(key.asset_key.path[-1], key.name) for key in graph.asset_check_keys}
    expected = {(LABEL_MODELS[m], a) for m, audits in LABEL_AUDITS.items() for a in audits}
    assert expected <= checks


def test_every_table_model_is_restated() -> None:
    assert set(LABEL_RESTATED) <= set(LABEL_MODELS)
    assert set(LABEL_MODELS) - set(LABEL_RESTATED) == {"staging.legal_events_canonical"}
