"""E2: accounting identity checks on canonical facts (AGENT_SPEC §4.3).

Each check is a pure function over the canonical frame and returns one row per
evaluated identity (`status` = `pass` | `fail` | `not_applicable`), so an asset
check can report both the failures and how much was actually checked.
Identities are evaluated per statement file (`source_document_hash` +
`source_member`) and per amount column. `not_applicable` is an outcome of its
own, never a pass: a small or micro filing has no cash-flow statement to tie,
and counting that as a pass would inflate every pass rate built on these rows.

Absent items are never imputed (invariant 4): a sum is checked over the
children that were reported (including the filer's own extra lines, unless the
parent is a "w tym" line), a parent with no reported statutory children is not
checked, and a cross-statement tie with a missing side is `not_applicable`.

`run_identity_checks` also judges each failure `material` or `immaterial`
(`severity`), and `grade` rolls that up into the per-file `quality_grade`. The
full frame is persisted as `identity_check_results` (AGENT_SPEC §5), the
per-check source `dq_mart` reports from (plan 0007 step A).

`prior_year_consistency` compares a statement's prior-year column with the
previously filed statement for the adjacent period; a difference is a
restatement finding (`restatement_events`), never a failure.
"""

# polars's signatures reference types pyright cannot resolve; scoped to this module.
# pyright: reportUnknownMemberType=false

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from typing import Literal

import polars as pl

from distress_radar.parsing.canonical_schema import (
    STATEMENT_TYPES,
    MappingConfig,
    Role,
    StatementAlternative,
    StatementBody,
    StatementName,
    StructureSpec,
)
from distress_radar.parsing.mapping_engine import VALUE_DTYPE

CheckName = Literal[
    "balance_sheet_balances", "subtotals_consistent", "profit_ties", "cashflow_ties"
]
IDENTITY_CHECKS: tuple[CheckName, ...] = (
    "balance_sheet_balances",
    "subtotals_consistent",
    "profit_ties",
    "cashflow_ties",
)
# A current-year subtotal failure no larger than this share of the statement's
# current total assets is immaterial: it grades `warn`, not `quarantined`.
SUBTOTAL_WARN_RELATIVE = Decimal("0.01")
DOCUMENT_KEY = ["source_document_hash", "source_member"]
CheckStatus = Literal["pass", "fail", "not_applicable"]
CHECK_STATUSES: tuple[CheckStatus, ...] = ("pass", "fail", "not_applicable")
Severity = Literal["material", "immaterial"]
SEVERITIES: tuple[Severity, ...] = ("material", "immaterial")

RESULT_SCHEMA: dict[str, pl.DataType | type[pl.DataType]] = {
    "krs": pl.String,
    "document_ref": pl.String,
    "source_document_hash": pl.String,
    "source_member": pl.String,
    "period_end": pl.Date,
    "column": pl.String,
    "check": pl.String,
    "line_item": pl.String,
    "status": pl.String,
    "expected": VALUE_DTYPE,
    "actual": VALUE_DTYPE,
    "difference": VALUE_DTYPE,
}
# `run_identity_checks` output: `RESULT_SCHEMA` plus the judgement `grade` rolls up.
SEVERITY_SCHEMA: dict[str, pl.DataType | type[pl.DataType]] = {
    **RESULT_SCHEMA,
    "severity": pl.String,
}
# The persisted dataset: each result with the lineage of the file it checks.
IDENTITY_CHECK_RESULTS_SCHEMA: dict[str, pl.DataType | type[pl.DataType]] = {
    "krs": pl.String,
    "fiscal_year": pl.Int32,
    "period_end": pl.Date,
    "document_ref": pl.String,
    "source_document_hash": pl.String,
    "source_member": pl.String,
    "structure_version": pl.String,
    "known_from": pl.Date,
    "ingestion_run_id": pl.String,
    "column": pl.String,
    "check": pl.String,
    "line_item": pl.String,
    "status": pl.String,
    "severity": pl.String,
    "expected": VALUE_DTYPE,
    "actual": VALUE_DTYPE,
    "difference": VALUE_DTYPE,
}
IDENTITY_CHECK_RESULTS_SORT_KEY = [
    "krs",
    "period_end",
    "document_ref",
    "source_member",
    "column",
    "check",
    "line_item",
]
FILED_BODIES_SCHEMA: dict[str, pl.DataType | type[pl.DataType]] = {
    "source_document_hash": pl.String,
    "source_member": pl.String,
    "structure_version": pl.String,
    "filed_bodies": pl.String,
}
UNRESOLVED_BODY_SCHEMA: dict[str, pl.DataType | type[pl.DataType]] = {
    "krs": pl.String,
    "document_ref": pl.String,
    "source_document_hash": pl.String,
    "source_member": pl.String,
    "structure_version": pl.String,
    "statement": pl.String,
    "body_used": pl.String,
}
RESTATEMENT_SCHEMA: dict[str, pl.DataType | type[pl.DataType]] = {
    "krs": pl.String,
    "fiscal_year": pl.Int32,
    "period_start": pl.Date,
    "period_end": pl.Date,
    "line_item": pl.String,
    "restated_column": pl.String,
    "originally_reported_value": VALUE_DTYPE,
    "restated_value": VALUE_DTYPE,
    "original_document_hash": pl.String,
    "original_source_member": pl.String,
    "original_document_ref": pl.String,
    "restating_document_hash": pl.String,
    "restating_source_member": pl.String,
    "restating_document_ref": pl.String,
    "known_from": pl.Date,
}


@dataclass(frozen=True)
class _Group:
    krs: str
    document_ref: str
    source_document_hash: str
    source_member: str
    period_end: date
    column: str
    spec: StructureSpec
    # Which body each statement was filed in. A small filing may carry the
    # small statements or the full ones, chosen per statement (plan 0005
    # step D), so the rules to check it against are per document.
    bodies: tuple[tuple[StatementName, str], ...]
    values: dict[str, Decimal]


def _filed_alternative(
    spec: StructureSpec, name: StatementName, paths: list[str]
) -> StatementAlternative | None:
    """The shape a statement was filed in, read from `source_element_path`.

    The path records the statement element the facts came from, which is the
    only thing that distinguishes `BilansJednostkaMala` from
    `BilansJednostkaInna` inside the same small envelope.
    """
    return next(
        (
            alt
            for alt in spec.alternatives(name)
            if any(path.startswith(f"{spec.statement_root}/{alt.xpath}/") for path in paths)
        ),
        None,
    )


def _bodies_filed(spec: StructureSpec, paths: list[str]) -> tuple[tuple[StatementName, str], ...]:
    """Which body each statement is checked against."""
    filed: list[tuple[StatementName, str]] = []
    for name in spec.statements:
        alts = spec.alternatives(name)
        # No path matched: fall back to the first alternative rather than
        # dropping the statement. An empty result would silently check nothing,
        # which is a far worse failure than checking against the wrong body.
        # `unresolved_bodies` reports where that fallback was actually used.
        filed.append((name, (_filed_alternative(spec, name, paths) or alts[0]).body))
    return tuple(filed)


def filed_bodies(frame: pl.DataFrame, config: MappingConfig) -> pl.DataFrame:
    """Each statement file's body set: the distinct bodies its statements were filed in.

    Sorted and joined with `+`, so a small envelope carrying one full-form
    statement reads e.g. `jednostka_inna+jednostka_mala`, and a single-body
    file reads as that body (plan 0007 amendment 6). Only statements the file
    actually contains count. A statement whose own shape cannot be read counts
    under the fallback body the checks used (`_bodies_filed`), which
    `unresolved_bodies` reports.
    """
    rows: list[dict[str, object]] = []
    for keys, part in frame.sort(DOCUMENT_KEY).group_by(DOCUMENT_KEY, maintain_order=True):
        spec = config.specs[part.row(0, named=True)["structure_version"]]
        paths = part["source_element_path"].to_list()
        present = set(part["statement_type"].to_list())
        bodies = {
            body
            for name, body in _bodies_filed(spec, paths)
            if STATEMENT_TYPES[name] in present
        }
        rows.append(
            {
                "source_document_hash": str(keys[0]),
                "source_member": str(keys[1]),
                "structure_version": spec.structure_version,
                "filed_bodies": "+".join(sorted(bodies)),
            }
        )
    return _frame(rows, FILED_BODIES_SCHEMA)


def unresolved_bodies(frame: pl.DataFrame, config: MappingConfig) -> pl.DataFrame:
    """Statements checked against a fallback body because their own could not be read.

    One row per (file, statement) that has facts but whose element paths match
    none of the shapes its spec accepts, so `_bodies_filed` fell back to the
    first. The fallback is deliberate — checking nothing is worse — but it is
    invisible in the results, and it means a filing may be checked against
    rules that are not its own. A statement the document simply does not
    contain is not a fallback and is not reported; nor is a statement whose
    spec accepts only one shape, where there is nothing to resolve.

    Empty is the expected state. A row means a body was renamed, a spec lost an
    alternative, or `source_element_path` stopped being written the way the
    spec declares it.
    """
    rows: list[dict[str, object]] = []
    for keys, part in frame.sort(DOCUMENT_KEY).group_by(DOCUMENT_KEY, maintain_order=True):
        first = part.row(0, named=True)
        spec = config.specs[first["structure_version"]]
        paths = part["source_element_path"].to_list()
        filed_types = set(part["statement_type"].to_list())
        for name in spec.statements:
            alts = spec.alternatives(name)
            if len(alts) == 1 or STATEMENT_TYPES[name] not in filed_types:
                continue
            if _filed_alternative(spec, name, paths) is not None:
                continue
            rows.append(
                {
                    "krs": first["krs"],
                    "document_ref": first["document_ref"],
                    "source_document_hash": str(keys[0]),
                    "source_member": str(keys[1]),
                    "structure_version": spec.structure_version,
                    "statement": name,
                    "body_used": alts[0].body,
                }
            )
    return _frame(rows, UNRESOLVED_BODY_SCHEMA)


def _groups(frame: pl.DataFrame, config: MappingConfig) -> Iterator[_Group]:
    for keys, part in frame.sort([*DOCUMENT_KEY, "column"]).group_by(
        [*DOCUMENT_KEY, "column"], maintain_order=True
    ):
        first = part.row(0, named=True)
        values: dict[str, Decimal] = dict(
            zip(part["line_item"].to_list(), part["value"].to_list(), strict=True)
        )
        spec = config.specs[first["structure_version"]]
        paths = part["source_element_path"].to_list()
        yield _Group(
            krs=first["krs"],
            document_ref=first["document_ref"],
            source_document_hash=str(keys[0]),
            source_member=str(keys[1]),
            period_end=first["period_end"],
            column=str(keys[2]),
            spec=spec,
            bodies=_bodies_filed(spec, paths),
            values=values,
        )


def _row(
    g: _Group,
    check: CheckName,
    line_item: str,
    expected: Decimal | None,
    actual: Decimal | None,
    tolerance: Decimal,
) -> dict[str, object]:
    status: CheckStatus
    if expected is None or actual is None:
        status, difference = "not_applicable", None
    else:
        difference = actual - expected
        status = "pass" if abs(difference) <= tolerance else "fail"
    return {
        "krs": g.krs,
        "document_ref": g.document_ref,
        "source_document_hash": g.source_document_hash,
        "source_member": g.source_member,
        "period_end": g.period_end,
        "column": g.column,
        "check": check,
        "line_item": line_item,
        "status": status,
        "expected": expected,
        "actual": actual,
        "difference": difference,
    }


def _frame(
    rows: list[dict[str, object]], schema: dict[str, pl.DataType | type[pl.DataType]]
) -> pl.DataFrame:
    return pl.DataFrame(rows, schema=schema, orient="row") if rows else pl.DataFrame(schema=schema)


def _role_value(g: _Group, config: MappingConfig, role: Role) -> tuple[str, Decimal | None]:
    codes = sorted(config.chart.codes_with_role(role) & set(g.values))
    if len(codes) > 1:
        raise ValueError(f"{g.source_member}: several {role} lines present {codes}")
    return (codes[0], g.values[codes[0]]) if codes else ("", None)


def balance_sheet_balances(
    frame: pl.DataFrame, config: MappingConfig, tolerance: Decimal
) -> pl.DataFrame:
    rows: list[dict[str, object]] = []
    for g in _groups(frame, config):
        code, assets = _role_value(g, config, "total_assets")
        _, eq_liab = _role_value(g, config, "total_equity_and_liabilities")
        rows.append(_row(g, "balance_sheet_balances", code or "BS", assets, eq_liab, tolerance))
    return _frame(rows, RESULT_SCHEMA)


def profit_ties(frame: pl.DataFrame, config: MappingConfig, tolerance: Decimal) -> pl.DataFrame:
    rows: list[dict[str, object]] = []
    for g in _groups(frame, config):
        code, income = _role_value(g, config, "net_result")
        _, balance = _role_value(g, config, "net_result_balance_sheet")
        rows.append(_row(g, "profit_ties", code or "IS", income, balance, tolerance))
    return _frame(rows, RESULT_SCHEMA)


def cashflow_ties(frame: pl.DataFrame, config: MappingConfig, tolerance: Decimal) -> pl.DataFrame:
    """Net cash flow == closing cash − opening cash.

    No side reported at all (no cash-flow statement, as in every small and
    micro filing) is one `not_applicable` row per column: recorded, so the
    exemption is visible, and never counted as a pass.

    A difference exactly explained by the reported exchange-rate effect on cash
    (the "w tym" line under the balance-sheet change) passes: filers present
    that effect outside the net cash flow.
    """
    rows: list[dict[str, object]] = []
    for g in _groups(frame, config):
        code, net = _role_value(g, config, "net_cash_flow")
        _, opening = _role_value(g, config, "cash_opening")
        _, closing = _role_value(g, config, "cash_closing")
        _, fx = _role_value(g, config, "cash_fx_effect")
        if net is None and opening is None and closing is None:
            rows.append(_row(g, "cashflow_ties", "CF", None, None, tolerance))
            continue
        movement = None if opening is None or closing is None else closing - opening
        row = _row(g, "cashflow_ties", code or "CF", movement, net, tolerance)
        explained_by_fx = (
            fx is not None
            and movement is not None
            and net is not None
            and abs(abs(movement - net) - abs(fx)) <= tolerance
        )
        if row["status"] == "fail" and fx and explained_by_fx:
            row["status"] = "pass"
        rows.append(row)
    return _frame(rows, RESULT_SCHEMA)


def _subtotal_rules(
    body: StatementBody, spec: StructureSpec, only: StatementName | None = None
) -> list[tuple[str, list[tuple[int, str]]]]:
    """(computed code, [(sign, operand code)]) for every sum and formula, in canonical codes."""

    def canon(code: str) -> str:
        return spec.code_overrides.get(code, code)

    rules: list[tuple[str, list[tuple[int, str]]]] = []
    for name, items in body.statements.items():
        if name in body.no_subtotal_check or (only is not None and name != only):
            continue
        for parent in items:
            if parent.code is None or parent.header:
                continue
            if parent.code in body.formulas:
                operands = [(sign, canon(code)) for sign, code in body.formulas[parent.code]]
            else:
                operands = [
                    (1, canon(child.code))
                    for child in items
                    if child.code is not None
                    and not child.of_which
                    and child.path.rpartition("/")[0] == parent.path
                ]
                if operands and parent.user_code is not None and not parent.user_of_which:
                    operands.append((1, canon(parent.user_code)))
            if operands:
                rules.append((canon(parent.code), operands))
    return rules


def subtotals_consistent(
    frame: pl.DataFrame, config: MappingConfig, tolerance: Decimal
) -> pl.DataFrame:
    rows: list[dict[str, object]] = []
    cache: dict[
        tuple[str, tuple[tuple[StatementName, str], ...]],
        list[tuple[str, list[tuple[int, str]]]],
    ] = {}
    for g in _groups(frame, config):
        key = (g.spec.structure_version, g.bodies)
        if key not in cache:
            cache[key] = [
                rule
                for name, body in g.bodies
                for rule in _subtotal_rules(config.bodies[body], g.spec, name)
            ]
        for code, operands in cache[key]:
            if code not in g.values:
                continue
            present = [(sign, g.values[c]) for sign, c in operands if c in g.values]
            if not present:
                continue
            total = sum((Decimal(sign) * value for sign, value in present), Decimal(0))
            rows.append(_row(g, "subtotals_consistent", code, total, g.values[code], tolerance))
    return _frame(rows, RESULT_SCHEMA)


CHECKS = {
    "balance_sheet_balances": balance_sheet_balances,
    "subtotals_consistent": subtotals_consistent,
    "profit_ties": profit_ties,
    "cashflow_ties": cashflow_ties,
}


def run_identity_checks(
    frame: pl.DataFrame, config: MappingConfig, tolerance: Decimal
) -> pl.DataFrame:
    """Every identity check over `frame`, with each failure's `severity` (`SEVERITY_SCHEMA`)."""
    parts = [CHECKS[name](frame, config, tolerance) for name in IDENTITY_CHECKS]
    results = pl.concat(parts).sort([*DOCUMENT_KEY, "column", "check", "line_item"])
    return _with_severity(frame, results, config)


def _with_severity(
    frame: pl.DataFrame, results: pl.DataFrame, config: MappingConfig
) -> pl.DataFrame:
    """Judge each `fail` row `material` or `immaterial`; null on every other row.

    `material`: a failure in the current-year column that is either a tie or a
    subtotal off by more than `SUBTOTAL_WARN_RELATIVE` of the file's current
    total assets (or with no total assets to compare). `immaterial`: every
    other failure: small subtotal differences, and anything confined to the
    prior-year columns, whose authority is the earlier filing (differences
    there are restatement findings).
    """
    assets_codes = config.chart.codes_with_role("total_assets")
    scale: dict[tuple[str, str], Decimal] = {
        (row[0], row[1]): row[2]
        for row in frame.filter(
            pl.col("line_item").is_in(sorted(assets_codes)) & (pl.col("column") == "current_year")
        )
        .select(*DOCUMENT_KEY, "value")
        .iter_rows()
    }

    def judge(row: dict[str, object]) -> Severity | None:
        if row["status"] != "fail":
            return None
        if row["column"] != "current_year":
            return "immaterial"
        if row["check"] != "subtotals_consistent":
            return "material"
        assets = scale.get((str(row["source_document_hash"]), str(row["source_member"])))
        difference = row["difference"]
        assert isinstance(difference, Decimal)
        if assets is None or abs(difference) > abs(assets) * SUBTOTAL_WARN_RELATIVE:
            return "material"
        return "immaterial"

    severities = [judge(row) for row in results.iter_rows(named=True)]
    return results.with_columns(pl.Series("severity", severities, dtype=pl.String)).select(
        list(SEVERITY_SCHEMA)
    )


def grade(frame: pl.DataFrame, results: pl.DataFrame) -> pl.DataFrame:
    """Set `quality_grade` per statement file from its judged identity results.

    `quarantined`: any `material` failure. `warn`: failures, all `immaterial`.
    `pass`: nothing failed. `results` is `run_identity_checks` output: the
    materiality rule lives in `_with_severity`, so the grade and the persisted
    `severity` can never disagree.
    """
    failures = results.filter(pl.col("status") == "fail")
    severe = {
        (str(row[0]), str(row[1]))
        for row in failures.filter(pl.col("severity") == "material")
        .select(DOCUMENT_KEY)
        .iter_rows()
    }
    failed = {(str(row[0]), str(row[1])) for row in failures.select(DOCUMENT_KEY).iter_rows()}
    keys = frame.select(DOCUMENT_KEY).unique(maintain_order=True)
    grades = [
        "quarantined" if key in severe else "warn" if key in failed else "pass"
        for key in keys.iter_rows()
    ]
    graded = keys.with_columns(pl.Series("_grade", grades, dtype=pl.String))
    return (
        frame.join(graded, on=DOCUMENT_KEY, how="left", maintain_order="left")
        .with_columns(pl.col("_grade").alias("quality_grade"))
        .drop("_grade")
        .select(frame.columns)
    )


def identity_check_results(results: pl.DataFrame, facts: pl.DataFrame) -> pl.DataFrame:
    """`run_identity_checks` output with its file's lineage, as persisted (AGENT_SPEC §5).

    Lineage is taken per file from the canonical facts the checks ran on, so
    every row can be joined back to its statement without the facts. Sorted by
    `IDENTITY_CHECK_RESULTS_SORT_KEY` for a byte-stable write (ADR 0008).
    """
    lineage = facts.select(
        *DOCUMENT_KEY, "fiscal_year", "structure_version", "known_from", "ingestion_run_id"
    ).unique()
    per_file = lineage.group_by(DOCUMENT_KEY).len().filter(pl.col("len") > 1)
    if not per_file.is_empty():
        raise ValueError(f"{per_file.height} statement files carry more than one lineage")
    return (
        results.join(lineage, on=DOCUMENT_KEY, how="left", validate="m:1")
        .select(list(IDENTITY_CHECK_RESULTS_SCHEMA))
        .sort(IDENTITY_CHECK_RESULTS_SORT_KEY)
    )


@dataclass(frozen=True)
class _Doc:
    key: tuple[str, str]
    document_ref: str
    fiscal_year: int
    period_start: date
    period_end: date
    known_from: date


def prior_year_consistency(frame: pl.DataFrame, tolerance: Decimal) -> pl.DataFrame:
    """Restatement events: prior-year figures that differ from what was filed before.

    For each statement file, the reference is the latest statement file of the
    same entity whose period ends the day before this one starts and whose
    `known_from` is on or before this one's. Both `prior_year` (KwotaB) and
    `prior_year_restated` (KwotaB1) are compared with its `current_year`.
    """
    docs = frame.select(
        "krs",
        *DOCUMENT_KEY,
        "document_ref",
        "fiscal_year",
        "period_start",
        "period_end",
        "known_from",
    ).unique()
    by_krs: dict[str, list[_Doc]] = {}
    for d in docs.sort(["krs", "known_from", "document_ref", *DOCUMENT_KEY]).iter_rows(named=True):
        by_krs.setdefault(d["krs"], []).append(
            _Doc(
                key=(d["source_document_hash"], d["source_member"]),
                document_ref=d["document_ref"],
                fiscal_year=d["fiscal_year"],
                period_start=d["period_start"],
                period_end=d["period_end"],
                known_from=d["known_from"],
            )
        )

    current: dict[tuple[str, str], dict[str, Decimal]] = {}
    restating: dict[tuple[str, str], dict[str, dict[str, Decimal]]] = {}
    for row in frame.select(*DOCUMENT_KEY, "column", "line_item", "value").iter_rows():
        key = (row[0], row[1])
        if row[2] == "current_year":
            current.setdefault(key, {})[row[3]] = row[4]
        else:
            restating.setdefault(key, {}).setdefault(row[2], {})[row[3]] = row[4]

    events: list[dict[str, object]] = []
    for krs in sorted(by_krs):
        entity_docs = by_krs[krs]
        for doc in entity_docs:
            columns = restating.get(doc.key)
            if not columns:
                continue
            candidates = [
                d
                for d in entity_docs
                if d.period_end == doc.period_start - timedelta(days=1)
                and d.known_from <= doc.known_from
            ]
            if not candidates:
                continue
            ref = candidates[-1]  # sorted by known_from, then document_ref
            original = current.get(ref.key, {})
            for column in sorted(columns):
                for line_item in sorted(columns[column]):
                    if line_item not in original:
                        continue
                    restated, was = columns[column][line_item], original[line_item]
                    if abs(restated - was) <= tolerance:
                        continue
                    events.append(
                        {
                            "krs": krs,
                            "fiscal_year": ref.fiscal_year,
                            "period_start": ref.period_start,
                            "period_end": ref.period_end,
                            "line_item": line_item,
                            "restated_column": column,
                            "originally_reported_value": was,
                            "restated_value": restated,
                            "original_document_hash": ref.key[0],
                            "original_source_member": ref.key[1],
                            "original_document_ref": ref.document_ref,
                            "restating_document_hash": doc.key[0],
                            "restating_source_member": doc.key[1],
                            "restating_document_ref": doc.document_ref,
                            "known_from": doc.known_from,
                        }
                    )
    return _frame(events, RESTATEMENT_SCHEMA)
