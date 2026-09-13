"""Dagster assets for stage A (acquisition). See AGENT_SPEC.md §6A.

Currently holds only `hello_world`, a Phase 0 placeholder proving the
orchestration wiring (Definitions entry point, asset materialization) works
end to end. It has no real inputs and produces no domain data. Replace it
with the real A1 `universe_discovery` asset — wrapping
`distress_radar.acquisition.universe_discovery` — when Phase 1 starts; see
`docs/plans/0001-phase-0-completion.md`.
"""

from __future__ import annotations

import dagster as dg


@dg.asset
def hello_world() -> str:
    """Phase 0 smoke-test asset.

    Inputs: none.
    Outputs: a fixed greeting string, materialized as the asset's value.
    Partition scheme: none (unpartitioned) — real acquisition assets will
    partition by discovery run, not by fiscal_year/as_of_month (those apply
    from stage C/H onward).
    """
    return "hello from distress_radar's Dagster wiring"


acquisition_assets = [hello_world]
