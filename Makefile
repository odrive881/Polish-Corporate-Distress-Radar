UV ?= uv
export UV_LINK_MODE := copy

.PHONY: install lock lint typecheck test test-integration check dev-up dev-down api app site report \
	transform-setup transform-plan transform-run transform-test docs-check hooks models \
	label-queue label-clear label-export eval eval-accept orchestrate

install:    ## create/sync .venv exactly from uv.lock (fails if lock is stale)
	$(UV) sync --locked --extra dev

dev-up:     ## start MinIO + Postgres for local development
	docker compose up -d

dev-down:   ## stop MinIO + Postgres (data persists in named volumes)
	docker compose down

lock:       ## re-resolve after editing pyproject.toml dependencies
	$(UV) lock

# The Polish spaCy model (plan 0013; ADR 0009, third addendum): pinned by version and SHA-256,
# fetched resumably (550 MB; `uv` restarts a failed download from zero), unpacked under .cache/.
# `extraction.masking` loads it from MODEL_DIR and refuses any other version.
MODEL_NAME := pl_core_news_lg
MODEL_VERSION := 3.8.0
MODEL_SHA256 := 3bc7296cd4d67fa9ee0904b25401b0e9a9a772d5c4756edf54143a2ff4c9dcc0
MODEL_DIR := .cache/models
MODEL_WHEEL := $(MODEL_DIR)/$(MODEL_NAME)-$(MODEL_VERSION)-py3-none-any.whl
MODEL_URL := https://github.com/explosion/spacy-models/releases/download/$(MODEL_NAME)-$(MODEL_VERSION)/$(MODEL_NAME)-$(MODEL_VERSION)-py3-none-any.whl

models:     ## fetch and unpack the pinned Polish spaCy model (resumable; checks its SHA-256)
	@mkdir -p $(MODEL_DIR)
	@if ! echo "$(MODEL_SHA256)  $(MODEL_WHEEL)" | sha256sum -c --status 2>/dev/null; then \
		for attempt in 1 2 3 4 5 6 7 8 9 10; do \
			curl -sSL -C - --retry 5 --connect-timeout 30 -o $(MODEL_WHEEL) $(MODEL_URL) && break; \
			echo "download interrupted, resuming ($$attempt)"; sleep 10; \
		done; \
	fi
	@echo "$(MODEL_SHA256)  $(MODEL_WHEEL)" | sha256sum -c
	@test -f $(MODEL_DIR)/$(MODEL_NAME)/$(MODEL_NAME)-$(MODEL_VERSION)/meta.json || \
		$(UV) run --locked python -m zipfile -e $(MODEL_WHEEL) $(MODEL_DIR)
	@echo "$(MODEL_NAME) $(MODEL_VERSION) in $(MODEL_DIR)"

# The golden set of text signals (plan 0013 step E). The queue is local (LABELLING_DIR); labelling is
# `uv run marimo edit notebooks/labelling/golden_set.py`; the export writes evals/text_signals/.
label-queue:  ## build or refresh the local labelling queues, keeping labels (needs make dev-up, make models)
	$(UV) run --locked python -m distress_radar.extraction.label_queue build

label-clear: ## record what the masker still flags on labelled pages as reviewed false positives (owner's call)
	$(UV) run --locked python -m distress_radar.extraction.label_queue clear

label-export: ## write the labelled pages to evals/text_signals/, after the masking check
	$(UV) run --locked python -m distress_radar.extraction.label_queue export

# Scoring the extractors on the golden set (plan 0013 step G). `eval` calls the model for model
# signals only after EXTRACTION_API_CONFIRMED (decision 2); the gate itself runs offline in `check`.
eval:        ## run the extractors in use over the golden set, write results (needs make dev-up for model signals)
	$(UV) run --locked python -m distress_radar.extraction.eval_run run

eval-accept: ## the owner accepts the current result for a signal: make eval-accept SIGNAL=litigation BY=owner
	$(UV) run --locked python -m distress_radar.extraction.eval_run accept $(SIGNAL) --by $(BY)

docs-check: ## mechanical doc drift: missing paths, the tree, plan and ADR statuses (in `check` too)
	$(UV) run --locked pytest -q tests/test_docs.py

hooks:      ## install the pre-commit personal-data scan of staged files (once per clone)
	printf '#!/bin/sh\n# Installed by `make hooks`: refuse staged personal data (ADR 0009).\nexec $(UV) run --locked python -m distress_radar.acquisition.personal_data_scan\n' > .git/hooks/pre-commit
	chmod +x .git/hooks/pre-commit

lint:       ## ruff: lint rules, and the formatter's layout (changes nothing; `ruff format` fixes it)
	$(UV) run --locked ruff check .
	$(UV) run --locked ruff format --check .

typecheck:
	$(UV) run --locked pyright

test:
	$(UV) run --locked pytest

test-integration:   ## tests needing live Postgres/MinIO — run `make dev-up` first
	$(UV) run --locked pytest -m integration

# The scores API (plan 0016 step D): read-only over Postgres, on 127.0.0.1:8000 only, never exposed.
api:        ## serve the local scores API (needs `make dev-up` and a scoring run)
	$(UV) run --locked python -m distress_radar.api.main

# The internal explorer (plan 0016 step E): Streamlit on localhost only; every page names companies.
# The public site (plan 0016 step F), built locally from site/sources/publish/publish.duckdb, which the
# `publish` job writes. Needs Node.js 20+ in WSL (decision 10). Builds only: deploying is the owner's call.
site:       ## build the static public site under site/build (needs Node.js and a `publish` run)
	@case "$$(command -v npm)" in ""|/mnt/*) echo "Node.js is not installed in WSL (a Windows npm is not used here): see README, Setup"; exit 1;; esac
	@test -f site/sources/publish/publish.duckdb || { echo "run the publish job first"; exit 1; }
	cd site && if [ -f package-lock.json ]; then npm ci; else npm install; fi && npm run sources && npm run build

# The methodology report (plan 0016 step G): figures regenerated from the publish database, then
# rendered by the Quarto CLI installed in WSL (decision 10). Renders only: nothing is deployed.
report:     ## regenerate the report's figures and render report/methodology.qmd to HTML
	@test -f site/sources/publish/publish.duckdb || { echo "run the publish job first"; exit 1; }
	$(UV) run --locked python -m distress_radar.methodology
	@case "$$(command -v quarto)" in ""|/mnt/*) echo "the Quarto CLI is not installed in WSL: see README, Setup"; exit 1;; esac
	quarto render report/methodology.qmd --output-dir _output

# Dagster's UI and daemon (plan 0018 step B, decision 0), on localhost only. DAGSTER_HOME keeps run
# history and schedule state between starts: from the environment, else .env, else .data/dagster. It
# must be absolute (Dagster refuses a relative one). The instance settings are dagster_defs/dagster.yaml,
# copied in on every start. Schedules and sensors fire only while this runs.
DAGSTER_HOME ?= $(or $(shell sed -n 's/^DAGSTER_HOME=//p' .env 2>/dev/null),$(CURDIR)/.data/dagster)
orchestrate:  ## Dagster UI and daemon on 127.0.0.1:3000, history kept in DAGSTER_HOME (needs `make dev-up`)
	@case "$(DAGSTER_HOME)" in /*) ;; *) echo "DAGSTER_HOME must be an absolute path: $(DAGSTER_HOME)"; exit 1;; esac
	mkdir -p "$(DAGSTER_HOME)"
	cp dagster_defs/dagster.yaml "$(DAGSTER_HOME)/dagster.yaml"
	DAGSTER_HOME="$(DAGSTER_HOME)" $(UV) run --locked dagster dev -m dagster_defs.definitions -h 127.0.0.1 -p 3000

app:        ## run the local Streamlit explorer (needs `make dev-up` and a scoring run)
	$(UV) run --locked streamlit run app/Home.py --server.address 127.0.0.1 --server.headless true \
		--browser.gatherUsageStats false

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
