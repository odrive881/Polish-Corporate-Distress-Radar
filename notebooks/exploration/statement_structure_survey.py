"""Statement structure survey (plan 0004 step A): what the stored statement files are.

Reproduces the plan 0004 survey table from MinIO and Postgres: for every
downloaded annual statement, the file(s) inside the stored download, the
detected structure version, and whether a mapping spec exists. Re-run before
Phase 3 and before any backfill, to see which versions still need a spec.

Read-only: it reads stored bytes and manifest rows and writes nothing.
Needs the compose services (`make dev-up`).

Run interactively: `uv run marimo edit notebooks/exploration/statement_structure_survey.py`
Run headless:      `uv run python notebooks/exploration/statement_structure_survey.py`
"""

import marimo

__generated_with = "0.24.2"
app = marimo.App(width="medium")


@app.cell
def _():
    import polars as pl
    import psycopg
    from lxml import etree

    from distress_radar.acquisition.raw_store import S3ObjectStore, raw_key
    from distress_radar.parsing.canonical_schema import load_mapping_config
    from distress_radar.parsing.containers import ContainerError, safe_parser, unwrap
    from distress_radar.parsing.version_detection import DetectionError, detect
    from distress_radar.settings import Settings

    settings = Settings()
    config = load_mapping_config()
    store = S3ObjectStore.from_endpoint(
        settings.minio_endpoint,
        settings.minio_access_key,
        settings.minio_secret_key.get_secret_value(),
        settings.minio_bucket,
    )
    return (
        ContainerError,
        DetectionError,
        config,
        detect,
        etree,
        pl,
        psycopg,
        raw_key,
        safe_parser,
        settings,
        store,
        unwrap,
    )


@app.cell
def _(psycopg, settings):
    with psycopg.connect(settings.postgres_conninfo) as conn:
        downloads = conn.execute(
            """
            SELECT sha256, krs, min(period_end)
            FROM filing_index
            WHERE sha256 IS NOT NULL AND rdf_type_code = '18' AND deleted_on IS NULL
            GROUP BY sha256, krs
            ORDER BY krs, 3
            """
        ).fetchall()
    return (downloads,)


@app.cell
def _(
    ContainerError,
    DetectionError,
    config,
    detect,
    downloads,
    etree,
    raw_key,
    safe_parser,
    store,
    unwrap,
):
    rows = []
    for sha, krs, period_end in downloads:
        try:
            members = unwrap(store.get(raw_key(sha)))
        except ContainerError as exc:
            rows.append((krs, period_end.year, "container_error", exc.reason_code, ""))
            continue
        for member in members:
            if member.kind != "xml_statement":
                rows.append((krs, period_end.year, member.kind, "", ""))
                continue
            root = etree.fromstring(member.data, safe_parser())
            try:
                detection = detect(root, config)
            except DetectionError as exc:
                rows.append((krs, period_end.year, "no_header", str(exc), ""))
                continue
            _, root_name, kod, wersja = detection.key
            spec = detection.spec.structure_version if detection.spec else ""
            rows.append(
                (krs, period_end.year, f"{root_name} {kod} {wersja}", detection.status, spec)
            )
    return (rows,)


@app.cell
def _(pl, rows):
    frame = pl.DataFrame(rows, schema=["krs", "year", "structure", "status", "spec"], orient="row")
    summary = (
        frame.group_by("structure", "status", "spec")
        .agg(
            pl.len().alias("files"),
            pl.col("krs").n_unique().alias("entities"),
            pl.col("year").min().alias("first_year"),
            pl.col("year").max().alias("last_year"),
        )
        .sort("files", descending=True)
    )
    print(summary)
    return frame, summary


if __name__ == "__main__":
    app.run()
