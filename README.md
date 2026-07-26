# Daastaan AI

> Turn a dream, memory, document, or fragment into a performed story—then keep
> directing it until it feels right.

Daastaan AI is a version-aware storytelling studio. It turns a prompt or
uploaded source into a cast, script, narration, scene artwork, optional score,
and finished audio or video episode. Rather than treating generation as a
one-shot result, it gives creators precise controls to revise a line, character,
scene, score, or ending while preserving the work that should remain unchanged.

This README documents the current `main` branch.

## What creators can do

| Area | What is available |
| --- | --- |
| **Start anywhere** | Write a prompt or upload text, PDF, Word, and image sources. OCR/extraction returns editable text before any generation begins. |
| **Choose the output** | Create audio, video, or both. Video adds scene artwork; the creator can set a genre hint and choose from English, major European/Japanese options, and a broad set of Indian languages. |
| **Follow the work live** | The Studio reports stage status, fan-out progress, and previews as the story is understood, cast, voiced, scored, and assembled. |
| **Listen and inspect** | Play the finished episode with a synchronised, seekable transcript, scene chapters, playback-speed control, character avatars, artwork, and downloads. |
| **Direct precisely** | Ask for a different performance or outcome at the scope of one line, a character’s lines, a scene, the background score, or the complete episode. |
| **Explore revisions** | Use Story Time Machine to select a previous version and branch the future from a chosen scene without overwriting the parent story. |
| **Test the ending** | Generate alternate endings and use the Cliffhanger Optimizer to examine tension, unresolved threads, and alternative ending suggestions. |
| **Understand the draft** | Story DNA exposes arc, pacing, dialogue balance, story traits, and character balance. Writers Room adds structured critique and priority actions. Plot Hole Hunter checks continuity without changing the story. |
| **Cut a shareable video** | Trim to scene boundaries, reframe for 16:9, 9:16, 1:1, or 4:5, style captions, add a title card/watermark, mix a local track, render, download, and share a cut. |
| **Keep a working library** | Search, sort, filter, favourite, and reopen stories from the listener library. |
| **Run the platform** | The admin console covers spend, users, review queue, runs, settings, and an audit trail. |

### The generation pipeline

Every episode progresses through an explicit pipeline, which is why the UI can
show meaningful status instead of a generic loading state:

1. **Story understanding** — interpret the source and establish its premise.
2. **Character registry** — identify the cast and their roles.
3. **Dialogue split** — shape the spoken script.
4. **Emotion detection** — annotate delivery intent.
5. **Narrator persona** — establish the narrator’s performance.
6. **Voice generation** — produce per-line audio.
7. **Music generation** — optionally request a local instrumental bed.
8. **Assembly** — compose narration, artwork, captions, and score.
9. **Episode** — deliver a playable audio and/or video result.

## Why revisions are safe

Daastaan treats a revision as a new `StoryVersion`, not as an in-place edit.
Reusable assets carry forward where they still apply; the affected downstream
work is regenerated and the final mix is rebuilt. A single-line direction, for
example, can re-run emotional delivery, that line’s TTS, and assembly instead
of recreating an entire episode.

```mermaid
flowchart LR
    C["Creator"] --> W["React + Vite studio"]
    W --> A["FastAPI"]
    A --> G["LangGraph story pipeline"]
    G --> Q["Celery + Redis queues"]
    Q --> F["Media fan-out\nvoices · artwork · optional music"]
    F --> S["Object storage + Postgres\nversioned state and assets"]
    F --> X["FFmpeg assembly and video edits"]
    X --> W
    A --> E["SSE event stream"]
    E --> W
    M["Native Apple-Silicon\nStable Audio MLX sidecar"] -. optional score .-> Q
```

The design has a few important consequences:

- The original version remains playable while a child version is generated.
- Story Time Machine and alternate endings branch from a chosen point instead
  of mutating history.
- Editor cuts are separate manifests and renders; a failed export does not put
  a finished story into a failed state.
- Media work is fan-out work, while final assembly is a durable downstream job.

## Live, observable generation

The browser follows a capped Redis stream over Server-Sent Events at
`GET /api/stories/{id}/events`. `Last-Event-ID` lets the client resume after a
reconnect, while `GET /api/stories/{id}/jobs` provides a polling fallback.

| Event | Example value in the Studio |
| --- | --- |
| `stage` | A stage starts, completes, or fails. |
| `stage_progress` | Per-line voice or per-scene media fan-out count. |
| `stage_preview` | A character, scene title, or script detail as it becomes available. |
| `stage_tokens` | Progress for a stage that has no creator-facing preview yet. |
| `asset`, `music_status`, `feedback`, `complete` | New media, optional-score state, applied direction, or a ready episode. |

The event schema is a typed discriminated union shared through the API contract,
so the frontend does not have to guess at response shapes.

## Video editor and sharing

The editor rebuilds a cut from its source artwork and per-line audio rather than
trimming the final MP4. That lets a creator:

- choose `16:9`, `9:16`, `1:1`, or `4:5` framing;
- crop or blur to fill a frame;
- trim safely at line/scene boundaries;
- style timing-aware captions, title cards, and watermarks;
- control narration and score gain, ducking, fades, loops, and an uploaded
  backing track;
- use quick scene clips and render a durable export;
- download the H.264/AAC MP4 master, MOV/MKV remuxes, or a VP9 WebM; and
- mint a revocable, unindexed share link once a render exists.

See [the video-editor guide](docs/guides/video-editor.md) for the manifest,
timeline, caption, and sharing details.

## Local background music

Music is optional. On Apple Silicon, the project can call a native Stable Audio
MLX sidecar through a signed private HTTP interface. The Docker worker never
loads model weights; it requests a job, validates the returned WAV, and passes a
`music_bed` into assembly. If the sidecar is unavailable, the episode completes
with narration only.

```bash
# Copy and fill the private sidecar credentials and paths first.
cp .env.music.example .env.music

# Run the native MLX sidecar on the music host.
make music-service

# Same Mac (Docker Desktop):
# MUSIC_ENABLED=true
# MUSIC_SERVICE_BASE_URL=http://host.docker.internal:8787

# Teammate on another Mac (personal Tailscale tailnet):
# MUSIC_ENABLED=true
# MUSIC_SERVICE_BASE_URL=https://subramanyas-macbook-air.tail64d7ec.ts.net
```

Join the music host's personal Tailscale account (invite from
`subramanya11rao@gmail.com`), then follow
[the local music sidecar guide](docs/guides/local-music-sidecar.md) § 4.

> **Note:** the Scenes tab also includes an ambience-planning interface. It is
> intentionally labelled planning-only; ambient audio is not currently rendered
> by the backend.

## Architecture and repository layout

| Path | Responsibility |
| --- | --- |
| [`apps/web`](apps/web) | Creator/listener app: compose, library, studio, revisions, player, editor, and share view. Runs on port `5173`. |
| [`apps/admin`](apps/admin) | Operator console: spend, users, review queue, runs, settings, and audit. Runs on port `5174`. |
| [`services/api`](services/api) | FastAPI service for auth, stories, feedback, media, progress, exports, sharing, editor, and administration. |
| [`services/agent`](services/agent) | LangGraph stages, model gateway, prompt work, Celery tasks, assembly, and edit rendering. |
| [`services/music`](services/music) | Native macOS Stable Audio MLX sidecar—not a Docker model runtime. |
| [`packages/contracts`](packages/contracts) | Infrastructure-free Pydantic contracts, stage registry, event types, and editing manifests. |
| [`packages/common`](packages/common) | Settings, database, object storage, IDs, logging, cache, and Celery configuration. |
| [`packages/api-types`](packages/api-types) | TypeScript API types generated from the FastAPI OpenAPI schema. |
| [`infra`](infra) | Shared Python container image and observability provisioning. |
| [`docs`](docs) | Product and operational guides, specifications, handoff notes, and README assets. |

Python is managed as a `uv` workspace. JavaScript is managed as an `npm`
workspace. The standard stack uses Redis plus either Compose-managed Postgres or
an existing host Postgres installation.

## Quick start

### Prerequisites

- Python **3.12** and [`uv`](https://docs.astral.sh/uv/)
- Node.js and npm (Node 20+ is recommended)
- Docker Desktop for the standard backend stack
- An OpenAI API key for model-backed generation

### Start the stack

```bash
# Install Python and JavaScript dependencies. Creates .env from the template.
make setup

# Add OPENAI_API_KEY to .env before generating a story.

# Start Redis, API, agent, Celery workers, and Postgres (unless host Postgres is selected).
make up

# In separate terminals:
make web      # http://localhost:5173
make admin    # http://localhost:5174
```

The API is available at `http://localhost:8000`; interactive OpenAPI docs are at
`http://localhost:8000/api/docs`.

### Use Docker Postgres or a host database

```dotenv
# .env
POSTGRES_MODE=docker  # default: Compose starts Postgres

# Or use an existing local/host database and skip the Compose Postgres service.
# POSTGRES_MODE=host
# DATABASE_URL=postgresql+psycopg://USER:PASS@host.docker.internal:5432/DB_NAME
```

For a quicker host-service loop, keep infrastructure in Docker and run the
application processes locally:

```bash
make infra
make api
make agent
make worker
```

Run `make help` for every available target.

## Configuration at a glance

| Setting | Purpose |
| --- | --- |
| `OPENAI_API_KEY` | Required for model-backed story generation. |
| `POSTGRES_MODE` | `docker` starts Compose Postgres; `host` uses the configured `DATABASE_URL`. |
| `DATABASE_URL` | SQLAlchemy/Postgres connection when using host Postgres or overriding defaults. |
| `LOCAL_MEDIA_DIR` | Local filesystem location for development media storage. |
| `WEB_BASE_URL` | Base URL used when minting public share links. |
| `MUSIC_ENABLED` / `MUSIC_CLIENT_ENABLED` | Enables the optional music stage/client. |
| `MUSIC_SERVICE_BASE_URL` | Trusted URL of the native music sidecar. Secrets stay in the gitignored `.env.music`. |
| `LOG_JSON` | Produces structured request logs for Loki/Grafana exploration. |

Start from [`.env.example`](.env.example) and, when needed,
[`.env.music.example`](.env.music.example). Do not commit either populated
environment file.

## Operations and debugging

```bash
make tools       # Redis Insight :5540 and Flower :5555
make observe     # Loki + Grafana :3000 (admin/admin)
make logs-api    # or logs-agent, logs-worker-media, logs-worker-music, ...
make ps          # Compose service status
```

Every HTTP response includes `X-Request-ID`. With `LOG_JSON=true`, use Grafana
Explore to search the request ID and follow a request across API, agent, worker,
and assembly logs.

### Response cache

Paid model work is content-addressed with a SHA-256 of its full input (prompt,
model, and parameters). Text completions live in Redis; audio/image cache entries
point to object storage. Cache hits still write a zero-cost ledger entry, so the
admin console can show both spend and avoided spend.

Regeneration bypasses the cache deliberately: a request to respoke a line must
produce a new take, not retrieve its previous audio.

### Guardrails worth preserving

- Paid calls flow through `ModelGateway`, keeping cost accounting and runtime
  model controls in one place.
- IDs are minted by `daastaan_common.ids`, never by a model, before they reach
  object keys or ffmpeg arguments.
- Media generation has deduplication guards because Celery may redeliver an
  acknowledged-late task.
- New API contracts require regenerated web types: run `make types` with the
  API running.
- JSONB columns use `MutableDict`; new schema columns also need the startup
  migration entry described in `packages/common/.../db.py`.

## Quality checks

```bash
make lint       # Ruff, mypy (non-blocking), and workspace lint
make test       # Python test suite
make check      # CI-style Python checks, tests, and JavaScript build
npm run build   # Build all JavaScript workspaces
```

The project includes focused tests for story versions, regeneration scope,
progress streams, ingest/OCR, casting, alternate endings, Story DNA, plot-hole
checks, the music boundary, video editing, exports, and share-link behaviour.

## Further reading

- [Video editor guide](docs/guides/video-editor.md)
- [Local music sidecar guide](docs/guides/local-music-sidecar.md)
- [Local music service specification](docs/specs/local-stable-audio-music-service.md)
- [Merged feature handoff](docs/handoff/2026-07-26-parallel-features.md)
- [API documentation](http://localhost:8000/api/docs) when the stack is running

## Known constraints

- The optional music sidecar is designed for a trusted Apple-Silicon host; it
  is intentionally isolated from the main Docker image and general service
  credentials.
- Video editing requires scene artwork and per-line audio, because cuts are
  recomposed from source media rather than altering a burned-in final video.
- Model pricing values used by the admin budget dashboard are estimates; review
  them before using the dashboard for accounting decisions.
