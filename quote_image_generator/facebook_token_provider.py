"""Optional client for loading a Facebook token from a protected worker."""

from __future__ import annotations

import datetime
import os
from collections.abc import Callable
from typing import Any
from urllib.parse import urlparse

import requests

from quote_image_generator.config import ConfigurationError, get_env_str

ENV_API_BASE_URL = "FACEBOOK_TOKEN_API_BASE_URL"
ENV_API_KEY = "FACEBOOK_TOKEN_API_KEY"
ENV_CF_ACCESS_CLIENT_ID = "CF_ACCESS_CLIENT_ID"
ENV_CF_ACCESS_CLIENT_SECRET = "CF_ACCESS_CLIENT_SECRET"

DEFAULT_REQUEST_TIMEOUT_SECONDS = 10
TOKEN_ENDPOINT_PATH = "/api/token"


class FacebookTokenProviderError(RuntimeError):
    """Raised when the optional remote token provider cannot supply a token."""


def _required_provider_env(name: str) -> str:
    value = get_env_str(name)
    if value is None:
        raise FacebookTokenProviderError(
            f"{name} is required when {ENV_API_BASE_URL} is configured."
        )
    return value


def _token_endpoint(base_url: str) -> str:
    parsed = urlparse(base_url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ConfigurationError(
            f"{ENV_API_BASE_URL} must be an absolute HTTP or HTTPS URL."
        )
    if parsed.query or parsed.fragment:
        raise ConfigurationError(
            f"{ENV_API_BASE_URL} must not contain a query string or fragment."
        )

    normalized = base_url.rstrip("/")
    if parsed.path.rstrip("/").endswith(TOKEN_ENDPOINT_PATH):
        return normalized
    return f"{normalized}{TOKEN_ENDPOINT_PATH}"


def _normalized_local_expiry(value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise FacebookTokenProviderError(
            "Facebook token provider response did not include a valid expiresAt."
        )

    raw_value = value.strip()
    iso_value = f"{raw_value[:-1]}+00:00" if raw_value.endswith("Z") else raw_value
    try:
        expiry = datetime.datetime.fromisoformat(iso_value)
    except ValueError as exc:
        raise FacebookTokenProviderError(
            "Facebook token provider returned an invalid expiresAt timestamp."
        ) from exc

    if expiry.tzinfo is None:
        now = datetime.datetime.now()
        local_expiry = expiry
    else:
        now = datetime.datetime.now(datetime.timezone.utc)
        if expiry.astimezone(datetime.timezone.utc) <= now:
            raise FacebookTokenProviderError(
                "Facebook token provider returned an expired access token."
            )
        local_expiry = expiry.astimezone().replace(tzinfo=None)

    if expiry.tzinfo is None and local_expiry <= now:
        raise FacebookTokenProviderError(
            "Facebook token provider returned an expired access token."
        )

    return local_expiry.strftime("%Y-%m-%d %H:%M:%S.%f")


def _read_response_json(response: Any) -> dict[str, object]:
    try:
        payload = response.json()
    except (TypeError, ValueError) as exc:
        raise FacebookTokenProviderError(
            "Facebook token provider returned an invalid JSON response."
        ) from exc

    if not isinstance(payload, dict):
        raise FacebookTokenProviderError(
            "Facebook token provider returned an invalid response."
        )
    return payload


def configure_facebook_token_from_provider(
    *,
    request_get: Callable[..., Any] | None = None,
) -> bool:
    """Load a Facebook access token when the optional provider is configured.

    The token is kept in process environment only. Static ``ACCESS_TOKEN`` and
    ``ACCESS_TOKEN_EXPIRY`` values remain supported when the provider URL is not
    configured.
    """

    base_url = get_env_str(ENV_API_BASE_URL)
    if base_url is None:
        return False

    api_key = _required_provider_env(ENV_API_KEY)
    client_id = _required_provider_env(ENV_CF_ACCESS_CLIENT_ID)
    client_secret = _required_provider_env(ENV_CF_ACCESS_CLIENT_SECRET)

    headers = {
        "Authorization": f"Bearer {api_key}",
        "CF-Access-Client-Id": client_id,
        "CF-Access-Client-Secret": client_secret,
    }
    get = request_get or requests.get

    try:
        response = get(
            _token_endpoint(base_url),
            headers=headers,
            timeout=DEFAULT_REQUEST_TIMEOUT_SECONDS,
            allow_redirects=False,
        )
    except requests.RequestException as exc:
        raise FacebookTokenProviderError(
            "Facebook token provider request failed."
        ) from exc

    if response.status_code != 200:
        raise FacebookTokenProviderError(
            f"Facebook token provider returned HTTP {response.status_code}."
        )

    payload = _read_response_json(response)
    access_token = payload.get("accessToken")
    if not isinstance(access_token, str) or not access_token.strip():
        raise FacebookTokenProviderError(
            "Facebook token provider response did not include an access token."
        )
    if payload.get("expired") is True:
        raise FacebookTokenProviderError(
            "Facebook token provider returned an expired access token."
        )

    normalized_expiry = _normalized_local_expiry(payload.get("expiresAt"))
    os.environ["ACCESS_TOKEN"] = access_token.strip()
    os.environ["ACCESS_TOKEN_EXPIRY"] = normalized_expiry
    return True
