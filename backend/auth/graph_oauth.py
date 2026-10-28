"""Microsoft Graph app-only OAuth helper (Sprint 37 step 1).

The Microsoft Teams (Workflows) channel uses it for its optional Microsoft
Entra sign-in (M1-100): a workflow whose trigger allows only people in the
tenant takes a client-credentials token for the Power Automate audience. The
old Teams adapter makes no Graph call (M1-96); the Graph scope is kept for the
Teams bot. Authentication is **app-only** (client credentials), not
user-delegated OAuth: the operator registers an app in Microsoft Entra and
pastes its ``tenant_id`` / ``client_id`` / ``client_secret``. There is no
browser redirect - token acquisition is a server-side POST to the token
endpoint of the commercial cloud only.

This module exposes one entry point - :func:`acquire_app_only_token` -
plus a small in-process cache keyed by tenant, client, scope and secret digest
so we don't hammer Microsoft for every request, and a token for one
audience is never handed to another. Tokens are valid for ~1 hour; the
cache evicts them 60 seconds before expiry to give callers a safety
margin. :func:`forget_token` drops one entry after the audience refuses it.

Tests inject ``http_client_factory`` returning an ``httpx.AsyncClient``
backed by ``httpx.MockTransport``.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import time
from dataclasses import dataclass
from typing import Callable

import httpx


logger = logging.getLogger(__name__)

GRAPH_DEFAULT_SCOPE = "https://graph.microsoft.com/.default"
TOKEN_ENDPOINT_TEMPLATE = (
    "https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/token"
)
EXPIRY_SAFETY_MARGIN_SECONDS = 60

HttpClientFactory = Callable[[], httpx.AsyncClient]


def _default_http_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=10.0)


@dataclass(frozen=True)
class GraphToken:
    access_token: str
    expires_at: float  # epoch seconds
    token_type: str = "Bearer"

    def is_expired(self, *, now: float | None = None) -> bool:
        return (now or time.time()) >= (self.expires_at - EXPIRY_SAFETY_MARGIN_SECONDS)


class GraphOAuthError(RuntimeError):
    """Raised when the token endpoint returns an error response.

    ``status_code`` is the token endpoint's HTTP status, or None when no
    answer came (a network error is then the ``__cause__``)."""

    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


_token_cache: dict[tuple[str, str, str, str], GraphToken] = {}
_cache_lock = asyncio.Lock()


def reset_token_cache() -> None:
    """Drop every cached token. Test-only helper; production code never
    calls this - the cache is process-local and self-evicting."""

    _token_cache.clear()


def forget_token(tenant_id: str, client_id: str, scope: str) -> None:
    """Drop this app's cached tokens for an audience, including rotated secrets."""
    for key in list(_token_cache):
        if key[:3] == (tenant_id, client_id, scope):
            _token_cache.pop(key, None)


async def acquire_app_only_token(
    *,
    tenant_id: str,
    client_id: str,
    client_secret: str,
    scope: str = GRAPH_DEFAULT_SCOPE,
    force_refresh: bool = False,
    http_client_factory: HttpClientFactory | None = None,
) -> GraphToken:
    """Return a valid Microsoft Graph access token for the given Azure AD
    app. Reuses an in-memory cached token when one exists and is still
    valid; otherwise calls the token endpoint with
    ``grant_type=client_credentials``.

    Raises :class:`GraphOAuthError` when Azure rejects the credentials.
    """

    if not tenant_id or not client_id or not client_secret:
        raise GraphOAuthError("tenant_id, client_id, and client_secret are required")

    cache_key = (
        tenant_id,
        client_id,
        scope,
        hashlib.sha256(client_secret.encode("utf-8")).hexdigest(),
    )

    async with _cache_lock:
        if not force_refresh:
            cached = _token_cache.get(cache_key)
            if cached is not None and not cached.is_expired():
                return cached

        factory = http_client_factory or _default_http_client
        url = TOKEN_ENDPOINT_TEMPLATE.format(tenant_id=tenant_id)
        try:
            async with factory() as client:
                resp = await client.post(
                    url,
                    data={
                        "grant_type": "client_credentials",
                        "client_id": client_id,
                        "client_secret": client_secret,
                        "scope": scope,
                    },
                    headers={"Content-Type": "application/x-www-form-urlencoded"},
                )
        except httpx.HTTPError as exc:
            raise GraphOAuthError(f"network: {exc}") from exc

        if resp.status_code != 200:
            try:
                body = resp.json()
                msg = body.get("error_description") or body.get("error") or resp.text
            except ValueError:
                msg = resp.text
            raise GraphOAuthError(
                f"http {resp.status_code}: {msg}", status_code=resp.status_code
            )

        try:
            data = resp.json()
        except ValueError as exc:
            raise GraphOAuthError("invalid token response (not JSON)") from exc

        access_token = data.get("access_token")
        expires_in = data.get("expires_in")
        if not access_token or not isinstance(expires_in, int):
            # Never the payload: it can hold an access_token.
            raise GraphOAuthError(
                "token endpoint returned no access_token or expires_in"
            )

        token = GraphToken(
            access_token=access_token,
            expires_at=time.time() + expires_in,
            token_type=data.get("token_type") or "Bearer",
        )
        _token_cache[cache_key] = token
        return token


async def verify_graph_credentials(
    *,
    tenant_id: str,
    client_id: str,
    client_secret: str,
    http_client_factory: HttpClientFactory | None = None,
) -> tuple[bool, str | None]:
    """Acquire a token AND hit ``GET /v1.0/organization`` to confirm the
    Azure AD app actually has Graph access. Not called today (M1-96); the
    Teams connector test fails with the being-rebuilt reason instead.

    Returns ``(True, None)`` on success or ``(False, error_message)``
    when either the token exchange or the Graph call fails. Never
    raises.
    """

    try:
        token = await acquire_app_only_token(
            tenant_id=tenant_id,
            client_id=client_id,
            client_secret=client_secret,
            force_refresh=True,
            http_client_factory=http_client_factory,
        )
    except GraphOAuthError as exc:
        return False, str(exc)

    factory = http_client_factory or _default_http_client
    try:
        async with factory() as client:
            resp = await client.get(
                "https://graph.microsoft.com/v1.0/organization",
                headers={"Authorization": f"{token.token_type} {token.access_token}"},
            )
    except httpx.HTTPError as exc:
        return False, f"network: {exc}"

    if resp.status_code != 200:
        return False, f"graph: http {resp.status_code}"
    return True, None
