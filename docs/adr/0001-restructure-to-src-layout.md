# 0001 — Restructure repository to the DIRECTORY_STRUCTURE.md layout

- **Status:** accepted
- **Date:** 2026-09-13

## Context

The repository started as a prototype with a flat `pipeline/` package, a root-level `conftest.py`, setuptools + mypy tooling, and a `plans/` folder of planning drafts. None of this matched `DIRECTORY_STRUCTURE.md`, which requires the src layout, hatch packaging, pyright strict, and a fixed set of top-level directories.

## Decision

Move to the authoritative tree in `DIRECTORY_STRUCTURE.md` §1–§2:

| From | To |
|---|---|
| `pipeline/parser.py` | `src/distress_radar/parsing/mapping_engine.py` |
| `pipeline/__init__.py` | `src/distress_radar/__init__.py` |
| `conftest.py` | `tests/conftest.py` |
| `tests/test_parser.py` | `tests/parsing/test_mapping_engine.py` |
| `tests/accounting_identities.py` | `tests/parsing/test_accounting_identities.py` |

- Tooling: hatchling build backend, pyright strict on `src/`, pytest with `--import-mode=importlib`.
- Directories from the tree are created empty (`__init__.py` for packages, `.gitkeep` otherwise). No placeholder content — thresholds, mappings, and prompts are added only with real values.
- `plans/` and `files.zip` were removed from the repo. `files.zip` held older copies of the root specs; `plans/polish-corporate-distress-radar.md` was an earlier draft of `docs/PROJECT_OVERVIEW.md`. The one durable decision in `plans/` is recorded as ADR 0002. Originals are archived outside the repo (`~/backups/LARGE_pipeline-planning-archive/`, plus a full pre-restructure tarball).

## Consequences

- The package is importable only after `make install`, so tests exercise the real install path.
- `tests/parsing/test_accounting_identities.py` is now collected; before the rename pytest silently skipped it.
- `mapping_engine.py` is still the prototype: ElementTree and `float`, four hard-coded figures. Rewriting it to lxml + `Decimal` + YAML specs is stage C2 work.
- No `uv.lock` yet — `uv` was not installed at restructure time.
