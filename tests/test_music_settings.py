"""Security boundaries for the optional private music worker."""

import pytest
from daastaan_common.settings import Settings
from pydantic import ValidationError


def test_music_worker_requires_long_credentials_when_enabled():
    with pytest.raises(ValidationError, match="MUSIC_SERVICE_HMAC_SECRET must be at least 32"):
        Settings(
            music_enabled=True,
            music_client_enabled=True,
            music_service_token="t" * 32,
            music_service_hmac_secret="short-secret",  # noqa: S106 - intentional invalid test input
        )


def test_music_api_process_can_orchestrate_without_receiving_credentials():
    settings = Settings(
        music_enabled=True,
        music_client_enabled=False,
        music_service_base_url="https://music-mac.example.ts.net",
    )
    assert settings.music_service_token is None


def test_music_base_url_rejects_non_private_http_target():
    with pytest.raises(ValidationError, match="HTTP only for localhost"):
        Settings(music_service_base_url="http://untrusted.example.com:8787")


def test_music_base_url_allows_private_lan_http():
    settings = Settings(music_service_base_url="http://10.205.54.73:8787")
    assert settings.music_service_base_url == "http://10.205.54.73:8787"
