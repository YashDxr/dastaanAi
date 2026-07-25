.DEFAULT_GOAL := help
SHELL := /bin/bash

# The virtualenv lives outside the repo.
#
# This checkout sits in an iCloud-synced folder, and iCloud both syncs the
# thousands of files in a venv and re-applies the macOS "hidden" flag to the
# editable-install .pth files. Python's site module skips hidden .pth files, so
# workspace packages intermittently vanish with a confusing ModuleNotFoundError.
# Keeping the environment out of the synced tree removes the problem entirely.
#
# Override with: make UV_PROJECT_ENVIRONMENT=/some/other/path
UV_PROJECT_ENVIRONMENT ?= $(HOME)/.venvs/daastaan
export UV_PROJECT_ENVIRONMENT

# Local runs need localhost instead of the Compose service hostnames.
LOCAL_ENV := DATABASE_URL=postgresql+psycopg://daastaan:daastaan@localhost:5432/daastaan \
             REDIS_URL=redis://localhost:6379/0 \
             LOCAL_MEDIA_DIR=./.media

.PHONY: help
help: ## Show this help
	@grep -hE '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2}'

# --- setup -----------------------------------------------------------------

.PHONY: setup
setup: sync node-setup ## First-time setup: Python deps, JS deps, .env
	@test -f .env || (cp .env.example .env && echo "created .env - add your OPENAI_API_KEY")

.PHONY: sync
sync: ## Install/refresh the Python workspace
	uv sync --all-packages
	@$(MAKE) --no-print-directory fix-pth

# Safety net for the hidden-.pth problem described at the top of this file.
# Should be unnecessary now the environment lives outside the synced folder, but
# it is cheap and saves a confusing debugging session if that changes. No-op on
# Linux, where chflags does not exist.
.PHONY: fix-pth
fix-pth:
	@if [ "$$(uname)" = "Darwin" ] && [ -d "$(UV_PROJECT_ENVIRONMENT)/lib" ]; then \
		find "$(UV_PROJECT_ENVIRONMENT)/lib" -name '*.pth' -exec chflags nohidden {} + \
			2>/dev/null || true; \
	fi

.PHONY: node-setup
node-setup: ## Install JS workspace dependencies
	npm install

# --- docker ----------------------------------------------------------------

.PHONY: up
up: ## Start the full backend stack
	docker compose up -d --build

.PHONY: infra
infra: ## Start only Postgres and Redis (for running services on the host)
	docker compose up -d postgres redis

.PHONY: down
down: ## Stop the stack
	docker compose down

.PHONY: nuke
nuke: ## Stop the stack and delete all data volumes
	docker compose down -v

.PHONY: logs
logs: ## Tail logs from every service
	docker compose logs -f --tail=100

.PHONY: ps
ps: ## Show container status
	docker compose ps

# --- running on the host ---------------------------------------------------

.PHONY: api
api: fix-pth ## Run the API with reload (needs: make infra)
	$(LOCAL_ENV) uv run --no-sync uvicorn daastaan_api.main:app --reload --port 8000

.PHONY: agent
agent: fix-pth ## Run the agent service with reload (needs: make infra)
	$(LOCAL_ENV) uv run --no-sync uvicorn daastaan_agent.main:app --reload --port 8100

.PHONY: worker
worker: fix-pth ## Run one worker across all queues (needs: make infra)
	$(LOCAL_ENV) uv run --no-sync celery -A daastaan_agent.worker worker \
		-Q agents,media,assembly -c 4 --loglevel info

.PHONY: web
web: ## Run the listener app on :5173
	npm run dev:web

.PHONY: admin
admin: ## Run the admin panel on :5174
	npm run dev:admin

# --- codegen and quality ---------------------------------------------------

.PHONY: types
types: ## Regenerate frontend types from the running API's OpenAPI schema
	npm run gen:types

.PHONY: fmt
fmt: fix-pth ## Format and autofix Python
	uv run --no-sync ruff format .
	uv run --no-sync ruff check --fix .

.PHONY: lint
lint: fix-pth ## Lint Python and JS
	uv run --no-sync ruff check .
	uv run --no-sync mypy packages services || true
	npm run lint

.PHONY: test
test: fix-pth ## Run the Python test suite
	uv run --no-sync pytest -q

.PHONY: check
check: fix-pth ## What CI would run
	uv run --no-sync ruff check .
	uv run --no-sync pytest -q
	npm run build
