# Dastaan local music service

This is a native macOS sidecar for the Stable Audio 3 MLX runtime. It is not a
Docker service: Apple Metal is available only from the host macOS process.

From the Stable Audio checkout, install only the small music model:

```bash
cd /path/to/stable-audio-3/optimized/mlx
./install.sh -y --download sm-music
```

Set local-only service configuration in the protected `.env.music` file (copy
`.env.music.example`) or a service-manager environment:

```bash
MUSIC_SERVICE_TOKEN=<48+ random characters>
MUSIC_SERVICE_HMAC_SECRET=<48+ random characters>
MUSIC_SERVICE_SA3_PATH=/path/to/stable-audio-3/optimized/mlx/sa3
MUSIC_SERVICE_DATA_DIR=$HOME/.local/share/daastaan-music
```

Then start the host process from the repository root:

```bash
uv run --package daastaan-music uvicorn --factory daastaan_music.main:create_app \
  --host 127.0.0.1 --port 8787
```

Docker workers on the same Mac use `http://host.docker.internal:8787`. For a
different laptop, keep the service loopback-bound and expose it only through a
Tailscale Serve HTTPS endpoint; do not use a public tunnel or port-forward.

Use `MUSIC_SERVICE_RUNNER=mock` only for the isolated automated test path. It
creates a quiet synthetic WAV, not generated music.
