"""Step A's two seed checks for the scripted downloads — plan 0014, decisions 3 and 5.

Both are tested on the seed as the HAR import left it, from the stores alone, before the
importer relies on them:
1. **Decision 5, type codes from names.** Every `filing_index` row's code, which came from RDF's
   own list, against `RdfDocumentTypes.code_for(name, period end)`: the detail's type name for
   detailed rows; for rows never expanded, the configured name of their own code (which checks
   only that its period bounds hold).
2. **Decision 3, pairing a correction group's members.** Each stored download shared by several
   rows, its members paired by `pair_by_prepared_date` (header `DataSporzadzenia` against each
   row's `prepared_date`), against the pairing the HAR import made (the members' tokens). A rule
   that pairs every group exactly so may switch on `script_import.PAIR_BY_PREPARED_DATE`.

**Counts only**, and document ids; no document content is printed. Read-only: reads Postgres
and MinIO, writes nothing. Needs `make dev-up`.

    uv run python notebooks/exploration/script_import_seed_checks.py
"""

import marimo

__generated_with = "0.9"
app = marimo.App(width="medium")


@app.cell
def _():
    import collections
    import io
    import zipfile
    from pathlib import PurePosixPath

    import psycopg

    from distress_radar.acquisition.document_retrieval import load_document_types
    from distress_radar.acquisition.raw_store import S3ObjectStore, raw_key
    from distress_radar.acquisition.redaction import file_token
    from distress_radar.acquisition.script_import import pair_by_prepared_date
    from distress_radar.settings import Settings

    settings = Settings()
    document_types = load_document_types()
    with psycopg.connect(settings.postgres_conninfo) as conn:
        filings = conn.execute(
            "SELECT krs, document_ref, rdf_type_code, rdf_type_name, period_end, prepared_date,"
            " detail_sha256 IS NOT NULL, sha256 FROM filing_index ORDER BY krs, document_ref"
        ).fetchall()
    return (
        PurePosixPath,
        S3ObjectStore,
        collections,
        document_types,
        file_token,
        filings,
        io,
        pair_by_prepared_date,
        raw_key,
        settings,
        zipfile,
    )


@app.cell
def _(collections, document_types, filings):
    outcomes = collections.Counter()
    misses = []
    for _krs, _ref, _code, _name, _end, _, _detailed, _ in filings:
        if _detailed and _name:
            _source = "detail name"
        elif _code in document_types.types:
            _source, _name = "configured name", document_types.types[_code].name
        else:
            outcomes[("no name", _code, "not placed")] += 1
            continue
        _found = document_types.code_for(_name, _end)
        _verdict = "agrees" if _found == _code else "differs"
        outcomes[(_source, _code, _verdict)] += 1
        if _found != _code:
            misses.append((str(_krs).strip(), _ref, _code, _found, _end.isoformat()))
    print(f"1. type codes from name and period: {len(filings)} rows")
    for _key, _n in sorted(outcomes.items(), key=lambda kv: (kv[0][0], str(kv[0][1]))):
        print("  ", *_key, _n)
    print("   differs (krs, ref, code, rule's code, period end):", misses)
    return misses, outcomes


@app.cell
def _(
    PurePosixPath,
    S3ObjectStore,
    collections,
    file_token,
    filings,
    io,
    pair_by_prepared_date,
    raw_key,
    settings,
    zipfile,
):
    store = S3ObjectStore.from_endpoint(
        settings.minio_endpoint,
        settings.minio_access_key,
        settings.minio_secret_key.get_secret_value(),
        settings.minio_bucket,
    )
    groups = collections.defaultdict(list)
    for _krs, _ref, _, _, _, _prepared, _, _sha in filings:
        if _sha is not None:
            groups[_sha].append((_ref, _prepared))
    pairing = collections.Counter()
    results = []
    for _sha, _rows in sorted(groups.items()):
        if len(_rows) < 2:
            continue
        _raw = store.get(raw_key(_sha))
        with zipfile.ZipFile(io.BytesIO(_raw)) as _archive:
            _members = [PurePosixPath(i.filename).stem for i in _archive.infolist()]
        _truth = {
            _ref: _stem
            for _ref, _ in _rows
            for _stem in _members
            if _stem == file_token(_ref, None)
        }
        _pairs = pair_by_prepared_date(_raw, dict(_rows))
        if _pairs is None:
            _verdict = "unpaired"
        elif {r: PurePosixPath(n).stem for r, n in _pairs.items()} == _truth and len(_truth) == len(
            _rows
        ):
            _verdict = "exact"
        else:
            _verdict = "wrong"
        pairing[_verdict] += 1
        results.append((_sha[:12], len(_rows), len(_truth), _verdict))
    print(f"2. correction groups: {sum(pairing.values())}, by outcome: {dict(pairing)}")
    print("   (download, rows, rows the HAR import paired, outcome):", results)
    return pairing, results


if __name__ == "__main__":
    app.run()
