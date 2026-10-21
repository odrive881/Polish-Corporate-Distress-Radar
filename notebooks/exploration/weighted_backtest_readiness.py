"""Is list v3's draw ready for its weighted backtest? — plan 0019 step G (2026-10-21).

`config/models/backtest_v10.yaml` is the first weighted backtest: list v3's draw only, each row
weighted by 1/π. It is recorded with its two pins unset, since neither exists until the owner's
script has searched the drawn companies and their legal sources are in. This reads the stores as
they are and says how far off that is, and what the pins would be today:

1. **The draw:** companies per group and stratum, their weights, and the check that the random
   group's weights add up to the frame (N_F).
2. **Acquisition, per stratum:** not resolved by `entity_master` (out of the segment), then the
   population rule's reasons (`population.classify`, over every drawn company), then whether the
   latest frozen label set holds the company.
3. **The included share, per stratum,** by count and by weight. A weight is 1/π of the whole
   draw, so a stratum only partly in the population is under-represented by exactly its share.
   Equal shares make that harmless (a constant factor); unequal ones bias every weighted figure,
   and adjusting for it is the owner's decision before the run (see the config's header). The
   random group's registration-year strata are printed too: they are its bootstrap strata.
4. **Distress among the included:** entities ever in distress in the latest label set, per
   stratum, and the weighted share. Rough: a share of entities over the label set's whole span,
   not a rate per window.
5. **The floor, estimated** as in `distress_events_outlook.py` section 2, over the included
   entities: distinct events in the regression's complete-case test rows, folds that can train
   only. A weight adds no evidence, so the floor counts events unweighted (decision 7).
6. **The pins, and what is still missing:** the draw spec's hash against its file, the latest label
   set, the population hash the `backtest` job would compute from it, and the feature set the
   store holds against v10's.

Run it after each delivery of list v3 and after each nightly check that gets through.
**Counts and KRS numbers only.** Read-only: Postgres and `WAREHOUSE_DIR`. Needs `make dev-up`.

    uv run python notebooks/exploration/weighted_backtest_readiness.py
"""

import marimo

__generated_with = "0.9"
app = marimo.App(width="medium")


@app.cell
def _():
    from dataclasses import replace

    import polars as pl
    import psycopg

    from distress_radar.acquisition.document_retrieval import load_document_types
    from distress_radar.acquisition.sampling import spec_sha256
    from distress_radar.models.dataset import WEIGHT, load_label_set, modelling_dataset
    from distress_radar.models.evaluation import STRATUM
    from distress_radar.models.population import entities_hash, read_population
    from distress_radar.models.splits import (
        events,
        label_timing,
        load_backtest_config,
        purged_folds,
    )
    from distress_radar.models.weights import read_draw_weights
    from distress_radar.parsing.canonical_schema import CONFIG_DIR
    from distress_radar.settings import Settings
    from distress_radar.warehouse import read_dataset

    BACKTEST = "backtest_v10"
    SPEC = CONFIG_DIR / "segments" / "construction_sme_v1_draw_v1.yaml"
    RUNS = ("main", "no_regime")
    settings = Settings()
    config = load_backtest_config(BACKTEST)
    assert config.weights is not None and config.population is not None
    assert config.champion is not None
    with psycopg.connect(settings.postgres_conninfo) as conn:
        weights = read_draw_weights(conn, config.weights)
        members = weights.members
        resolved = {
            str(r[0])
            for r in conn.execute(
                "SELECT trim(krs) FROM entity_master WHERE trim(krs) = ANY(%s)", (sorted(members),)
            ).fetchall()
        }
        acquisition = read_population(
            conn,
            members,
            config.population,
            load_document_types().download_codes,
            members,
        )
        latest = conn.execute(
            "SELECT label_set_hash, label_version, created_at FROM label_sets "
            "ORDER BY created_at DESC LIMIT 1"
        ).fetchone()
    assert latest is not None, "no frozen label set"
    labels = load_label_set(settings.warehouse_dir, str(latest[0]))
    labelled = set(labels.get_column("krs").unique().to_list())
    features = read_dataset(settings.warehouse_dir, "feature_store")
    stored_sets = sorted(features.get_column("feature_set_version").unique().to_list())

    # One row per drawn company: its display group (A, or B by status), its bootstrap stratum and
    # weight, and where acquisition and labelling stand.
    def _status(krs: str) -> str:
        if krs not in resolved:
            return "not in entity_master"
        if krs in acquisition.excluded:
            return acquisition.excluded[krs]
        return "included" if krs in labelled else "complete, not labelled"

    drawn = weights.frame.with_columns(
        pl.when(pl.col(STRATUM).str.starts_with("B:"))
        .then(pl.col(STRATUM))
        .otherwise(pl.lit("A"))
        .alias("group"),
        pl.col("krs").map_elements(_status, return_dtype=pl.String).alias("status"),
    )
    included = drawn.filter(pl.col("status") == "included")
    print(
        f"{BACKTEST}: draw {config.weights.draw_spec_sha256[:12]}…, {drawn.height} companies; "
        f"latest label set {str(latest[0])[:12]}… ({latest[1]}, frozen "
        f"{latest[2]:%Y-%m-%d %H:%M}); feature_store holds {stored_sets}"
    )
    return (
        RUNS,
        SPEC,
        STRATUM,
        WEIGHT,
        config,
        drawn,
        entities_hash,
        events,
        features,
        included,
        label_timing,
        labelled,
        labels,
        latest,
        modelling_dataset,
        pl,
        purged_folds,
        replace,
        spec_sha256,
        stored_sets,
    )


@app.cell
def _(WEIGHT, drawn, pl):
    print("\n1. The draw")
    _by = drawn.group_by("group").agg(pl.len().alias("n"), pl.col(WEIGHT).sum().alias("w"))
    for _group, _n, _w in _by.sort("group").iter_rows():
        print(f"   {_group}: {_n} companies, weights sum to {_w:,.1f}")
    _a = drawn.filter(pl.col("random_group"))
    print(
        f"   random group alone: {_a.height} companies, Σ 1/π = {_a[WEIGHT].sum():,.1f} "
        "(N_F if none carried a distress status; one that does is also in B's reach, so weighs less)"
    )


@app.cell
def _(drawn, pl):
    print("\n2. Acquisition, per group (a company's first reason; `included` = in the population)")
    _table = drawn.group_by("group", "status").len().sort("group", "status")
    for (_group,), _rows in _table.group_by("group", maintain_order=True):
        _parts = ", ".join(f"{s} {n}" for _, s, n in _rows.iter_rows())
        print(f"   {_group}: {_parts}")


@app.cell
def _(STRATUM, WEIGHT, drawn, pl):
    print("\n3. The included share, by count and by weight")

    def _shares(frame: pl.DataFrame, key: str) -> pl.DataFrame:
        _inc = pl.col("status") == "included"
        return (
            frame.group_by(key)
            .agg(
                pl.len().alias("n"),
                _inc.sum().alias("included"),
                (pl.col(WEIGHT) * _inc).sum().alias("w_in"),
                pl.col(WEIGHT).sum().alias("w"),
            )
            .with_columns((pl.col("w_in") / pl.col("w")).alias("share"))
            .sort(key)
        )

    _groups = _shares(drawn, "group")
    for _g, _n, _i, _, _, _s in _groups.iter_rows():
        print(f"   {_g}: {_i} of {_n} included, {_s:.1%} of its weight")
    _spread = _groups["share"].max() - _groups["share"].min()
    if _groups["included"].sum() == 0:
        print("   nothing included yet")
    elif _spread > 0.05:
        print(
            f"   the groups' shares differ by {_spread:.1%}: plain 1/π weights would misstate the "
            "groups' balance (the config's header: the owner's decision before the run)"
        )
    _years = _shares(drawn.filter(pl.col(STRATUM).str.starts_with("A:")), STRATUM)
    print(
        "   random group by registration year (bootstrap strata), included/drawn: "
        + ", ".join(f"{s[2:]} {i}/{n}" for s, n, i, *_ in _years.iter_rows())
    )


@app.cell
def _(WEIGHT, config, included, labels, pl):
    print("\n4. Distress among the included (ever in distress in the latest label set)")
    _ever = set(
        labels.filter(pl.col("outcome_class").is_in(config.distress_classes))
        .get_column("krs")
        .unique()
        .to_list()
    )
    _frame = included.with_columns(pl.col("krs").is_in(sorted(_ever)).alias("ever"))
    for (_group,), _rows in _frame.sort("group").group_by("group", maintain_order=True):
        print(f"   {_group}: {_rows['ever'].sum()} of {_rows.height}")
    if _frame.height:
        _w = _frame[WEIGHT].sum()
        _we = _frame.filter(pl.col("ever"))[WEIGHT].sum()
        print(
            f"   weighted share ever in distress: {_we / _w:.2%}; unweighted: "
            f"{_frame['ever'].mean():.2%} (rough: entities over the label set's span)"
        )


@app.cell
def _(
    RUNS,
    config,
    events,
    features,
    included,
    label_timing,
    labels,
    modelling_dataset,
    pl,
    purged_folds,
    replace,
    stored_sets,
):
    _floor = config.champion.min_promotion_events
    print(
        f"\n5. The floor, estimated over the included ({_floor} needed; on the stored "
        f"{stored_sets}, whose regression inputs v10 shares)"
    )
    _modelled = labels.filter(pl.col("krs").is_in(included["krs"].to_list()))
    if _modelled.is_empty() or len(stored_sets) != 1:
        print("   nothing to estimate: no included entity, or the store holds several sets")
    else:
        _timing = label_timing(labels)
        _complete = pl.all_horizontal(
            pl.col(c).is_not_null() for c in config.logistic_regression.features
        )
        for _horizon in config.horizons:
            _base = modelling_dataset(features, _modelled, _horizon, config.distress_classes)
            for _run in RUNS:
                _frame = (
                    _base.frame if _run == "main" else _base.frame.filter(~pl.col("regime_flag"))
                )
                _found: set[tuple[object, ...]] = set()
                for _fold in purged_folds(replace(_base, frame=_frame), config.test_years, _timing):
                    if events(_fold.train.filter(_complete)).height >= config.min_events:
                        _found |= set(events(_fold.test.filter(_complete)).rows())
                _mark = "reaches" if len(_found) >= _floor else "short of"
                print(f"   {_horizon} months, {_run}: {len(_found)} events, {_mark} {_floor}")


@app.cell
def _(SPEC, config, drawn, entities_hash, included, labelled, latest, spec_sha256, stored_sets):
    print("\n6. The pins, and what is still missing")
    _spec_ok = spec_sha256(SPEC) == config.weights.draw_spec_sha256
    _hash = entities_hash(included["krs"].to_list())
    _unsearched = drawn.filter(drawn["status"] == "rdf_not_searched").height
    # The companies outside the segment (not in `entity_master`) are never labelled.
    _unlabelled = drawn.filter(
        (drawn["status"] != "not in entity_master") & ~drawn["krs"].is_in(sorted(labelled))
    ).height
    _missing = [
        *(["the draw spec file no longer hashes to the pinned spec"] if not _spec_ok else []),
        *([f"{_unsearched} drawn companies not searched on RDF"] if _unsearched else []),
        *([f"{_unlabelled} drawn companies not in the latest label set"] if _unlabelled else []),
        *(
            [f"feature_store holds {stored_sets}, v10 reads {config.feature_set_version}"]
            if stored_sets != [config.feature_set_version]
            else []
        ),
    ]
    print(f"   draw spec: {'matches its file' if _spec_ok else 'DIFFERS from its file'}")
    print("   the pins as they would be today (fill them only once nothing is missing):")
    print(f"      label_set_hash: {latest[0]}")
    print(f"      population.entities_hash: {_hash}  ({included.height} entities)")
    if _missing:
        print("   not ready: " + "; ".join(_missing))
    else:
        print("   ready: fill the two pins in config/models/backtest_v10.yaml, commit, run")


if __name__ == "__main__":
    app.run()
