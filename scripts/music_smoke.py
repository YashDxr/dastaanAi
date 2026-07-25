#!/usr/bin/env python3
"""Black-box smoke test for a running private Dastaan music sidecar.

It intentionally uses only the Python standard library so it can be invoked
from any local Dastaan checkout without gaining access to the MLX environment.
The sidecar must already be running on loopback or a private Tailnet URL.
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import io
import json
import os
import secrets
import sys
import time
import urllib.error
import urllib.request
import wave
from pathlib import Path
from urllib.parse import urlparse


def _headers(
    secret: bytes,
    token: str,
    method: str,
    path: str,
    body: bytes,
    *,
    idempotency_key: str = "",
) -> dict[str, str]:
    timestamp = str(int(time.time()))
    nonce = secrets.token_hex(16)
    body_hash = hashlib.sha256(body).hexdigest()
    signing_input = (
        f"{method}\n{path}\n{timestamp}\n{body_hash}\n{nonce}\n{idempotency_key}"
    ).encode()
    signature = hmac.new(secret, signing_input, hashlib.sha256).hexdigest()
    return {
        "Authorization": f"Bearer {token}",
        "X-Daastaan-Timestamp": timestamp,
        "X-Daastaan-Nonce": nonce,
        "X-Daastaan-Signature": signature,
    }


def _request(
    *,
    base_url: str,
    token: str,
    secret: bytes,
    method: str,
    path: str,
    body: bytes = b"",
    extra_headers: dict[str, str] | None = None,
) -> tuple[int, bytes]:
    idempotency_key = (extra_headers or {}).get("Idempotency-Key", "")
    headers = _headers(
        secret,
        token,
        method,
        path,
        body,
        idempotency_key=idempotency_key,
    )
    if extra_headers:
        headers.update(extra_headers)
    request = urllib.request.Request(  # noqa: S310 - URL is supplied by trusted local config.
        f"{base_url}{path}",
        data=body or None,
        headers=headers,
        method=method,
    )
    try:
        # The sidecar is loopback/Tailnet-only. Never inherit a corporate proxy
        # that could receive the bearer token, HMAC, or generated audio.
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(request, timeout=30) as response:  # noqa: S310
            return response.status, response.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()


def _validate_wav(audio: bytes, expected_seconds: int) -> None:
    with wave.open(io.BytesIO(audio), "rb") as handle:
        if (handle.getnchannels(), handle.getframerate(), handle.getsampwidth()) != (2, 44_100, 2):
            raise ValueError("response is not a 44.1 kHz, stereo, 16-bit WAV")
        duration_seconds = handle.getnframes() / handle.getframerate()
    if not expected_seconds - 1 <= duration_seconds <= expected_seconds + 2:
        raise ValueError(f"unexpected WAV duration: {duration_seconds:.2f}s")


def _load_env_file(path: Path, *, allowed: set[str] | None = None) -> None:
    """Load a minimal KEY=value file without overriding exported variables."""
    if not path.is_file():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if key and (allowed is None or key in allowed) and key not in os.environ:
            os.environ[key] = value.strip().strip('"').strip("'")


def _load_local_config() -> None:
    """Read the sidecar URL and private credentials when the shell lacks them.

    Unlike Pydantic's native service settings, this standard-library smoke tool
    would otherwise make `make music-smoke` mysteriously fail after following
    the guide. Only the non-secret URL is read from shared `.env`; credentials
    come only from `.env.music`. Both support the simple KEY=value template and
    never overwrite explicitly exported values.
    """
    _load_env_file(Path(".env"), allowed={"MUSIC_SERVICE_BASE_URL"})
    _load_env_file(Path(os.environ.get("MUSIC_SMOKE_ENV_FILE", ".env.music")))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--duration", type=int, default=10, choices=range(5, 61))
    parser.add_argument(
        "--prompt",
        default="warm instrumental piano and strings, gentle hopeful rise, no vocals",
    )
    args = parser.parse_args()

    _load_local_config()
    base_url = os.environ.get("MUSIC_SERVICE_BASE_URL", "http://127.0.0.1:8787").rstrip("/")
    parsed_url = urlparse(base_url)
    if parsed_url.scheme not in {"http", "https"} or not parsed_url.netloc:
        print("MUSIC_SERVICE_BASE_URL must be an absolute HTTP(S) URL.", file=sys.stderr)
        return 2
    token = os.environ.get("MUSIC_SERVICE_TOKEN")
    secret_text = os.environ.get("MUSIC_SERVICE_HMAC_SECRET")
    if not token or not secret_text:
        print("Set MUSIC_SERVICE_TOKEN and MUSIC_SERVICE_HMAC_SECRET first.", file=sys.stderr)
        return 2

    secret = secret_text.encode()
    payload = json.dumps(
        {
            "prompt": args.prompt,
            "negative_prompt": "vocals, singing, speech, lyrics, humming",
            "duration_seconds": args.duration,
            "seed": 12345,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    key = f"music-smoke-{secrets.token_hex(8)}"
    job_id: str | None = None
    try:
        status_code, response_body = _request(
            base_url=base_url,
            token=token,
            secret=secret,
            method="POST",
            path="/v1/music/jobs",
            body=payload,
            extra_headers={
                "Content-Type": "application/json",
                "Idempotency-Key": key,
            },
        )
        if status_code != 202:
            raise RuntimeError(f"job was rejected ({status_code}): {response_body[:300]!r}")
        job_id = str(json.loads(response_body)["job_id"])
        print(f"Submitted music job {job_id}; waiting for completion…")

        deadline = time.monotonic() + 10 * 60
        while time.monotonic() < deadline:
            status_code, response_body = _request(
                base_url=base_url,
                token=token,
                secret=secret,
                method="GET",
                path=f"/v1/music/jobs/{job_id}",
            )
            if status_code != 200:
                raise RuntimeError(f"unable to read job ({status_code}): {response_body[:300]!r}")
            job = json.loads(response_body)
            if job["status"] == "succeeded":
                break
            if job["status"] in {"failed", "expired"}:
                raise RuntimeError(f"music job failed: {job.get('error_code', 'unknown_error')}")
            time.sleep(1)
        else:
            raise TimeoutError("music job did not finish within 10 minutes")

        status_code, audio = _request(
            base_url=base_url,
            token=token,
            secret=secret,
            method="GET",
            path=f"/v1/music/jobs/{job_id}/audio",
        )
        if status_code != 200:
            raise RuntimeError(f"audio download failed ({status_code}): {audio[:300]!r}")
        _validate_wav(audio, args.duration)
        print(f"PASS: downloaded and validated {len(audio):,} bytes of generated WAV audio.")
        return 0
    except (KeyError, TypeError, ValueError, RuntimeError, TimeoutError) as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1
    finally:
        if job_id:
            _request(
                base_url=base_url,
                token=token,
                secret=secret,
                method="DELETE",
                path=f"/v1/music/jobs/{job_id}",
            )


if __name__ == "__main__":
    raise SystemExit(main())
