"""The methodology report's figures (plan 0016 step G).

Every number in `report/methodology.qmd` comes from here, and from one source: the publish database
the public site reads (`publish.PUBLISH_DB`), with its small cells already suppressed and its
identifiers in `provenance`. So the report and the site cannot disagree, and the report crosses
the publish boundary no further than the site does. The only other input is the registry's served
version per horizon (the champion rule's verdict), which names model kinds, never companies.

The figures are written as Markdown files under `report/_figures/`, which the report includes;
`make report` regenerates them, then renders. Generated, never committed.

    uv run python -m distress_radar.methodology
"""

# polars's signatures reference types pyright cannot resolve; scoped to this module.
# pyright: reportUnknownMemberType=false

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path

import duckdb
import polars as pl
from mlflow import MlflowClient

from distress_radar.labels import LabelConfig, load_label_config
from distress_radar.models.serving import ServedVersion, resolve
from distress_radar.models.splits import BacktestConfig, load_backtest_config
from distress_radar.publish import PUBLISH_DB, STAGES
from distress_radar.settings import Settings

FIGURES_DIR = Path("report/_figures")
TABLES = (
    "provenance",
    "pipeline_coverage",
    "dq_coverage",
    "backtest_metrics",
    "backtest_reliability",
)
NA = "n/a"
SUPPRESSED = "suppressed"


def read_publish(path: Path) -> dict[str, pl.DataFrame]:
    """The published tables the report reads, as frames."""
    with duckdb.connect(path.as_posix(), read_only=True) as con:
        out: dict[str, pl.DataFrame] = {}
        for name in TABLES:
            cursor = con.execute(f'SELECT * FROM "{name}"')
            columns = [c[0] for c in cursor.description or []]
            out[name] = pl.DataFrame(
                cursor.fetchall(), schema=columns, orient="row", infer_schema_length=None
            )
    return out


def _table(header: Sequence[str], rows: Sequence[Sequence[str]]) -> str:
    lines = ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
    lines += ["| " + " | ".join(row) + " |" for row in rows]
    return "\n".join(lines) + "\n"


def _number(value: object, digits: int = 3) -> str:
    return NA if value is None else f"{float(value):.{digits}f}"  # type: ignore[arg-type]


def _count(value: object) -> str:
    return NA if value is None else str(int(value))  # type: ignore[call-overload]


def metric(row: Mapping[str, object], name: str) -> str:
    """A pooled metric with its interval, as the backtest report prints it: `0.034 [0.027–0.040]`."""
    if row.get("suppressed"):
        return SUPPRESSED
    if not row.get("evaluable"):
        return NA
    if row.get(name) is None:
        return f"{NA} (undefined)"
    low, high = row.get(f"{name}_low"), row.get(f"{name}_high")
    interval = "" if low is None or high is None else f" [{_number(low)}–{_number(high)}]"
    return _number(row[name]) + interval


def provenance_figure(tables: Mapping[str, pl.DataFrame]) -> str:
    rows = [[f"`{i}`", f"`{v}`"] for i, v in tables["provenance"].sort("identifier").iter_rows()]
    return _table(["identifier", "value"], rows)


def coverage_figure(tables: Mapping[str, pl.DataFrame]) -> str:
    frame = tables["pipeline_coverage"]
    lists = sorted(set(frame.get_column("list").to_list()))
    rows: list[list[str]] = []
    for stage in STAGES:
        row = [stage.replace("_", " ")]
        for name in lists:
            cell = frame.filter((pl.col("list") == name) & (pl.col("stage") == stage))
            row.append(
                SUPPRESSED
                if cell.is_empty() or cell.row(0, named=True)["suppressed"]
                else _count(cell.row(0, named=True)["entities"])
            )
        rows.append(row)
    return _table(["stage", *lists], rows)


def dq_figure(tables: Mapping[str, pl.DataFrame]) -> str:
    rows = [
        [
            "unknown" if r["fiscal_year"] is None else str(r["fiscal_year"]),
            *(
                [SUPPRESSED] * 6
                if r["suppressed"]
                else [
                    _count(r["files_stored"]),
                    _count(r["parsed"]),
                    _count(r["graded_pass"]),
                    _count(r["graded_warn"]),
                    _count(r["graded_quarantined"]),
                    _count(r["needs_pdf_tier"]),
                ]
            ),
        ]
        for r in tables["dq_coverage"].sort("fiscal_year", nulls_last=True).iter_rows(named=True)
    ]
    return _table(
        ["fiscal year", "stored", "parsed", "pass", "warn", "quarantined", "filed as PDF"], rows
    )


def metrics_figure(tables: Mapping[str, pl.DataFrame], horizon: int, run: str = "main") -> str:
    frame = tables["backtest_metrics"].filter(
        (pl.col("horizon_months") == horizon) & (pl.col("run") == run)
    )
    rows = [
        [
            str(r["model"]),
            _count(r["scored_rows"]) if not r["suppressed"] else SUPPRESSED,
            _count(r["test_events"]) if not r["suppressed"] else SUPPRESSED,
            _count(r["entities"]) if not r["suppressed"] else SUPPRESSED,
            metric(r, "brier"),
            metric(r, "log_loss"),
            metric(r, "auc"),
            metric(r, "top_decile_precision"),
        ]
        for r in frame.sort("model").iter_rows(named=True)
    ]
    return _table(
        [
            "model",
            "scored rows",
            "events",
            "companies",
            "Brier",
            "log loss",
            "AUC",
            "top-decile precision",
        ],
        rows,
    )


def reliability_figure(tables: Mapping[str, pl.DataFrame], horizon: int, run: str = "main") -> str:
    frame = tables["backtest_reliability"].filter(
        (pl.col("horizon_months") == horizon) & (pl.col("run") == run)
    )
    if frame.is_empty():
        return "No pooled cell at this horizon has enough events for a reliability table.\n"
    rows = [
        [
            str(r["model"]),
            f"{float(r['bin_low']):.1f}–{float(r['bin_high']):.1f}",
            *(
                [SUPPRESSED] * 4
                if r["suppressed"]
                else [
                    _count(r["rows"]),
                    _count(r["entities"]),
                    _number(r["mean_predicted"]),
                    _number(r["observed_rate"]),
                ]
            ),
        ]
        for r in frame.sort("model", "bin_low").iter_rows(named=True)
    ]
    return _table(["model", "probability", "rows", "companies", "mean predicted", "observed"], rows)


def _verdict(v: ServedVersion) -> str:
    """Promoted or not, and on what basis (plan 0019 decision 8)."""
    if v.alias != "champion":
        return "nothing promoted"
    return "promoted, provisional (unweighted)" if v.provisional else "promoted, final (weighted)"


def served_figure(served: Mapping[int, ServedVersion]) -> str:
    """The champion rule's verdict: what serves each horizon, and whether anything was promoted."""
    rows = [
        [
            f"{horizon} months",
            v.model.replace("_", " "),
            f"{v.name} v{v.version}",
            v.alias,
            _verdict(v),
            str(v.training_eve),
        ]
        for horizon, v in sorted(served.items())
    ]
    return _table(
        ["horizon", "model", "registered version", "alias", "verdict", "training eve"], rows
    )


def settings_figure(config: BacktestConfig, labels: LabelConfig) -> str:
    """The rules the figures rest on, read from the published backtest's config and its label
    version, so no prose states a number a new version could change."""
    rows = [
        ["backtest", f"`{config.backtest}`"],
        ["horizons", ", ".join(f"{h} months" for h in config.horizons)],
        ["outcome classes", ", ".join(c.replace("_", " ") for c in config.distress_classes)],
        ["test years", ", ".join(map(str, config.test_years))],
        ["fewest events for a metric", str(config.min_events)],
        [
            "intervals",
            (
                f"{config.bootstrap.level:.0%} percentile, {config.bootstrap.replicates} resamples of "
                "companies"
            ),
        ],
        ["reliability bands", f"{config.reliability_bins} of equal width"],
        ["label version", f"`{labels.label_version}`"],
        ["`alive` settled after", f"{labels.alive_lag_months} months (registry lag allowance)"],
        [
            "an unanswered petition expires after",
            "never"
            if labels.petition_expiry_months is None
            else f"{labels.petition_expiry_months} months",
        ],
        [
            "companies already in a proceeding",
            "excluded" if labels.exclude_in_proceeding else "kept",
        ],
    ]
    rows.append(
        ["regression inputs", ", ".join(f"`{f}`" for f in config.logistic_regression.features)]
    )
    if config.champion is not None:
        champion = config.champion
        rows += [
            ["incumbent", champion.incumbent.replace("_", " ")],
            [
                "a challenger is promoted when",
                (
                    "its pooled Brier score is lower, the paired company-bootstrap interval of the "
                    f"difference wholly below zero; its AUC is at most {champion.auc_margin} lower; "
                    f"both in every one of the runs {', '.join(champion.runs)}; and the pooled cells "
                    f"rest on at least {champion.min_promotion_events} distinct events"
                ),
            ],
        ]
    return _table(["setting", "value"], rows)


def check_served(served: Mapping[int, ServedVersion], backtest: str) -> None:
    """Refuse served versions registered by another backtest than the one published: the report
    would pair one run's verdict with another's figures."""
    other = {
        h: v.tags.get("backtest") for h, v in served.items() if v.tags.get("backtest") != backtest
    }
    if other:
        raise ValueError(
            f"the served versions come from {other}, the publish database from {backtest}"
        )


def figures(
    tables: Mapping[str, pl.DataFrame],
    served: Mapping[int, ServedVersion],
    config: BacktestConfig,
    labels: LabelConfig,
) -> dict[str, str]:
    """Every figure the report includes, by file stem."""
    out = {
        "settings": settings_figure(config, labels),
        "provenance": provenance_figure(tables),
        "coverage": coverage_figure(tables),
        "dq_coverage": dq_figure(tables),
        "served": served_figure(served),
    }
    for horizon in sorted(served):
        out[f"metrics_{horizon}"] = metrics_figure(tables, horizon)
        out[f"reliability_{horizon}"] = reliability_figure(tables, horizon)
    return out


def write_figures(figures: Mapping[str, str], out_dir: Path) -> list[Path]:
    """Replace the figure files whole: a figure no longer produced does not linger."""
    out_dir.mkdir(parents=True, exist_ok=True)
    for old in out_dir.glob("*.md"):
        old.unlink()
    written: list[Path] = []
    for name, text in sorted(figures.items()):
        path = out_dir / f"{name}.md"
        path.write_text(text, encoding="utf-8")
        written.append(path)
    return written


def main(repo_dir: Path = Path()) -> list[Path]:
    """Read the publish database and the served versions, write the figures."""
    settings = Settings()
    database = repo_dir / PUBLISH_DB
    if not database.exists():
        raise FileNotFoundError(f"{database} is missing: run the publish job first")
    tables = read_publish(database)
    backtest = tables["provenance"].filter(pl.col("identifier") == "backtest").get_column("value")
    config = load_backtest_config(str(backtest[0]))
    client = MlflowClient(tracking_uri=settings.mlflow_tracking_uri)
    served = {h: resolve(client, h) for h in config.horizons}
    check_served(served, config.backtest)
    label_version = tables["provenance"].filter(pl.col("identifier") == "label_version")
    labels = load_label_config(str(label_version.get_column("value")[0]))
    return write_figures(figures(tables, served, config, labels), repo_dir / FIGURES_DIR)


if __name__ == "__main__":
    for path in main():
        print(path)
