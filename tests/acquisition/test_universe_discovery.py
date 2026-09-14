from datetime import UTC, datetime
from pathlib import Path

from distress_radar.acquisition.universe_discovery import load_seed, load_segment

REPO_ROOT = Path(__file__).resolve().parents[2]
SEGMENTS = REPO_ROOT / "config" / "segments"
NOW = datetime(2026, 9, 14, 12, 0, tzinfo=UTC)


def _write(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "seed.yaml"
    path.write_text(body, encoding="utf-8")
    return path


def test_seed_loading(tmp_path: Path):
    path = _write(
        tmp_path,
        """
discovery_source: manual_seed
entities:
  - {krs: "0000163893", regon: "430036025", name: "MARBUD"}
  - {krs: "0000507997"}
""",
    )

    result = load_seed(path, ingestion_run_id="run-1", discovered_at=NOW)

    assert result.quarantine == []
    assert [c.krs for c in result.candidates] == ["0000163893", "0000507997"]
    first = result.candidates[0]
    assert first.discovery_source == "manual_seed"
    assert first.regon_hint == "430036025"
    assert first.ingestion_run_id == "run-1"
    assert first.discovered_at == NOW


def test_malformed_krs_goes_to_quarantine(tmp_path: Path):
    path = _write(
        tmp_path,
        """
discovery_source: manual_seed
entities:
  - {krs: "0000163893"}
  - {krs: "163893"}
  - {krs: "00001638AB"}
  - {krs: 0000163893}
  - {krs: 0001234567}
  - {krs: 1234567890}
  - {regon: "430036025"}
""",
    )

    result = load_seed(path, ingestion_run_id="run-1", discovered_at=NOW)

    assert [c.krs for c in result.candidates] == ["0000163893"]
    reasons = [(q.entity_key, q.reason_code) for q in result.quarantine]
    assert reasons == [
        ("163893", "malformed_krs"),
        ("00001638AB", "malformed_krs"),
        # unquoted 0000163893 has digits 8/9, so YAML keeps it a string: a valid duplicate
        ("342391", "malformed_krs"),  # unquoted 0-7 digits: YAML 1.1 octal int, not re-padded
        ("1234567890", "malformed_krs"),  # unquoted: YAML int, even though 10 digits
        ("seed.yaml#6", "malformed_seed_entry"),
    ]
    assert all(q.stage == "A1" and q.ingestion_run_id == "run-1" for q in result.quarantine)
    assert all(q.source_document_hash is None for q in result.quarantine)


def test_duplicate_krs_collapses_to_one_candidate(tmp_path: Path):
    path = _write(
        tmp_path,
        """
discovery_source: manual_seed
entities:
  - {krs: "0000163893"}
  - {krs: "0000163893"}
""",
    )

    result = load_seed(path, ingestion_run_id="run-1", discovered_at=NOW)

    assert [c.krs for c in result.candidates] == ["0000163893"]
    assert result.quarantine == []


def test_committed_seed_and_segment_are_valid():
    segment = load_segment(SEGMENTS / "construction_sme_v1.yaml")
    result = load_seed(
        SEGMENTS / "construction_sme_v1_seed.yaml", ingestion_run_id="run-1", discovered_at=NOW
    )

    assert segment.pkd_section == "F"
    assert segment.pkd_match == "predominant"
    assert segment.size_classes == ["small", "medium"]
    assert segment.min_history_years == 3
    assert result.quarantine == []
    assert 15 <= len(result.candidates) <= 25
