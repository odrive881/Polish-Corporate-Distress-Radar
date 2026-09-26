"""Column-wise value hash of `financial_statements_canonical` — plan 0005 step H.

Proves plan 0005 decision 6: adding structure versions must not move the values
already in the canonical table. A new spec changes the mapping-config hash, so
every file gets a new `spec_hash` row and a new `ingestion_run_id` by design;
nothing else may differ. This computes a hash per structure version over every
column *except* `ingestion_run_id`, so a run id rotating is visible in the row
counts and the manifest but never hides a changed figure.

Recipe (so a later comparison means something): rows sorted by the engine's own
`SORT_KEY`, then for each column in order, SHA-256 over the column name and the
`str()` of each value, NUL-separated. It is the column order and the sort that
make it reproducible — not the Parquet bytes, which also carry run ids.

A second hash, **without lineage**, drops `source_document_hash` and `source_member` too and sorts by every remaining column. It is the
one to compare across a re-store of the raw objects (plan 0011 step E), which moves every
file's hash and member path by design and must move no figure.

Read-only: reads `WAREHOUSE_DIR`, touches no database, no object store, no
network. Run it before and after a mapping change:

    uv run python notebooks/exploration/canonical_value_hash.py
    uv run marimo edit notebooks/exploration/canonical_value_hash.py
"""

import marimo

__generated_with = "0.9"
app = marimo.App(width="medium")


@app.cell
def _():
    import hashlib

    import marimo as mo
    import polars as pl

    from distress_radar.parsing.mapping_engine import SORT_KEY
    from distress_radar.settings import Settings
    from distress_radar.warehouse import read_dataset

    return SORT_KEY, Settings, hashlib, mo, pl, read_dataset


@app.cell
def _(SORT_KEY, Settings, read_dataset):
    facts = read_dataset(Settings().warehouse_dir, "financial_statements_canonical").sort(SORT_KEY)
    # Everything a mapping change may not move. `ingestion_run_id` is excluded:
    # it is lineage, and a new mapping-config hash is meant to rotate it.
    VALUE_COLUMNS = [c for c in facts.columns if c != "ingestion_run_id"]
    # The full-form versions mapped before plan 0005 — the subset decision 6 is
    # about, kept as its own row so the figure survives later additions.
    PHASE_2 = ("full-2018-v1-0", "full-2018-v1-2", "full-2018-v1-2-tys", "full-2025-w2-v1-0")
    return PHASE_2, VALUE_COLUMNS, facts


@app.cell
def _(VALUE_COLUMNS, hashlib, pl):
    def column_hash(frame: pl.DataFrame) -> str:
        digest = hashlib.sha256()
        for column in VALUE_COLUMNS:
            digest.update(column.encode())
            digest.update(b"\0".join(str(v).encode() for v in frame[column].to_list()))
        return digest.hexdigest()

    return (column_hash,)


@app.cell
def _(PHASE_2, column_hash, facts, mo, pl):
    def rows() -> list[dict[str, object]]:
        out: list[dict[str, object]] = []
        for version in sorted(facts["structure_version"].unique().to_list()):
            part = facts.filter(pl.col("structure_version") == version)
            out.append({"scope": version, "rows": part.height, "value_hash": column_hash(part)})
        phase_2 = facts.filter(pl.col("structure_version").is_in(PHASE_2))
        out.append(
            {
                "scope": "phase 2 full-form (decision 6)",
                "rows": phase_2.height,
                "value_hash": column_hash(phase_2),
            }
        )
        out.append({"scope": "all rows", "rows": facts.height, "value_hash": column_hash(facts)})
        return out

    hashes = rows()
    for row in hashes:
        print(f"{row['scope']:32} {row['rows']:>6}  {row['value_hash']}")
    mo.ui.table(hashes)
    return (hashes,)


@app.cell
def _(facts, hashlib):
    # Lineage columns a re-store of the raw objects rewrites (plan 0011 step E). What is left
    # is the figures and what they describe; sorted by all of it, so no lineage sets the order.
    LINEAGE = {"source_document_hash", "source_member", "ingestion_run_id"}
    kept = [c for c in facts.columns if c not in LINEAGE]
    values = facts.select(kept).sort(kept)
    lineage_free = hashlib.sha256()
    for column in kept:
        lineage_free.update(column.encode())
        lineage_free.update(b"\0".join(str(v).encode() for v in values[column].to_list()))
    print(f"{'all rows, without lineage':32} {values.height:>6}  {lineage_free.hexdigest()}")
    return (lineage_free,)


if __name__ == "__main__":
    app.run()
