"""Isolated HTTP tests for the native background-music sidecar.

The tests deliberately use the deterministic tone runner, not model weights.
They exercise the production API contract, HMAC authentication, durable
idempotency, queue worker, and WAV download without requiring an Apple GPU.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import stat
import time
from pathlib import Path

import daastaan_music.runner as runner_module
from daastaan_music.main import create_app
from daastaan_music.runner import MlxCliRunner
from daastaan_music.settings import MusicServiceSettings
from fastapi.testclient import TestClient

TOKEN = "t" * 48
SECRET = "s" * 48


def _headers(
    method: str,
    path: str,
    body: bytes = b"",
    *,
    token: str = TOKEN,
    idempotency_key: str = "",
) -> dict[str, str]:
    timestamp = str(int(time.time()))
    nonce = secrets.token_hex(16)
    body_hash = hashlib.sha256(body).hexdigest()
    payload = (
        f"{method}\n{path}\n{timestamp}\n{body_hash}\n{nonce}\n{idempotency_key}"
    ).encode()
    signature = hmac.new(SECRET.encode(), payload, hashlib.sha256).hexdigest()
    return {
        "Authorization": f"Bearer {token}",
        "X-Daastaan-Timestamp": timestamp,
        "X-Daastaan-Nonce": nonce,
        "X-Daastaan-Signature": signature,
    }


def _submit(client: TestClient, *, key: str = "story-v1:music-bed:single"):
    path = "/v1/music/jobs"
    body = json.dumps(
        {
            "prompt": "Instrumental warm piano and strings, no vocals.",
            "negative_prompt": "vocals, speech, lyrics",
            "duration_seconds": 5,
            "seed": 123,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    headers = _headers("POST", path, body, idempotency_key=key)
    headers["Content-Type"] = "application/json"
    headers["Idempotency-Key"] = key
    return client.post(path, content=body, headers=headers)


def _wait_for_terminal(client: TestClient, job_id: str) -> dict:
    path = f"/v1/music/jobs/{job_id}"
    for _ in range(80):
        response = client.get(path, headers=_headers("GET", path))
        assert response.status_code == 200
        payload = response.json()
        if payload["status"] in {"succeeded", "failed"}:
            return payload
        time.sleep(0.05)
    raise AssertionError("music job did not reach a terminal state")


def _client(tmp_path) -> TestClient:
    settings = MusicServiceSettings(
        service_token=TOKEN,
        service_hmac_secret=SECRET,
        data_dir=tmp_path / "music-sidecar",
        runner="mock",
        max_queue=3,
    )
    return TestClient(create_app(settings=settings))


def test_authenticated_job_generates_downloadable_wav(tmp_path):
    with _client(tmp_path) as client:
        accepted = _submit(client)
        assert accepted.status_code == 202
        job_id = accepted.json()["job_id"]

        terminal = _wait_for_terminal(client, job_id)
        assert terminal["status"] == "succeeded"
        assert terminal["seed"] == 123
        assert "prompt" not in terminal

        audio_path = f"/v1/music/jobs/{job_id}/audio"
        audio = client.get(audio_path, headers=_headers("GET", audio_path))
        assert audio.status_code == 200
        assert audio.headers["content-type"].startswith("audio/wav")
        assert audio.content[:4] == b"RIFF"


def test_idempotency_returns_one_job(tmp_path):
    with _client(tmp_path) as client:
        first = _submit(client, key="same-key")
        second = _submit(client, key="same-key")
        assert first.status_code == 202
        assert second.status_code == 202
        assert first.json()["job_id"] == second.json()["job_id"]


def test_requests_without_valid_credentials_are_rejected(tmp_path):
    with _client(tmp_path) as client:
        response = client.get(
            "/readyz",
            headers=_headers("GET", "/readyz", token="invalid-" + TOKEN),
        )
        assert response.status_code == 401


def test_idempotency_key_is_covered_by_request_signature(tmp_path):
    with _client(tmp_path) as client:
        response = _submit(client, key="signed-key")
        assert response.status_code == 202

        path = "/v1/music/jobs"
        body = json.dumps(
            {
                "prompt": "Instrumental warm piano and strings, no vocals.",
                "negative_prompt": "vocals, speech, lyrics",
                "duration_seconds": 5,
                "seed": 123,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        headers = _headers("POST", path, body, idempotency_key="signed-key")
        headers["Content-Type"] = "application/json"
        headers["Idempotency-Key"] = "tampered-key"
        tampered = client.post(path, content=body, headers=headers)
        assert tampered.status_code == 401


def test_terminal_outputs_expire_when_a_client_never_deletes_them(tmp_path):
    with _client(tmp_path) as client:
        accepted = _submit(client)
        job_id = accepted.json()["job_id"]
        assert _wait_for_terminal(client, job_id)["status"] == "succeeded"

        service = client.app.state.music_service
        stored = service.store.get(job_id)
        assert stored and stored.output_path
        output_path = Path(stored.output_path)
        assert output_path.is_file()

        removed = service.cleanup_expired(
            now=time.time() + service.settings.output_retention_seconds + 1
        )
        assert removed == 1
        assert service.store.get(job_id) is None
        assert not output_path.exists()


def test_missing_mlx_executable_is_not_reported_ready(tmp_path):
    settings = MusicServiceSettings(
        service_token=TOKEN,
        service_hmac_secret=SECRET,
        data_dir=tmp_path / "music-sidecar",
        sa3_path=str(tmp_path / "not-a-real-sa3"),
    )
    assert not MlxCliRunner(settings).ready()


def test_readyz_requires_pre_downloaded_small_model_weights(tmp_path, monkeypatch):
    executable = tmp_path / "sa3"
    executable.write_text("#!/bin/sh\n", encoding="utf-8")
    executable.chmod(executable.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setattr(runner_module.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(runner_module.platform, "machine", lambda: "arm64")
    settings = MusicServiceSettings(
        service_token=TOKEN,
        service_hmac_secret=SECRET,
        data_dir=tmp_path / "music-sidecar",
        sa3_path=str(executable),
    )
    runner = MlxCliRunner(settings)
    assert not runner.ready()

    weights_dir = tmp_path / "models" / "mlx"
    weights_dir.mkdir(parents=True)
    for filename in runner_module._SM_MUSIC_WEIGHT_FILES:
        (weights_dir / filename).touch()
    assert runner.ready()
