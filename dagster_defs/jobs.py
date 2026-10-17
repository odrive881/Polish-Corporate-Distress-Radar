"""The jobs (plan 0018 step C moved them here from `definitions.py`, so sensors can name them).

Wiring only. A job and an asset never share a name: Dagster rejects the repository when it
resolves every job (plan 0018 step B; `tests/test_orchestration.py`).
"""

from __future__ import annotations

import dagster as dg

from distress_radar import errors


@dg.failure_hook
def report_failure(context: dg.HookContext) -> None:
    """A failed step's exception to Sentry, scrubbed (plan 0018 step H); nothing when it is off."""
    if context.op_exception is not None:
        errors.report(context.op_exception, job=context.job_name, asset=context.op.name)


# Every job carries it: Dagster catches a step's exception, so no excepthook ever sees one.
HOOKS = {report_failure}

# Plan 0008: registry bytes to a frozen label set in one run (KRS and MSiG fetched, legal
# events rebuilt, label models rebuilt and audited, the set frozen).
legal_to_labels = dg.define_asset_job(
    "legal_to_labels",
    selection=dg.AssetSelection.groups("legal", "labels"),
    hooks=HOOKS,
    description="A4 fetches → legal_events → label models and audits → frozen outcome_labels.",
)

# Plan 0010 step G: the stored datasets the store reads, rebuilt, then the store. Offline: no
# asset in it fetches (owner decision 4 for steps F-H); `legal_to_labels` does the fetching.
features = dg.define_asset_job(
    "features",
    selection=dg.AssetSelection.assets(
        "financial_statements_canonical", "restatement_events", "legal_events", "feature_store"
    ),
    hooks=HOOKS,
    description="Parsing outputs and legal events rebuilt from stored bytes → feature_store.",
)

# Plan 0012 step G: the backtest, alone and by hand. No schedule: a backtest is a decision, not a
# refresh. It reads the stored `feature_store` and a frozen label set; it rebuilds neither.
backtest = dg.define_asset_job(
    "backtest",
    selection=dg.AssetSelection.assets("backtest_run"),
    hooks=HOOKS,
    description="The out-of-time backtest: report under WAREHOUSE_DIR, one MLflow run per cell set.",
)

# Plan 0016 step C: the served model's scores at the latest month-end, by hand like the backtest
# (scheduling is Phase 10). It reads the registry and the stored datasets; it rebuilds neither.
scoring = dg.define_asset_job(
    "scoring",
    selection=dg.AssetSelection.assets("scores"),
    hooks=HOOKS,
    description="The served model's scores at the latest month-end → Postgres scores_history.",
)

# Plan 0016 step F: the public site's database, by hand, from a clean tree. It reads the DQ marts,
# the backtest's MLflow runs and predictions; it rebuilds none of them, and deploys nothing.
publish = dg.define_asset_job(
    "publish",
    selection=dg.AssetSelection.assets("publish_database"),
    hooks=HOOKS,
    description="Aggregates only, small cells suppressed → site/sources/publish/publish.duckdb.",
)

# Plan 0013 step H: the notes of every stored statement read into `text_signals` and
# `text_coverage`. By hand: model signals cost money once confirmed, and responses are stored,
# so a rerun replays them.
text = dg.define_asset_job(
    "text",
    selection=dg.AssetSelection.groups("text"),
    hooks=HOOKS,
    description="Notes embedded in stored statements → text_signals, text_coverage.",
)

# Plan 0018 step C (decision 3): the re-search queue for the owner's script; its schedules are in
# `schedules.py`. It reads Postgres and writes one file; it fetches nothing.
rdf_queue = dg.define_asset_job(
    "rdf_queue",
    selection=dg.AssetSelection.assets("rdf_research_queue"),
    hooks=HOOKS,
    description="Which companies the owner's RDF script should search next → .cache/rdf_queue/.",
)

# Plan 0018 step D: the alerts, after new features and after new scores (`sensors.py`). Not named
# `alerts`, the asset's name. Reads Postgres and the stored store; local only, sends nothing.
alerting = dg.define_asset_job(
    "alerting",
    selection=dg.AssetSelection.assets("alerts"),
    hooks=HOOKS,
    description="Score crossings and jumps, tripwires, going-concern flags, overdue filings → alerts.",
)

# Plan 0018 steps F and G: the served model's input drift and the stored scores' realised
# calibration, after each new scoring run (`sensors.py`). Local reports only (decision 6).
monitoring = dg.define_asset_job(
    "monitoring",
    selection=dg.AssetSelection.groups("monitoring"),
    hooks=HOOKS,
    description="Input drift and realised calibration → reports/drift/, reports/calibration/.",
)

jobs = [
    legal_to_labels,
    features,
    backtest,
    scoring,
    publish,
    text,
    rdf_queue,
    alerting,
    monitoring,
]
