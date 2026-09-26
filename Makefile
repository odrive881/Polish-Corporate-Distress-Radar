UV ?= uv
export UV_LINK_MODE := copy

.PHONY: install lock lint typecheck test test-integration check dev-up dev-down \
	transform-setup transform-plan transform-run transform-test docs-check hooks

install:    ## create/sync .venv exactly from uv.lock (fails if lock is stale)
	$(UV) sync --locked --extra dev

dev-up:     ## start MinIO + Postgres for local development
	docker compose up -d

dev-down:   ## stop MinIO + Postgres (data persists in named volumes)
	docker compose down

lock:       ## re-resolve after editing pyproject.toml dependencies
	$(UV) lock

docs-check: ## mechanical doc drift: missing paths, the tree, plan and ADR statuses (in `check` too)
	$(UV) run --locked pytest -q tests/test_docs.py

hooks:      ## install the pre-commit personal-data scan of staged files (once per clone)
	printf '#!/bin/sh\n# Installed by `make hooks`: refuse staged personal data (ADR 0009).\nexec $(UV) run --locked python -m distress_radar.acquisition.personal_data_scan\n' > .git/hooks/pre-commit
	chmod +x .git/hooks/pre-commit

lint:
	$(UV) run --locked ruff check .

typecheck:
	$(UV) run --locked pyright

test:
	$(UV) run --locked pytest

test-integration:   ## tests needing live Postgres/MinIO — run `make dev-up` first
	$(UV) run --locked pytest -m integration

# SQLMesh (plan 0007, ADR 0010). plan/run use the `local` gateway: DuckDB under
# WAREHOUSE_DIR, state in Postgres (make dev-up). transform-test uses the `test`
# gateway, in-memory only, so `make check` needs no running services.
SQLMESH = $(UV) run --locked sqlmesh -p transform --log-file-dir .cache/sqlmesh/logs

transform-setup:   ## install DuckDB's postgres extension once (runs never download it)
	$(UV) run --locked python -c "import duckdb; duckdb.sql('INSTALL postgres')"

transform-plan:    ## plan and apply the SQLMesh models to prod (needs make dev-up)
	$(SQLMESH) plan --auto-apply --no-prompts

transform-run:     ## run the SQLMesh models (needs make dev-up)
	$(SQLMESH) run

transform-test:    ## SQLMesh unit tests, no services needed
	$(SQLMESH) --gateway test test

check: lint typecheck test transform-test   ## the single gate agents and CI run
