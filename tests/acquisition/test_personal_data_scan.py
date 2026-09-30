"""The pre-commit personal-data scan, against a throwaway git repository."""

import io
import shutil
import subprocess
import zipfile
from pathlib import Path

import pytest

from distress_radar.acquisition.personal_data_scan import main, scan

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="needs git")

SIGNED = (
    b"<?xml version='1.0'?><Sprawozdanie><Bilans>1.00</Bilans>"
    b"<ds:Signature xmlns:ds='http://www.w3.org/2000/09/xmldsig#'>"
    b"<DaneZPOsobyFizycznej><PESEL>00000000000</PESEL></DaneZPOsobyFizycznej></ds:Signature>"
    b"</Sprawozdanie>"
)
CLEAN = b"<?xml version='1.0'?><Sprawozdanie><Bilans>1.00</Bilans></Sprawozdanie>"


def _zip(name: str, data: bytes) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(name, data)
    return buffer.getvalue()


def _repo(tmp_path: Path) -> Path:
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    return tmp_path


def _stage(repo: Path, path: str, data: bytes) -> None:
    (repo / path).parent.mkdir(parents=True, exist_ok=True)
    (repo / path).write_bytes(data)
    subprocess.run(["git", "add", path], cwd=repo, check=True)


def test_scan_refuses_recordings_and_marked_data_and_passes_the_rest() -> None:
    files = {
        "a.har": b"{}",
        "signed.xml": SIGNED,
        "named.zip": _zip("SF Jan Testowy.xml", CLEAN),
        "clean.xml": CLEAN,
        "notes.md": b"PESEL is a word docs may use",
    }
    found = scan(files, files.__getitem__)
    assert set(found) == {"a.har", "signed.xml", "named.zip"}
    assert not any("Jan" in m for markers in found.values() for m in markers)


def test_the_hook_reads_what_is_staged_not_the_working_tree(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    _stage(repo, "tests/fixtures/sf.xml", SIGNED)
    (repo / "tests/fixtures/sf.xml").write_bytes(CLEAN)  # fixed on disk, not re-staged
    assert main([], cwd=repo) == 1
    subprocess.run(["git", "add", "tests/fixtures/sf.xml"], cwd=repo, check=True)
    assert main([], cwd=repo) == 0


def test_explicit_paths_are_scanned_from_disk(tmp_path: Path) -> None:
    (tmp_path / "x.xml").write_bytes(SIGNED)
    assert main(["x.xml"], cwd=tmp_path) == 1


# --- eval files: the masking check (plan 0013 step E) ---------------------------------------------


class _Masker:
    """Finds one invented name, so the scan is tested without loading the model."""

    def __call__(self, text: str) -> object:
        ents = []
        if (start := text.find("Jan Testowy")) >= 0:
            ents.append(
                type("E", (), {"start_char": start, "end_char": start + 11, "label_": "persName"})()
            )
        return type("Doc", (), {"ents": ents})()


def test_eval_files_get_the_masking_check_and_nothing_else_loads_the_model() -> None:
    files = {
        "evals/text_signals/litigation.jsonl": b'{"text": "Pozew podpisa\\u0142 Jan Testowy."}\n',
        "evals/text_signals/pages.jsonl": b'{"text": "[osoba] podpisa\\u0142 pozew."}\n',
        "evals/text_signals/labelling_guide.md": b"Jan Testowy",
    }
    found = scan(files, files.__getitem__, lambda: _Masker())
    assert list(found) == ["evals/text_signals/litigation.jsonl"]
    assert not any("Testowy" in m for m in found["evals/text_signals/litigation.jsonl"])

    def _no_model() -> object:
        raise AssertionError("the model is loaded only for eval files")

    assert scan({"clean.xml": CLEAN}, {"clean.xml": CLEAN}.__getitem__, _no_model) == {}


def test_an_eval_file_without_the_model_is_refused() -> None:
    files = {"evals/text_signals/pages.jsonl": b'{"text": "x"}\n'}

    def _missing() -> object:
        raise FileNotFoundError("pl_core_news_lg 3.8.0 is not at .cache/models: run `make models`")

    [finding] = scan(files, files.__getitem__, _missing)["evals/text_signals/pages.jsonl"]
    assert finding.startswith("masking check cannot run")
