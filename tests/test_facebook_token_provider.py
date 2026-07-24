import re
import os
from datetime import datetime, timedelta

import pytest
import requests

from quote_image_generator import facebook_token_provider


class _FakeResponse:
    def __init__(self, status_code=200, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload
        self.text = text or ""

    @property
    def ok(self):
        return self.status_code == 200

    def raise_for_status(self):
        if not self.ok:
            raise requests.HTTPError(self.text or f"HTTP {self.status_code}")

    def json(self):
        return self._payload


class _RecordingRequestGet:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def __call__(self, url, headers=None, timeout=None, **_kwargs):
        self.calls.append((url, headers, timeout, _kwargs))
        return self.response


_ENV_KEYS = (
    "FACEBOOK_TOKEN_API_BASE_URL",
    "FACEBOOK_TOKEN_API_KEY",
    "CF_ACCESS_CLIENT_ID",
    "CF_ACCESS_CLIENT_SECRET",
    "TOKEN_API_KEY",
    "ACCESS_TOKEN",
    "ACCESS_TOKEN_EXPIRY",
)


@pytest.fixture
def clean_facebook_env(monkeypatch):
    for name in _ENV_KEYS:
        monkeypatch.delenv(name, raising=False)


def _iso_like_expiry(minutes_from_now: int, *, microseconds=654321) -> str:
    now = datetime.now() + timedelta(minutes=minutes_from_now)
    return now.replace(microsecond=microseconds).strftime("%Y-%m-%d %H:%M:%S.%f")


def test_configure_facebook_token_from_provider_disabled_without_base_url(
    clean_facebook_env, monkeypatch
):
    request_get = _RecordingRequestGet(
        _FakeResponse(payload={"accessToken": "ignored"})
    )
    monkeypatch.setenv("FACEBOOK_TOKEN_API_KEY", "unused-key")

    result = facebook_token_provider.configure_facebook_token_from_provider(
        request_get=request_get
    )

    assert result is False
    assert request_get.calls == []
    assert os.getenv("ACCESS_TOKEN") is None


def test_configure_facebook_token_from_provider_rejects_legacy_api_key_name(
    clean_facebook_env, monkeypatch
):
    monkeypatch.setenv("FACEBOOK_TOKEN_API_BASE_URL", "https://provider.example")
    monkeypatch.setenv("TOKEN_API_KEY", "legacy-only-key")
    request_get = _RecordingRequestGet(_FakeResponse(payload={"ok": True}))

    with pytest.raises(
        facebook_token_provider.FacebookTokenProviderError,
        match=r"FACEBOOK_TOKEN_API_KEY is required",
    ):
        facebook_token_provider.configure_facebook_token_from_provider(
            request_get=request_get
        )

    assert request_get.calls == []


def test_configure_facebook_token_from_provider_rejects_missing_cloudflare_service_credentials(
    clean_facebook_env, monkeypatch
):
    monkeypatch.setenv("FACEBOOK_TOKEN_API_BASE_URL", "https://provider.example")
    monkeypatch.setenv("FACEBOOK_TOKEN_API_KEY", "api-key")
    request_get = _RecordingRequestGet(_FakeResponse(payload={"ok": True}))

    with pytest.raises(facebook_token_provider.FacebookTokenProviderError):
        facebook_token_provider.configure_facebook_token_from_provider(
            request_get=request_get
        )

    assert request_get.calls == []


def test_configure_facebook_token_from_provider_sets_token_and_expiry_with_service_headers(
    clean_facebook_env, monkeypatch
):
    monkeypatch.setenv("FACEBOOK_TOKEN_API_BASE_URL", "https://provider.example")
    monkeypatch.setenv("FACEBOOK_TOKEN_API_KEY", "api-key")
    monkeypatch.setenv(
        "CF_ACCESS_CLIENT_ID",
        "cloudflare-client-id",
    )
    monkeypatch.setenv(
        "CF_ACCESS_CLIENT_SECRET",
        "cloudflare-client-secret",
    )

    expires_at = _iso_like_expiry(10)
    response = _FakeResponse(
        status_code=200,
        payload={"accessToken": "fresh-token", "expiresAt": expires_at},
    )
    request_get = _RecordingRequestGet(response)

    result = facebook_token_provider.configure_facebook_token_from_provider(
        request_get=request_get
    )

    assert result is True
    assert request_get.calls == [
        (
            "https://provider.example/api/token",
            {
                "Authorization": "Bearer api-key",
                "CF-Access-Client-Id": "cloudflare-client-id",
                "CF-Access-Client-Secret": "cloudflare-client-secret",
            },
            10,
            {"allow_redirects": False},
        )
    ]
    assert facebook_token_provider.os.getenv("ACCESS_TOKEN") == "fresh-token"
    expiry = facebook_token_provider.os.getenv("ACCESS_TOKEN_EXPIRY")
    assert expiry is not None
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d{6}", expiry)
    datetime.strptime(expiry, "%Y-%m-%d %H:%M:%S.%f")


@pytest.mark.parametrize(
    "env_updates",
    [
        {},
        {"CF_ACCESS_CLIENT_ID": "client-id"},
        {"CF_ACCESS_CLIENT_SECRET": "client-secret"},
    ],
)
def test_configure_facebook_token_from_provider_requires_both_service_headers(
    clean_facebook_env, monkeypatch, env_updates
):
    monkeypatch.setenv("FACEBOOK_TOKEN_API_BASE_URL", "https://provider.example")
    monkeypatch.setenv("FACEBOOK_TOKEN_API_KEY", "api-key")
    for key, value in env_updates.items():
        monkeypatch.setenv(key, value)

    request_get = _RecordingRequestGet(_FakeResponse(payload={"ok": True}))

    with pytest.raises(facebook_token_provider.FacebookTokenProviderError):
        facebook_token_provider.configure_facebook_token_from_provider(
            request_get=request_get
        )

    assert request_get.calls == []


@pytest.mark.parametrize(
    ("expires_at", "payload_overrides"),
    [
        (_iso_like_expiry(60), {"expired": True}),
        (_iso_like_expiry(-60), {}),
        ("", {}),
        ("not-a-timestamp", {}),
    ],
)
def test_configure_facebook_token_from_provider_rejects_expired_or_malformed_payload(
    clean_facebook_env, monkeypatch, expires_at, payload_overrides
):
    monkeypatch.setenv("FACEBOOK_TOKEN_API_BASE_URL", "https://provider.example")
    monkeypatch.setenv("FACEBOOK_TOKEN_API_KEY", "api-key")
    monkeypatch.setenv("CF_ACCESS_CLIENT_ID", "cloudflare-client-id")
    monkeypatch.setenv(
        "CF_ACCESS_CLIENT_SECRET",
        "cloudflare-client-secret",
    )
    request_get = _RecordingRequestGet(
        _FakeResponse(
            status_code=200,
            payload={"accessToken": "fresh-token", "expiresAt": expires_at}
            | payload_overrides,
        )
    )

    with pytest.raises(facebook_token_provider.FacebookTokenProviderError):
        facebook_token_provider.configure_facebook_token_from_provider(
            request_get=request_get
        )


@pytest.mark.parametrize(
    "status_or_payload",
    [
        _FakeResponse(status_code=500, text="leaked-key=sk-secret"),
        _FakeResponse(
            status_code=200,
            payload={"foo": "bar"},
            text='{"error":"token-response with secret=provider-token"}',
        ),
    ],
)
def test_configure_facebook_token_from_provider_safe_failure_does_not_leak_secrets(
    clean_facebook_env, monkeypatch, status_or_payload
):
    monkeypatch.setenv("FACEBOOK_TOKEN_API_BASE_URL", "https://provider.example")
    monkeypatch.setenv("FACEBOOK_TOKEN_API_KEY", "api-key")
    monkeypatch.setenv("CF_ACCESS_CLIENT_ID", "cloudflare-client-id")
    monkeypatch.setenv(
        "CF_ACCESS_CLIENT_SECRET",
        "cloudflare-client-secret",
    )

    request_get = _RecordingRequestGet(status_or_payload)

    with pytest.raises(facebook_token_provider.FacebookTokenProviderError) as excinfo:
        facebook_token_provider.configure_facebook_token_from_provider(
            request_get=request_get
        )

    assert request_get.calls
    assert status_or_payload.text not in str(excinfo.value)
    assert "provider-token" not in str(excinfo.value)
    assert "api-key" not in str(excinfo.value)
