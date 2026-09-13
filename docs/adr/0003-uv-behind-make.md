# 0003 — Run the Makefile's targets through uv

- **Status:** accepted
- **Date:** 2026-09-13

## Context

`uv.lock` (ADR 0001) was generated but never enforced: `make install` called `pip install -e ".[dev]"`, which ignores the lockfile and can silently resolve different tool versions than another machine. Every target sourced `.venv/bin/activate`, which only works once `.venv` exists. CI called `pip`/`ruff`/`pyright`/`pytest` directly rather than the make targets, giving two independent definitions of "what the checks are" that would drift as more targets are added (leakage tests, extraction eval, mapping coverage).

`CLAUDE.md` and ADR 0002 already make `make check` the one-command gate agents run — that contract is the useful part and is kept. What needed to change was what happens *inside* the targets, not the interface itself.

## Decision

Make owns the command names; uv owns the environment.

- `make install` → `uv sync --locked --extra dev` (rebuilds `.venv` from `uv.lock`, fails if the lock is stale)
- `make lint` / `typecheck` / `test` → `uv run --locked <tool>`
- `make lock` → `uv lock`, run after editing dependencies in `pyproject.toml`
- `check: lint typecheck test` unchanged as the aggregate gate
- CI now installs uv (`astral-sh/setup-uv`) and calls `make install && make check` instead of maintaining its own pip/tool steps

`--locked` on every target means an agent cannot add a dependency to `pyproject.toml` without also running `make lock` — the checks fail closed rather than silently drifting from the lockfile.

## Consequences

- Local and CI checks can't diverge: both run the exact same make targets.
- `.venv` is now uv-managed; direct `pip install` into it will be overwritten by the next `make install`.
- `CLAUDE.md` and `README.md` were updated to describe the uv-based flow instead of activate/pip.
- As the harness grows (Phase 5 leakage test, Phase 7 extraction eval gate, etc.), add one target per gate and chain it into `check`; keep each target to one line and push logic into `src/` or pytest instead.
