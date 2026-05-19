"""OAuth 2.1 + PKCE client for HTTP-transport MCP servers (Sprint 42 step 3).

Implements the binding requirements of the MCP authorization spec
(`SPEC_VERSION` below pins the snapshot consulted in Session 093):

  * **RFC 9728 — OAuth 2.0 Protected Resource Metadata.** Two discovery
    paths: (1) parse `WWW-Authenticate: Bearer resource_metadata="..."`
    on a 401 from the MCP server, (2) fall back to well-known URIs at
    `<host>/.well-known/oauth-protected-resource[/<path>]`. Both MUST
    be supported per spec §2.3. The PRM document yields the
    ``authorization_servers`` list.

  * **RFC 8414 — Authorization Server Metadata** (with OIDC Discovery
    fallback). For each authz server, fetch metadata via the standard
    well-known paths and validate ``issuer`` equals the URL used to
    fetch (RFC 8414 §3.3).

  * **PKCE S256.** Clients MUST verify ``code_challenge_methods_supported``
    contains ``S256``; if absent, refuse to proceed (spec §6.1.4).

  * **RFC 8707 — Resource Indicators.** ``resource`` parameter on every
    authorize and token request (canonical MCP server URI).

  * **RFC 9207 — Authorization Server Issuer Identification.** Record
    the ``issuer`` at authorize-request time; validate the ``iss``
    parameter on the redirect.

  * **RFC 7591 — Dynamic Client Registration.** Preferred path when the
    authz server publishes a ``registration_endpoint``. Falls back to
    pre-registered ``client_id`` + ``client_secret`` when DCR is
    unavailable.

  * **OAuth 2.1 §4.3.1 — Refresh-token rotation.** ``refresh_access_token``
    expects a potentially-new refresh_token in the response. Callers
    persist it via ``MCPServerOAuthTokenRepo.rotate``.

Tests inject ``http_client_factory`` returning an ``httpx.AsyncClient``
backed by ``httpx.MockTransport`` to exercise discovery + exchange +
refresh without real network.
"""

from __future__ import annotations

import base64
import dataclasses
import hashlib
import os
import re
import secrets
import time
from typing import Any, Callable, Mapping
from urllib.parse import quote, urlencode, urlparse

import httpx
from jose import JWTError, jwt

# Snapshot of the MCP authorization spec consulted in Session 093.
# Bump this when the spec advances to a numbered version (likely
# 2025-11 or similar) and re-validate.
SPEC_VERSION = "draft/2026-05-19"

# State JWT carries the per-request record described in spec §5.1:
# server_id (the OpsMender mcp_servers row we're authorizing), the
# authz server issuer recorded at authorize time (for RFC 9207), the
# code_verifier (for the eventual token exchange), and the original
# resource indicator (for RFC 8707 consistency).
STATE_AUDIENCE = "opsmender-mcp-oauth"
STATE_TTL_SECONDS = 600  # 10 min — covers slow operator-side consent flows

# PKCE code verifier length: spec says 43-128 URL-safe chars (RFC 7636).
PKCE_VERIFIER_LENGTH = 64


# ---------------------------------------------------------------------------
# httpx injection
# ---------------------------------------------------------------------------

HttpClientFactory = Callable[[], httpx.AsyncClient]


def _default_http_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=10.0)


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class MCPOAuthError(RuntimeError):
    """Base class for MCP OAuth client failures."""


class MCPPKCENotSupportedError(MCPOAuthError):
    """Authz server's metadata lacks `S256` in code_challenge_methods_supported.

    The MCP authz spec requires clients to refuse to proceed in this
    case (§6.1.4). Surface to the operator with a clear message.
    """


class MCPAuthorizationRequiredError(MCPOAuthError):
    """Refresh failed (token revoked, refresh_token expired, etc.).

    Caller should clear the persisted tokens and surface a Reconnect
    button to the operator.
    """


class MCPIssuerMismatchError(MCPOAuthError):
    """The redirect's `iss` parameter didn't match the recorded issuer.

    RFC 9207 §2.4 mitigation — protects against mix-up attacks where
    one authorization server's tokens get accepted by another.
    """


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclasses.dataclass(slots=True, frozen=True)
class ProtectedResourceMetadata:
    """Parsed RFC 9728 document published by the MCP server."""

    resource: str  # canonical URI of the MCP server itself
    authorization_servers: list[str]
    scopes_supported: list[str] | None = None
    bearer_methods_supported: list[str] | None = None


@dataclasses.dataclass(slots=True, frozen=True)
class AuthzServerMetadata:
    """Parsed RFC 8414 / OIDC Discovery document.

    Stores only the fields the OAuth client actually uses. The
    ``issuer`` value is the one this client *recorded* at authorize-
    request time; per RFC 9207 it must equal the ``iss`` in the
    redirect response.
    """

    issuer: str
    authorization_endpoint: str
    token_endpoint: str
    registration_endpoint: str | None = None
    code_challenge_methods_supported: list[str] | None = None
    grant_types_supported: list[str] | None = None
    scopes_supported: list[str] | None = None


@dataclasses.dataclass(slots=True, frozen=True)
class ClientRegistration:
    """Result of RFC 7591 DCR or pre-registered credentials."""

    client_id: str
    client_secret: str | None  # None for public clients


@dataclasses.dataclass(slots=True, frozen=True)
class TokenResponse:
    """Result of a successful code exchange or refresh.

    The spec says public clients **MUST** rotate refresh tokens on every
    refresh (OAuth 2.1 §4.3.1). The caller MUST persist
    ``refresh_token`` from every successful response and discard the
    prior one — unless ``refresh_token`` is ``None``, in which case the
    AS opted not to rotate this turn and the prior token stays valid.
    """

    access_token: str
    token_type: str
    expires_in: int | None  # seconds, may be absent
    refresh_token: str | None  # MAY be omitted; spec §6.4
    scope: list[str] | None  # granted scopes (may differ from requested)


@dataclasses.dataclass(slots=True, frozen=True)
class PKCEPair:
    code_verifier: str
    code_challenge: str  # always S256-computed


# ---------------------------------------------------------------------------
# PKCE
# ---------------------------------------------------------------------------


def generate_pkce_pair() -> PKCEPair:
    """Generate a fresh PKCE verifier + S256 challenge per RFC 7636.

    Verifier is URL-safe-base64 of 32 random bytes (yields 43 chars,
    well inside the 43-128 range the spec mandates). Challenge is
    base64url(SHA256(verifier)) without padding.
    """

    verifier = (
        base64.urlsafe_b64encode(secrets.token_bytes(PKCE_VERIFIER_LENGTH))
        .decode("ascii")
        .rstrip("=")
    )
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")
    return PKCEPair(code_verifier=verifier, code_challenge=challenge)


# ---------------------------------------------------------------------------
# State JWT
# ---------------------------------------------------------------------------


def _jwt_secret() -> str:
    """Read the project JWT secret without forcing a circular import."""

    # Late-import so this module stays importable in scripts that haven't
    # bootstrapped the full app config (e.g. one-shot CLIs).
    from backend.config_loader import AppConfig

    return AppConfig.load().auth.jwt_secret


def _jwt_algorithm() -> str:
    from backend.config_loader import AppConfig

    return AppConfig.load().auth.jwt_algorithm


def sign_state(
    *,
    server_id: str,
    issuer: str,
    code_verifier: str,
    resource: str,
    org_id: str,
    client_id: str | None = None,
    client_secret: str | None = None,
) -> str:
    """Sign a short-lived state JWT carrying the per-request record.

    Stored fields:
      - ``server_id`` — the OpsMender mcp_servers row.
      - ``issuer`` — recorded authz-server issuer for RFC 9207 validation.
      - ``cv`` — the PKCE code_verifier (the redirect handler needs it
        to complete the code exchange).
      - ``res`` — the RFC 8707 resource indicator (must match on token req).
      - ``org`` — for the tenant boundary check on the callback.
      - ``cid`` / ``csec`` — short-lived client registration needed for
        the callback's token exchange when DCR is used.
    """

    now = int(time.time())
    payload = {
        "iss": "opsmender",
        "aud": STATE_AUDIENCE,
        "sub": server_id,
        "asiss": issuer,
        "cv": code_verifier,
        "res": resource,
        "org": org_id,
        "iat": now,
        "exp": now + STATE_TTL_SECONDS,
    }
    if client_id:
        payload["cid"] = client_id
    if client_secret:
        payload["csec"] = client_secret
    return jwt.encode(payload, _jwt_secret(), algorithm=_jwt_algorithm())


def verify_state(token: str) -> dict[str, Any]:
    """Decode + validate a state JWT. Raises ``ValueError`` on failure."""

    try:
        return jwt.decode(
            token,
            _jwt_secret(),
            algorithms=[_jwt_algorithm()],
            audience=STATE_AUDIENCE,
        )
    except JWTError as exc:
        raise ValueError(f"Invalid MCP OAuth state: {exc}") from exc


# ---------------------------------------------------------------------------
# Protected Resource Metadata discovery (RFC 9728)
# ---------------------------------------------------------------------------


_RESOURCE_METADATA_RE = re.compile(r'resource_metadata="?([^",]+)"?', re.IGNORECASE)


def parse_www_authenticate(header_value: str | None) -> str | None:
    """Extract the ``resource_metadata`` URL from a `WWW-Authenticate` header.

    Returns ``None`` if the header is missing or doesn't carry a
    ``resource_metadata`` parameter — caller then falls back to the
    well-known URIs.
    """

    if not header_value:
        return None
    match = _RESOURCE_METADATA_RE.search(header_value)
    if not match:
        return None
    return match.group(1).strip()


def _well_known_prm_urls(mcp_server_url: str) -> list[str]:
    """Build the spec-mandated fallback URIs for PRM discovery.

    Per spec §2.3: try the path-suffixed location first, then the root.
    """

    parsed = urlparse(mcp_server_url)
    host_root = f"{parsed.scheme}://{parsed.netloc}"
    path = parsed.path.rstrip("/")

    urls = []
    if path:
        urls.append(f"{host_root}/.well-known/oauth-protected-resource{path}")
    urls.append(f"{host_root}/.well-known/oauth-protected-resource")
    return urls


async def discover_protected_resource_metadata(
    mcp_server_url: str,
    *,
    www_authenticate: str | None = None,
    http_client_factory: HttpClientFactory | None = None,
) -> ProtectedResourceMetadata:
    """Find and parse the MCP server's PRM document.

    Resolution order matches the MCP authz spec §2.3:

      1. If ``www_authenticate`` carries ``resource_metadata="..."``,
         fetch that URL directly.
      2. Otherwise, try ``<host>/.well-known/oauth-protected-resource/<path>``
         then ``<host>/.well-known/oauth-protected-resource``.

    Returns the parsed document. Raises ``MCPOAuthError`` if none of
    the candidates yield a valid response.
    """

    factory = http_client_factory or _default_http_client
    candidates: list[str] = []

    hinted = parse_www_authenticate(www_authenticate)
    if hinted:
        candidates.append(hinted)
    candidates.extend(_well_known_prm_urls(mcp_server_url))

    async with factory() as client:
        last_error: str | None = None
        for url in candidates:
            try:
                resp = await client.get(url)
            except httpx.HTTPError as exc:
                last_error = f"{url}: {exc}"
                continue
            if resp.status_code == 200:
                try:
                    body = resp.json()
                except ValueError:
                    last_error = f"{url}: non-JSON body"
                    continue
                return _parse_prm(body)
            last_error = f"{url}: HTTP {resp.status_code}"

    raise MCPOAuthError(
        f"Could not discover Protected Resource Metadata for "
        f"{mcp_server_url} — tried {len(candidates)} candidates "
        f"(last error: {last_error})"
    )


def _parse_prm(body: dict[str, Any]) -> ProtectedResourceMetadata:
    if not isinstance(body, dict):
        raise MCPOAuthError("PRM response was not a JSON object")
    resource = body.get("resource")
    auth_servers = body.get("authorization_servers")
    if not isinstance(resource, str) or not resource:
        raise MCPOAuthError("PRM document missing required `resource` field")
    if not isinstance(auth_servers, list) or not auth_servers:
        raise MCPOAuthError(
            "PRM document missing required `authorization_servers` list"
        )
    return ProtectedResourceMetadata(
        resource=resource,
        authorization_servers=[str(s) for s in auth_servers],
        scopes_supported=body.get("scopes_supported"),
        bearer_methods_supported=body.get("bearer_methods_supported"),
    )


# ---------------------------------------------------------------------------
# Authorization Server Metadata (RFC 8414 / OIDC Discovery)
# ---------------------------------------------------------------------------


def _well_known_authz_urls(issuer: str) -> list[str]:
    """Build the spec-mandated fallback URIs for authz-server metadata.

    Per spec §2.4 — try in order:
      /.well-known/oauth-authorization-server[/<path>]
      /.well-known/openid-configuration[/<path>]
      <path>/.well-known/openid-configuration
    """

    parsed = urlparse(issuer)
    host_root = f"{parsed.scheme}://{parsed.netloc}"
    path = parsed.path.rstrip("/")

    urls: list[str] = []
    if path:
        urls.append(f"{host_root}/.well-known/oauth-authorization-server{path}")
        urls.append(f"{host_root}/.well-known/openid-configuration{path}")
        urls.append(f"{host_root}{path}/.well-known/openid-configuration")
    urls.append(f"{host_root}/.well-known/oauth-authorization-server")
    urls.append(f"{host_root}/.well-known/openid-configuration")
    return urls


async def fetch_authz_server_metadata(
    issuer: str,
    *,
    http_client_factory: HttpClientFactory | None = None,
) -> AuthzServerMetadata:
    """Fetch + validate authorization server metadata.

    Walks the well-known fallback list, returns the first successful
    response whose ``issuer`` matches the requested URL exactly (RFC
    8414 §3.3). Raises ``MCPPKCENotSupportedError`` when
    ``code_challenge_methods_supported`` is absent or omits ``S256``
    (spec §6.1.4 — clients MUST refuse to proceed).
    """

    factory = http_client_factory or _default_http_client
    candidates = _well_known_authz_urls(issuer)
    last_error: str | None = None

    async with factory() as client:
        for url in candidates:
            try:
                resp = await client.get(url)
            except httpx.HTTPError as exc:
                last_error = f"{url}: {exc}"
                continue
            if resp.status_code != 200:
                last_error = f"{url}: HTTP {resp.status_code}"
                continue
            try:
                body = resp.json()
            except ValueError:
                last_error = f"{url}: non-JSON body"
                continue
            metadata = _parse_authz_metadata(body, expected_issuer=issuer)
            if metadata is None:
                last_error = (
                    f"{url}: issuer mismatch "
                    f"(got {body.get('issuer')!r}, expected {issuer!r})"
                )
                continue
            return metadata

    raise MCPOAuthError(
        f"Could not fetch authorization server metadata for {issuer} — "
        f"tried {len(candidates)} candidates (last error: {last_error})"
    )


def _parse_authz_metadata(
    body: dict[str, Any], *, expected_issuer: str
) -> AuthzServerMetadata | None:
    if not isinstance(body, dict):
        return None
    issuer = body.get("issuer")
    if not isinstance(issuer, str) or issuer != expected_issuer:
        return None

    authorize_endpoint = body.get("authorization_endpoint")
    token_endpoint = body.get("token_endpoint")
    if not isinstance(authorize_endpoint, str) or not isinstance(token_endpoint, str):
        return None

    code_methods = body.get("code_challenge_methods_supported")
    if not isinstance(code_methods, list) or "S256" not in code_methods:
        # Spec §6.1.4 — refuse to proceed.
        raise MCPPKCENotSupportedError(
            f"Authorization server {issuer} does not advertise S256 PKCE "
            f"(code_challenge_methods_supported={code_methods!r})"
        )

    return AuthzServerMetadata(
        issuer=issuer,
        authorization_endpoint=authorize_endpoint,
        token_endpoint=token_endpoint,
        registration_endpoint=body.get("registration_endpoint"),
        code_challenge_methods_supported=code_methods,
        grant_types_supported=body.get("grant_types_supported"),
        scopes_supported=body.get("scopes_supported"),
    )


# ---------------------------------------------------------------------------
# RFC 7591 Dynamic Client Registration
# ---------------------------------------------------------------------------


async def register_client_dynamically(
    metadata: AuthzServerMetadata,
    *,
    redirect_uris: list[str],
    client_name: str = "OpsMender AI",
    http_client_factory: HttpClientFactory | None = None,
) -> ClientRegistration:
    """Register OpsMender as an OAuth client via RFC 7591 DCR.

    Raises ``MCPOAuthError`` if the authz server doesn't advertise a
    ``registration_endpoint``. Callers should catch this and fall back
    to pre-registered credentials.
    """

    if not metadata.registration_endpoint:
        raise MCPOAuthError(
            f"Authorization server {metadata.issuer} does not support "
            "Dynamic Client Registration (no registration_endpoint). "
            "Provide pre-registered client_id + client_secret instead."
        )

    factory = http_client_factory or _default_http_client
    payload = {
        "client_name": client_name,
        "redirect_uris": redirect_uris,
        "token_endpoint_auth_method": "client_secret_basic",
        "grant_types": ["authorization_code", "refresh_token"],
        "response_types": ["code"],
    }
    async with factory() as client:
        try:
            resp = await client.post(metadata.registration_endpoint, json=payload)
        except httpx.HTTPError as exc:
            raise MCPOAuthError(
                f"DCR request to {metadata.registration_endpoint} failed: {exc}"
            ) from exc

    if resp.status_code not in (200, 201):
        raise MCPOAuthError(f"DCR rejected: HTTP {resp.status_code} {resp.text[:200]}")

    body = resp.json()
    client_id = body.get("client_id")
    if not isinstance(client_id, str) or not client_id:
        raise MCPOAuthError("DCR response missing client_id")
    secret = body.get("client_secret")
    return ClientRegistration(
        client_id=client_id,
        client_secret=secret if isinstance(secret, str) and secret else None,
    )


# ---------------------------------------------------------------------------
# Authorize URL
# ---------------------------------------------------------------------------


def build_authorize_url(
    metadata: AuthzServerMetadata,
    *,
    client_id: str,
    redirect_uri: str,
    resource: str,
    scopes: list[str],
    state: str,
    code_challenge: str,
) -> str:
    """Build the URL the operator is redirected to for consent."""

    params = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "state": state,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
        # RFC 8707 — bind the issued token to this specific MCP server URI.
        "resource": resource,
    }
    if scopes:
        params["scope"] = " ".join(scopes)
    return f"{metadata.authorization_endpoint}?{urlencode(params, quote_via=quote)}"


# ---------------------------------------------------------------------------
# RFC 9207 issuer validation
# ---------------------------------------------------------------------------


def verify_redirect_issuer(received_iss: str | None, expected_issuer: str) -> None:
    """Validate the ``iss`` parameter on the OAuth redirect (RFC 9207 §2.4).

    Raises :class:`MCPIssuerMismatchError` if the received value is
    absent or doesn't match. The recorded ``expected_issuer`` comes
    from the state JWT minted at authorize-request time.
    """

    if not received_iss:
        # RFC 9207 §2.3 — when the AS advertises issuer identification,
        # the redirect MUST carry `iss`. We treat absence as a failure
        # because mix-up attacks rely on the legitimate AS not signaling.
        # (Honest AS's that DON'T advertise iss identification are out
        # of scope for the MCP authz spec, which mandates RFC 9207.)
        raise MCPIssuerMismatchError(
            f"Redirect did not include `iss` parameter "
            f"(expected {expected_issuer!r}) — possible mix-up attack."
        )
    if received_iss != expected_issuer:
        raise MCPIssuerMismatchError(
            f"Redirect `iss` mismatch: got {received_iss!r}, "
            f"expected {expected_issuer!r}"
        )


# ---------------------------------------------------------------------------
# Code exchange
# ---------------------------------------------------------------------------


async def exchange_code(
    metadata: AuthzServerMetadata,
    *,
    code: str,
    code_verifier: str,
    redirect_uri: str,
    resource: str,
    client_registration: ClientRegistration,
    http_client_factory: HttpClientFactory | None = None,
) -> TokenResponse:
    """Exchange an authorization code for tokens.

    Honors RFC 8707 by re-including ``resource`` on the token request.
    """

    factory = http_client_factory or _default_http_client
    data = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": redirect_uri,
        "code_verifier": code_verifier,
        "client_id": client_registration.client_id,
        "resource": resource,
    }
    auth: tuple[str, str] | None = None
    if client_registration.client_secret:
        # Per spec §3 + OAuth 2.1, confidential clients SHOULD use
        # client_secret_basic. Public clients (no secret) just include
        # client_id in the body.
        auth = (client_registration.client_id, client_registration.client_secret)

    async with factory() as client:
        try:
            resp = await client.post(metadata.token_endpoint, data=data, auth=auth)
        except httpx.HTTPError as exc:
            raise MCPOAuthError(
                f"Token endpoint {metadata.token_endpoint} unreachable: {exc}"
            ) from exc

    return _parse_token_response(resp, op="code exchange")


# ---------------------------------------------------------------------------
# Refresh
# ---------------------------------------------------------------------------


async def refresh_access_token(
    metadata: AuthzServerMetadata,
    *,
    refresh_token: str,
    resource: str,
    client_registration: ClientRegistration,
    http_client_factory: HttpClientFactory | None = None,
) -> TokenResponse:
    """Refresh an access token.

    Per OAuth 2.1 §4.3.1, the response may include a *new*
    ``refresh_token`` — callers MUST persist whichever value comes back
    (use :meth:`MCPServerOAuthTokenRepo.rotate`). When the response
    omits ``refresh_token``, the prior one stays valid.

    Raises :class:`MCPAuthorizationRequiredError` on `invalid_grant` /
    4xx — the operator needs to re-consent.
    """

    factory = http_client_factory or _default_http_client
    data = {
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
        "client_id": client_registration.client_id,
        "resource": resource,
    }
    auth: tuple[str, str] | None = None
    if client_registration.client_secret:
        auth = (client_registration.client_id, client_registration.client_secret)

    async with factory() as client:
        try:
            resp = await client.post(metadata.token_endpoint, data=data, auth=auth)
        except httpx.HTTPError as exc:
            raise MCPOAuthError(
                f"Token endpoint {metadata.token_endpoint} unreachable: {exc}"
            ) from exc

    if resp.status_code == 400:
        try:
            err = resp.json().get("error", "")
        except ValueError:
            err = ""
        if err in {"invalid_grant", "invalid_token", "expired_token"}:
            raise MCPAuthorizationRequiredError(
                f"Refresh rejected ({err}) — operator must re-authorize."
            )

    return _parse_token_response(resp, op="refresh")


# ---------------------------------------------------------------------------
# Shared response parser
# ---------------------------------------------------------------------------


def _parse_token_response(resp: httpx.Response, *, op: str) -> TokenResponse:
    if resp.status_code != 200:
        try:
            body = resp.json()
            error = body.get("error", "")
            detail = body.get("error_description", "")
        except ValueError:
            error = ""
            detail = resp.text[:200]
        raise MCPOAuthError(f"{op} failed: HTTP {resp.status_code} {error} — {detail}")

    body = resp.json()
    access = body.get("access_token")
    if not isinstance(access, str) or not access:
        raise MCPOAuthError(f"{op} response missing access_token")

    scope_raw = body.get("scope")
    scope: list[str] | None
    if isinstance(scope_raw, str) and scope_raw:
        scope = scope_raw.split()
    elif isinstance(scope_raw, list):
        scope = [str(s) for s in scope_raw]
    else:
        scope = None

    return TokenResponse(
        access_token=access,
        token_type=body.get("token_type", "Bearer"),
        expires_in=body.get("expires_in"),
        refresh_token=body.get("refresh_token"),
        scope=scope,
    )


# ---------------------------------------------------------------------------
# Canonical resource indicator
# ---------------------------------------------------------------------------


def canonical_resource_uri(mcp_server_url: str) -> str:
    """Build the RFC 8707 ``resource`` value from an MCP server URL.

    Spec §6.2: the canonical form has lowercase scheme + host and
    preserves the path component the operator addresses.
    """

    parsed = urlparse(mcp_server_url)
    scheme = (parsed.scheme or "https").lower()
    netloc = parsed.netloc.lower()
    path = parsed.path or ""
    # Strip trailing slash for stability — RFC 8707 examples are
    # canonical without it.
    if path.endswith("/") and len(path) > 1:
        path = path.rstrip("/")
    return f"{scheme}://{netloc}{path}"
