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

This is the recommended path for teammates running the Dastaan Docker stack on
a different Mac. It replaces the LAN-IP approach (fragile, IP changes on every
network switch) with a permanent private HTTPS URL that survives Wi-Fi changes
and works across different networks.

### 4a. One-time setup on the music Mac (already done for this repo)

Tailscale is installed on the music Mac under the personal account
`subramanya11rao@gmail.com` (not a work / webknot.in tailnet). Serve is active.
The sidecar URL for this Mac is:

```
https://subramanyas-macbook-air.tail64d7ec.ts.net
```

Steps taken (for reference / reproduction on a fresh Mac):

```bash
# Install
brew install tailscale
brew services start tailscale

# Log in with the personal Gmail that owns this music tailnet
tailscale up

# Enable Tailscale Serve — proxies the loopback sidecar over private HTTPS
# (first time: open the enable-Serve link Tailscale prints, then re-run)
tailscale serve --bg --https=443 http://127.0.0.1:8787
tailscale serve status
```

Then start the sidecar as usual (loopback only is fine — Tailscale Serve handles
the HTTPS termination):

```bash
make music-service
```

Do **not** use `tailscale funnel`, public ngrok links, router port-forwarding,
or any public load balancer for this service.

### 4b. Setup on the calling laptop (teammate's Mac)

1. Get an invite into the music Mac's personal tailnet (owner sends from
   [Users → Invite](https://login.tailscale.com/admin/users) while logged in as
   `subramanya11rao@gmail.com`). Install Tailscale and accept the invite with
   **your own** Google/GitHub — you do not use the owner's password:

   ```bash
   brew install tailscale
   brew services start tailscale
   tailscale up   # accept invite / join the personal music tailnet
   ```

2. Verify you can reach the sidecar:

   ```bash
   curl -fsS https://subramanyas-macbook-air.tail64d7ec.ts.net/healthz
   # → {"status":"ok"}
   ```

3. Add to your **`.env`** (shared Compose env):

   ```dotenv
   MUSIC_ENABLED=true
   MUSIC_SERVICE_BASE_URL=https://subramanyas-macbook-air.tail64d7ec.ts.net
   MUSIC_SERVICE_TIMEOUT_SECONDS=600
   MUSIC_SERVICE_POLL_INTERVAL_SECONDS=2
   MUSIC_DURATION_SECONDS=30
   ```

4. Create **`.env.music`** (private, gitignored — get values from the music Mac owner):

   ```dotenv
   MUSIC_SERVICE_TOKEN=<token from music Mac's .env.music>
   MUSIC_SERVICE_HMAC_SECRET=<hmac secret from music Mac's .env.music>
   MUSIC_SERVICE_SA3_PATH=/Users/subramanyarao/src/stable-audio-3/optimized/mlx/sa3
   MUSIC_SERVICE_DATA_DIR=/Users/subramanyarao/.local/share/daastaan-music
   MUSIC_SERVICE_OUTPUT_RETENTION_SECONDS=3600
   ```

5. Recreate the music worker so it picks up the new config:

   ```bash
   docker compose up -d --force-recreate worker-music
   ```

6. Confirm the container can reach the sidecar:

   ```bash
   docker compose exec worker-music curl -fsS \
     https://subramanyas-macbook-air.tail64d7ec.ts.net/healthz
   # → {"status":"ok"}
   ```

### Why not LAN IP?

The Dastaan settings validator rejects raw LAN HTTP URLs (e.g.
`http://10.x.x.x:8787`) — only `localhost`/`host.docker.internal` (HTTP) or
`*.ts.net` (HTTPS) are allowed. LAN IPs also change every time the music Mac
joins a different network. The Tailscale URL is permanent.

### Credential rotation

Pause or disable music, replace both `MUSIC_SERVICE_TOKEN` and
`MUSIC_SERVICE_HMAC_SECRET` in `.env.music` on the music host and every
authorized worker host, restart `make music-service`, and recreate only
`worker-music`. Existing stories remain playable.

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
