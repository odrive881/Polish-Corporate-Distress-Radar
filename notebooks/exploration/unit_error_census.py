"""Filings off by a factor of 1,000 — plan 0020, § "Outstanding", item 2 (2026-10-21).

`0000310926` filed its FY2021 and FY2022 statements on the thousands schema (`…-tys`) with amounts in
złoty, so C multiplied them by 1,000, and the figures reached `feature_store` (plan 0020 step E). No
check for unit mistakes exists (PROJECT_OVERVIEW stage 5). Whether to build one is the owner's; this
counts how often it happens, from the canonical facts as they are:

1. **The same period reported twice.** A filing's `current_year` total assets (`BS.ASSETS`) against
   a later filing's `prior_year` (or `prior_year_restated`) for the same period end. Both are the
   filer's own figure for one balance sheet, so a ratio near 1,000 (or 1/1,000) is a unit error
   in one of them: the strongest signal.
2. **Within one filing.** Its `current_year` total assets against its own `prior_year`: a filer who
   changed units between years on one form, or a correction typed in the other unit.
3. **A one-year spike.** Consecutive fiscal years' `current_year` totals that jump by about 1,000
   and come back. Weaker: a real company can grow a thousandfold, though rarely and not back.

A pair is "near 1,000" when log10 of its ratio is within 0.5 of ±3 (a factor between about 316 and
3,162). Each flagged pair of section 1 is confirmed by **every line both columns carry**: a unit
error scales all of them, a real change does not, so the share of shared lines near 1,000 is
printed beside it, and for section 2 the same over the filing's two columns (a first year's growth
from start-up capital moves the total, not every line). The likely wrong filing is the one whose own
current-year total sits near 1,000 from the median of the entity's other years.

4. **What a flagged filing reaches:** its quality grade, and whether its entity is in the latest
   backtest's population and in `feature_store`.

**Counts, KRS numbers, years and structure versions only.** No amount is printed. Read-only:
`WAREHOUSE_DIR`, and Postgres for the population. Needs `make dev-up`.

    uv run python notebooks/exploration/unit_error_census.py
"""

import marimo

__generated_with = "0.9"
app = marimo.App(width="medium")


@app.cell
def _():
    import math

    import polars as pl
    import psycopg

    from distress_radar.acquisition.document_retrieval import load_document_types
    from distress_radar.models.dataset import load_label_set
    from distress_radar.models.population import read_population
    from distress_radar.models.splits import load_backtest_config
    from distress_radar.settings import Settings
    from distress_radar.warehouse import read_dataset

    TOTAL = "BS.ASSETS"
    TOLERANCE = 0.5  # in log10 units around ±3
    PRIOR = ("prior_year", "prior_year_restated")
    settings = Settings()
    facts = read_dataset(settings.warehouse_dir, "financial_statements_canonical").filter(
        pl.col("value").is_not_null() & (pl.col("value") != 0)
    )
    facts = facts.with_columns(
        pl.col("structure_version").str.ends_with("-tys").alias("thousands"),
        pl.col("value").cast(pl.Float64).abs().log10().alias("log_value"),
    )
    filings = facts.select(
        "source_document_hash",
        "krs",
        "fiscal_year",
        "period_end",
        "known_from",
        "structure_version",
        "thousands",
        "quality_grade",
    ).unique("source_document_hash")
    print(
        f"{filings.height} filings of {filings['krs'].n_unique()} entities with non-zero facts; "
        f"{filings['thousands'].sum()} on a thousands schema"
    )

    def near_thousand(log_ratio: pl.Expr) -> pl.Expr:
        return (log_ratio.abs() - 3).abs() <= TOLERANCE

    return (
        PRIOR,
        TOLERANCE,
        TOTAL,
        facts,
        filings,
        load_backtest_config,
        load_document_types,
        load_label_set,
        math,
        near_thousand,
        pl,
        psycopg,
        read_dataset,
        read_population,
        settings,
    )


@app.cell
def _(PRIOR, TOTAL, facts, near_thousand, pl):
    # Section 1: a filing's current year against a later filing's prior year, same entity and period.
    _keys = ["krs", "period_end"]
    _current = facts.filter(pl.col("column") == "current_year").select(
        *_keys,
        "line_item",
        "statement_type",
        pl.col("source_document_hash").alias("doc_a"),
        pl.col("known_from").alias("known_a"),
        pl.col("log_value").alias("log_a"),
    )
    # A prior-year column describes the period before the filing's own; its period end is the
    # filing's period end one fiscal year back, read from the filer's own earlier current column.
    _periods = (
        facts.filter(pl.col("column") == "current_year")
        .select("krs", "fiscal_year", "period_end")
        .unique(["krs", "fiscal_year"])
    )
    _prior = (
        facts.filter(pl.col("column").is_in(PRIOR))
        .with_columns((pl.col("fiscal_year") - 1).alias("prior_fiscal_year"))
        .join(
            _periods.rename({"fiscal_year": "prior_fiscal_year", "period_end": "prior_period_end"}),
            on=["krs", "prior_fiscal_year"],
            how="inner",
        )
        .select(
            "krs",
            pl.col("prior_period_end").alias("period_end"),
            "line_item",
            "statement_type",
            pl.col("source_document_hash").alias("doc_b"),
            pl.col("known_from").alias("known_b"),
            pl.col("log_value").alias("log_b"),
        )
    )
    lines = (
        _current.join(_prior, on=[*_keys, "line_item", "statement_type"], how="inner")
        .filter((pl.col("doc_a") != pl.col("doc_b")) & (pl.col("known_b") >= pl.col("known_a")))
        .with_columns((pl.col("log_a") - pl.col("log_b")).alias("log_ratio"))
    )
    _pairs = lines.group_by("krs", "period_end", "doc_a", "doc_b").agg(
        pl.len().alias("shared_lines"),
        near_thousand(pl.col("log_ratio")).mean().alias("share_near_1000"),
        pl.col("log_ratio").filter(pl.col("line_item") == TOTAL).first().alias("total_log_ratio"),
    )
    flagged_pairs = _pairs.filter(
        pl.col("total_log_ratio").is_not_null() & near_thousand(pl.col("total_log_ratio"))
    ).sort("krs", "period_end")
    _with_total = _pairs.filter(pl.col("total_log_ratio").is_not_null())
    print("\n1. The same period reported twice (current year, then a later filing's prior year)")
    print(
        f"   {_with_total.height} pairs with total assets on both sides; "
        f"{flagged_pairs.height} near 1,000, in {flagged_pairs['krs'].n_unique()} entities"
    )
    _agree = _with_total.filter(pl.col("total_log_ratio").abs() < 0.01).height
    print(
        f"   agreeing within 2.3%: {_agree}; other differences: "
        f"{_with_total.height - _agree - flagged_pairs.height}"
    )
    return flagged_pairs, lines


@app.cell
def _(PRIOR, TOTAL, facts, near_thousand, pl):
    # Section 2: within one filing, current year against prior year, line by line.
    _line = ["source_document_hash", "statement_type", "line_item"]
    _cur = facts.filter(pl.col("column") == "current_year").select(
        *_line, "krs", "fiscal_year", pl.col("log_value").alias("log_c")
    )
    _pri = (
        facts.filter(pl.col("column").is_in(PRIOR))
        .group_by(_line)
        .agg(pl.col("log_value").first().alias("log_p"))
    )
    _within = (
        _cur.join(_pri, on=_line, how="inner")
        .with_columns((pl.col("log_c") - pl.col("log_p")).alias("log_ratio"))
        .group_by("source_document_hash", "krs", "fiscal_year")
        .agg(
            near_thousand(pl.col("log_ratio")).mean().alias("share_near_1000"),
            pl.col("log_ratio").filter(pl.col("line_item") == TOTAL).first().alias("log_ratio"),
        )
        .filter(pl.col("log_ratio").is_not_null())
    )
    # A first year's growth from start-up capital moves the total, not every line.
    within_flagged = _within.filter(near_thousand(pl.col("log_ratio"))).sort("krs", "fiscal_year")
    print("\n2. Within one filing (current year against its own prior year)")
    print(
        f"   {_within.height} filings with both columns; {within_flagged.height} near 1,000, in "
        f"{within_flagged['krs'].n_unique()} entities"
    )
    _scaled = within_flagged.filter(pl.col("share_near_1000") >= 0.8).height
    print(f"   of which {_scaled} with at least 80% of their shared lines near 1,000")
    return (within_flagged,)


@app.cell
def _(TOTAL, facts, near_thousand, pl):
    # Section 3: one year's current total against its neighbours', latest filing per year.
    _yearly = (
        facts.filter((pl.col("line_item") == TOTAL) & (pl.col("column") == "current_year"))
        .sort("known_from")
        .group_by("krs", "fiscal_year")
        .last()
        .sort("krs", "fiscal_year")
        .with_columns(
            (pl.col("log_value") - pl.col("log_value").shift(1).over("krs")).alias("up"),
            (pl.col("log_value").shift(-1).over("krs") - pl.col("log_value")).alias("down"),
            (pl.col("fiscal_year") - pl.col("fiscal_year").shift(1).over("krs")).alias("gap_up"),
            (pl.col("fiscal_year").shift(-1).over("krs") - pl.col("fiscal_year")).alias("gap_down"),
        )
    )
    _jumps = _yearly.filter(near_thousand(pl.col("up")) & (pl.col("gap_up") == 1))
    spikes = _yearly.filter(
        near_thousand(pl.col("up"))
        & near_thousand(pl.col("down"))
        & (pl.col("up") * pl.col("down") < 0)
        & (pl.col("gap_up") == 1)
        & (pl.col("gap_down") == 1)
    )
    print("\n3. Year to year (latest filing per fiscal year)")
    print(
        f"   {_jumps.height} one-year jumps near 1,000 in {_jumps['krs'].n_unique()} entities; "
        f"{spikes.height} of them spikes that come back the next year"
    )
    yearly = _yearly.select("krs", "fiscal_year", "log_value")
    return spikes, yearly


@app.cell
def _(filings, flagged_pairs, near_thousand, pl, spikes, within_flagged, yearly):
    # Section 4 first lists every flagged filing with the signals that point at it.
    _rows: list[dict[str, object]] = []
    for _r in flagged_pairs.iter_rows(named=True):
        _rows.append(
            {
                "doc": _r["doc_a"],
                "signal": "1:current",
                "share": _r["share_near_1000"],
                "up": _r["total_log_ratio"] > 0,
            }
        )
        _rows.append(
            {
                "doc": _r["doc_b"],
                "signal": "1:prior",
                "share": _r["share_near_1000"],
                "up": _r["total_log_ratio"] < 0,
            }
        )
    _rows += [
        {"doc": d, "signal": "2:within", "share": sh, "up": None}
        for d, sh in within_flagged.select("source_document_hash", "share_near_1000").iter_rows()
    ]
    _rows += [
        {"doc": d, "signal": "3:spike", "share": None, "up": None}
        for d in spikes["source_document_hash"].to_list()
    ]
    signals = pl.DataFrame(
        _rows, schema={"doc": pl.String, "signal": pl.String, "share": pl.Float64, "up": pl.Boolean}
    )
    # The likely wrong filing: its own current-year total sits near 1,000 from the median of the
    # entity's other years (latest filing per year). A filing flagged only for its prior column
    # (section 1's later side, or section 2) can be right in its own year.
    _current = filings.select("source_document_hash", "krs", "fiscal_year").join(
        yearly, on=["krs", "fiscal_year"], how="left"
    )
    _others = (
        _current.join(yearly, on="krs", suffix="_other")
        .filter(pl.col("fiscal_year_other") != pl.col("fiscal_year"))
        .group_by("source_document_hash")
        .agg(pl.col("log_value_other").median().alias("log_median_other"))
    )
    flagged = (
        signals.group_by("doc")
        .agg(
            pl.col("signal").unique().sort().alias("signals"),
            pl.col("share").max().alias("share_near_1000"),
        )
        .join(filings, left_on="doc", right_on="source_document_hash")
        .join(_others, left_on="doc", right_on="source_document_hash", how="left")
        .join(
            _current.select("source_document_hash", "log_value"),
            left_on="doc",
            right_on="source_document_hash",
            how="left",
        )
        .with_columns(
            near_thousand(pl.col("log_value") - pl.col("log_median_other")).alias("likely_wrong")
        )
        .sort("krs", "fiscal_year")
    )
    return (flagged,)


@app.cell
def _(
    flagged,
    load_backtest_config,
    load_document_types,
    load_label_set,
    pl,
    psycopg,
    read_dataset,
    read_population,
    settings,
):
    _features = set(
        read_dataset(settings.warehouse_dir, "feature_store").get_column("krs").unique().to_list()
    )
    _config = load_backtest_config(settings.backtest_version)
    with psycopg.connect(settings.postgres_conninfo) as _conn:
        _labels = load_label_set(settings.warehouse_dir, _config.label_set_hash)
        _population = (
            read_population(
                _conn,
                _labels.get_column("krs").unique().to_list(),
                _config.population,
                load_document_types().download_codes,
            ).included
            if _config.population is not None
            else frozenset(_labels.get_column("krs").to_list())
        )
    print(f"\n4. The flagged filings ({flagged.height}, {flagged['krs'].n_unique()} entities)")
    print(
        "   krs, fiscal year, structure version, grade, signals, share of shared lines near 1,000, "
        f"own year off from its other years, in {settings.backtest_version}'s population, in feature_store"
    )
    for _r in flagged.iter_rows(named=True):
        _share = "-" if _r["share_near_1000"] is None else f"{_r['share_near_1000']:.0%}"
        _wrong = "-" if _r["likely_wrong"] is None else ("yes" if _r["likely_wrong"] else "no")
        print(
            f"   {_r['krs']} FY{_r['fiscal_year']} {_r['structure_version']} {_r['quality_grade']} "
            f"{','.join(_r['signals'])} {_share} {_wrong} "
            f"{'yes' if _r['krs'] in _population else 'no'} "
            f"{'yes' if _r['krs'] in _features else 'no'}"
        )
    _scaled = flagged.filter(pl.col("share_near_1000") >= 0.8)
    _wrong = flagged.filter(pl.col("likely_wrong").fill_null(False))
    print(
        "   at least 80% of shared lines near 1,000 (every line scaled, so a unit error): "
        f"{_scaled.height} filings of {_scaled['krs'].n_unique()} entities"
    )
    print(
        f"   own year off from the entity's other years: {_wrong.height} filings of "
        f"{_wrong['krs'].n_unique()} entities: "
        + ", ".join(f"{k} FY{y}" for k, y in _wrong.select("krs", "fiscal_year").iter_rows())
    )


if __name__ == "__main__":
    app.run()
