"""List v1's composition census — ADR 0014 § Consequences, plan 0014 step 0.

ADR 0014 accepted a hand-filtered Rejestr.io list (`discovery_source` `rejestr_io_v1`) as the first
universe beyond the seed, purposive, not a probability sample. This measures what it holds, from the
pipeline's own stores, so its base rate is never read as the population's:

1. **A2 outcome:** resolved, or quarantined and why (PKD section F, legal form).
2. **PKD:** the predominant division (41, 42, 43) and the classification version GUS reports.
3. **Legal form and registry status** as GUS reports them.
4. **Region:** the seat's voivodeship (GUS).
5. **Registration year** in KRS (GUS `dataWpisuDoRejestruEwidencji`).
6. **Filed years:** non-deleted statement periods in the scripted listing, for entities whose search
   completed; entities not searched yet, or searched only during an outage, counted apart.
7. **Size, as far as the filings show it:** the form of the latest parsed statement (full, small,
   micro) and its total assets and revenue in bands. Not the §4.4 size class, which needs average
   employment (AGENT_SPEC §4.4; deferred, plan 0010).
8. **Distress, as the pipeline's own legal events find it:** entities with a bankruptcy, restructuring
   or liquidation event (KRS extracts and MSiG), by the year of the first, and deregistrations.

**Counts only**: no name, address or document content is printed. Read-only: Postgres, MinIO and
`WAREHOUSE_DIR`. Needs `make dev-up`, and `krs_extracts`, `msig_notices`, `legal_events` and
`financial_statements_canonical` materialized after the list's A2.

    uv run python notebooks/exploration/list_v1_composition_census.py
"""

import marimo

__generated_with = "0.9"
app = marimo.App(width="medium")


@app.cell
def _():
    import collections
    import re

    import polars as pl
    import psycopg

    from distress_radar.acquisition.raw_store import S3ObjectStore, raw_key
    from distress_radar.acquisition.script_import import (
        DOCUMENTS_FILE,
        FETCH_TIER,
        current_documents,
        filed_years,
        read_documents,
    )
    from distress_radar.features.config import load_line_items
    from distress_radar.parsing.canonical_schema import load_mapping_config
    from distress_radar.settings import Settings

    SOURCE = "rejestr_io_v1"
    settings = Settings()
    store = S3ObjectStore.from_endpoint(
        settings.minio_endpoint,
        settings.minio_access_key,
        settings.minio_secret_key.get_secret_value(),
        settings.minio_bucket,
    )
    with psycopg.connect(settings.postgres_conninfo) as conn:
        listed = [
            str(k).strip()
            for (k,) in conn.execute(
                "SELECT krs FROM universe_candidates WHERE discovery_source = %s", (SOURCE,)
            )
        ]
        quarantined = dict(
            conn.execute(
                """
                SELECT q.entity_key, q.reason_code FROM quarantine_events q
                JOIN universe_candidates c ON c.krs = q.entity_key AND c.discovery_source = %s
                WHERE q.stage = 'A2'
                """,
                (SOURCE,),
            ).fetchall()
        )
        masters = {
            str(k).strip(): (form, status, pkd, codes, sha)
            for k, form, status, pkd, codes, sha in conn.execute(
                """
                SELECT m.krs, m.legal_form_code, m.status, m.pkd_predominant, m.pkd_codes,
                       m.source_document_hash
                FROM entity_master m
                JOIN universe_candidates c ON c.krs = m.krs AND c.discovery_source = %s
                """,
                (SOURCE,),
            )
        }
        searches = conn.execute(
            """
            SELECT s.krs, s.found, s.complete FROM rdf_listed_entities s
            JOIN universe_candidates c ON c.krs = s.krs AND c.discovery_source = %s
            ORDER BY s.krs, s.searched_at
            """,
            (SOURCE,),
        ).fetchall()
        # The stored `documents.csv` listings: entities the importer skipped for too few years
        # have no `filing_index` rows, so their years are counted from the listing itself.
        documents_listings = [
            sha
            for (sha,) in conn.execute(
                """
                SELECT f.sha256 FROM raw_document_fetches f
                WHERE f.source_url = %s ORDER BY f.fetched_at
                """,
                (f"{FETCH_TIER}:{DOCUMENTS_FILE}",),
            )
        ]
    resolved = sorted(masters)
    print(f"list {SOURCE}: {len(listed)} KRS numbers")
    return (
        collections,
        listed,
        load_line_items,
        load_mapping_config,
        current_documents,
        documents_listings,
        filed_years,
        masters,
        pl,
        quarantined,
        raw_key,
        re,
        read_documents,
        resolved,
        searches,
        settings,
        store,
    )


@app.cell
def _(collections, listed, masters, quarantined):
    print("\n1. A2 outcome")
    print(f"   resolved: {len(masters)}")
    for _reason, _n in collections.Counter(quarantined.values()).most_common():
        print(f"   quarantined {_reason}: {_n}")
    print(f"   no outcome yet: {len(set(listed) - set(masters) - set(quarantined))}")


@app.cell
def _(collections, masters):
    print("\n2. PKD (predominant division, classification version)")
    _division = collections.Counter((p or "none")[:2] for _, _, p, _, _ in masters.values())
    print("   division:", dict(sorted(_division.items())))
    _version = collections.Counter(
        next((c["version"] for c in codes if c.get("predominant")), "none")
        for _, _, _, codes, _ in masters.values()
    )
    print("   version:", dict(sorted(_version.items())))
    print("\n3. Legal form and registry status (GUS)")
    print("   legal form code:", dict(collections.Counter(f for f, *_ in masters.values())))
    print("   status:", dict(collections.Counter(s for _, s, *_ in masters.values())))


@app.cell
def _(collections, masters, raw_key, re, store):
    def _field(data: bytes, name: str) -> str | None:
        _m = re.search(rb"<praw_" + name.encode() + rb">([^<]*)<", data)
        return _m[1].decode().strip() or None if _m else None

    regions, registered = collections.Counter(), {}
    for _krs, (*_, _sha) in masters.items():
        _data = store.get(raw_key(_sha))
        regions[(_field(_data, "adSiedzWojewodztwo_Nazwa") or "none").upper()] += 1
        registered[_krs] = _field(_data, "dataWpisuDoRejestruEwidencji")
    print("\n4. Region (voivodeship of the seat)")
    for _name, _n in sorted(regions.items(), key=lambda kv: (-kv[1], kv[0])):
        print(f"   {_name}: {_n}")
    print("\n5. Registration year in KRS (GUS)")
    _years = collections.Counter("unknown" if d is None else d[:4] for d in registered.values())
    _bands = collections.Counter()
    for _y, _n in _years.items():
        if _y == "unknown":
            _bands["unknown"] += _n
        elif int(_y) < 2010:
            _bands["before 2010"] += _n
        elif int(_y) < 2019:
            _bands["2010-2018"] += _n
        elif int(_y) < 2022:
            _bands["2019-2021"] += _n
        else:
            _bands["2022 or later"] += _n
    for _band in ("before 2010", "2010-2018", "2019-2021", "2022 or later", "unknown"):
        print(f"   {_band}: {_bands[_band]}")
    return (registered,)


@app.cell
def _(
    collections,
    current_documents,
    documents_listings,
    filed_years,
    raw_key,
    read_documents,
    resolved,
    searches,
    store,
):
    print("\n6. Filed years (non-deleted statement periods in the stored listings)")
    _tabs = [t for sha in documents_listings for t in read_documents(store.get(raw_key(sha)))[0]]
    _current, _ = current_documents(_tabs)
    _years = filed_years(_current.values())
    _latest = {}
    for _krs, _found, _complete in searches:
        _latest[str(_krs).strip()] = (_found, _complete)
    _bands = collections.Counter()
    for _krs in resolved:
        _search = _latest.get(_krs)
        if _search is None:
            _bands["not searched (or only during the outage)"] += 1
        elif not _search[0]:
            _bands["searched, not found on RDF"] += 1
        elif not _search[1]:
            _bands["search incomplete"] += 1
        else:
            _n = _years.get(_krs, 0)
            _bands["0" if _n == 0 else "1-2" if _n < 3 else "3-5" if _n < 6 else "6 or more"] += 1
    for _band in (
        "0",
        "1-2",
        "3-5",
        "6 or more",
        "search incomplete",
        "searched, not found on RDF",
        "not searched (or only during the outage)",
    ):
        print(f"   {_band}: {_bands[_band]}")


@app.cell
def _(collections, load_line_items, load_mapping_config, pl, resolved, settings):
    _forms = {s.structure_version: s.form for s in load_mapping_config().specs.values()}
    _revenue = list(load_line_items("line_items_v3").inputs["revenue"])
    _facts = (
        pl.scan_parquet(
            str(settings.warehouse_dir / "financial_statements_canonical" / "**" / "*.parquet"),
            hive_partitioning=True,
        )
        .filter(pl.col("krs").is_in(resolved) & (pl.col("column") == "current_year"))
        .select("krs", "period_end", "structure_version", "line_item", "value")
        .collect()
    )
    _latest = _facts.group_by("krs").agg(pl.col("period_end").max())
    _last = _facts.join(_latest, on=["krs", "period_end"])
    print("\n7. Size as the filings show it (latest parsed statement; not the §4.4 class)")
    print(f"   entities with a parsed statement: {_latest.height} of {len(resolved)}")
    _form = collections.Counter(
        _forms.get(v, "unknown")
        for v in _last.group_by("krs").agg(pl.col("structure_version").first())["structure_version"]
    )
    print("   form filed:", dict(sorted(_form.items())))

    def _bands(codes: list[str]) -> dict[str, int]:
        _values = (
            _last.filter(pl.col("line_item").is_in(codes))
            .group_by("krs")
            .agg(pl.col("value").first())["value"]
        )
        _out = collections.Counter()
        for _v in _values:
            _v = float(_v)
            _out[
                "< 2m"
                if _v < 2e6
                else "2-10m"
                if _v < 10e6
                else "10-50m"
                if _v < 50e6
                else "50m or more"
            ] += 1
        return {b: _out[b] for b in ("< 2m", "2-10m", "10-50m", "50m or more")}

    print("   total assets (PLN):", _bands(["BS.ASSETS"]))
    print("   revenue (PLN):", _bands(_revenue))
    print(
        "   latest period year:",
        dict(sorted(collections.Counter(str(d.year) for d in _latest["period_end"]).items())),
    )


@app.cell
def _(collections, pl, resolved, settings):
    _events = pl.read_parquet(
        str(settings.warehouse_dir / "legal_events" / "**" / "*.parquet"),
        hive_partitioning=True,
    ).filter(pl.col("krs").is_in(resolved))
    print("\n8. Distress as the pipeline's legal events find it (KRS extracts, MSiG)")
    print(f"   entities with any legal event: {_events['krs'].n_unique()} of {len(resolved)}")
    _classed = _events.filter(pl.col("outcome_class").is_not_null())
    _first = _classed.group_by("krs").agg(
        pl.col("event_date").min(), pl.col("outcome_class").sort_by("event_date").first()
    )
    print(f"   entities with a bankruptcy, restructuring or liquidation event: {_first.height}")
    print("   by the class of the first:", dict(collections.Counter(_first["outcome_class"])))
    _years = collections.Counter(
        "unknown" if d is None else "before 2019" if d.year < 2019 else str(d.year)
        for d in _first["event_date"]
    )
    print("   by the year of the first:", dict(sorted(_years.items())))
    _gone = _events.filter(pl.col("event_type") == "deregistered")["krs"].n_unique()
    print(f"   deregistered: {_gone}")


if __name__ == "__main__":
    app.run()
