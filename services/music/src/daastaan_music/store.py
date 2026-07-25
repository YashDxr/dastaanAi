"""Durable single-host job state backed by SQLite."""

from __future__ import annotations

import sqlite3
import threading
import time
import uuid
from pathlib import Path

from .models import MusicJobRequest, StoredMusicJob


class IdempotencyConflict(ValueError):
    """A caller reused a key with a different request body."""


class MusicJobStore:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self._lock = threading.Lock()
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=15, check_same_thread=False)
        connection.row_factory = sqlite3.Row
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS music_jobs (
                    job_id TEXT PRIMARY KEY,
                    idempotency_key TEXT NOT NULL UNIQUE,
                    request_hash TEXT NOT NULL,
                    status TEXT NOT NULL,
                    prompt TEXT NOT NULL,
                    negative_prompt TEXT NOT NULL,
                    duration_seconds INTEGER NOT NULL,
                    seed INTEGER NOT NULL,
                    output_path TEXT,
                    output_sha256 TEXT,
                    error_code TEXT,
                    attempts INTEGER NOT NULL DEFAULT 0,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL
                )
                """
            )

    @staticmethod
    def _row(row: sqlite3.Row) -> StoredMusicJob:
        return StoredMusicJob(
            job_id=str(row["job_id"]),
            idempotency_key=str(row["idempotency_key"]),
            request_hash=str(row["request_hash"]),
            status=str(row["status"]),
            prompt=str(row["prompt"]),
            negative_prompt=str(row["negative_prompt"]),
            duration_seconds=int(row["duration_seconds"]),
            seed=int(row["seed"]),
            output_path=str(row["output_path"]) if row["output_path"] else None,
            output_sha256=str(row["output_sha256"]) if row["output_sha256"] else None,
            error_code=str(row["error_code"]) if row["error_code"] else None,
            attempts=int(row["attempts"]),
        )

    def create_or_get(
        self,
        *,
        idempotency_key: str,
        request_hash: str,
        request: MusicJobRequest,
        seed: int,
    ) -> tuple[StoredMusicJob, bool]:
        """Return an existing matching job or atomically create a queued one."""
        now = time.time()
        with self._lock, self._connect() as connection:
            existing = connection.execute(
                "SELECT * FROM music_jobs WHERE idempotency_key = ?",
                (idempotency_key,),
            ).fetchone()
            if existing is not None:
                job = self._row(existing)
                if job.request_hash != request_hash:
                    raise IdempotencyConflict("idempotency key belongs to a different request")
                return job, False

            job_id = uuid.uuid4().hex
            connection.execute(
                """
                INSERT INTO music_jobs (
                    job_id, idempotency_key, request_hash, status, prompt,
                    negative_prompt, duration_seconds, seed, created_at, updated_at
                ) VALUES (?, ?, ?, 'queued', ?, ?, ?, ?, ?, ?)
                """,
                (
                    job_id,
                    idempotency_key,
                    request_hash,
                    request.prompt,
                    request.negative_prompt,
                    request.duration_seconds,
                    seed,
                    now,
                    now,
                ),
            )
            row = connection.execute(
                "SELECT * FROM music_jobs WHERE job_id = ?", (job_id,)
            ).fetchone()
            if row is None:
                raise RuntimeError("new music job could not be read")
            return self._row(row), True

    def get(self, job_id: str) -> StoredMusicJob | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM music_jobs WHERE job_id = ?", (job_id,)
            ).fetchone()
        return self._row(row) if row is not None else None

    def claim(self, job_id: str) -> StoredMusicJob | None:
        """Move exactly one queued job into running state."""
        with self._lock, self._connect() as connection:
            now = time.time()
            changed = connection.execute(
                """
                UPDATE music_jobs
                SET status = 'running', attempts = attempts + 1, updated_at = ?
                WHERE job_id = ? AND status = 'queued'
                """,
                (now, job_id),
            ).rowcount
            if not changed:
                return None
            row = connection.execute(
                "SELECT * FROM music_jobs WHERE job_id = ?", (job_id,)
            ).fetchone()
            if row is None:
                raise RuntimeError("claimed music job could not be read")
            return self._row(row)

    def mark_succeeded(self, job_id: str, *, output_path: Path, output_sha256: str) -> None:
        with self._lock, self._connect() as connection:
            connection.execute(
                """
                UPDATE music_jobs
                SET status = 'succeeded', output_path = ?, output_sha256 = ?,
                    error_code = NULL, updated_at = ?
                WHERE job_id = ?
                """,
                (str(output_path), output_sha256, time.time(), job_id),
            )

    def mark_failed(self, job_id: str, error_code: str) -> None:
        with self._lock, self._connect() as connection:
            connection.execute(
                """
                UPDATE music_jobs
                SET status = 'failed', error_code = ?, updated_at = ?
                WHERE job_id = ?
                """,
                (error_code[:120], time.time(), job_id),
            )

    def requeue(self, job_id: str) -> None:
        """Return an interrupted in-flight job to the durable FIFO queue."""
        with self._lock, self._connect() as connection:
            connection.execute(
                """
                UPDATE music_jobs
                SET status = 'queued', error_code = NULL, updated_at = ?
                WHERE job_id = ? AND status = 'running'
                """,
                (time.time(), job_id),
            )

    def recover_incomplete(self) -> list[str]:
        """Queue jobs interrupted by a service restart for one clean retry."""
        with self._lock, self._connect() as connection:
            connection.execute(
                "UPDATE music_jobs SET status = 'queued', updated_at = ? WHERE status = 'running'",
                (time.time(),),
            )
            rows = connection.execute(
                "SELECT job_id FROM music_jobs WHERE status = 'queued' ORDER BY created_at"
            ).fetchall()
        return [str(row["job_id"]) for row in rows]

    def delete_terminal_before(self, cutoff: float) -> list[StoredMusicJob]:
        """Delete stale terminal rows and return their owned output paths.

        A successful client normally deletes its job after uploading to shared
        storage. This bounded-retention fallback handles client loss, invalid
        output, and an interrupted worker without accumulating local WAVs.
        """
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM music_jobs
                WHERE status IN ('succeeded', 'failed', 'expired') AND updated_at < ?
                """,
                (cutoff,),
            ).fetchall()
            jobs = [self._row(row) for row in rows]
            if jobs:
                connection.executemany(
                    "DELETE FROM music_jobs WHERE job_id = ?",
                    [(job.job_id,) for job in jobs],
                )
        return jobs

    def delete(self, job_id: str) -> StoredMusicJob | None:
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM music_jobs WHERE job_id = ?", (job_id,)
            ).fetchone()
            if row is None:
                return None
            job = self._row(row)
            connection.execute("DELETE FROM music_jobs WHERE job_id = ?", (job_id,))
        return job
