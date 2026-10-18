"""Events against the champion rule's floor — plan 0014, § "Events" (2026-10-18).

The champion rule needs 50 distinct events in the paired test rows, at both horizons and in both
the `main` and `no_regime` runs (plan 0015 step F). List v2's distress group is expected to bring
them. This asks, from the stores as they are, how many it does, before any `backtest_v<n>` is
pinned:

1. **Distress per list:** entities, entities ever in distress in the latest frozen label set, and
   distinct events by class, per `universe_candidates` source.
2. **The floor, estimated:** with the latest label set, the current `feature_store` and the
   population rule applied anew (no pin), the backtest's own folds (`purged_folds`, `events`) and
   the incumbent's complete-case rule give, per horizon, run and test year, the distinct events in
   the rows the regression would score in folds where it trains on at least `min_events` events.
   That is the paired count's ceiling: a challenger scores every row, so the pair is the
   regression's rows. The real count comes from the backtest run itself.
3. **Why an entity's event does not count:** outside the population (with the population's
   reason), no test row (the event outside the test years' windows), no complete-case test row, or
   only in folds that cannot train.
4. **Rejestr.io's flag against the pipeline:** each list v2 file's status column (the only column
   read besides the KRS number) against whether the legal events hold an event of that class.
   ADR 0014's addendum asks for this agreement to be reported.

Run it after a nightly registry check that gets through (the distress group's legal sources were
never fetched before), and after each delivery. **Counts and KRS numbers only.** Read-only:
Postgres, `WAREHOUSE_DIR`, and the list files in `LISTS_DIR`. Needs `make dev-up`.

    uv run python notebooks/exploration/distress_events_outlook.py
"""

import marimo

__generated_with = "0.9"
app = marimo.App(width="medium")


@app.cell
def _():
    import csv
    from dataclasses import replace
    from pathlib import Path

    import polars as pl
    import psycopg

    from distress_radar.acquisition.document_retrieval import load_document_types
    from distress_radar.models.dataset import (
        load_feature_store,
        load_label_set,
        modelling_dataset,
    )
    from distress_radar.models.population import read_lists, read_population
    from distress_radar.models.splits import (
        events,
        label_timing,
        load_backtest_config,
        purged_folds,
    )
    from distress_radar.settings import Settings
    from distress_radar.warehouse import read_dataset

    BACKTEST = "backtest_v8"  # the config whose rules (not pins) are applied
    FLOOR = 50
    RUNS = ("main", "no_regime")
    LISTS_DIR = Path(".cache/universe_lists")
    LIST_FILES = {
        "rejestr_io_v2_distress": ("Merged distress sample (65-35 split).csv", "distress_type"),
        "rejestr_io_v2_random": ("Merged sample (600).csv", "in_distress"),
    }
    settings = Settings()
    config = load_backtest_config(BACKTEST)
    assert config.population is not None and config.champion is not None
    with psycopg.connect(settings.postgres_conninfo) as conn:
        latest = conn.execute(
            "SELECT label_set_hash, label_version, created_at FROM label_sets "
            "ORDER BY created_at DESC LIMIT 1"
        ).fetchone()
        assert latest is not None, "no frozen label set"
        labels = load_label_set(settings.warehouse_dir, str(latest[0]))
        lists = read_lists(conn)
        population = read_population(
            conn,
            labels.get_column("krs").unique().to_list(),
            config.population,
            load_document_types().download_codes,
        )
    features = load_feature_store(settings.warehouse_dir, config.feature_set_version)
    legal = read_dataset(settings.warehouse_dir, "legal_events")
    print(
        f"label set {str(latest[0])[:12]}… ({latest[1]}, frozen {latest[2]:%Y-%m-%d %H:%M}), "
        f"{labels['krs'].n_unique()} entities; population applied anew: "
        f"{len(population.included)} included, {len(population.excluded)} left out"
    )
    print(f"rules of {BACKTEST}: test years {config.test_years}, min_events {config.min_events}")
    return (
        FLOOR,
        LISTS_DIR,
        LIST_FILES,
        RUNS,
        config,
        csv,
        events,
        features,
        label_timing,
        labels,
        legal,
        lists,
        modelling_dataset,
        pl,
        population,
        purged_folds,
        replace,
    )


@app.cell
def _(config, labels, lists, pl):
    _distress = labels.filter(pl.col("outcome_class").is_in(config.distress_classes))
    print("\n1. Distress per list (an entity may be on several)")
    for _name, _krs in lists.items():
        _rows = _distress.filter(pl.col("krs").is_in(sorted(_krs)))
        _labelled = labels.filter(pl.col("krs").is_in(sorted(_krs)))["krs"].n_unique()
        _events = (
            _rows.select("krs", "outcome_class", "event_date", "trigger_event_type")
            .unique()
            .group_by("outcome_class")
            .len()
        )
        print(
            f"   {_name}: {len(_krs)} entities, {_labelled} labelled, "
            f"{_rows['krs'].n_unique()} ever in distress; events by class "
            f"{dict(sorted(_events.iter_rows()))}"
        )


@app.cell
def _(
    FLOOR,
    RUNS,
    config,
    events,
    features,
    label_timing,
    labels,
    modelling_dataset,
    pl,
    population,
    purged_folds,
    replace,
):
    _timing = label_timing(labels)
    _modelled = labels.filter(pl.col("krs").is_in(sorted(population.included)))
    _inputs = list(config.logistic_regression.features)
    _complete = pl.all_horizontal(pl.col(c).is_not_null() for c in _inputs)
    counted: dict[tuple[int, str], set[tuple[object, ...]]] = {}
    tested: dict[tuple[int, str], set[str]] = {}
    complete_tested: dict[tuple[int, str], set[str]] = {}
    print(f"\n2. The floor, estimated (the regression's complete-case rows; {FLOOR} needed)")
    for _horizon in config.horizons:
        _base = modelling_dataset(features, _modelled, _horizon, config.distress_classes)
        for _run in RUNS:
            _frame = _base.frame if _run == "main" else _base.frame.filter(~pl.col("regime_flag"))
            _found: set[tuple[object, ...]] = set()
            _tested: set[str] = set()
            _complete_tested: set[str] = set()
            _years = []
            for _fold in purged_folds(replace(_base, frame=_frame), config.test_years, _timing):
                _test_events = events(_fold.test)
                _tested |= set(_test_events["krs"].to_list())
                _train_events = events(_fold.train.filter(_complete)).height
                _paired = events(_fold.test.filter(_complete))
                _complete_tested |= set(_paired["krs"].to_list())
                _ok = _train_events >= config.min_events
                if _ok:
                    _found |= set(_paired.rows())
                _years.append(
                    f"{_fold.test_year}: {_paired.height}{'' if _ok else ' (cannot train)'}"
                )
            counted[(_horizon, _run)] = _found
            tested[(_horizon, _run)] = _tested
            complete_tested[(_horizon, _run)] = _complete_tested
            _mark = "reaches" if len(_found) >= FLOOR else "short of"
            print(
                f"   {_horizon} months, {_run}: {len(_found)} events, {_mark} {FLOOR}; "
                + ", ".join(_years)
            )
    return complete_tested, counted, tested


@app.cell
def _(complete_tested, config, counted, labels, lists, pl, population, tested):
    # Section 3 at 12 months, `main`: each entity ever in distress, by why its event counts or not.
    _key = (12, "main")
    _counted_krs = {row[0] for row in counted[_key]}
    _distress = (
        labels.filter(pl.col("outcome_class").is_in(config.distress_classes))["krs"]
        .unique()
        .to_list()
    )

    def _why(krs: str) -> str:
        if krs in population.excluded:
            return f"outside the population: {population.excluded[krs]}"
        if krs in _counted_krs:
            return "counted"
        if krs not in tested[_key]:
            return "no test row (event outside the test years' windows)"
        if krs not in complete_tested[_key]:
            return "no complete-case test row (the regression cannot score it)"
        return "only in folds that cannot train"

    print("\n3. Entities ever in distress, 12 months, main: why each event counts or not")
    for _name, _krs in lists.items():
        _mine = [k for k in _distress if k in _krs]
        if not _mine:
            continue
        _reasons: dict[str, int] = {}
        for _k in _mine:
            _reasons[_why(_k)] = _reasons.get(_why(_k), 0) + 1
        print(f"   {_name} ({len(_mine)}): {dict(sorted(_reasons.items(), key=lambda x: -x[1]))}")


@app.cell
def _(LISTS_DIR, LIST_FILES, csv, legal, pl):
    # Only the KRS column and the status column are read; company names are never loaded.
    _classes = (
        legal.filter(pl.col("outcome_class").is_not_null())
        .group_by("krs")
        .agg(
            pl.col("outcome_class").unique().alias("classes"),
            pl.col("event_date").min().alias("first_event"),
        )
    )
    _by_krs = {r["krs"]: r for r in _classes.iter_rows(named=True)}
    _expected = {"w upadlosci": "bankruptcy", "w likwidacji": "liquidation", "True": None}
    print("\n4. Rejestr.io's flag against the pipeline's legal events")
    for _source, (_file, _column) in LIST_FILES.items():
        _path = LISTS_DIR / _file
        if not _path.exists():
            print(f"   {_source}: {_path} not found, skipped")
            continue
        with _path.open(encoding="utf-8-sig", newline="") as _f:
            _rows = [(r["krs"].strip(), r[_column].strip()) for r in csv.DictReader(_f)]
        _table: dict[tuple[str, str], int] = {}
        _years: dict[int, int] = {}
        for _krs, _flag in _rows:
            _found = _by_krs.get(_krs)
            if _flag == "False":
                _result = "has an event" if _found else "none"
            elif _found is None:
                _result = "no event"
            elif _expected.get(_flag) is None or _expected[_flag] in _found["classes"]:
                _result = "agrees"
            else:
                _result = f"other class: {sorted(_found['classes'])}"
            _table[(_flag, _result)] = _table.get((_flag, _result), 0) + 1
            if _found is not None and _found["first_event"] is not None:
                _year = _found["first_event"].year
                _years[_year] = _years.get(_year, 0) + 1
        print(f"   {_source}, {len(_rows)} rows:")
        for (_flag, _result), _n in sorted(_table.items()):
            print(f"      flag {_flag!r}: {_result}: {_n}")
        print(f"      first event year, flagged or not: {dict(sorted(_years.items()))}")


if __name__ == "__main__":
    app.run()
