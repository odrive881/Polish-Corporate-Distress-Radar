UV ?= uv
export UV_LINK_MODE := copy

.PHONY: install lock lint typecheck test check

install:    ## create/sync .venv exactly from uv.lock (fails if lock is stale)
	$(UV) sync --locked --extra dev

lock:       ## re-resolve after editing pyproject.toml dependencies
	$(UV) lock

lint:
	$(UV) run --locked ruff check .

typecheck:
	$(UV) run --locked pyright

test:
	$(UV) run --locked pytest

check: lint typecheck test   ## the single gate agents and CI run
