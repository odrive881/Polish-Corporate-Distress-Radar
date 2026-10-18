"""A source down for every request stops the legal fetches at once (2026-10-18).

That night the open KRS API and MSiG served a maintenance page for hours; each asset spent
54 minutes of pacing on 806 entities and reported each as failed. Now the first such answer
stops the run, and nothing is recorded for the entities after it.
"""

from __future__ import annotations

import asyncio
from collections import Counter
from datetime import date
from typing import Any

import dagster as dg
import pytest
from dagster_defs.assets import legal

from distress_radar.acquisition.base import PermanentSourceError, SourceUnavailable
from distress_radar.acquisition.msig_client import load_vocabulary

ENTITIES = ["0000000001", "0000000002", "0000000003"]


def _down_from(second: bool, calls: list[str]):
    async def fetch(krs: str, **_: Any) -> Any:
        calls.append(krs)
        if krs == ENTITIES[0] and second:
            raise PermanentSourceError(f"KRS {krs}: one bad record")
        raise SourceUnavailable("krs_api is unavailable: maintenance page (Przerwa techniczna)")

    return fetch


@pytest.mark.parametrize("after_one", [False, True])
def test_the_krs_fetch_stops_at_the_first_maintenance_page(
    monkeypatch: pytest.MonkeyPatch, after_one: bool
):
    calls: list[str] = []
    monkeypatch.setattr(legal, "fetch_extract", _down_from(after_one, calls))
    monkeypatch.setattr(legal.manifest, "latest_legal_fetch", lambda *_: None)
    with pytest.raises(dg.Failure, match="KRS API: .*unavailable") as info:
        asyncio.run(legal._fetch_all(dg.build_asset_context(), None, None, None, ENTITIES))  # type: ignore[arg-type]
    done = 1 if after_one else 0
    assert calls == ENTITIES[: done + 1]
    assert f"stopped after {done} of 3 entities" in str(info.value.description)


def test_the_msig_search_stops_at_the_first_maintenance_page(monkeypatch: pytest.MonkeyPatch):
    calls: list[str] = []
    monkeypatch.setattr(legal, "fetch_entity_notices", _down_from(False, calls))
    monkeypatch.setattr(legal.manifest, "known_msig_notices", lambda *_: set())
    failures: list[str] = []
    with pytest.raises(dg.Failure, match="MSiG: .*unavailable"):
        asyncio.run(
            legal._search_all(
                dg.build_asset_context(),
                None,  # type: ignore[arg-type]
                None,  # type: ignore[arg-type]
                None,  # type: ignore[arg-type]
                ENTITIES,
                load_vocabulary(),
                date(2026, 10, 18),
                Counter(),
                failures,
            )
        )
    assert calls == ENTITIES[:1] and failures == []
