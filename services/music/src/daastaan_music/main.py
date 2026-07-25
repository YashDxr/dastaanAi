"""Authenticated asynchronous HTTP wrapper around the native Stable Audio CLI."""

from __future__ import annotations

import asyncio
import fcntl
import hashlib
import hmac
import logging
import secrets
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, TextIO

from fastapi import FastAPI, Header, HTTPException, Request, Response, status
from fastapi.responses import FileResponse

from .models import MusicJobAccepted, MusicJobRequest, MusicJobStatus, StoredMusicJob
from .runner import MlxCliRunner, MockToneRunner, MusicRunner, validate_wav_file
from .settings import MusicServiceSettings, get_music_settings
from .store import IdempotencyConflict, MusicJobStore

log = logging.getLogger(__name__)


class QueueFullError(RuntimeError):
    pass


class RequestAuthenticator:
    """Bearer + body-HMAC authentication with a small replay window."""

    def __init__(self, settings: MusicServiceSettings) -> None:
        self.token = settings.service_token
        self.secret = (
            settings.service_hmac_secret.encode() if settings.service_hmac_secret else None
        )
        self.max_age_seconds = settings.signature_max_age_seconds
        self._seen: dict[str, float] = {}

    @property
    def configured(self) -> bool:
        return bool(self.token and self.secret and len(self.secret) >= 32)

    async def verify(self, request: Request) -> bytes:
        if not self.configured:
            raise HTTPException(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                "music service authentication is not configured",
            )
        authorization = request.headers.get("Authorization", "")
        if not hmac.compare_digest(authorization, f"Bearer {self.token}"):
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid music service credentials")

        timestamp_header = request.headers.get("X-Daastaan-Timestamp")
        nonce = request.headers.get("X-Daastaan-Nonce")
        signature = request.headers.get("X-Daastaan-Signature")
        if not timestamp_header or not nonce or not signature:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "missing request signature")
        if len(nonce) < 16 or len(nonce) > 128:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid request nonce")
        try:
            timestamp = int(timestamp_header)
        except ValueError as exc:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid request timestamp") from exc
        if abs(time.time() - timestamp) > self.max_age_seconds:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "expired request signature")

        body = await request.body()
        body_hash = hashlib.sha256(body).hexdigest()
        # Idempotency controls durable job identity, so it is part of the
        # authenticated request rather than an attacker-modifiable header.
        idempotency_key = request.headers.get("Idempotency-Key", "")
        payload = (
            f"{request.method.upper()}\n{request.url.path}\n{timestamp}\n{body_hash}\n{nonce}"
            f"\n{idempotency_key}"
        ).encode()
        expected = hmac.new(self.secret or b"", payload, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(signature, expected):
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid request signature")

        now = time.time()
        self._seen = {key: expires for key, expires in self._seen.items() if expires > now}
        replay_key = f"{timestamp_header}:{nonce}:{signature}"
        if replay_key in self._seen:
            raise HTTPException(status.HTTP_409_CONFLICT, "replayed request signature")
        self._seen[replay_key] = now + self.max_age_seconds
        return body


class MusicJobService:
    """Owns one FIFO worker and a durable queue on the local Mac."""

    def __init__(
        self,
        *,
        settings: MusicServiceSettings,
        store: MusicJobStore,
        runner: MusicRunner,
    ) -> None:
        self.settings = settings
        self.store = store
        self.runner = runner
        self.queue: asyncio.Queue[str] = asyncio.Queue(maxsize=settings.max_queue)
        self._worker_task: asyncio.Task[None] | None = None
        self._cleanup_task: asyncio.Task[None] | None = None
        self._active_job_id: str | None = None
        self._stopping = False
        self._host_lock: TextIO | None = None

    async def start(self) -> None:
        self.settings.output_dir.mkdir(parents=True, exist_ok=True)
        self._stopping = False
        self._acquire_host_lock()
        self.cleanup_expired()
        for job_id in self.store.recover_incomplete():
            try:
                self.queue.put_nowait(job_id)
            except asyncio.QueueFull:
                self.store.mark_failed(job_id, "queue_full_after_restart")
        self._worker_task = asyncio.create_task(self._worker(), name="music-inference-worker")
        self._cleanup_task = asyncio.create_task(self._cleanup_loop(), name="music-output-cleanup")

    async def stop(self) -> None:
        self._stopping = True
        # Kill the active child first. `_worker()` returns the job to queued
        # state rather than marking it failed, so a later service start can run
        # it once without ever overlapping two MLX processes on this 16 GB Mac.
        self.runner.cancel()
        if self._cleanup_task:
            self._cleanup_task.cancel()
            try:
                await self._cleanup_task
            except asyncio.CancelledError:
                pass
        if self._worker_task:
            if self._active_job_id is None:
                self._worker_task.cancel()
            try:
                await self._worker_task
            except asyncio.CancelledError:
                pass
        self._release_host_lock()

    async def submit(
        self,
        *,
        request: MusicJobRequest,
        idempotency_key: str,
        request_hash: str,
    ) -> StoredMusicJob:
        seed = request.seed if request.seed is not None else secrets.randbelow(2**31)
        job, created = self.store.create_or_get(
            idempotency_key=idempotency_key,
            request_hash=request_hash,
            request=request,
            seed=seed,
        )
        if created:
            try:
                self.queue.put_nowait(job.job_id)
            except asyncio.QueueFull as exc:
                self.store.mark_failed(job.job_id, "queue_full")
                raise QueueFullError("music queue is full") from exc
        elif job.status == "queued":
            # A duplicate request after a host restart is safe; `claim()` makes
            # multiple queue entries harmless while ensuring work is not lost.
            try:
                self.queue.put_nowait(job.job_id)
            except asyncio.QueueFull:
                pass
        return job

    async def _worker(self) -> None:
        while not self._stopping:
            job_id = await self.queue.get()
            try:
                job = self.store.claim(job_id)
                if job is None:
                    continue
                if self._stopping:
                    self.store.requeue(job_id)
                    continue
                self._active_job_id = job_id
                output_path = self.settings.output_dir / f"{job.job_id}.wav"
                try:
                    await asyncio.to_thread(self.runner.generate, job, output_path)
                    _, output_hash = await asyncio.to_thread(validate_wav_file, output_path)
                    self.store.mark_succeeded(
                        job.job_id,
                        output_path=output_path,
                        output_sha256=output_hash,
                    )
                except Exception as exc:
                    output_path.unlink(missing_ok=True)
                    if self._stopping:
                        self.store.requeue(job.job_id)
                    else:
                        # Never expose an MLX stack trace through the API. The
                        # local host log retains the complete operator detail.
                        log.exception("music_job_failed", extra={"job_id": job.job_id})
                        self.store.mark_failed(job.job_id, type(exc).__name__.lower())
            finally:
                if self._active_job_id == job_id:
                    self._active_job_id = None
                self.queue.task_done()

    def ready(self) -> bool:
        return bool(
            not self._stopping
            and self._worker_task
            and not self._worker_task.done()
            and self.runner.ready()
            and self.settings.output_dir.exists()
        )

    def cleanup_expired(self, *, now: float | None = None) -> int:
        cutoff = (now if now is not None else time.time()) - self.settings.output_retention_seconds
        expired = self.store.delete_terminal_before(cutoff)
        for job in expired:
            _delete_output_if_owned(job, self.settings.output_dir)
        return len(expired)

    async def _cleanup_loop(self) -> None:
        interval = min(300, max(30, self.settings.output_retention_seconds // 6))
        while not self._stopping:
            await asyncio.sleep(interval)
            if not self._stopping:
                self.cleanup_expired()

    def _acquire_host_lock(self) -> None:
        lock_path = self.settings.data_dir / ".music-inference.lock"
        handle = lock_path.open("a+", encoding="utf-8")
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            handle.close()
            raise RuntimeError(
                "another local music sidecar already owns this data directory"
            ) from exc
        self._host_lock = handle

    def _release_host_lock(self) -> None:
        if self._host_lock is None:
            return
        try:
            fcntl.flock(self._host_lock.fileno(), fcntl.LOCK_UN)
        finally:
            self._host_lock.close()
            self._host_lock = None


def _status_view(job: StoredMusicJob) -> MusicJobStatus:
    valid_states = {"queued", "running", "succeeded", "failed", "expired"}
    state = job.status if job.status in valid_states else "failed"
    return MusicJobStatus(
        job_id=job.job_id,
        status=state,  # type: ignore[arg-type]
        seed=job.seed,
        duration_seconds=job.duration_seconds,
        error_code=job.error_code,
    )


def _completed_output_path(job: StoredMusicJob, output_root: Path) -> Path | None:
    """Resolve and confine an artifact path before returning it to a caller."""
    if not job.output_path:
        return None
    path = Path(job.output_path).resolve()
    root = output_root.resolve()
    if not path.is_relative_to(root) or not path.is_file():
        return None
    return path


def _delete_output_if_owned(job: StoredMusicJob, output_root: Path) -> None:
    path = _completed_output_path(job, output_root)
    if path is not None:
        path.unlink(missing_ok=True)


def create_app(
    *,
    settings: MusicServiceSettings | None = None,
    runner: MusicRunner | None = None,
) -> FastAPI:
    settings = settings or get_music_settings()
    store = MusicJobStore(settings.jobs_db_path)
    runner = runner or (MockToneRunner() if settings.runner == "mock" else MlxCliRunner(settings))
    service = MusicJobService(settings=settings, store=store, runner=runner)
    authenticator = RequestAuthenticator(settings)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        await service.start()
        try:
            yield
        finally:
            await service.stop()

    app = FastAPI(
        title="Dastaan local music service",
        version="1.0.0",
        docs_url=None,
        redoc_url=None,
        lifespan=lifespan,
    )
    app.state.music_service = service

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        # Deliberately data-free; expose only process liveness to a local
        # supervisor. `readyz` and all job endpoints require signed access.
        return {"status": "ok"}

    @app.get("/readyz")
    async def readyz(request: Request) -> dict[str, str]:
        await authenticator.verify(request)
        if not service.ready():
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "music model is not ready")
        return {"status": "ready"}

    @app.post(
        "/v1/music/jobs",
        response_model=MusicJobAccepted,
        status_code=status.HTTP_202_ACCEPTED,
    )
    async def create_job(
        request: Request,
        idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
    ) -> MusicJobAccepted:
        body = await authenticator.verify(request)
        if not idempotency_key or len(idempotency_key) > 256:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "valid Idempotency-Key is required")
        try:
            payload = MusicJobRequest.model_validate_json(body)
        except ValueError as exc:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                "invalid music job payload",
            ) from exc
        try:
            job = await service.submit(
                request=payload,
                idempotency_key=idempotency_key,
                request_hash=hashlib.sha256(body).hexdigest(),
            )
        except IdempotencyConflict as exc:
            raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
        except QueueFullError as exc:
            raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, str(exc)) from exc
        return MusicJobAccepted(job_id=job.job_id, status=job.status)  # type: ignore[arg-type]

    @app.get("/v1/music/jobs/{job_id}", response_model=MusicJobStatus)
    async def get_job(job_id: str, request: Request) -> MusicJobStatus:
        await authenticator.verify(request)
        job = store.get(job_id)
        if job is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "music job not found")
        return _status_view(job)

    @app.get("/v1/music/jobs/{job_id}/audio")
    async def get_audio(job_id: str, request: Request) -> Response:
        await authenticator.verify(request)
        job = store.get(job_id)
        if job is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "music job not found")
        if job.status != "succeeded":
            raise HTTPException(status.HTTP_409_CONFLICT, "music job has no completed audio")
        output_path = _completed_output_path(job, settings.output_dir)
        if output_path is None:
            raise HTTPException(status.HTTP_410_GONE, "music output has expired")
        return FileResponse(output_path, media_type="audio/wav", filename=f"{job_id}.wav")

    @app.delete("/v1/music/jobs/{job_id}", status_code=status.HTTP_204_NO_CONTENT)
    async def delete_job(job_id: str, request: Request) -> Response:
        await authenticator.verify(request)
        job = store.delete(job_id)
        if job is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "music job not found")
        _delete_output_if_owned(job, settings.output_dir)
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    return app
