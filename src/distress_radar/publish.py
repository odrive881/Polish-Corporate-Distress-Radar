"""The publish boundary (plan 0016 step F, owner decisions 0 and 9; AGENT_SPEC §10, §12).

The only database the public site reads, `site/sources/publish/publish.duckdb`, is built here from
a fixed set of aggregate tables, never from a query a page writes:

- `dq_checks` and `dq_coverage`: `marts.dq_mart` and `marts.dq_mart_coverage` as SQLMesh built them;
- `pipeline_coverage`: per universe list, how many entities reached each stage, from listed to
  modelled;
- `backtest_metrics`: the pinned backtest's pooled cells per run, horizon and model, as its MLflow
  runs logged them (counts, Brier score, log loss, AUC, top-decile precision, their intervals);
- `backtest_reliability`: the pooled reliability tables of the cells that speak (evaluable),
  rebuilt from `model_predictions` exactly as the backtest builds them, and refused when the rows
  disagree with what MLflow logged;
- `provenance`: the identifiers every figure is regenerable from (AGENT_SPEC §12): the code commit,
  the feature-store snapshot, the label version and set, the feature set, the backtest, and the
  small-cell threshold.

**Small cells** (decision 9): every table carries `entities` (the distinct companies a cell covers)
and `suppressed`. A cell under the threshold publishes its measures as null and is kept, whatever
threshold SQLMesh built the marts with; the threshold itself must be set to at least 5
(`Settings.publish_min_cell_entities`), or nothing is built. **No identifier crosses**:
`check_publishable` refuses a database with an identifying column, or holding any KRS, NIP or REGON
of the entity tables as a value.

No sector or regional rate and no predicted rate is published (decision 0): the lists are
purposive (ADR 0014).
"""

# polars's signatures reference types pyright cannot resolve; scoped to this module.
# pyright: reportUnknownMemberType=false

from __future__ import annotations

import os
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

import duckdb
import polars as pl
from mlflow import MlflowClient
from mlflow.entities import Run

from distress_radar.models.dataset import TARGET
from distress_radar.models.evaluation import METRICS, reliability
from distress_radar.models.registry import EXPERIMENT

PUBLISH_DB = Path("site/sources/publish/publish.duckdb")
FORBIDDEN_COLUMNS = frozenset(
    {"krs", "nip", "regon", "name", "company_name", "document_ref", "source_document_hash"}
)
POOLED = "pooled"
COUNTS = ("train_rows", "train_events", "scored_rows", "test_events")
STAGES = (
    "listed",
    "identified",
    "searched_on_rdf",
    "statements_stored",
    "statements_parsed",
    "labelled",
    "modelled",
)
DQ_CHECK_MEASURES = (
    "files",
    "passed",
    "failed_material",
    "failed_immaterial",
    "not_applicable",
    "pass_rate",
)
DQ_COVERAGE_MEASURES = (
    "files_stored",
    "parsed",
    "graded_pass",
    "graded_warn",
    "graded_quarantined",
    "not_yet_mapped",
    "needs_pdf_tier",
    "needs_pdf_tier_without_later_filing",
    "quarantined_before_grading",
)


def suppress(frame: pl.DataFrame, measures: Sequence[str], threshold: int) -> pl.DataFrame:
    """Null every measure, and `entities`, of a cell covering fewer than `threshold` entities;
    `suppressed` is true there, and stays true where an upstream threshold already suppressed."""
    small = pl.col("entities").is_null() | (pl.col("entities") < threshold)
    if "suppressed" in frame.columns:
        small = small | pl.col("suppressed")
    return (
        frame.with_columns(small.alias("_small"))
        .with_columns(
            *(pl.when(pl.col("_small")).then(None).otherwise(pl.col(m)).alias(m) for m in measures),
            pl.when(pl.col("_small")).then(None).otherwise(pl.col("entities")).alias("entities"),
            pl.col("_small").alias("suppressed"),
        )
        .drop("_small")
    )


def dq_tables(
    dq_mart: pl.DataFrame, dq_mart_coverage: pl.DataFrame, threshold: int
) -> dict[str, pl.DataFrame]:
    return {
        "dq_checks": suppress(dq_mart, DQ_CHECK_MEASURES, threshold).sort(
            "structure_version", "filed_bodies", "fiscal_year", "check", nulls_last=True
        ),
        "dq_coverage": suppress(dq_mart_coverage, DQ_COVERAGE_MEASURES, threshold).sort(
            "fiscal_year", nulls_last=True
        ),
    }


def pipeline_coverage(
    lists: Mapping[str, frozenset[str]], stages: Mapping[str, frozenset[str]], threshold: int
) -> pl.DataFrame:
    """Per list and stage (`STAGES`, in order), how many of the list's entities reached it."""
    missing = [s for s in STAGES[1:] if s not in stages]
    if missing:
        raise ValueError(f"no entities given for the stages {missing}")
    rows = [
        {
            "list": name,
            "stage_order": order,
            "stage": stage,
            "entities": len(members if stage == "listed" else members & stages[stage]),
        }
        for name, members in sorted(lists.items())
        for order, stage in enumerate(STAGES)
    ]
    frame = pl.DataFrame(
        rows,
        schema={
            "list": pl.String,
            "stage_order": pl.Int32,
            "stage": pl.String,
            "entities": pl.Int64,
        },
    )
    return suppress(frame, (), threshold)


def _backtest_runs(client: MlflowClient, backtest: str) -> list[Run]:
    """The backtest's finished top-level MLflow runs: one per run, horizon and model."""
    experiment = client.get_experiment_by_name(EXPERIMENT)
    if experiment is None:
        raise LookupError(f"no MLflow experiment {EXPERIMENT}")
    return [
        r
        for r in client.search_runs(
            [experiment.experiment_id],
            filter_string=f"params.backtest = '{backtest}' and attributes.status = 'FINISHED'",
            max_results=10_000,
        )
        if "mlflow.parentRunId" not in r.data.tags
    ]


def backtest_params(client: MlflowClient, backtest: str) -> dict[str, str]:
    """The identifiers every run of the backtest shares (`cells_from_mlflow` checks they agree)."""
    runs = _backtest_runs(client, backtest)
    if not runs:
        raise LookupError(f"no finished MLflow run of {backtest}")
    return dict(runs[0].data.params)


def cells_from_mlflow(client: MlflowClient, backtest: str) -> pl.DataFrame:
    """The pinned backtest's cells, one row per run, horizon, model and test year (`pooled` for
    the pooled cell), from its finished top-level MLflow runs: one per run, horizon and model."""
    runs = _backtest_runs(client, backtest)
    if not runs:
        raise LookupError(f"no finished MLflow run of {backtest}")
    keys = [
        (r.data.params["run"], r.data.params["horizon_months"], r.data.params["model"])
        for r in runs
    ]
    if len(set(keys)) != len(keys):
        raise ValueError(f"{backtest} was run more than once: its MLflow runs repeat a cell")
    for name in ("code_commit", "data_snapshot_hash", "label_set_hash", "feature_set_hash"):
        values = {r.data.params.get(name) for r in runs}
        if len(values) != 1:
            raise ValueError(
                f"{backtest}'s MLflow runs disagree on {name}: {sorted(map(str, values))}"
            )
    rows: list[dict[str, object]] = []
    for run in runs:
        metrics = run.data.metrics
        years = sorted({k.rsplit(".", 1)[1] for k in metrics if k.startswith("evaluable.")})
        for year in years:
            row: dict[str, object] = {
                "run": run.data.params["run"],
                "horizon_months": int(run.data.params["horizon_months"]),
                "model": run.data.params["model"],
                "test_year": year,
                "evaluable": bool(metrics[f"evaluable.{year}"]),
                **{c: int(metrics[f"{c}.{year}"]) for c in COUNTS},
            }
            for name in METRICS:
                for suffix in ("", "_low", "_high"):
                    row[f"{name}{suffix}"] = metrics.get(f"{name}{suffix}.{year}")
            rows.append(row)
    schema: dict[str, pl.DataType | type[pl.DataType]] = {
        "run": pl.String,
        "horizon_months": pl.Int32,
        "model": pl.String,
        "test_year": pl.String,
        "evaluable": pl.Boolean,
        **dict.fromkeys(COUNTS, pl.Int64),
        **{f"{n}{s}": pl.Float64 for n in METRICS for s in ("", "_low", "_high")},
    }
    return pl.DataFrame(rows, schema=schema).sort("run", "horizon_months", "model", "test_year")


@dataclass(frozen=True)
class Pooled:
    metrics: pl.DataFrame
    reliability: pl.DataFrame


def pooled_tables(
    cells: pl.DataFrame,
    predictions: pl.DataFrame,
    *,
    backtest: str,
    min_events: int,
    bins: int,
    threshold: int,
) -> Pooled:
    """The pooled cells with the entities they cover, and the reliability tables of those that
    speak. A cell's pooled rows are the test rows of the folds that trained on at least
    `min_events` events, as in `backtest.run_backtest`; their count must equal MLflow's
    `scored_rows`, or the predictions are not the ones the backtest logged."""
    rows = predictions.filter(pl.col("backtest") == backtest)
    folds = cells.filter(pl.col("test_year") != POOLED)
    entities_by_cell: list[dict[str, object]] = []
    tables: list[pl.DataFrame] = []
    pooled_cells = cells.filter(pl.col("test_year") == POOLED)
    for cell in pooled_cells.iter_rows(named=True):
        key = (
            (pl.col("run") == cell["run"])
            & (pl.col("horizon_months") == cell["horizon_months"])
            & (pl.col("model") == cell["model"])
        )
        speaking = (
            folds.filter(key & (pl.col("train_events") >= min_events))
            .get_column("test_year")
            .cast(pl.Int32)
            .to_list()
        )
        pooled = rows.filter(key & pl.col("test_year").is_in(speaking))
        if pooled.height != cell["scored_rows"]:
            raise ValueError(
                f"{backtest} {cell['run']}/{cell['model']}/{cell['horizon_months']}m: "
                f"{pooled.height} pooled rows in model_predictions, MLflow logged "
                f"{cell['scored_rows']}"
            )
        entities_by_cell.append(
            {
                "run": cell["run"],
                "horizon_months": cell["horizon_months"],
                "model": cell["model"],
                "entities": pooled.get_column("krs").n_unique(),
            }
        )
        if cell["evaluable"] and not pooled.is_empty():
            entities = (
                pooled.with_columns(
                    (pl.col("probability") * bins)
                    .floor()
                    .clip(0, bins - 1)
                    .cast(pl.Int32)
                    .alias("bin")
                )
                .group_by("bin")
                .agg(pl.col("krs").n_unique().cast(pl.Int64).alias("entities"))
                .with_columns((pl.col("bin") / bins).alias("bin_low"))
                .drop("bin")
            )
            tables.append(
                reliability(pooled.select("probability", TARGET), bins)
                .join(entities, on="bin_low", how="left")
                .select(
                    pl.lit(cell["run"]).alias("run"),
                    pl.lit(cell["horizon_months"], pl.Int32).alias("horizon_months"),
                    pl.lit(cell["model"]).alias("model"),
                    pl.all(),
                )
            )
    metric_columns = [
        c
        for c in pooled_cells.columns
        if c not in ("run", "horizon_months", "model", "test_year", "evaluable")
    ]
    entity_counts = pl.DataFrame(
        entities_by_cell,
        schema={
            "run": pl.String,
            "horizon_months": pl.Int32,
            "model": pl.String,
            "entities": pl.Int64,
        },
    )
    metric_frame = (
        pooled_cells.drop("test_year")
        .join(entity_counts, on=["run", "horizon_months", "model"], how="left")
        .sort("run", "horizon_months", "model")
    )
    reliability_frame = (
        pl.concat(tables).sort("run", "horizon_months", "model", "bin_low")
        if tables
        else pl.DataFrame(
            schema={
                "run": pl.String,
                "horizon_months": pl.Int32,
                "model": pl.String,
                "bin_low": pl.Float64,
                "bin_high": pl.Float64,
                "rows": pl.Int64,
                "distress_rows": pl.Int64,
                "mean_predicted": pl.Float64,
                "observed_rate": pl.Float64,
                "entities": pl.Int64,
            }
        )
    )
    return Pooled(
        metrics=suppress(metric_frame, metric_columns, threshold),
        reliability=suppress(
            reliability_frame,
            ("rows", "distress_rows", "mean_predicted", "observed_rate"),
            threshold,
        ),
    )


def provenance(identifiers: Mapping[str, str | int]) -> pl.DataFrame:
    """One row per identifier, in name order: what every published figure is regenerable from."""
    return pl.DataFrame(
        {
            "identifier": sorted(identifiers),
            "value": [str(identifiers[k]) for k in sorted(identifiers)],
        }
    )


def write_publish_db(tables: Mapping[str, pl.DataFrame], path: Path) -> Path:
    """Write the tables into a new DuckDB file at `path`, replacing the old one whole: built beside
    it, then moved into place, so the site never reads a half-built database."""
    path.parent.mkdir(parents=True, exist_ok=True)
    staging = path.with_suffix(".building.duckdb")
    staging.unlink(missing_ok=True)
    with duckdb.connect(staging.as_posix()) as con:
        for name, frame in sorted(tables.items()):
            con.register("_frame", frame)
            con.execute(f'CREATE TABLE "{name}" AS SELECT * FROM _frame')
            con.unregister("_frame")
    os.replace(staging, path)
    return path


def check_publishable(path: Path, identifiers: Iterable[str]) -> None:
    """Refuse a publish database with an identifying column, or holding any of `identifiers`
    (every KRS, NIP and REGON of the entity tables) as a value of any text column."""
    known = {i.strip() for i in identifiers if i and i.strip()}
    with duckdb.connect(path.as_posix(), read_only=True) as con:
        columns = con.execute(
            "SELECT table_name, column_name, data_type FROM information_schema.columns"
        ).fetchall()
        bad = sorted(f"{t}.{c}" for t, c, _ in columns if str(c).lower() in FORBIDDEN_COLUMNS)
        if bad:
            raise ValueError(f"identifying columns in the publish database: {bad}")
        for table, column, data_type in columns:
            if str(data_type).upper() not in ("VARCHAR", "TEXT"):
                continue
            values = con.execute(f'SELECT DISTINCT "{column}" FROM "{table}"').fetchall()
            hits = sorted({str(v) for (v,) in values if v is not None and str(v).strip() in known})
            if hits:
                raise ValueError(
                    f"{table}.{column} holds {len(hits)} entity identifier(s): nothing published"
                )
