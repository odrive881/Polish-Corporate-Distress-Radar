"""`build_feature_store` against a real manifest (plan 0010 step E).

Builds the manifest schema in a throwaway Postgres schema (`make dev-up`; run via
`make test-integration`), fills it with one synthetic entity, and builds the store from it and a
synthetic warehouse: the loading path (char(10) keys, timestamptz fetches, the joins) end to end.
"""

# polars's signatures reference types pyright cannot resolve; scoped to this module.
# pyright: reportUnknownMemberType=false

import uuid
from collections.abc import Iterator
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

import polars as pl
import psycopg
import pytest

from distress_radar.acquisition import manifest as acquisition_manifest
from distress_radar.features.asof_assembly import build_feature_store
from distress_radar.features.config import load_feature_set
from distress_radar.parsing import manifest as parsing_manifest
from distress_radar.parsing.accounting_identities import RESTATEMENT_SCHEMA
from distress_radar.parsing.legal_events import LEGAL_EVENTS_SCHEMA
from distress_radar.parsing.mapping_engine import CANONICAL_COLUMNS
from distress_radar.settings import Settings
from distress_radar.warehouse import write_dataset

pytestmark = pytest.mark.integration

KRS = "0000000001"
SHA = "a" * 64
FILED = date(2022, 6, 30)


@pytest.fixture
def conn() -> Iterator[psycopg.Connection]:
    schema = f"test_features_{uuid.uuid4().hex[:12]}"
    with psycopg.connect(Settings().postgres_conninfo, autocommit=True) as connection:
        connection.execute(f'CREATE SCHEMA "{schema}"')  # type: ignore[arg-type]
        connection.execute(f'SET search_path TO "{schema}"')  # type: ignore[arg-type]
        try:
            acquisition_manifest.ensure_schema(connection)
            parsing_manifest.ensure_schema(connection)
            _seed(connection)
            yield connection
        finally:
            connection.execute(f'DROP SCHEMA "{schema}" CASCADE')  # type: ignore[arg-type]


def _seed(conn: psycopg.Connection) -> None:
    now = datetime(2026, 9, 23, 12, 0, tzinfo=UTC)
    conn.execute(
        "INSERT INTO raw_documents VALUES (%s, 'k', 1, 'application/zip', %s, 'run-1')",
        (SHA, now),
    )
    conn.execute(
        "INSERT INTO entity_master (krs, regon, name, legal_form_code, status, pkd_codes,"
        " source_document_hash, pkd_source_document_hash, known_from, ingestion_run_id)"
        " VALUES (%s, '1', 'Test sp. z o.o.', '117', 'active', '[]', %s, %s, %s, 'run-1')",
        (KRS, SHA, SHA, date(2020, 1, 1)),
    )
    for source in ("KRS", "MSiG"):
        conn.execute(
            "INSERT INTO legal_source_fetches VALUES (%s, %s, %s, %s, %s, 'run-1', true)",
            (KRS, source, SHA, SHA, now),
        )
    conn.execute(
        "INSERT INTO filing_index (krs, document_ref, rdf_type_code, status, period_start,"
        " period_end, submission_date, is_correction, file_name, discovered_at, ingestion_run_id)"
        " VALUES (%s, 'doc-1', '18', 'NIEUSUNIETY', %s, %s, %s, false, 'sf.xml', %s, 'run-1')",
        (KRS, date(2021, 1, 1), date(2021, 12, 31), FILED, now),
    )
    conn.execute(
        "INSERT INTO parsed_documents (sha256, source_member, spec_hash, krs, document_ref,"
        " member_kind, status, first_ingestion_run_id, first_parsed_at)"
        " VALUES (%s, 'zip:sf.xml', 's', %s, 'doc-1', 'xml_statement', 'valid', 'run-1', %s)",
        (SHA, KRS, now),
    )


def _warehouse(root: Path) -> None:
    figures = {"BS.ASSETS": "1000", "BS.ASSETS.B": "600", "BS.EQUITY_LIABILITIES.B.III": "300"}
    canonical = pl.DataFrame(
        [
            {
                "krs": KRS,
                "nip": None,
                "regon": None,
                "fiscal_year": 2021,
                "period_start": date(2021, 1, 1),
                "period_end": date(2021, 12, 31),
                "line_item": code,
                "value": Decimal(value),
                "statement_type": "balance_sheet",
                "variant": "n/a",
                "column": "current_year",
                "structure_version": "full-2018-v1-2",
                "source_document_hash": SHA,
                "source_member": "zip:sf.xml",
                "source_element_path": code,
                "document_ref": "doc-1",
                "known_from": FILED,
                "ingestion_run_id": "run-1",
                "quality_grade": "pass",
            }
            for code, value in figures.items()
        ],
        schema=CANONICAL_COLUMNS,
        orient="row",
    )
    write_dataset(canonical, root, "financial_statements_canonical", "fiscal_year")
    write_dataset(
        pl.DataFrame(schema=RESTATEMENT_SCHEMA), root, "restatement_events", "fiscal_year"
    )
    events = pl.DataFrame(
        [
            {
                "krs": KRS,
                "event_type": "registered",
                "stage": "signal",
                "event_date": date(2021, 11, 3),
                "known_from": date(2021, 11, 3),
                "source": "KRS",
                "ends": [],
                "precludes_silent_exit": False,
                "event_year": 2021,
            }
        ],
        schema=LEGAL_EVENTS_SCHEMA,
        orient="row",
    )
    write_dataset(events, root, "legal_events", "event_year")


def test_build_from_the_manifest_is_complete_and_repeatable(
    conn: psycopg.Connection, tmp_path: Path
) -> None:
    _warehouse(tmp_path)
    config = load_feature_set("feature_set_v1")
    build = build_feature_store(conn, tmp_path, config, grid_start=date(2012, 1, 31))
    frame = build.frame
    # From the registration's month to the cutoff's (fetched 2026-09-23): Nov 2021 – Aug 2026.
    assert frame.height == 58
    assert frame.get_column("as_of_date").min() == date(2021, 11, 30)
    after = frame.filter(pl.col("as_of_date") == date(2022, 7, 31)).row(0, named=True)
    assert after["current_ratio"] == pytest.approx(2.0)
    assert after["current_ratio__known_from"] == FILED
    assert after["days_to_file_latest"] == (FILED - date(2021, 12, 31)).days
    assert after["curators_ever"] == 0
    before = frame.filter(pl.col("as_of_date") == date(2022, 5, 31)).row(0, named=True)
    assert before["current_ratio"] is None and before["days_to_file_latest"] is None

    first = {
        str(p): p.read_bytes() for p in sorted((tmp_path / "feature_store").rglob("*.parquet"))
    }
    build_feature_store(conn, tmp_path, config, grid_start=date(2012, 1, 31))
    again = {
        str(p): p.read_bytes() for p in sorted((tmp_path / "feature_store").rglob("*.parquet"))
    }
    assert first == again
