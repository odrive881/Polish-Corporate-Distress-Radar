"""E2 asset checks: one named check per accounting identity (AGENT_SPEC §4.3, §6E2).

Each check re-runs its rule from `distress_radar.parsing.accounting_identities`
on the materialized canonical facts. A check passes when every file that fails
the rule is graded accordingly (`quarantined`, or `warn` for small subtotal
differences), i.e. no failing figure reaches consumers as `pass`. The failing
entity/period set is reported either way.
"""

from collections.abc import Callable
from decimal import Decimal

import dagster as dg
import polars as pl

from dagster_defs.assets.parsing import (
    CANONICAL,
    RESTATEMENTS,
    financial_statements_canonical,
    restatement_events,
)
from distress_radar.parsing.accounting_identities import CHECKS, IDENTITY_CHECKS
from distress_radar.parsing.canonical_schema import MappingConfig, load_mapping_config
from distress_radar.settings import Settings
from distress_radar.warehouse import read_dataset

MAX_LISTED = 50


def _identity_check(name: str) -> dg.AssetChecksDefinition:
    rule: Callable[[pl.DataFrame, MappingConfig, Decimal], pl.DataFrame] = CHECKS[name]

    @dg.asset_check(asset=financial_statements_canonical, name=name, description=rule.__doc__)
    def _check() -> dg.AssetCheckResult:
        settings = Settings()
        facts = read_dataset(settings.warehouse_dir, CANONICAL)
        results = rule(facts, load_mapping_config(), settings.identity_tolerance_pln)
        failed = results.filter(pl.col("status") == "fail").join(
            facts.select("source_document_hash", "source_member", "quality_grade").unique(),
            on=["source_document_hash", "source_member"],
            how="left",
        )
        ungraded = failed.filter(pl.col("quality_grade") == "pass")
        failing_files = (
            failed.select("krs", "period_end", "document_ref", "quality_grade")
            .unique()
            .sort("krs", "period_end", "document_ref")
        )
        counts = dict(sorted(results.group_by("status").len().iter_rows()))
        return dg.AssetCheckResult(
            passed=ungraded.is_empty(),
            metadata={
                "results": counts,
                "failing_files": failing_files.height,
                "failing_files_listed": [
                    f"{r['krs']} {r['period_end']} {r['document_ref']} ({r['quality_grade']})"
                    for r in failing_files.head(MAX_LISTED).iter_rows(named=True)
                ],
                "failures_graded_pass": ungraded.height,
            },
        )

    return _check


@dg.asset_check(asset=restatement_events, name="prior_year_consistency")
def prior_year_consistency_check() -> dg.AssetCheckResult:
    """Restatements are findings, not failures: always passes, reports counts."""
    events = read_dataset(Settings().warehouse_dir, RESTATEMENTS)
    return dg.AssetCheckResult(
        passed=True,
        metadata={
            "restatement_events": events.height,
            "entities": events.select("krs").unique().height,
        },
    )


accounting_identity_checks = [_identity_check(name) for name in IDENTITY_CHECKS] + [
    prior_year_consistency_check
]
