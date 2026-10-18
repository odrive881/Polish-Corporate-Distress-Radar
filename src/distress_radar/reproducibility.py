"""Whether the offline chain reproduces itself (invariant 5; plan 0021 step A).

Each stage is tested on its own; this compares the chain's outputs as a whole. A dataset's
*digest* does not depend on how it is laid out on disk: its columns are taken in name order, each
row is hashed, the row hashes are sorted, and the SHA-256 of them and of the schema is the digest.
So two runs that write the same rows into other files, row groups or orders agree, and a single
changed value does not.

The runner is orchestration (`dagster_defs/reproduce.py`); what it compares is here.
"""

# polars's signatures reference types pyright cannot resolve; scoped to this module.
# pyright: reportUnknownMemberType=false

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

import duckdb
import polars as pl

from distress_radar.transform_project import LABEL_MODELS, read_model
from distress_radar.warehouse import read_dataset

# The datasets the offline chain writes, in chain order (plan 0021 decision 1).
DATASETS = (
    "financial_statements_canonical",
    "statement_disclosures",
    "identity_check_results",
    "restatement_events",
    "entity_size_class_history",
    "legal_events",
    "feature_store",
)
# The label marts in `transform.duckdb`. `marts.outcome_labels` fixes the frozen label set, which is
# named by the hash of those rows, so it is compared through the mart.
MARTS = tuple(m for m in LABEL_MODELS if m.startswith("marts."))
_SEEDS = (0, 1, 2, 3)  # fixed: the same rows hash alike in every run


def dataset_digest(frame: pl.DataFrame) -> str:
    """The SHA-256 of a frame's schema and its sorted row hashes, whatever its row order."""
    columns = sorted(frame.columns)
    ordered = frame.select(columns)
    digest = hashlib.sha256()
    digest.update(repr([(c, str(ordered.schema[c])) for c in columns]).encode())
    if ordered.height:
        digest.update(ordered.hash_rows(*_SEEDS).sort().to_numpy().tobytes())
    digest.update(str(ordered.height).encode())
    return digest.hexdigest()


def warehouse_digests(warehouse_dir: Path) -> dict[str, str | None]:
    """Every chain dataset's digest and the label marts', None for one not built there."""
    found: dict[str, str | None] = {}
    for name in DATASETS:
        try:
            found[name] = dataset_digest(read_dataset(warehouse_dir, name))
        except FileNotFoundError:
            found[name] = None
    for mart in MARTS:
        if not (warehouse_dir / "transform.duckdb").exists():
            found[mart] = None
            continue
        try:
            found[mart] = dataset_digest(read_model(mart, warehouse_dir))
        except duckdb.CatalogException:
            found[mart] = None
    return found


@dataclass(frozen=True)
class Comparison:
    name: str
    first: str | None
    second: str | None
    stored: str | None

    @property
    def reproduced(self) -> bool:
        return self.first is not None and self.first == self.second

    @property
    def stored_is_current(self) -> bool:
        return self.stored is not None and self.stored == self.first


def compare(
    first: dict[str, str | None], second: dict[str, str | None], stored: dict[str, str | None]
) -> list[Comparison]:
    return [
        Comparison(name, first.get(name), second.get(name), stored.get(name))
        for name in [*DATASETS, *MARTS]
    ]
