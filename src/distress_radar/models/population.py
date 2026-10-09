"""The modelling population (plan 0015, owner decision 10): entities whose filings are all in.

A model that reads nulls natively cannot tell a company that filed nothing from one whose filings
were never fetched. Its rows would teach it the state of the downloads, not of the company. So a
backtest whose config names a `population` keeps only the labelled entities whose acquisition is
complete, and reports every other one with its reason:

- `rdf_not_searched`: no stored RDF search (list v1's companies the owner's script has not reached,
  or reached only during an outage);
- `rdf_not_found`: the latest search found no entity on RDF;
- `rdf_search_incomplete`: the latest search did not list every document (plan 0014);
- `no_statement_imported`: the search was complete, nothing was stored (an entity skipped for too
  few filed years, outside v1's scope);
- `statements_held_back`: a statement of a downloaded type is listed but not stored (a ZIP the
  importer held back for missing tabs, plan 0014).

An entity whose discovery source is named in `complete_by_capture` (the seed, captured by hand and
closed out in plan 0003) needs no stored search; the held-back rule still applies to it.

The rule reads acquisition records, never an outcome, so it selects no row by its label. It is
pinned like the label set: the SHA-256 of the sorted included KRS numbers, in the config.
"""

# polars's signatures reference types pyright cannot resolve; scoped to this module.
# pyright: reportUnknownMemberType=false

from __future__ import annotations

import hashlib
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Literal

import polars as pl
from psycopg import Connection
from pydantic import BaseModel, ConfigDict, Field

REASONS = (
    "rdf_not_searched",
    "rdf_not_found",
    "rdf_search_incomplete",
    "no_statement_imported",
    "statements_held_back",
)
SEARCHES_SCHEMA: dict[str, pl.DataType | type[pl.DataType]] = {
    "krs": pl.String,
    "searched_at": pl.Datetime("us", "UTC"),
    "found": pl.Boolean,
    "complete": pl.Boolean,
}
FILINGS_SCHEMA: dict[str, pl.DataType | type[pl.DataType]] = {
    "krs": pl.String,
    "rdf_type_code": pl.String,
    "stored": pl.Boolean,
}
SOURCES_SCHEMA: dict[str, pl.DataType | type[pl.DataType]] = {
    "krs": pl.String,
    "discovery_source": pl.String,
}


class PopulationConfig(BaseModel):
    """`population` in a backtest config: the rule, the sources complete by capture, the pin."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    rule: Literal["acquisition_complete"]
    complete_by_capture: tuple[str, ...]
    entities_hash: str = Field(pattern=r"^[0-9a-f]{64}$")


@dataclass(frozen=True)
class Population:
    included: frozenset[str]
    excluded: dict[str, str]  # krs -> reason, one of REASONS

    @property
    def entities_hash(self) -> str:
        return entities_hash(self.included)

    def reasons(self) -> dict[str, int]:
        """Excluded entities per reason, in `REASONS` order, zeros kept."""
        counts: dict[str, int] = dict.fromkeys(REASONS, 0)
        for reason in self.excluded.values():
            counts[reason] += 1
        return counts


def entities_hash(krs: Iterable[str]) -> str:
    return hashlib.sha256("\n".join(sorted(krs)).encode("ascii")).hexdigest()


def classify(
    entities: Iterable[str],
    searches: pl.DataFrame,
    filings: pl.DataFrame,
    sources: pl.DataFrame,
    *,
    complete_by_capture: Iterable[str],
    download_codes: Iterable[str],
) -> Population:
    """Each entity in `entities` included, or excluded with the first reason that applies.

    `searches`: stored RDF searches (`SEARCHES_SCHEMA`), the latest per entity decides.
    `filings`: non-deleted `filing_index` rows (`FILINGS_SCHEMA`), `stored` when their bytes are.
    `sources`: `universe_candidates` (`SOURCES_SCHEMA`); an entity may come from several.
    """
    captured = set(
        sources.filter(pl.col("discovery_source").is_in(list(complete_by_capture)))
        .get_column("krs")
        .to_list()
    )
    latest = {
        r["krs"]: (r["found"], r["complete"])
        for r in searches.sort("krs", "searched_at")
        .group_by("krs", maintain_order=True)
        .last()
        .iter_rows(named=True)
    }
    statements = filings.filter(pl.col("rdf_type_code").is_in(list(download_codes)))
    stored = set(statements.filter(pl.col("stored")).get_column("krs").to_list())
    held_back = set(statements.filter(~pl.col("stored")).get_column("krs").to_list())
    included: set[str] = set()
    excluded: dict[str, str] = {}
    for krs in sorted(set(entities)):
        search = latest.get(krs)
        if krs not in captured and search is None:
            excluded[krs] = "rdf_not_searched"
        elif krs not in captured and search is not None and not search[0]:
            excluded[krs] = "rdf_not_found"
        elif krs not in captured and search is not None and not search[1]:
            excluded[krs] = "rdf_search_incomplete"
        elif krs not in stored:
            excluded[krs] = "no_statement_imported"
        elif krs in held_back:
            excluded[krs] = "statements_held_back"
        else:
            included.add(krs)
    return Population(included=frozenset(included), excluded=excluded)


def read_population(
    conn: Connection,
    entities: Iterable[str],
    config: PopulationConfig,
    download_codes: Iterable[str],
) -> Population:
    """`classify` over the Postgres acquisition records: `rdf_listed_entities`, `filing_index`
    (non-deleted rows) and `universe_candidates`."""
    searches = pl.DataFrame(
        conn.execute(
            "SELECT trim(krs), searched_at, found, complete FROM rdf_listed_entities"
        ).fetchall(),
        schema=SEARCHES_SCHEMA,
        orient="row",
    )
    filings = pl.DataFrame(
        conn.execute(
            """
            SELECT trim(krs), rdf_type_code, sha256 IS NOT NULL FROM filing_index
            WHERE status IS DISTINCT FROM 'USUNIETY' AND deleted_on IS NULL
            """
        ).fetchall(),
        schema=FILINGS_SCHEMA,
        orient="row",
    )
    sources = pl.DataFrame(
        conn.execute("SELECT trim(krs), discovery_source FROM universe_candidates").fetchall(),
        schema=SOURCES_SCHEMA,
        orient="row",
    )
    return classify(
        entities,
        searches,
        filings,
        sources,
        complete_by_capture=config.complete_by_capture,
        download_codes=download_codes,
    )
