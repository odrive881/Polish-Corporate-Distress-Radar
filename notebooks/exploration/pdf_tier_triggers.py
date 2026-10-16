"""Plan 0006's triggers, measured — plan 0017 step I.

Plan 0006 (the C3 PDF tier) is deferred until a trigger fires. Two can be measured from the stores:

1. **A live entity's most recent statement is a PDF**, with no later filing to supply comparatives.
2. **PDF-only years not recoverable from a later filing**, at a share that distorts the universe or
   concentrated in entities with a distress event (which would bias the positive class).

For each `needs_pdf_tier` file on the latest parsing run (`parsed_documents`, as
`staging.parsed_documents_current` selects it), this asks:

- **Covered by XML:** a parsed statement file of the same entity for the same period exists (the
  filer also filed the structured statement, or a correction of it). Nothing is lost.
- **Recoverable:** a parsed statement file whose period starts the day after the PDF's ends carries a
  `prior_year` column (plan 0006's substitute; the pairing `prior_year_consistency` uses), dated by
  that later filing's `known_from`.
- **Unrecoverable:** neither. Split by whether a later statement file of any status exists (none:
  the PDF is the entity's latest, trigger 1) and by the period's year (pre-2018 periods are out of
  v1 scope, plan 0006).

Then each entity against its legal events (an event with an outcome class, as the census counts
them) and against `backtest_v7`'s population.

**Counts and KRS numbers only**: no document content, file name or company name is printed.
Read-only: Postgres and `WAREHOUSE_DIR`. Needs `make dev-up`, and `financial_statements_canonical`
and `legal_events` materialized after the latest parsing run.

    uv run python notebooks/exploration/pdf_tier_triggers.py
"""

import marimo

__generated_with = "0.9"
app = marimo.App(width="medium")


@app.cell
def _():
    import collections
    from datetime import timedelta

    import polars as pl
    import psycopg

    from distress_radar.acquisition.document_retrieval import load_document_types
    from distress_radar.models.dataset import load_label_set
    from distress_radar.models.population import read_population
    from distress_radar.models.splits import load_backtest_config
    from distress_radar.settings import Settings

    BACKTEST = "backtest_v7"
    settings = Settings()
    _latest_run = """
        WITH lr AS (
          SELECT last_seen_run_id FROM parsed_documents WHERE last_seen_at IS NOT NULL
          ORDER BY last_seen_at DESC, last_seen_run_id LIMIT 1
        )
        SELECT trim(p.krs), p.status, p.sha256, p.source_member, f.period_start, f.period_end,
               f.submission_date, f.is_correction
        FROM parsed_documents p JOIN lr USING (last_seen_run_id)
        LEFT JOIN filing_index f ON f.krs = p.krs AND f.document_ref = p.document_ref
    """
    _schema = {
        "krs": pl.String,
        "status": pl.String,
        "sha256": pl.String,
        "source_member": pl.String,
        "period_start": pl.Date,
        "period_end": pl.Date,
        "known_from": pl.Date,
        "is_correction": pl.Boolean,
    }
    with psycopg.connect(settings.postgres_conninfo) as conn:
        files = pl.DataFrame(conn.execute(_latest_run).fetchall(), schema=_schema, orient="row")
        _config = load_backtest_config(BACKTEST)
        assert _config.population is not None
        _population = read_population(
            conn,
            load_label_set(settings.warehouse_dir, _config.label_set_hash)
            .get_column("krs")
            .unique()
            .to_list(),
            _config.population,
            load_document_types().download_codes,
        )
    assert _population.entities_hash == _config.population.entities_hash, "population moved"
    population = _population.included
    pdfs = files.filter(pl.col("status") == "needs_pdf_tier")
    print(
        f"latest parsing run: {files.height} statement files; needs_pdf_tier: {pdfs.height} files "
        f"of {pdfs['krs'].n_unique()} entities"
    )
    return BACKTEST, collections, files, pdfs, pl, population, settings, timedelta


@app.cell
def _(files, pdfs, pl, settings, timedelta):
    _canonical = pl.scan_parquet(
        str(settings.warehouse_dir / "financial_statements_canonical" / "**" / "*.parquet")
    )
    # One row per parsed file: its period, its grade, and whether it carries a prior-year column.
    parsed = (
        _canonical.group_by("krs", "source_document_hash", "source_member")
        .agg(
            pl.col("period_start").first(),
            pl.col("period_end").first(),
            pl.col("known_from").first(),
            pl.col("quality_grade").first(),
            (pl.col("column") == "prior_year").any().alias("has_prior_year"),
        )
        .collect()
    )
    _rows = []
    for p in pdfs.iter_rows(named=True):
        _own = parsed.filter(pl.col("krs") == p["krs"])
        _same = _own.filter(pl.col("period_end") == p["period_end"])
        _next = _own.filter(
            (pl.col("period_start") == p["period_end"] + timedelta(days=1))
            & pl.col("has_prior_year")
        )
        _later_any = files.filter(
            (pl.col("krs") == p["krs"])
            & (pl.col("period_end") > p["period_end"])
            & (pl.col("status") != "needs_pdf_tier")
        )
        _usable_next = _next.filter(pl.col("quality_grade") != "quarantined")
        _rows.append(
            {
                **p,
                "covered_by_xml": _same.height > 0,
                "recoverable": _next.height > 0,
                "recoverable_usable": _usable_next.height > 0,
                "recovered_known_from": _next["known_from"].min() if _next.height else None,
                "later_statement_file": _later_any.height > 0,
            }
        )
    result = pl.DataFrame(_rows).with_columns(
        pl.when(pl.col("covered_by_xml"))
        .then(pl.lit("covered by XML, same period"))
        .when(pl.col("recoverable"))
        .then(pl.lit("recoverable from the next filing"))
        .when(pl.col("later_statement_file"))
        .then(pl.lit("unrecoverable, a later statement exists"))
        .otherwise(pl.lit("unrecoverable, the entity's latest statement"))
        .alias("outcome"),
        pl.col("period_end").dt.year().alias("period_year"),
    )
    print("\n1. Each PDF file, by outcome")
    for _outcome, _group in result.group_by("outcome", maintain_order=True):
        print(f"   {_outcome[0]}: {_group.height} files, {_group['krs'].n_unique()} entities")
    _graded_out = result.filter(pl.col("recoverable") & ~pl.col("recoverable_usable"))
    print(f"   of the recoverable, the next filing graded quarantined: {_graded_out.height} files")
    _late = result.filter(
        pl.col("recoverable") & (pl.col("recovered_known_from") > pl.col("known_from"))
    )
    print(f"   of the recoverable, known only later than the PDF: {_late.height} files")
    print("   by period year:", dict(sorted(result.group_by("period_year").len().iter_rows())))
    print("   corrections among them:", result["is_correction"].sum())
    return (result,)


@app.cell
def _(collections, pl, population, result, settings, BACKTEST):
    _events = (
        pl.read_parquet(
            str(settings.warehouse_dir / "legal_events" / "**" / "*.parquet"),
            hive_partitioning=True,
        )
        .filter(pl.col("outcome_class").is_not_null())
        .group_by("krs")
        .agg(pl.col("event_date").min().alias("first_event"))
    )
    first_event = dict(_events.iter_rows())
    _gone = set(
        pl.read_parquet(
            str(settings.warehouse_dir / "legal_events" / "**" / "*.parquet"),
            hive_partitioning=True,
        )
        .filter(pl.col("event_type") == "deregistered")["krs"]
        .to_list()
    )
    _unrec = result.filter(pl.col("outcome").str.starts_with("unrecoverable"))
    print("\n2. The unrecoverable files, entity by entity")
    for _krs, _group in _unrec.sort("krs", "period_end").group_by("krs", maintain_order=True):
        _k = _krs[0]
        _first = first_event.get(_k)
        _periods = ", ".join(
            f"{r['period_end']} (known {r['known_from']}"
            + (", after the first event" if _first and r["known_from"] > _first else "")
            + (", latest" if not r["later_statement_file"] else "")
            + ")"
            for r in _group.iter_rows(named=True)
        )
        print(
            f"   {_k}: first event {_first or 'none'}"
            f"{', deregistered' if _k in _gone else ''}"
            f"; {'in' if _k in population else 'not in'} {BACKTEST}'s population; {_periods}"
        )

    _all_entities = result["krs"].unique().to_list()
    _unrec_entities = _unrec["krs"].unique().to_list()
    print("\n3. Trigger 2: concentration in distressed entities")
    print(
        f"   {BACKTEST}'s population: {len(population)} entities, "
        f"{sum(k in first_event for k in population)} with a distress event"
    )
    print(
        f"   entities with a PDF file: {len(_all_entities)}, "
        f"{sum(k in first_event for k in _all_entities)} with a distress event, "
        f"{sum(k in population for k in _all_entities)} in the population"
    )
    print(
        f"   entities with an unrecoverable PDF: {len(_unrec_entities)}, "
        f"{sum(k in first_event for k in _unrec_entities)} with a distress event, "
        f"{sum(k in population for k in _unrec_entities)} in the population"
    )
    _usable = _unrec.filter(
        pl.col("period_year") >= 2018,
        pl.struct("krs", "known_from").map_elements(
            lambda r: first_event.get(r["krs"]) is None or r["known_from"] <= first_event[r["krs"]],
            return_dtype=pl.Boolean,
        ),
    )
    print(
        "   unrecoverable, in v1 scope (period 2018 on) and known before any distress event: "
        f"{_usable.height} files of {_usable['krs'].n_unique()} entities"
    )

    _latest = _unrec.filter(~pl.col("later_statement_file"))
    _live = [
        k for k in _latest["krs"].unique().to_list() if k not in first_event and k not in _gone
    ]
    print("\n4. Trigger 1: a live entity whose latest statement is a PDF")
    print(
        f"   entities whose latest statement is an unrecoverable PDF: {_latest['krs'].n_unique()}"
    )
    print(f"   of them live (no distress event, not deregistered): {len(_live)}: {sorted(_live)}")
    print(
        "   their latest PDF period ends:",
        dict(
            collections.Counter(
                str(d.year) for d in _latest.filter(pl.col("krs").is_in(_live))["period_end"]
            )
        ),
    )


if __name__ == "__main__":
    app.run()
