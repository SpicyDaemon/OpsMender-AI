"""Microsoft Bot Framework JWT verification (Sprint 37 step 4).

Teams sends adaptive-card ``Action.Submit`` payloads to the bot's
messaging endpoint as an Activity envelope wrapped in an HTTPS POST.
The request carries a JWT in the ``Authorization`` header that Microsoft
signs with a key from
``https://login.botframework.com/v1/.well-known/keys``.

This module exposes one entry point :func:`verify_bot_framework_token`
that:

1. Fetches and caches the public JWKS.
2. Decodes the bearer token with ``python-jose``.
3. Validates ``aud`` against the configured bot app id, ``iss`` against
   the documented Bot Framework issuer, and the standard expiry/nbf
   claims.

Tests inject ``http_client_factory`` returning an ``httpx.AsyncClient``
backed by ``httpx.MockTransport``.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any, Callable

import httpx
from jose import jwt as jose_jwt
from jose.exceptions import JWTError


logger = logging.getLogger(__name__)

BOT_FRAMEWORK_OPENID_CONFIG_URL = (
    "https://login.botframework.com/v1/.well-known/openidconfiguration"
)
BOT_FRAMEWORK_ISSUER = "https://api.botframework.com"
JWKS_TTL_SECONDS = 24 * 3600

HttpClientFactory = Callable[[], httpx.AsyncClient]


def _default_http_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=10.0)


@dataclass
class _CachedJWKS:
    expires_at: float
    keys: dict[str, Any]


_jwks_cache: dict[str, _CachedJWKS] = {}


def reset_caches() -> None:
    """Clear the JWKS cache. Test-only helper."""

    _jwks_cache.clear()


class BotFrameworkAuthError(RuntimeError):
    """Raised when JWT validation fails."""


async def _fetch_jwks(
    *,
    http_client_factory: HttpClientFactory | None = None,
    openid_config_url: str = BOT_FRAMEWORK_OPENID_CONFIG_URL,
) -> dict[str, Any]:
    cached = _jwks_cache.get(openid_config_url)
    now = time.time()
    if cached is not None and cached.expires_at > now:
        return cached.keys

    factory = http_client_factory or _default_http_client
    try:
        async with factory() as client:
            cfg_resp = await client.get(openid_config_url)
            if cfg_resp.status_code != 200:
                raise BotFrameworkAuthError(
                    f"openid config http {cfg_resp.status_code}"
                )
            try:
                cfg = cfg_resp.json()
            except ValueError as exc:
                raise BotFrameworkAuthError("openid config not JSON") from exc
            jwks_uri = cfg.get("jwks_uri")
            if not jwks_uri:
                raise BotFrameworkAuthError("openid config missing jwks_uri")
            jwks_resp = await client.get(jwks_uri)
            if jwks_resp.status_code != 200:
                raise BotFrameworkAuthError(f"jwks http {jwks_resp.status_code}")
            try:
                keys = jwks_resp.json()
            except ValueError as exc:
                raise BotFrameworkAuthError("jwks not JSON") from exc
    except httpx.HTTPError as exc:
        raise BotFrameworkAuthError(f"network: {exc}") from exc

    _jwks_cache[openid_config_url] = _CachedJWKS(
        expires_at=now + JWKS_TTL_SECONDS, keys=keys
    )
    return keys


def _extract_bearer(authorization: str | None) -> str:
    if not authorization:
        raise BotFrameworkAuthError("missing Authorization header")
    parts = authorization.split(None, 1)
    if len(parts) != 2 or parts[0].lower() != "bearer" or not parts[1].strip():
        raise BotFrameworkAuthError("malformed Authorization header")
    return parts[1].strip()


async def verify_bot_framework_token(
    *,
    authorization: str | None,
    expected_audience: str,
    http_client_factory: HttpClientFactory | None = None,
    issuer: str = BOT_FRAMEWORK_ISSUER,
    openid_config_url: str = BOT_FRAMEWORK_OPENID_CONFIG_URL,
) -> dict[str, Any]:
    """Verify a Bot Framework bearer JWT and return its claims.

    ``expected_audience`` must match the bot's app id (the
    ``bot_app_id`` field on the Teams connector). Raises
    :class:`BotFrameworkAuthError` on any failure — including missing
    header, malformed token, unknown signing key, audience/issuer
    mismatch, or expiry.
    """

    token = _extract_bearer(authorization)

    jwks = await _fetch_jwks(
        http_client_factory=http_client_factory,
        openid_config_url=openid_config_url,
    )
    try:
        claims = jose_jwt.decode(
            token,
            jwks,
            audience=expected_audience,
            issuer=issuer,
            options={"verify_at_hash": False},
        )
    except JWTError as exc:
        raise BotFrameworkAuthError(f"jwt: {exc}") from exc
    return claims
