"""Dagster Definitions entry point.

Run locally with:

    uv run dagster dev -m dagster_defs.definitions

(module form, run from the repo root — not `-f dagster_defs/definitions.py`,
which loads this file outside its package and breaks the absolute imports
below).

Orchestration wiring only: this module and its `assets/`/`checks/` siblings
import from `src/distress_radar/`, never the reverse. See
`DIRECTORY_STRUCTURE.md` §3 "The core boundary".
"""

from __future__ import annotations

import dagster as dg

from dagster_defs.assets.acquisition import acquisition_assets

defs = dg.Definitions(assets=acquisition_assets)
