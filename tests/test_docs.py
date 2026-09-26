"""Mechanical doc drift, caught where it starts (`make docs-check`; part of `make check`).

The judgment half of a doc sweep (does this section still describe the design?) stays with the
close-out of each plan. This file holds the half a script can decide:
- a living doc names a repository path that does not exist;
- the authoritative tree (`DIRECTORY_STRUCTURE.md`) misses a file in a directory it enumerates;
- a plan or ADR has no status, or a plan marked complete still has open items.

Plans and ADRs are records, never rewritten, so a path they name may since have moved: only the
living docs are held to existing paths.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path, PurePosixPath

import pytest

ROOT = Path(__file__).parent.parent

# Documents that describe the project as it is now (CLAUDE.md's reading list and the README).
LIVING = [
    "CLAUDE.md",
    "README.md",
    "AGENT_SPEC.md",
    "DIRECTORY_STRUCTURE.md",
    "docs/PROJECT_OVERVIEW.md",
    "docs/TECHNICAL_ARCHITECTURE.md",
    "docs/data_inventory.md",
    "docs/glossary.md",
]
# Named by the living docs as planned or deferred, so not on disk yet. When one is written,
# `test_planned_paths_are_still_planned` asks for it to be taken off this list.
PLANNED = {
    "config/mappings/pkd_crosswalk.yaml",  # A2 crosswalk, not yet written
    "config/statutory/size_thresholds.yaml",  # deferred with size class (plan 0010 decision 4)
}
_PATH = re.compile(
    r"`((?:src|config|tests|docs|dagster_defs|transform|notebooks|evals|prompts|app|site|report)"
    r"/[A-Za-z0-9_./-]+)`"
)
# Directories whose every file the tree lists (elsewhere it names only the directory).
ENUMERATED = ("src/", "config/", "dagster_defs/", "tests/features/")
NOT_ENUMERATED = ("config/xsd/",)  # vendored XSDs: the tree names the directory and its catalog
_STATUS = re.compile(r"^## Status\b|\*\*Status:", re.MULTILINE)
_OPEN_ITEM = re.compile(r"^\s*- \[ \]", re.MULTILINE)


def _tracked() -> list[str]:
    out = subprocess.run(
        ["git", "ls-files"], cwd=ROOT, capture_output=True, text=True, check=True
    ).stdout
    return out.split()


def _referenced(doc: str) -> set[str]:
    text = (ROOT / doc).read_text(encoding="utf-8")
    return {
        m.group(1).rstrip("/.")
        for m in _PATH.finditer(text)
        if "NNNN" not in m.group(1)  # naming templates, e.g. docs/adr/NNNN-short-title.md
    }


@pytest.mark.parametrize("doc", LIVING)
def test_living_docs_name_only_paths_that_exist(doc: str) -> None:
    missing = sorted(p for p in _referenced(doc) - PLANNED if not (ROOT / p).exists())
    assert not missing, f"{doc} names paths that do not exist: {missing}"


def test_planned_paths_are_still_planned() -> None:
    written = sorted(p for p in PLANNED if (ROOT / p).exists())
    assert not written, f"now written, take off PLANNED: {written}"


def test_the_tree_lists_every_file_in_the_directories_it_enumerates() -> None:
    tree = (ROOT / "DIRECTORY_STRUCTURE.md").read_text(encoding="utf-8")
    unlisted = sorted(
        path
        for path in _tracked()
        if path.startswith(ENUMERATED)
        and not path.startswith(NOT_ENUMERATED)
        and PurePosixPath(path).name not in {"__init__.py", ".gitkeep"}
        and PurePosixPath(path).name not in tree
    )
    assert not unlisted, f"DIRECTORY_STRUCTURE.md does not list: {unlisted}"


PLANS = sorted((ROOT / "docs" / "plans").glob("[0-9][0-9][0-9][0-9]-*.md"))
ADRS = sorted((ROOT / "docs" / "adr").glob("[0-9][0-9][0-9][0-9]-*.md"))


@pytest.mark.parametrize("plan", PLANS, ids=lambda p: p.stem[:4])
def test_every_plan_states_its_status(plan: Path) -> None:
    assert _STATUS.search(plan.read_text(encoding="utf-8")), f"{plan.name} has no status line"


@pytest.mark.parametrize("plan", PLANS, ids=lambda p: p.stem[:4])
def test_a_complete_plan_has_no_open_items(plan: Path) -> None:
    text = plan.read_text(encoding="utf-8")
    status = _STATUS.search(text)
    line = text[status.start() : text.find("\n", status.start())] if status else ""
    if re.search(r"\b(complete|done)\b", line, re.IGNORECASE):
        assert not _OPEN_ITEM.search(text), f"{plan.name} is marked complete with open items"


@pytest.mark.parametrize("adr", ADRS, ids=lambda p: p.stem[:4])
def test_every_adr_states_its_status(adr: Path) -> None:
    assert re.search(r"^- \*\*Status:\*\*", adr.read_text(encoding="utf-8"), re.MULTILINE)
