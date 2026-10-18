"""Whether the ministry's open sources are up again (plan 0018, retry after a maintenance page).

The nightly registry check stops at the first maintenance page (`SourceUnavailable`, 2026-10-18),
and its retry waits until both sources answer. The probe asks each one question whose answer
names no one: the KRS extract of a number no company has, and the MSiG notice count for it. So
nothing a probe receives could hold personal data (invariant 6), and nothing it receives is kept:
no raw store, no HTTP cache, no log of the body. One request per source per probe, far under
ADR 0011's pacing.
"""

from __future__ import annotations

from datetime import date

import httpx

from distress_radar.acquisition.base import USER_AGENT, ms_maintenance_page
from distress_radar.acquisition.krs_extract import EXTRACT_PARAMS, KRS_API
from distress_radar.acquisition.msig_client import MSIG_API, SEARCH_FROM

# No company has KRS number 0000000000 (numbering starts at 1), so both answers are empty.
PROBE_KRS = "0000000000"
TIMEOUT_SECONDS = 30.0


def probes(today: date) -> dict[str, tuple[str, dict[str, str]]]:
    """Source name → (URL, query) of its probe."""
    return {
        "KRS API": (f"{KRS_API}/OdpisAktualny/{PROBE_KRS}", EXTRACT_PARAMS),
        "MSiG": (
            f"{MSIG_API}/Monitor/SearchCount",
            {"krs": PROBE_KRS, "signatureType": "A", "from": SEARCH_FROM, "to": today.isoformat()},
        ),
    }


def unavailable_reason(response: httpx.Response) -> str | None:
    """Why a probe's answer says the source is down, or None if it is up. A 404 is up: it is the
    source answering that the number does not exist."""
    page = ms_maintenance_page(response)
    if page is not None:
        return page
    if response.status_code >= 500 or response.status_code == 429:
        return f"HTTP {response.status_code}"
    return None


async def probe_sources(
    today: date, transport: httpx.AsyncBaseTransport | None = None
) -> dict[str, str | None]:
    """Source name → None if it answered, else why not."""
    found: dict[str, str | None] = {}
    async with httpx.AsyncClient(
        headers={"User-Agent": USER_AGENT},
        timeout=TIMEOUT_SECONDS,
        follow_redirects=True,
        transport=transport,
    ) as client:
        for source, (url, params) in probes(today).items():
            try:
                found[source] = unavailable_reason(await client.get(url, params=params))
            except httpx.HTTPError as exc:
                found[source] = f"{type(exc).__name__}"
    return found
