"""A3 — the re-search queue for the owner's PAD script (plan 0018 step C, decision 3).

RDF is never fetched by the pipeline (ADR 0013): statements arrive only through the owner's script.
What the pipeline can do is say which companies the script should search next, so AGENT_SPEC §6L's
"weekly filing check" and the early-summer filing wave become a list the script reads. Each entity
of `entity_master` lands in the first tier that fits it:

1. `not_searched`: no stored search. A search that found nothing during an outage is never stored
   (plan 0014), so a company reached only in an outage is here too. Sources complete by capture
   (the seed, plan 0003) are not: they were captured by hand.
2. `search_incomplete`: its latest search did not list every document.
3. `filing_due`: its next statement's deadline (`config/statutory/filing_deadlines.yaml`, from
   the latest listed period end plus twelve months) has passed since its last complete search,
   so RDF may hold a statement the pipeline has not seen.
4. `oldest_search`: everything else, the longest since a complete search first.

Within a tier: the oldest search first (never searched before any), then by KRS. The file holds
KRS numbers only, one a line (ADR 0014: nothing else of the list leaves the pipeline), and the
same inputs on the same date give the same bytes.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Literal
from zoneinfo import ZoneInfo

import polars as pl
from psycopg import Connection

from distress_radar.features.statutory import FilingDeadlines, add_months

Tier = Literal["not_searched", "search_incomplete", "filing_due", "oldest_search"]
TIERS: tuple[Tier, ...] = ("not_searched", "search_incomplete", "filing_due", "oldest_search")
SEARCHES_SCHEMA: dict[str, pl.DataType | type[pl.DataType]] = {
    "krs": pl.String,
    "searched_at": pl.Datetime("us", "UTC"),
    "complete": pl.Boolean,
}
STATEMENTS_SCHEMA: dict[str, pl.DataType | type[pl.DataType]] = {
    "krs": pl.String,
    "period_end": pl.Date,
}
_WARSAW = ZoneInfo("Europe/Warsaw")


@dataclass(frozen=True)
class QueueEntry:
    krs: str
    tier: Tier
    last_complete_search: date | None  # Warsaw date
    next_deadline: date | None


def build_queue(
    entities: Iterable[str],
    searches: pl.DataFrame,
    statements: pl.DataFrame,
    *,
    captured: Iterable[str],
    deadlines: FilingDeadlines,
    on: date,
) -> list[QueueEntry]:
    """Every entity in `entities`, in the order the script should search them on `on`.

    `searches`: stored RDF searches (`SEARCHES_SCHEMA`), `rdf_listed_entities`.
    `statements`: the period ends of listed, non-deleted statements (`STATEMENTS_SCHEMA`).
    `captured`: entities whose source is complete by capture (the seed).
    """
    captured_set = set(captured)
    latest: dict[str, bool] = {
        r["krs"]: r["complete"]
        for r in searches.sort("krs", "searched_at")
        .group_by("krs", maintain_order=True)
        .last()
        .iter_rows(named=True)
    }
    last_complete: dict[str, date] = {
        r["krs"]: r["searched_at"].astimezone(_WARSAW).date()
        for r in searches.filter(pl.col("complete"))
        .group_by("krs")
        .agg(pl.col("searched_at").max())
        .iter_rows(named=True)
    }
    period_ends: dict[str, date] = {
        r["krs"]: r["period_end"]
        for r in statements.group_by("krs").agg(pl.col("period_end").max()).iter_rows(named=True)
    }
    entries: list[QueueEntry] = []
    for krs in sorted(set(entities)):
        searched = last_complete.get(krs)
        period_end = period_ends.get(krs)
        due = deadlines.deadline(add_months(period_end, 12)) if period_end is not None else None
        tier: Tier
        if krs not in latest and krs not in captured_set:
            tier = "not_searched"
        elif krs in latest and not latest[krs]:
            tier = "search_incomplete"
        elif due is not None and due <= on and (searched is None or searched < due):
            tier = "filing_due"
        else:
            tier = "oldest_search"
        entries.append(QueueEntry(krs, tier, searched, due))
    entries.sort(
        key=lambda e: (
            TIERS.index(e.tier),
            e.last_complete_search is not None,
            e.last_complete_search or date.min,
            e.krs,
        )
    )
    return entries


def read_queue(
    conn: Connection,
    *,
    complete_by_capture: Iterable[str],
    download_codes: Iterable[str],
    deadlines: FilingDeadlines,
    on: date,
) -> list[QueueEntry]:
    """`build_queue` over the Postgres records: `entity_master`, `rdf_listed_entities`,
    `filing_index` (listed statements of a downloaded type, not deleted, as the population rule
    reads them) and `universe_candidates` (the sources complete by capture)."""
    entities = [r[0] for r in conn.execute("SELECT trim(krs) FROM entity_master").fetchall()]
    searches = pl.DataFrame(
        conn.execute("SELECT trim(krs), searched_at, complete FROM rdf_listed_entities").fetchall(),
        schema=SEARCHES_SCHEMA,
        orient="row",
    )
    statements = pl.DataFrame(
        conn.execute(
            """
            SELECT trim(krs), period_end FROM filing_index
            WHERE status IS DISTINCT FROM 'USUNIETY' AND deleted_on IS NULL
              AND rdf_type_code = ANY(%s)
            """,
            (sorted(download_codes),),
        ).fetchall(),
        schema=STATEMENTS_SCHEMA,
        orient="row",
    )
    captured = [
        r[0]
        for r in conn.execute(
            "SELECT DISTINCT trim(krs) FROM universe_candidates WHERE discovery_source = ANY(%s)",
            (sorted(complete_by_capture),),
        ).fetchall()
    ]
    return build_queue(
        entities, searches, statements, captured=captured, deadlines=deadlines, on=on
    )


def write_queue(entries: Iterable[QueueEntry], directory: Path, on: date) -> Path:
    """`queue_<on>.txt` in `directory`: the KRS numbers in order, one a line."""
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"queue_{on.isoformat()}.txt"
    path.write_text("".join(f"{e.krs}\n" for e in entries), encoding="ascii")
    return path
