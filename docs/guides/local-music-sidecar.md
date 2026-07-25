# Running local background music on an Apple-Silicon Mac

This implementation creates one optional, loopable instrumental bed per story.
The existing Dastaan assembler mixes it beneath narration. If the local host is
unavailable, the story still completes with narration only.

## 1. Install the model on the Mac host

Do not install Stable Audio inside the Dastaan Docker image. It needs macOS and
Apple Metal through MLX.

```bash
git clone https://github.com/Stability-AI/stable-audio-3.git ~/src/stable-audio-3
cd ~/src/stable-audio-3/optimized/mlx
git checkout 124e8a799f57a1f665495ecb72e547d0a62867f1
./install.sh -y --download sm-music
```

Use only `sm-music` and its `same-s` decoder. Do not download `medium`, the UI,
or training assets on this 16 GB Mac.

The installer puts `uv` in `~/.local/bin` on a clean Mac. Add it to the current
shell before running the Make targets (or put the line in `~/.zshrc`):

```bash
export PATH="$HOME/.local/bin:$PATH"
uv --version
```

## 2. Configure the private host service

Generate two independent secrets and put them in the protected local
`.env.music` file (copy `.env.music.example`). They must match the dedicated
`worker-music` container, but must not appear in the shared `.env` supplied to
the API, agent, TTS, image, or assembly containers.

```bash
python3 -c 'import secrets; print(secrets.token_urlsafe(48))'
```

```dotenv
# .env (shared, non-secret)
MUSIC_ENABLED=true
MUSIC_SERVICE_BASE_URL=http://host.docker.internal:8787

# .env.music (private, gitignored)
MUSIC_SERVICE_TOKEN=<first generated secret>
MUSIC_SERVICE_HMAC_SECRET=<second generated secret>
MUSIC_SERVICE_SA3_PATH=/Users/YOU/src/stable-audio-3/optimized/mlx/sa3
MUSIC_SERVICE_DATA_DIR=/Users/YOU/.local/share/daastaan-music
```

Start the service natively, bound to loopback only:

```bash
make music-service
```

`GET /healthz` is a process check. All job, readiness, and audio endpoints need
the bearer token plus a fresh HMAC signature and nonce. The sidecar has no
Postgres, Redis, object-store, OpenAI, or Databricks credentials.

## 3. Connect Docker workers on the same Mac

The compose `worker-music` service is a one-concurrency HTTP client. It never
loads model weights. With Docker Desktop on the same Mac, leave this in the
Dastaan `.env`:

```dotenv
MUSIC_SERVICE_BASE_URL=http://host.docker.internal:8787
```

Then start the stack as usual:

```bash
make up
```

For the single host-process worker used in local development, `make worker`
loads `.env.music` only into that worker before it subscribes to the `music`
queue. `make api` and `make agent` do not load the sidecar credentials.

`worker-music` receives only the `music` Celery queue. A successful WAV becomes
a `music_bed` media asset and is fed into the final MP3 mix. If the service is
offline, its optional stage is marked unavailable and assembly continues without music.

Verify Docker Desktop can reach the loopback-bound Mac service before enabling
the feature for stories:

```bash
docker compose exec worker-music curl -fsS http://host.docker.internal:8787/healthz
```

Only `worker-music` receives `.env.music`; the API, agent service, TTS/image
workers, assembly worker, and Flower receive no music bearer/HMAC credentials.

## 4. Connect workers on another trusted laptop

Keep the host service on `127.0.0.1`. On Tailscale 1.52 or newer, use its
private HTTPS Serve reverse proxy on the music Mac, then configure remote
Dastaan worker containers with the resulting Tailnet URL:

```bash
tailscale serve --bg --https=443 http://127.0.0.1:8787
tailscale serve status
```

`--bg` is the current Serve flag for a background proxy; `tailscale serve 8787`
is the equivalent foreground form while verifying the setup. Do not use
`tailscale funnel` for this service.

```dotenv
MUSIC_SERVICE_BASE_URL=https://music-mac.your-tailnet.ts.net
```

Create a Tailnet grant/ACL that allows only Dastaan agent-host tags to reach the
music Mac. Keep the bearer/HMAC credentials enabled as defense in depth. Do not
use Tailscale Funnel, public ngrok links, public Gradio sharing, router
port-forwarding, or a public load balancer.

The agent accepts only `https://*.ts.net` for a remote sidecar URL. Keep the
sidecar loopback-bound; Tailscale Serve terminates private Tailnet HTTPS without
opening the laptop to the public internet. When rotating credentials, pause or
disable music, replace both values in `.env.music` on the music host and every
authorized worker host, restart `make music-service` and recreate only
`worker-music`, then re-enable music. Existing stories remain playable and any
in-flight optional score can be regenerated from feedback.

## 5. Test in isolation

Automated isolated coverage does not need model weights. It runs the real HTTP
sidecar lifecycle with a synthetic WAV runner:

```bash
make music-test
```

For a black-box service smoke test, start `make music-service` in one terminal,
then run this in another. It submits a fixed-seed job, polls, downloads and
validates the WAV, then deletes the sidecar's temporary output:

```bash
make music-smoke
```

For a direct model smoke test (useful before starting the service), run this
after model installation:

```bash
cd ~/src/stable-audio-3/optimized/mlx
./sa3 --prompt "warm instrumental piano and strings, gentle hopeful rise" \
  --negative-prompt "vocals, singing, speech, lyrics, humming" \
  --cfg 3.0 --dit sm-music --decoder same-s --seconds 10 --steps 8 \
  --seed 12345 --out ~/Desktop/daastaan-bgm-smoke.wav
```

Listen to the output, then verify it is 44.1 kHz stereo WAV with `ffprobe`.
The service uses the same fixed model/decoder, duration limits, and validation
before handing a result to Dastaan.

## Scope of this laptop-sized release

This is intentionally one global, loopable instrumental bed per story. It is
generated from derived genre/mood/scene metadata, mixed beneath narration, and
can be changed with feedback such as “make the background score darker.” It is
not yet the older multi-cue score plan: scene-timed stems, crossfades, and
per-cue mix-only editing need a later timeline/media-contract expansion.

Music feedback may describe mood, pace, energy, or instruments. The worker
rejects artist-imitation, song/lyrics, recognizable-melody, and vocal direction
before it reaches the local model.
