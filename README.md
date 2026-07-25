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
services/music        Native macOS Stable Audio MLX sidecar (not a Docker model runtime).
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

### Inspecting the response cache

Redis Insight opens on the cache automatically: the connection is preconfigured
to `redis:6379` **db 1**, aliased `daastaan-cache`. Filter on `daastaan:cache:*`,
which splits into:

| Pattern | Holds |
| --- | --- |
| `daastaan:cache:llm:<sha>` | The structured completion itself |
| `daastaan:cache:tts:<sha>` | An object-store *pointer*, not audio bytes |
| `daastaan:cache:image:<sha>` | An object-store *pointer*, not image bytes |
| `daastaan:cache:index:<story_id>` | Set of every key that story wrote |

The cache is on db 1 specifically so Celery's `celery-task-meta-*` keys, which
live on db 0 and outnumber cache entries several times over, stay out of the
way. Every entry carries `_stage`, `_model`, `_story_id`, `_version_id` and a
short `_preview`, so a hashed key can be traced back to what produced it.

Two things that look like bugs and are not. Stories generated before the cache
existed wrote no keys and nothing backfills them, so they will not appear until
identical content is generated again. And keys are content-addressed with no
story id in them - that is what lets two users share a hit - so the per-story
index is the only way to browse by story.

To iterate faster, run Redis/(optional) Postgres in Docker and the services on your host:

```bash
make infra          # Redis (+ Postgres when POSTGRES_MODE=docker)
make api            # reload-on-save
make worker         # one worker across all queues
```

`make help` lists everything.

### Local background music (Apple Silicon)

The BGM model stays in a native macOS sidecar so its MLX runtime can access
Metal; Docker workers call it over a signed private HTTP API. Start with
`MUSIC_ENABLED=false`, then follow [the local music sidecar guide](docs/guides/local-music-sidecar.md).
The feature flag and URL live in `.env`; copy `.env.music.example` to the
gitignored `.env.music` for the credentials that Compose injects into
`worker-music` only.

## How a generation flows

```
POST /api/stories
  -> chain(run_stage x 7)          sequential reasoning stages, queue: agents
  -> chord(
       group(tts_line x N,         one task per line,   queue: media
             gen_image x M,        one task per scene,  queue: media
             gen_music x 1),       one optional bed,    queue: music
       assemble)                   ffmpeg composition,  queue: assembly
```

Tasks pass a `version_id`, not the state. Each one loads the current
`StoryState` from the database, does its work, and saves it back, which is what
makes any individual task safe to retry.

A regeneration runs the same chain built from a shorter slice of the stage
registry. "Make line 12 angrier" re-runs emotion tagging for that one line, its
TTS, and assembly - three steps, not thirty.

Finished stories also expose three **alternate ending** choices in the listener
studio. Each choice forks a normal child `StoryVersion` at the penultimate scene
and re-enters at `story_understanding`, so every later scene, line, asset, and
mix stays consistent with the new decision while the parent version remains
unchanged.

Progress reaches the browser over SSE (`GET /api/stories/{id}/events`) fed by
Redis pub/sub, with `GET /api/stories/{id}/jobs` as the polling fallback while
the stream is down. The WebSocket at `/ws/stories/{id}` is still served for
non-browser clients.

## Response cache

Every paid call is cached by a SHA-256 of its full input - model, parameters and
prompt - so identical work is never bought twice. The scope is global: keys
contain no user or story id, which is safe because a hit can only be served to a
byte-identical request. Text results sit in Redis; audio and images go to the
object store under a content-addressed key with Redis holding the pointer, so a
Redis flush costs a re-probe rather than a re-purchase.

A cache hit still writes a `cost_ledger` row, at zero cost with `cache_hit` set,
so the admin console can report what the cache saved instead of the spend simply
being lower with nothing to attribute it to.

**Regenerations always bypass the cache.** A line respeak reaches TTS with the
same text, voice and instructions as the take it is replacing, so a read-through
cache would return that exact take and the regeneration would appear to do
nothing. `CACHE_ENABLED=false` turns the whole thing off while tuning prompts.

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
- **JSONB columns use `MutableDict`.** SQLAlchemy cannot see an in-place edit to
  a plain dict, so `version.state_json["key"] = value` would be silently dropped
  on flush. `_json_column` in `models.py` wraps every JSONB column to make those
  writes stick; keep new JSONB columns going through it.
- **A new column needs an entry in `_ADDED_COLUMNS`.** There is no Alembic here.
  `create_all` adds missing tables but never missing columns, so column
  additions are replayed at startup as `ALTER TABLE ... ADD COLUMN IF NOT
  EXISTS` from that list in `db.py`.
- **A "run" in the admin console is a `StoryVersion`**, derived from `jobs` and
  `cost_ledger` in `services/api/src/daastaan_api/analytics.py`. The
  `pipeline_runs` table is legacy and nothing writes to it.

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
