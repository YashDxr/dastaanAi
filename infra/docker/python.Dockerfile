# One image for the API, the agent service, and all three worker pools.
#
# They differ only in their command, and a single image means one build to wait
# on and no chance of the API and the workers drifting onto different versions of
# the shared contracts package.
#
# ffmpeg is installed here rather than pulled in as a Python wheel: only the
# assembly worker needs it, but a separate image for one apt package is not worth
# the extra build.

FROM python:3.12-slim-bookworm

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg curl \
    && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:0.11 /uv /usr/local/bin/uv

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/app/.venv

WORKDIR /app

# Dependency layer first: manifests only, so editing application code does not
# invalidate the (slow) dependency install.
COPY pyproject.toml uv.lock ./
COPY packages/contracts/pyproject.toml packages/contracts/
COPY packages/common/pyproject.toml packages/common/
COPY services/api/pyproject.toml services/api/
COPY services/agent/pyproject.toml services/agent/
COPY services/music/pyproject.toml services/music/
RUN uv sync --all-packages --frozen --no-install-workspace --no-dev

COPY packages packages
COPY services services
RUN uv sync --all-packages --frozen --no-dev

ENV PATH="/app/.venv/bin:$PATH"

# Media lives on a volume shared with the API so the local storage backend works
# across containers. Owned by the runtime user, which is not root.
RUN useradd --create-home --uid 10001 daastaan \
    && mkdir -p /data/media \
    && chown -R daastaan:daastaan /data/media /app
USER daastaan

EXPOSE 8000
CMD ["uvicorn", "daastaan_api.main:app", "--host", "0.0.0.0", "--port", "8000"]
