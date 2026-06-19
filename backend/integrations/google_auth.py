"""Minimal Google service-account OAuth helper shared by native adapters."""

from __future__ import annotations

import time
from typing import Any, Iterable

import httpx
import jwt

from backend.integrations.http import required


async def service_account_access_token(
    client: httpx.AsyncClient,
    auth: dict[str, Any],
    *,
    scopes: Iterable[str],
) -> str:
    """Exchange a signed service-account assertion for an OAuth access token."""

    client_email = required(
        auth.get("client_email") or auth.get("service_account_email"),
        "client_email",
    )
    private_key = required(auth.get("private_key"), "private_key")
    token_uri = str(auth.get("token_uri") or "https://oauth2.googleapis.com/token")
    now = int(time.time())
    claims: dict[str, Any] = {
        "iss": client_email,
        "scope": " ".join(scopes),
        "aud": token_uri,
        "iat": now - 30,
        "exp": now + 3600,
    }
    delegated_user = auth.get("delegated_user") or auth.get("subject")
    if delegated_user:
        claims["sub"] = str(delegated_user)
    try:
        assertion = jwt.encode(claims, private_key, algorithm="RS256")
    except Exception as exc:  # noqa: BLE001 - normalize credential parse failures
        raise ValueError("invalid Google service-account private key") from exc
    response = await client.post(
        token_uri,
        data={
            "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
            "assertion": assertion,
        },
    )
    if response.status_code >= 400:
        raise RuntimeError(f"Google OAuth HTTP {response.status_code}")
    return required(response.json().get("access_token"), "access_token")
