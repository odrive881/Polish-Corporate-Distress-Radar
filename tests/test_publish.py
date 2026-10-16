"""The publish boundary (plan 0016 step F): small cells suppressed whatever SQLMesh built, pooled
cells and reliability tables rebuilt exactly as the backtest builds them, and nothing identifying
in the database."""

from __future__ import annotations

import re
from pathlib import Path

import duckdb
import polars as pl
import pytest
from mlflow import MlflowClient
from mlflow.entities import Metric, Param

from distress_radar import publish as pub
from distress_radar.models.evaluation import reliability
from distress_radar.models.registry import EXPERIMENT


def test_a_small_cell_publishes_no_measure_and_is_kept() -> None:
    frame = pl.DataFrame(
        {
            "cell": ["a", "b", "c"],
            "files": [40, 3, None],
            "entities": [12, 4, None],
            "suppressed": [False, False, True],  # c: already suppressed by SQLMesh
        }
    )
    out = pub.suppress(frame, ["files"], threshold=10)
    assert out.get_column("cell").to_list() == ["a", "b", "c"]
    assert out.get_column("files").to_list() == [40, None, None]
    assert out.get_column("entities").to_list() == [12, None, None]
    assert out.get_column("suppressed").to_list() == [False, True, True]


def test_a_threshold_applies_even_where_sqlmesh_suppressed_nothing() -> None:
    dq_mart = pl.DataFrame(
        {
            "structure_version": ["v", "v"],
            "filed_bodies": ["b", "b"],
            "fiscal_year": [2024, 2025],
            "check": ["k", "k"],
            **{m: [1, 1] for m in pub.DQ_CHECK_MEASURES},
            "entities": [30, 6],
            "suppressed": [False, False],
        }
    )
    coverage = pl.DataFrame(
        {
            "fiscal_year": [2024],
            **{m: [1] for m in pub.DQ_COVERAGE_MEASURES},
            "entities": [7],
            "suppressed": [False],
        }
    )
    tables = pub.dq_tables(dq_mart, coverage, threshold=10)
    assert tables["dq_checks"].get_column("suppressed").to_list() == [False, True]
    assert tables["dq_checks"].row(1, named=True)["pass_rate"] is None
    assert tables["dq_coverage"].row(0, named=True)["files_stored"] is None


def test_pipeline_coverage_counts_each_lists_entities_per_stage() -> None:
    lists = {
        "seed": frozenset(f"{i:010d}" for i in range(12)),
        "small": frozenset(f"{i:010d}" for i in range(100, 103)),
    }
    reached = frozenset(f"{i:010d}" for i in range(11)) | {"0000000100"}
    stages = dict.fromkeys(pub.STAGES[1:], reached)
    out = pub.pipeline_coverage(lists, stages, threshold=10)
    seed = out.filter(pl.col("list") == "seed")
    assert seed.get_column("stage").to_list() == list(pub.STAGES)
    assert seed.get_column("entities").to_list() == [12] + [11] * 6
    assert out.filter(pl.col("list") == "small").get_column("suppressed").all()
    with pytest.raises(ValueError, match="no entities given"):
        pub.pipeline_coverage(lists, {"identified": reached}, threshold=10)


def _cells() -> pl.DataFrame:
    rows = [
        # run, model, year, train_events, scored_rows, test_events, evaluable
        ("main", "m", "2023", 2, 2, 1, False),  # trained on too few events: not pooled
        ("main", "m", "2024", 3, 40, 2, False),
        ("main", "m", "2025", 5, 40, 2, False),
        ("main", "m", pub.POOLED, 3, 80, 4, True),
        ("main", "weak", "2025", 1, 40, 2, False),
        ("main", "weak", pub.POOLED, 0, 0, 0, False),
    ]
    return pl.DataFrame(
        [
            {
                "run": r,
                "horizon_months": 12,
                "model": m,
                "test_year": y,
                "evaluable": ev,
                "train_rows": 100,
                "train_events": te,
                "scored_rows": sr,
                "test_events": events,
                **{
                    f"{n}{s}": (0.1 if ev else None)
                    for n in ("brier", "auc")
                    for s in ("", "_low", "_high")
                },
            }
            for r, m, y, te, sr, events, ev in rows
        ]
    )


def _predictions() -> pl.DataFrame:
    rows: list[dict[str, object]] = []
    for model in ("m", "weak"):
        for year, count in ((2023, 2), (2024, 40), (2025, 40)):
            for i in range(count):
                rows.append(
                    {
                        "backtest": "bt",
                        "run": "main",
                        "horizon_months": 12,
                        "model": model,
                        "test_year": year,
                        "krs": f"{i % 20:010d}",
                        "probability": (i % 10) / 10 + 0.05,
                        "distress": i % 10 == 9,
                    }
                )
    return pl.DataFrame(rows, schema_overrides={"horizon_months": pl.Int32, "test_year": pl.Int32})


def test_pooled_rows_are_the_folds_that_trained_on_enough_events() -> None:
    pooled = pub.pooled_tables(
        _cells(), _predictions(), backtest="bt", min_events=3, bins=10, threshold=1
    )
    metrics = {r["model"]: r for r in pooled.metrics.iter_rows(named=True)}
    assert metrics["m"]["entities"] == 20 and metrics["m"]["brier"] == 0.1
    assert metrics["weak"]["suppressed"] and metrics["weak"]["entities"] is None  # covers none
    # Only the evaluable cell gets a reliability table, equal to the backtest's own function.
    assert set(pooled.reliability.get_column("model").to_list()) == {"m"}
    rows = _predictions().filter((pl.col("model") == "m") & pl.col("test_year").is_in([2024, 2025]))
    expected = reliability(rows, 10)
    assert pooled.reliability.select(expected.columns).equals(expected)
    assert pooled.reliability.get_column("entities").to_list() == [2] * 10


def test_bins_and_cells_under_the_threshold_are_suppressed() -> None:
    pooled = pub.pooled_tables(
        _cells(), _predictions(), backtest="bt", min_events=3, bins=10, threshold=5
    )
    assert pooled.reliability.get_column("suppressed").all()
    assert pooled.reliability.get_column("observed_rate").null_count() == 10
    weak = pooled.metrics.filter(pl.col("model") == "weak").row(0, named=True)
    assert weak["suppressed"] and weak["scored_rows"] is None


def test_predictions_that_disagree_with_mlflow_are_refused() -> None:
    fewer = _predictions().filter(
        ~((pl.col("model") == "m") & (pl.col("test_year") == 2025)).or_(
            pl.col("krs") != "0000000001"
        )
    )
    with pytest.raises(ValueError, match="MLflow logged 80"):
        pub.pooled_tables(_cells(), fewer, backtest="bt", min_events=3, bins=10, threshold=1)


def test_cells_are_read_from_the_backtests_finished_runs(tmp_path: Path) -> None:
    client = MlflowClient(tracking_uri=f"sqlite:///{tmp_path / 'mlflow.db'}")
    experiment = client.create_experiment(EXPERIMENT, artifact_location=tmp_path.as_uri())
    for model in ("m", "n"):
        run = client.create_run(experiment)
        run_id = run.info.run_id
        client.log_batch(
            run_id,
            metrics=[
                Metric(k, v, 0, 0)
                for k, v in {
                    "evaluable.2025": 1.0,
                    "evaluable.pooled": 1.0,
                    "train_rows.2025": 10,
                    "train_events.2025": 3,
                    "scored_rows.2025": 5,
                    "test_events.2025": 3,
                    "train_rows.pooled": 10,
                    "train_events.pooled": 3,
                    "scored_rows.pooled": 5,
                    "test_events.pooled": 3,
                    "brier.pooled": 0.2,
                    "brier_low.pooled": 0.1,
                }.items()
            ],
            params=[
                Param(k, v)
                for k, v in {
                    "backtest": "bt",
                    "run": "main",
                    "horizon_months": "12",
                    "model": model,
                    "code_commit": "a" * 40,
                    "data_snapshot_hash": "b" * 64,
                    "label_set_hash": "c" * 64,
                    "feature_set_hash": "d" * 64,
                }.items()
            ],
        )
        client.set_terminated(run_id)
    cells = pub.cells_from_mlflow(client, "bt")
    assert cells.height == 4
    pooled = cells.filter((pl.col("model") == "m") & (pl.col("test_year") == pub.POOLED)).row(
        0, named=True
    )
    assert pooled["brier"] == 0.2 and pooled["brier_low"] == 0.1 and pooled["auc"] is None
    assert pooled["scored_rows"] == 5 and pooled["evaluable"]
    assert pub.backtest_params(client, "bt")["code_commit"] == "a" * 40
    with pytest.raises(LookupError, match="no finished MLflow run"):
        pub.cells_from_mlflow(client, "other")


def test_the_database_is_replaced_whole(tmp_path: Path) -> None:
    path = tmp_path / "site" / "publish.duckdb"
    pub.write_publish_db({"a": pl.DataFrame({"x": [1]}), "b": pl.DataFrame({"y": ["z"]})}, path)
    pub.write_publish_db({"a": pl.DataFrame({"x": [2]})}, path)
    with duckdb.connect(path.as_posix(), read_only=True) as con:
        assert con.execute("SELECT table_name FROM information_schema.tables").fetchall() == [
            ("a",)
        ]
        assert con.execute("SELECT x FROM a").fetchall() == [(2,)]
    pub.check_publishable(path, ["0000000001"])


@pytest.mark.parametrize(
    ("table", "message"),
    [
        (pl.DataFrame({"krs": ["x"]}), "identifying columns"),
        (pl.DataFrame({"NIP": ["x"]}), "identifying columns"),
        (pl.DataFrame({"note": ["fine", "0000000001"]}), "1 entity identifier"),
        (pl.DataFrame({"note": [" 1234567890 "]}), "1 entity identifier"),
    ],
)
def test_nothing_identifying_crosses(tmp_path: Path, table: pl.DataFrame, message: str) -> None:
    path = pub.write_publish_db({"t": table}, tmp_path / "publish.duckdb")
    with pytest.raises(ValueError, match=message):
        pub.check_publishable(path, ["0000000001", "1234567890", None])  # type: ignore[list-item]


def test_the_publish_job_runs_the_publish_asset_alone() -> None:
    from dagster_defs.definitions import defs

    job = defs.resolve_job_def("publish")
    assert {k.to_user_string() for k in job.asset_layer.selected_asset_keys} == {"publish"}


SITE = Path(__file__).parents[1] / "site"
_SQL = re.compile(r"```sql (\w+)\n(.*?)```", re.DOTALL)
_COLUMN = re.compile(r"<Column id=(\w+)")


def _synthetic_publish_db(path: Path) -> Path:
    cells = _cells()
    pooled = pub.pooled_tables(
        cells, _predictions(), backtest="bt", min_events=3, bins=10, threshold=1
    )
    dq = pub.dq_tables(
        pl.DataFrame(
            {
                "structure_version": ["v"],
                "filed_bodies": ["b"],
                "fiscal_year": [2024],
                "check": ["k"],
                **{m: [1] for m in pub.DQ_CHECK_MEASURES},
                "entities": [30],
                "suppressed": [False],
            }
        ),
        pl.DataFrame(
            {
                "fiscal_year": [2024],
                **{m: [1] for m in pub.DQ_COVERAGE_MEASURES},
                "entities": [30],
                "suppressed": [False],
            }
        ),
        threshold=10,
    )
    lists = {"seed": frozenset({"0000000001"})}
    tables = {
        **dq,
        "pipeline_coverage": pub.pipeline_coverage(
            lists, dict.fromkeys(pub.STAGES[1:], lists["seed"]), threshold=1
        ),
        "backtest_metrics": pooled.metrics,
        "backtest_reliability": pooled.reliability,
        "provenance": pub.provenance({"code_commit": "a" * 40}),
    }
    return pub.write_publish_db(tables, path)


def test_every_source_query_names_a_published_table() -> None:
    queries = sorted((SITE / "sources" / "publish").glob("*.sql"))
    assert {q.stem for q in queries} == {
        "dq_checks",
        "dq_coverage",
        "pipeline_coverage",
        "backtest_metrics",
        "backtest_reliability",
        "provenance",
    }


@pytest.mark.parametrize("page", sorted((SITE / "pages").glob("*.md")), ids=lambda p: p.stem)
def test_every_page_query_runs_on_the_publish_database(tmp_path: Path, page: Path) -> None:
    """Evidence exposes each source query as `publish.<name>`: the same here, over a synthetic
    database. Each `<Column id>` must be a column one of the page's queries returns."""
    database = _synthetic_publish_db(tmp_path / "publish.duckdb")
    text = page.read_text(encoding="utf-8")
    returned: set[str] = set()
    with duckdb.connect() as con:
        con.execute(f"ATTACH '{database.as_posix()}' AS publish (READ_ONLY)")
        for _, query in _SQL.findall(text):
            cursor = con.execute(query)
            returned |= {c[0] for c in cursor.description or []}
    assert set(_COLUMN.findall(text)) <= returned
