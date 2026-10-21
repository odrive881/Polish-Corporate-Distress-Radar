"""A weighted backtest's weights (plan 0019 step D, decisions 5, 6 and 10; ADR 0014 addendum).

A drawn list's companies carry their probability of being drawn, π, in `universe_draws`, under the
hash of the spec that drew them (`acquisition/sampling.py`). A weighted backtest pins one spec and
its list versions; each entity drawn under them gets the weight 1/π and a bootstrap stratum:

- `B:<status>` for a company of the distress group (decision 6: B is resampled per status);
- `A:<registration year>` for one of the random group drawn systematically (decision 10: the
  systematic step stratified by registration year), or `A` for one drawn by permutation.

A company in both groups has one π (the probability of being drawn at all) and is resampled with
B. The weights are read from the draw alone, never from a label, so changing every label changes
no weight.

With `nonresponse: by_group` (2026-10-21), each group's weights (A, and B per status) are then
scaled by the weight of its drawn entities the population rule classified over the weight of
those it included, so the included carry the whole group's weight. The rule reads acquisition
records, never an outcome, so the factors read no label either. A drawn entity the rule never
classified (outside the segment, so never labelled) is not a non-response and is left out of both.
"""

# polars's signatures reference types pyright cannot resolve; scoped to this module.
# pyright: reportUnknownMemberType=false

from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction

import polars as pl
from psycopg import Connection

from distress_radar.models.dataset import WEIGHT
from distress_radar.models.evaluation import STRATUM
from distress_radar.models.population import DRAW_REASON, Population
from distress_radar.models.splits import WeightsConfig

DRAWS_SCHEMA: dict[str, pl.DataType | type[pl.DataType]] = {
    "krs": pl.String,
    "list_version": pl.String,
    "draw_group": pl.String,
    "stratum": pl.String,
    "pi_numerator": pl.Int64,
    "pi_denominator": pl.Int64,
    "registration_year": pl.Int32,
}
WEIGHTS_SCHEMA: dict[str, pl.DataType | type[pl.DataType]] = {
    "krs": pl.String,
    WEIGHT: pl.Float64,
    STRATUM: pl.String,
    "random_group": pl.Boolean,
}


@dataclass(frozen=True)
class DrawWeights:
    """One row per drawn entity: `krs`, its weight, its bootstrap stratum, and whether the random
    group drew it (the "A alone" check)."""

    frame: pl.DataFrame  # WEIGHTS_SCHEMA, sorted by krs
    draw_spec_sha256: str
    # (group, factor) per group when adjusted for non-response, in group order; empty otherwise.
    adjustment: tuple[tuple[str, float], ...] = ()

    @property
    def members(self) -> frozenset[str]:
        return frozenset(self.frame.get_column("krs").to_list())

    @property
    def random_group(self) -> frozenset[str]:
        return frozenset(self.frame.filter(pl.col("random_group")).get_column("krs").to_list())


def draw_weights(draws: pl.DataFrame, config: WeightsConfig) -> DrawWeights:
    """The weights of the draw `config` pins, from its `universe_draws` rows (`DRAWS_SCHEMA`)."""
    versions = [config.random, *([config.distress] if config.distress else [])]
    rows = draws.filter(pl.col("list_version").is_in(versions))
    found = set(rows.get_column("list_version").unique().to_list())
    if found != set(versions):
        raise ValueError(
            f"draw {config.draw_spec_sha256}: no rows for {sorted(set(versions) - found)}"
        )
    records: list[dict[str, object]] = []
    for (krs,), group in rows.sort("krs", "draw_group").group_by("krs", maintain_order=True):
        pis = {
            Fraction(int(n), int(d))
            for n, d in group.select("pi_numerator", "pi_denominator").iter_rows()
        }
        if len(pis) != 1:
            raise ValueError(f"{krs}: drawn with {len(pis)} different probabilities in one draw")
        (pi,) = pis
        in_a = group.filter(pl.col("list_version") == config.random)
        in_b = (
            group.filter(pl.col("list_version") == config.distress)
            if config.distress is not None
            else group.clear()
        )
        if not in_b.is_empty():
            stratum = f"B:{in_b.get_column('stratum').item(0)}"
        else:
            year = in_a.get_column("registration_year").item(0)
            stratum = "A" if year is None else f"A:{year}"
        records.append(
            {
                "krs": str(krs),
                WEIGHT: float(1 / pi),
                STRATUM: stratum,
                "random_group": not in_a.is_empty(),
            }
        )
    return DrawWeights(
        pl.DataFrame(records, schema=WEIGHTS_SCHEMA).sort("krs"), config.draw_spec_sha256
    )


def read_draw_weights(conn: Connection, config: WeightsConfig) -> DrawWeights:
    """`draw_weights` over Postgres `universe_draws`, the pinned spec's rows only."""
    draws = pl.DataFrame(
        conn.execute(
            """
            SELECT trim(krs), list_version, draw_group, stratum, pi_numerator, pi_denominator,
                   registration_year
            FROM universe_draws WHERE draw_spec_sha256 = %s
            """,
            (config.draw_spec_sha256,),
        ).fetchall(),
        schema=DRAWS_SCHEMA,
        orient="row",
    )
    if draws.is_empty():
        raise ValueError(f"universe_draws holds no rows for draw spec {config.draw_spec_sha256}")
    return draw_weights(draws, config)


def group(stratum: str) -> str:
    """A bootstrap stratum's group: `A` for the random group's, `B:<status>` as it is."""
    return "A" if stratum.startswith("A") else stratum


def nonresponse_adjusted(weights: DrawWeights, population: Population) -> DrawWeights:
    """`weights` with each group's weights scaled by its classified drawn entities' weight over
    its included entities' weight. Refused when a group has classified entities and none included:
    its weight would vanish from every figure."""
    classified = population.included | {
        krs for krs, reason in population.excluded.items() if reason != DRAW_REASON
    }
    frame = weights.frame.with_columns(
        pl.col(STRATUM).map_elements(group, return_dtype=pl.String).alias("_group")
    )
    totals = (
        frame.filter(pl.col("krs").is_in(sorted(classified)))
        .group_by("_group")
        .agg(
            pl.col(WEIGHT).sum().alias("classified"),
            pl.col(WEIGHT)
            .filter(pl.col("krs").is_in(sorted(population.included)))
            .sum()
            .alias("included"),
        )
        .sort("_group")
    )
    empty = totals.filter(pl.col("included") == 0).get_column("_group").to_list()
    if empty:
        raise ValueError(f"no entity of {empty} is in the population: it cannot be adjusted for")
    factors = {str(g): float(c / i) for g, c, i in totals.iter_rows()}
    adjusted = frame.with_columns(
        (
            pl.col(WEIGHT)
            * pl.col("_group").replace_strict(factors, default=1.0, return_dtype=pl.Float64)
        ).alias(WEIGHT)
    ).drop("_group")
    return DrawWeights(adjusted, weights.draw_spec_sha256, tuple(sorted(factors.items())))
