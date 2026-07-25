.DEFAULT_GOAL := help
SHELL := /bin/bash

# The virtualenv lives outside the repo (iCloud-safe). Override if needed.
UV_PROJECT_ENVIRONMENT ?= $(HOME)/.venvs/daastaan
export UV_PROJECT_ENVIRONMENT

# Local runs need localhost instead of Compose service hostnames.
# DB name matches the DBeaver database on this machine (dastaanai).
LOCAL_ENV := DATABASE_URL=postgresql+psycopg://yashsingh@localhost:5432/dastaanai \
             REDIS_URL=redis://localhost:6379/0 \
             LOCAL_MEDIA_DIR=./.media \
             MUSIC_SERVICE_BASE_URL=http://127.0.0.1:8787 \
             MUSIC_CLIENT_ENABLED=true

# Read POSTGRES_MODE from .env (default docker). host = skip Compose Postgres.
POSTGRES_MODE := $(shell sed -n 's/^POSTGRES_MODE=//p' .env 2>/dev/null | tail -1)
ifeq ($(strip $(POSTGRES_MODE)),)
POSTGRES_MODE := docker
endif

# Compose profile flags derived from POSTGRES_MODE.
ifeq ($(POSTGRES_MODE),host)
COMPOSE_POSTGRES_FLAGS :=
else
COMPOSE_POSTGRES_FLAGS := --profile postgres
endif

.PHONY: help
help: ## Show this help
	@grep -hE '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-20s\033[0m %s\n", $$1, $$2}'
	@echo
	@echo "  POSTGRES_MODE=$(POSTGRES_MODE)  (from .env; docker|host)"

# --- setup -----------------------------------------------------------------

.PHONY: setup
setup: sync node-setup ## First-time setup: Python deps, JS deps, .env
	@test -f .env || (cp .env.example .env && echo "created .env - add your OPENAI_API_KEY")

.PHONY: sync
sync: ## Install/refresh the Python workspace
	uv sync --all-packages
	@$(MAKE) --no-print-directory fix-pth

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
up: ## Start backend (honours POSTGRES_MODE=docker|host)
	docker compose $(COMPOSE_POSTGRES_FLAGS) up -d --build

.PHONY: infra
infra: ## Start Redis (+ Postgres when POSTGRES_MODE=docker)
	docker compose $(COMPOSE_POSTGRES_FLAGS) up -d redis $(if $(filter docker,$(POSTGRES_MODE)),postgres,)

.PHONY: tools
tools: ## Redis Insight (:5540) + Flower (:5555)
	docker compose --profile tools up -d redis-insight flower

.PHONY: observe
observe: ## Loki + Promtail + Grafana (:3000). Prefer LOG_JSON=true
	docker compose --profile observability up -d
	@echo "Grafana: http://localhost:3000  (admin/admin)"
	@echo "Explore → Loki → {compose_project=\"daastaan\"} |= \"request_id\""

.PHONY: down
down: ## Stop the stack (all profiles)
	docker compose --profile postgres --profile tools --profile observability down

.PHONY: nuke
nuke: ## Stop the stack and delete all data volumes
	docker compose --profile postgres --profile tools --profile observability down -v

.PHONY: ps
ps: ## Show container status
	docker compose ps -a

# --- logs (one command per service) ----------------------------------------

.PHONY: logs
logs: ## Tail logs from every running service
	docker compose logs -f --tail=100

.PHONY: logs-api
logs-api: ## Tail API logs
	docker compose logs -f --tail=200 api

.PHONY: logs-agent
logs-agent: ## Tail agent service logs
	docker compose logs -f --tail=200 agent

.PHONY: logs-worker-agents
logs-worker-agents: ## Tail agents-queue worker logs
	docker compose logs -f --tail=200 worker-agents

.PHONY: logs-worker-media
logs-worker-media: ## Tail media-queue worker logs
	docker compose logs -f --tail=200 worker-media

.PHONY: logs-worker-music
logs-worker-music: ## Tail local-music queue worker logs
	docker compose logs -f --tail=200 worker-music

.PHONY: logs-worker-assembly
logs-worker-assembly: ## Tail assembly-queue worker logs
	docker compose logs -f --tail=200 worker-assembly

.PHONY: logs-postgres
logs-postgres: ## Tail Postgres logs (POSTGRES_MODE=docker only)
	docker compose logs -f --tail=200 postgres

.PHONY: logs-redis
logs-redis: ## Tail Redis logs
	docker compose logs -f --tail=200 redis

.PHONY: logs-workers
logs-workers: ## Tail all three Celery workers
	docker compose logs -f --tail=200 worker-agents worker-media worker-assembly

# --- running on the host ---------------------------------------------------

.PHONY: api
api: fix-pth ## Run the API with reload (needs: make infra)
	$(LOCAL_ENV) uv run --no-sync uvicorn daastaan_api.main:app --reload --port 8000

.PHONY: agent
agent: fix-pth ## Run the agent service with reload (needs: make infra)
	$(LOCAL_ENV) uv run --no-sync uvicorn daastaan_agent.main:app --reload --port 8100

.PHONY: worker
worker: fix-pth ## Run one worker across all queues (needs: make infra)
	@set -a; test ! -f .env.music || . ./.env.music; set +a; \
	$(LOCAL_ENV) uv run --no-sync celery -A daastaan_agent.worker worker \
		-Q agents,media,music,assembly -c 4 --loglevel info

.PHONY: music-service
music-service: ## Run the native private Stable Audio MLX sidecar on this Mac
	uv run --package daastaan-music uvicorn --factory daastaan_music.main:create_app \
		--host 127.0.0.1 --port 8787

.PHONY: music-test
music-test: ## Run isolated music-sidecar and agent-boundary tests
	uv run --package daastaan-music pytest -q tests/test_music_service.py tests/test_music_client.py tests/test_music_settings.py

.PHONY: music-smoke
music-smoke: ## Submit, download, validate, and remove one real music-service job
	uv run --no-sync python scripts/music_smoke.py

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
