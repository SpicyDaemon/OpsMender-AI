"""Minimal OIDC client used by the per-tenant SSO flow.

Implements the authorization-code flow against any IdP that ships an
OpenID Connect discovery document (Okta, Azure AD, Google, Auth0,
Keycloak, etc.). Discovery results are cached in-process for 10 minutes.

This intentionally avoids ``authlib`` so that OpsMender keeps its dependency
surface small. The flow:

1. ``build_authorize_url(config, state, redirect_uri)`` - returns the URL
   the browser should redirect to.
2. ``exchange_code(config, code, redirect_uri)`` - POSTs the code to the
   IdP's token endpoint and returns the parsed id_token claims.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode

import httpx
from jose.exceptions import JWKError, JWTError

from backend.auth.jwt_decode import decode as decode_jwt


_DISCOVERY_TTL_SECONDS = 600
_discovery_cache: dict[str, tuple[float, dict[str, Any]]] = {}
_jwks_cache: dict[str, tuple[float, dict[str, Any]]] = {}

# An identity provider signs id tokens with a private key, so only these
# asymmetric algorithms are ever accepted (M1-58). The RSASSA-PSS ones (PS256
# and friends) are left out: python-jose cannot verify them, so a token signed
# that way is refused like any other algorithm not on this list.
_ASYMMETRIC_ALGORITHMS = (
    "RS256",
    "RS384",
    "RS512",
    "ES256",
    "ES384",
    "ES512",
)


@dataclass
class OIDCClientConfig:
    discovery_url: str
    client_id: str
    client_secret: str
    scopes: str = "openid email profile"


class OIDCError(Exception):
    pass


async def _get_discovery(url: str) -> dict[str, Any]:
    now = time.time()
    cached = _discovery_cache.get(url)
    if cached and cached[0] > now:
        return cached[1]

    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.get(url)
    if resp.status_code != 200:
        raise OIDCError(f"Discovery URL returned {resp.status_code}")
    doc = resp.json()
    for required in ("authorization_endpoint", "token_endpoint", "jwks_uri", "issuer"):
        if required not in doc:
            raise OIDCError(f"Discovery doc missing required field: {required}")
    _discovery_cache[url] = (now + _DISCOVERY_TTL_SECONDS, doc)
    return doc


async def _get_jwks(jwks_uri: str) -> dict[str, Any]:
    now = time.time()
    cached = _jwks_cache.get(jwks_uri)
    if cached and cached[0] > now:
        return cached[1]
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.get(jwks_uri)
    if resp.status_code != 200:
        raise OIDCError(f"JWKS endpoint returned {resp.status_code}")
    keys = resp.json()
    _jwks_cache[jwks_uri] = (now + _DISCOVERY_TTL_SECONDS, keys)
    return keys


def _id_token_algorithms(doc: dict[str, Any]) -> list[str]:
    """The algorithms an id token may be signed with: the asymmetric ones the
    discovery document lists, or RS256 when it lists none of them."""
    listed = doc.get("id_token_signing_alg_values_supported")
    if not isinstance(listed, list):
        listed = []
    return [alg for alg in _ASYMMETRIC_ALGORITHMS if alg in listed] or ["RS256"]


async def build_authorize_url(
    config: OIDCClientConfig, *, state: str, redirect_uri: str, nonce: str
) -> str:
    doc = await _get_discovery(config.discovery_url)
    params = {
        "response_type": "code",
        "client_id": config.client_id,
        "redirect_uri": redirect_uri,
        "scope": config.scopes,
        "state": state,
        "nonce": nonce,
    }
    sep = "&" if "?" in doc["authorization_endpoint"] else "?"
    return f"{doc['authorization_endpoint']}{sep}{urlencode(params)}"


async def exchange_code(
    config: OIDCClientConfig,
    *,
    code: str,
    redirect_uri: str,
    nonce: str | None = None,
) -> dict[str, Any]:
    """Exchange the authorization code for tokens, verify the id_token,
    and return its claims."""
    doc = await _get_discovery(config.discovery_url)

    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.post(
            doc["token_endpoint"],
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": redirect_uri,
                "client_id": config.client_id,
                "client_secret": config.client_secret,
            },
            headers={"Accept": "application/json"},
        )
    if resp.status_code != 200:
        raise OIDCError(
            f"Token endpoint returned {resp.status_code}: {resp.text[:200]}"
        )
    body = resp.json()
    id_token = body.get("id_token")
    if not id_token:
        raise OIDCError("Token response missing id_token")

    jwks = await _get_jwks(doc["jwks_uri"])

    try:
        claims = decode_jwt(
            id_token,
            jwks,
            algorithms=_id_token_algorithms(doc),
            audience=config.client_id,
            issuer=doc["issuer"],
            options={"verify_at_hash": False},
        )
    except (JWTError, JWKError) as exc:
        raise OIDCError(f"id_token validation failed: {exc}") from exc

    if nonce is not None and claims.get("nonce") != nonce:
        raise OIDCError("id_token nonce mismatch")

    return claims


def reset_caches() -> None:
    """Clear discovery + JWKS caches (test-only helper)."""
    _discovery_cache.clear()
    _jwks_cache.clear()
