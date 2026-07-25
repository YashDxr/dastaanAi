# Daastaan AI

Turns a raw dream, memory, or idea into a cinematic audio drama, and lets you
reshape it in plain language ("make her sound more afraid") without regenerating
the whole thing.

## Layout

```
packages/contracts    Pydantic contracts + stage registry. Pure Pydantic, no infra deps.
packages/common       Settings, database, object storage, logging, the Celery app.
packages/api-types    TypeScript types generated from the API's OpenAPI schema.
services/api          FastAPI: auth, stories, feedback, media streaming, admin.
services/agent        LangGraph stages, TTS, images, ffmpeg assembly, Celery workers.
apps/web              Listener app (Vite + React), port 5173.
apps/admin            Operator panel (Vite + React), port 5174.
infra/docker          Shared Dockerfile for the API, agent, and all workers.
infra/observability   Loki, Promtail, Grafana provisioning for request-id search.
```

Python is a `uv` workspace; JavaScript is an `npm` workspace. One Redis, one
image for every Python process. Postgres is either Compose-managed or your host
install (`POSTGRES_MODE`).

## Getting started

```bash
make setup          # Python deps, JS deps, and a .env from the template
# add your OPENAI_API_KEY to .env
make up             # Redis, API, agent, workers (+ Postgres if POSTGRES_MODE=docker)
make web            # listener app on http://localhost:5173
make admin          # operator panel on http://localhost:5174
```

The API is on http://localhost:8000, with docs at `/api/docs`.

### Postgres: Docker or your local install

Set in `.env`:

```bash
POSTGRES_MODE=docker   # Compose Postgres (default)
# or
POSTGRES_MODE=host     # your laptop Postgres — Compose will not start a DB container
```

For `host` mode, point containers at your machine:

```bash
DATABASE_URL=postgresql+psycopg://USER:PASS@host.docker.internal:5432/YOUR_DB
```

Create the database/user on the host once; the API still runs `create_all` on startup.

### Tools and debugging

```bash
make tools            # Redis Insight :5540 + Flower :5555
make observe          # Loki + Grafana :3000 (admin/admin). Prefer LOG_JSON=true
make logs-api         # also: logs-agent, logs-worker-media, logs-redis, …
```

Every HTTP response includes `X-Request-ID`. With `LOG_JSON=true` and `make observe`, open Grafana Explore and search:

```
{compose_project="daastaan"} |= "YOUR_REQUEST_ID"
```

Or use the provisioned **Daastaan request debugger** dashboard.

To iterate faster, run Redis/(optional) Postgres in Docker and the services on your host:

```bash
make infra          # Redis (+ Postgres when POSTGRES_MODE=docker)
make api            # reload-on-save
make worker         # one worker across all queues
```

`make help` lists everything.

## How a generation flows

```
POST /api/stories
  -> chain(run_stage x 7)          sequential reasoning stages, queue: agents
  -> chord(
       group(tts_line x N,         one task per line,   queue: media
             gen_image x M),       one task per scene,  queue: media
       assemble)                   ffmpeg composition,  queue: assembly
```

Tasks pass a `version_id`, not the state. Each one loads the current
`StoryState` from the database, does its work, and saves it back, which is what
makes any individual task safe to retry.

A regeneration runs the same chain built from a shorter slice of the stage
registry. "Make line 12 angrier" re-runs emotion tagging for that one line, its
TTS, and assembly - three steps, not thirty.

## Things worth knowing before you change something

- **Every paid call goes through `ModelGateway`.** That is what keeps the cost
  ledger complete and lets the admin panel switch models at runtime. Do not call
  the OpenAI SDK from a node or a task.
- **Ids are minted by `daastaan_common.ids`, never by a model.** Object keys and
  ffmpeg arguments are built from them, so model output never reaches a path or
  a command line.
- **Media generation is guarded by `dedupe_key`.** Celery runs with `acks_late`,
  so a task can be redelivered after it already succeeded; the guard is what
  stops that from being billed twice.
- **Databricks is optional everywhere.** `DB_BACKEND` and `STORAGE_BACKEND`
  switch between Lakebase/UC Volumes and local Postgres/filesystem. Nothing in
  the runtime path requires a Databricks account.
- **Regenerate the frontend types after changing a Pydantic schema:**
  `make types` with the API running.

## Known rough edges

- `npm audit` reports a js-yaml advisory from `@redocly/openapi-core`, a
  build-time dependency of the OpenAPI type generator. It pins the version
  exactly and ignores `overrides`. The generator only ever parses our own
  `openapi.json`, so it is not reachable by user input.
- **The Python virtualenv lives at `~/.venvs/daastaan`, not in the repo.** This
  checkout sits in an iCloud-synced Desktop folder, and iCloud kept re-applying
  the macOS "hidden" flag to the editable-install `.pth` files. Python's `site`
  module skips hidden `.pth` files, so workspace packages would intermittently
  fail to import with a confusing `ModuleNotFoundError`. The Makefile sets
  `UV_PROJECT_ENVIRONMENT` to keep the environment out of the synced tree, so
  use `make` targets rather than bare `uv run`, or export that variable
  yourself. Moving the repo somewhere outside iCloud would remove the need for
  this entirely, and would also stop iCloud from syncing `node_modules`.
- Prices in `services/agent/src/daastaan_agent/pricing.py` are estimates. Check
  them against current OpenAI pricing before trusting the budget dashboard.
