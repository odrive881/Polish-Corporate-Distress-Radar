UV ?= uv
export UV_LINK_MODE := copy

.PHONY: install lock lint typecheck test test-integration check dev-up dev-down

install:    ## create/sync .venv exactly from uv.lock (fails if lock is stale)
	$(UV) sync --locked --extra dev

dev-up:     ## start MinIO + Postgres for local development
	docker compose up -d

dev-down:   ## stop MinIO + Postgres (data persists in named volumes)
	docker compose down

lock:       ## re-resolve after editing pyproject.toml dependencies
	$(UV) lock

lint:
	$(UV) run --locked ruff check .

typecheck:
	$(UV) run --locked pyright

test:
	$(UV) run --locked pytest

test-integration:   ## tests needing live Postgres/MinIO — run `make dev-up` first
	$(UV) run --locked pytest -m integration

check: lint typecheck test   ## the single gate agents and CI run
