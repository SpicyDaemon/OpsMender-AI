"""Tests for ``backend.mcp.oauth`` — the OAuth 2.1 + PKCE client for
HTTP-transport MCP servers (Sprint 42 step 3).

Every network interaction is mocked via ``httpx.MockTransport``. The
``http_client_factory`` injection point lets tests swap in a stub
client without touching the production default.

Coverage map:

  * PKCE generator             — 1 test
  * State JWT                  — 3 tests
  * WWW-Authenticate parsing   — 2 tests
  * PRM discovery              — 4 tests
  * Authz server metadata      — 4 tests
  * Dynamic Client Registration — 2 tests
  * Authorize URL              — 2 tests
  * RFC 9207 issuer validation — 3 tests
  * Code exchange              — 3 tests
  * Refresh                    — 4 tests
  * Canonical resource URI     — 2 tests
"""

from __future__ import annotations

import base64
import hashlib
import json
import time
from urllib.parse import parse_qs, urlparse

import httpx
import pytest

from backend.mcp.oauth import (
    AuthzServerMetadata,
    ClientRegistration,
    MCPAuthorizationRequiredError,
    MCPIssuerMismatchError,
    MCPOAuthError,
    MCPPKCENotSupportedError,
    ProtectedResourceMetadata,
    build_authorize_url,
    canonical_resource_uri,
    discover_protected_resource_metadata,
    exchange_code,
    fetch_authz_server_metadata,
    generate_pkce_pair,
    parse_www_authenticate,
    refresh_access_token,
    register_client_dynamically,
    sign_state,
    verify_redirect_issuer,
    verify_state,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _pin_secrets(monkeypatch):
    """Force a deterministic JWT secret so state-JWT tests are stable."""

    monkeypatch.setenv("OPSMENDER_JWT_SECRET", "sprint-42-mcp-oauth-test-secret")
    monkeypatch.setenv("OPSMENDER_SECRET_KEY", "sprint-42-mcp-oauth-test-secret")
    # Drop AppConfig cache so the env override is honored.
    from backend.config_loader import AppConfig

    if hasattr(AppConfig, "_cached"):
        AppConfig._cached = None  # type: ignore[attr-defined]


def _client_factory_with(handler):
    """Build an ``http_client_factory`` returning a MockTransport client."""

    transport = httpx.MockTransport(handler)

    def factory() -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=transport, timeout=5.0)

    return factory


def _authz_metadata(**overrides) -> AuthzServerMetadata:
    base = dict(
        issuer="https://auth.example.com",
        authorization_endpoint="https://auth.example.com/authorize",
        token_endpoint="https://auth.example.com/token",
        registration_endpoint="https://auth.example.com/register",
        code_challenge_methods_supported=["S256"],
        grant_types_supported=["authorization_code", "refresh_token"],
        scopes_supported=["openid", "profile"],
    )
    base.update(overrides)
    return AuthzServerMetadata(**base)


# ---------------------------------------------------------------------------
# PKCE generator
# ---------------------------------------------------------------------------


class TestPKCE:
    def test_verifier_in_spec_range_and_challenge_is_s256(self):
        pair = generate_pkce_pair()

        # RFC 7636 §4.1 — verifier length 43-128, URL-safe alphabet.
        assert 43 <= len(pair.code_verifier) <= 128
        allowed = set(
            "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~"
        )
        assert all(c in allowed for c in pair.code_verifier)

        # Challenge = base64url(SHA256(verifier)), no padding.
        recomputed = (
            base64.urlsafe_b64encode(
                hashlib.sha256(pair.code_verifier.encode("ascii")).digest()
            )
            .decode("ascii")
            .rstrip("=")
        )
        assert pair.code_challenge == recomputed


# ---------------------------------------------------------------------------
# State JWT
# ---------------------------------------------------------------------------


class TestStateJWT:
    def test_round_trip_recovers_all_fields(self):
        token = sign_state(
            server_id="00000000-0000-0000-0000-000000000111",
            issuer="https://auth.example.com",
            code_verifier="verifier-value",
            resource="https://mcp.example.com/mcp",
            org_id="00000000-0000-0000-0000-0000000000aa",
        )
        decoded = verify_state(token)

        assert decoded["sub"] == "00000000-0000-0000-0000-000000000111"
        assert decoded["asiss"] == "https://auth.example.com"
        assert decoded["cv"] == "verifier-value"
        assert decoded["res"] == "https://mcp.example.com/mcp"
        assert decoded["org"] == "00000000-0000-0000-0000-0000000000aa"
        assert decoded["aud"] == "opsmender-mcp-oauth"

    def test_tampered_token_is_rejected(self):
        token = sign_state(
            server_id="s",
            issuer="https://i",
            code_verifier="v",
            resource="https://r",
            org_id="o",
        )
        # Flip a character in the signature segment.
        head, payload, sig = token.split(".")
        tampered = f"{head}.{payload}.{sig[:-2]}AA"

        with pytest.raises(ValueError, match="Invalid MCP OAuth state"):
            verify_state(tampered)

    def test_expired_token_is_rejected(self, monkeypatch):
        # Force the JWT to have already expired by patching time.time().
        real_time = time.time
        monkeypatch.setattr(
            "backend.mcp.oauth.time.time", lambda: real_time() - 99999
        )
        token = sign_state(
            server_id="s",
            issuer="https://i",
            code_verifier="v",
            resource="https://r",
            org_id="o",
        )
        # Restore time so verify_state sees the fresh clock.
        monkeypatch.undo()

        with pytest.raises(ValueError, match="Invalid MCP OAuth state"):
            verify_state(token)


# ---------------------------------------------------------------------------
# WWW-Authenticate parser
# ---------------------------------------------------------------------------


class TestWWWAuthenticate:
    def test_extracts_resource_metadata_url(self):
        header = (
            'Bearer resource_metadata="https://mcp.example.com/'
            '.well-known/oauth-protected-resource", error="invalid_token"'
        )
        assert (
            parse_www_authenticate(header)
            == "https://mcp.example.com/.well-known/oauth-protected-resource"
        )

    def test_returns_none_when_resource_metadata_absent(self):
        assert parse_www_authenticate("Bearer realm=\"foo\"") is None
        assert parse_www_authenticate(None) is None
        assert parse_www_authenticate("") is None


# ---------------------------------------------------------------------------
# Protected Resource Metadata discovery (RFC 9728)
# ---------------------------------------------------------------------------


class TestPRMDiscovery:
    async def test_via_www_authenticate_header(self):
        prm_doc = {
            "resource": "https://mcp.example.com/mcp",
            "authorization_servers": ["https://auth.example.com"],
            "scopes_supported": ["read", "write"],
        }

        seen_urls: list[str] = []

        def handler(req: httpx.Request) -> httpx.Response:
            seen_urls.append(str(req.url))
            return httpx.Response(200, json=prm_doc)

        result = await discover_protected_resource_metadata(
            "https://mcp.example.com/mcp",
            www_authenticate=(
                'Bearer resource_metadata="https://mcp.example.com'
                '/.well-known/oauth-protected-resource"'
            ),
            http_client_factory=_client_factory_with(handler),
        )

        assert result.resource == "https://mcp.example.com/mcp"
        assert result.authorization_servers == ["https://auth.example.com"]
        # The hinted URL was tried first.
        assert seen_urls[0] == (
            "https://mcp.example.com/.well-known/oauth-protected-resource"
        )

    async def test_well_known_path_suffixed_first(self):
        seen_urls: list[str] = []

        def handler(req: httpx.Request) -> httpx.Response:
            seen_urls.append(str(req.url))
            return httpx.Response(
                200,
                json={
                    "resource": "https://mcp.example.com/public/mcp",
                    "authorization_servers": ["https://auth.example.com"],
                },
            )

        result = await discover_protected_resource_metadata(
            "https://mcp.example.com/public/mcp",
            http_client_factory=_client_factory_with(handler),
        )

        # Spec §2.3: path-suffixed URL is tried before the root.
        assert seen_urls[0] == (
            "https://mcp.example.com/.well-known/"
            "oauth-protected-resource/public/mcp"
        )
        assert result.resource == "https://mcp.example.com/public/mcp"

    async def test_falls_back_to_root_when_path_suffixed_404s(self):
        seen_urls: list[str] = []

        def handler(req: httpx.Request) -> httpx.Response:
            seen_urls.append(str(req.url))
            if "/oauth-protected-resource/public/mcp" in str(req.url):
                return httpx.Response(404)
            return httpx.Response(
                200,
                json={
                    "resource": "https://mcp.example.com/public/mcp",
                    "authorization_servers": ["https://auth.example.com"],
                },
            )

        result = await discover_protected_resource_metadata(
            "https://mcp.example.com/public/mcp",
            http_client_factory=_client_factory_with(handler),
        )
        # Both candidates probed in order.
        assert len(seen_urls) == 2
        assert seen_urls[1].endswith("/.well-known/oauth-protected-resource")
        assert result.authorization_servers == ["https://auth.example.com"]

    async def test_raises_when_every_candidate_fails(self):
        def handler(req: httpx.Request) -> httpx.Response:
            return httpx.Response(404)

        with pytest.raises(MCPOAuthError, match="Could not discover"):
            await discover_protected_resource_metadata(
                "https://mcp.example.com/mcp",
                http_client_factory=_client_factory_with(handler),
            )


# ---------------------------------------------------------------------------
# Authz server metadata (RFC 8414 / OIDC discovery)
# ---------------------------------------------------------------------------


class TestAuthzServerMetadata:
    async def test_happy_path_returns_parsed_metadata(self):
        body = {
            "issuer": "https://auth.example.com",
            "authorization_endpoint": "https://auth.example.com/authorize",
            "token_endpoint": "https://auth.example.com/token",
            "registration_endpoint": "https://auth.example.com/register",
            "code_challenge_methods_supported": ["S256", "plain"],
            "grant_types_supported": ["authorization_code", "refresh_token"],
        }

        def handler(req: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=body)

        meta = await fetch_authz_server_metadata(
            "https://auth.example.com",
            http_client_factory=_client_factory_with(handler),
        )

        assert meta.issuer == "https://auth.example.com"
        assert meta.token_endpoint == "https://auth.example.com/token"
        assert meta.registration_endpoint == "https://auth.example.com/register"
        assert "S256" in meta.code_challenge_methods_supported

    async def test_issuer_mismatch_walks_to_next_candidate(self):
        good_body = {
            "issuer": "https://auth.example.com",
            "authorization_endpoint": "https://auth.example.com/authorize",
            "token_endpoint": "https://auth.example.com/token",
            "code_challenge_methods_supported": ["S256"],
        }
        bad_body = {
            "issuer": "https://attacker.example",  # mismatch
            "authorization_endpoint": "https://attacker.example/authorize",
            "token_endpoint": "https://attacker.example/token",
            "code_challenge_methods_supported": ["S256"],
        }

        call_count = {"n": 0}

        def handler(req: httpx.Request) -> httpx.Response:
            call_count["n"] += 1
            # First candidate (oauth-authorization-server) returns bad
            # issuer; we fall through to the OIDC variant.
            if call_count["n"] == 1:
                return httpx.Response(200, json=bad_body)
            return httpx.Response(200, json=good_body)

        meta = await fetch_authz_server_metadata(
            "https://auth.example.com",
            http_client_factory=_client_factory_with(handler),
        )
        assert meta.issuer == "https://auth.example.com"
        assert call_count["n"] >= 2

    async def test_missing_s256_refuses_to_proceed(self):
        body = {
            "issuer": "https://auth.example.com",
            "authorization_endpoint": "https://auth.example.com/authorize",
            "token_endpoint": "https://auth.example.com/token",
            # `S256` deliberately absent — spec §6.1.4 says REFUSE.
            "code_challenge_methods_supported": ["plain"],
        }

        def handler(req: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=body)

        with pytest.raises(MCPPKCENotSupportedError, match="S256"):
            await fetch_authz_server_metadata(
                "https://auth.example.com",
                http_client_factory=_client_factory_with(handler),
            )

    async def test_missing_required_endpoints_walks_on(self):
        body_missing_token = {
            "issuer": "https://auth.example.com",
            "authorization_endpoint": "https://auth.example.com/authorize",
            "code_challenge_methods_supported": ["S256"],
            # token_endpoint deliberately omitted.
        }

        def handler(req: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=body_missing_token)

        with pytest.raises(MCPOAuthError, match="Could not fetch"):
            await fetch_authz_server_metadata(
                "https://auth.example.com",
                http_client_factory=_client_factory_with(handler),
            )


# ---------------------------------------------------------------------------
# Dynamic Client Registration (RFC 7591)
# ---------------------------------------------------------------------------


class TestDynamicClientRegistration:
    async def test_happy_path_returns_credentials(self):
        captured: dict[str, object] = {}

        def handler(req: httpx.Request) -> httpx.Response:
            captured["url"] = str(req.url)
            captured["body"] = json.loads(req.content)
            return httpx.Response(
                201,
                json={
                    "client_id": "client-abc123",
                    "client_secret": "secret-xyz",
                    "client_name": "OpsMender AI",
                },
            )

        reg = await register_client_dynamically(
            _authz_metadata(),
            redirect_uris=["https://opsmender.example.com/mcp/callback"],
            http_client_factory=_client_factory_with(handler),
        )

        assert reg.client_id == "client-abc123"
        assert reg.client_secret == "secret-xyz"
        assert captured["url"] == "https://auth.example.com/register"
        assert captured["body"]["redirect_uris"] == [
            "https://opsmender.example.com/mcp/callback"
        ]
        assert "authorization_code" in captured["body"]["grant_types"]
        assert "refresh_token" in captured["body"]["grant_types"]

    async def test_missing_registration_endpoint_raises_for_fallback(self):
        meta = _authz_metadata(registration_endpoint=None)
        with pytest.raises(MCPOAuthError, match="Dynamic Client Registration"):
            await register_client_dynamically(
                meta,
                redirect_uris=["https://x"],
            )


# ---------------------------------------------------------------------------
# Authorize URL
# ---------------------------------------------------------------------------


class TestAuthorizeURL:
    def test_includes_every_required_oauth_2_1_parameter(self):
        url = build_authorize_url(
            _authz_metadata(),
            client_id="client-abc",
            redirect_uri="https://opsmender.example.com/mcp/callback",
            resource="https://mcp.example.com/mcp",
            scopes=["openid", "profile", "mcp:read"],
            state="state-token-jwt",
            code_challenge="challenge-value",
        )

        parsed = urlparse(url)
        assert parsed.scheme + "://" + parsed.netloc == "https://auth.example.com"
        params = {k: v[0] for k, v in parse_qs(parsed.query).items()}

        assert params["response_type"] == "code"
        assert params["client_id"] == "client-abc"
        assert params["redirect_uri"] == (
            "https://opsmender.example.com/mcp/callback"
        )
        assert params["state"] == "state-token-jwt"
        assert params["code_challenge"] == "challenge-value"
        assert params["code_challenge_method"] == "S256"
        # RFC 8707 — resource MUST be included.
        assert params["resource"] == "https://mcp.example.com/mcp"

    def test_scopes_joined_with_spaces(self):
        url = build_authorize_url(
            _authz_metadata(),
            client_id="c",
            redirect_uri="https://x/cb",
            resource="https://r",
            scopes=["a", "b", "c"],
            state="s",
            code_challenge="ch",
        )
        params = {k: v[0] for k, v in parse_qs(urlparse(url).query).items()}
        assert params["scope"] == "a b c"


# ---------------------------------------------------------------------------
# RFC 9207 issuer validation
# ---------------------------------------------------------------------------


class TestIssuerValidation:
    def test_match_passes(self):
        # No exception expected.
        verify_redirect_issuer(
            "https://auth.example.com", "https://auth.example.com"
        )

    def test_mismatch_raises(self):
        with pytest.raises(MCPIssuerMismatchError, match="mismatch"):
            verify_redirect_issuer(
                "https://attacker.example", "https://auth.example.com"
            )

    def test_missing_iss_raises(self):
        with pytest.raises(MCPIssuerMismatchError, match="did not include"):
            verify_redirect_issuer(None, "https://auth.example.com")


# ---------------------------------------------------------------------------
# Code exchange
# ---------------------------------------------------------------------------


class TestCodeExchange:
    async def test_success_returns_parsed_token_response(self):
        captured: dict[str, object] = {}

        def handler(req: httpx.Request) -> httpx.Response:
            captured["url"] = str(req.url)
            captured["form"] = dict(
                p.split("=", 1) for p in req.content.decode().split("&")
            )
            return httpx.Response(
                200,
                json={
                    "access_token": "at-xyz",
                    "token_type": "Bearer",
                    "expires_in": 3600,
                    "refresh_token": "rt-xyz",
                    "scope": "openid profile",
                },
            )

        result = await exchange_code(
            _authz_metadata(),
            code="abc123",
            code_verifier="verifier-xyz",
            redirect_uri="https://opsmender.example.com/cb",
            resource="https://mcp.example.com/mcp",
            client_registration=ClientRegistration(
                client_id="c", client_secret="s"
            ),
            http_client_factory=_client_factory_with(handler),
        )

        assert result.access_token == "at-xyz"
        assert result.refresh_token == "rt-xyz"
        assert result.expires_in == 3600
        assert result.scope == ["openid", "profile"]

    async def test_resource_parameter_is_sent(self):
        captured: dict[str, str] = {}

        def handler(req: httpx.Request) -> httpx.Response:
            form = dict(
                p.split("=", 1) for p in req.content.decode().split("&")
            )
            captured.update(form)
            return httpx.Response(
                200, json={"access_token": "at", "token_type": "Bearer"}
            )

        await exchange_code(
            _authz_metadata(),
            code="abc",
            code_verifier="v",
            redirect_uri="https://x/cb",
            resource="https%3A%2F%2Fmcp.example.com%2Fmcp",
            client_registration=ClientRegistration(client_id="c", client_secret=None),
            http_client_factory=_client_factory_with(handler),
        )
        # urlencoded form preserves the resource value.
        assert "resource" in captured

    async def test_invalid_grant_raises_oauth_error(self):
        def handler(req: httpx.Request) -> httpx.Response:
            return httpx.Response(
                400,
                json={
                    "error": "invalid_grant",
                    "error_description": "Authorization code expired",
                },
            )

        with pytest.raises(MCPOAuthError, match="invalid_grant"):
            await exchange_code(
                _authz_metadata(),
                code="abc",
                code_verifier="v",
                redirect_uri="https://x/cb",
                resource="https://r",
                client_registration=ClientRegistration(
                    client_id="c", client_secret="s"
                ),
                http_client_factory=_client_factory_with(handler),
            )


# ---------------------------------------------------------------------------
# Refresh
# ---------------------------------------------------------------------------


class TestRefresh:
    async def test_success_with_new_refresh_token(self):
        def handler(req: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={
                    "access_token": "at-2",
                    "token_type": "Bearer",
                    "expires_in": 3600,
                    "refresh_token": "rt-new",
                },
            )

        result = await refresh_access_token(
            _authz_metadata(),
            refresh_token="rt-old",
            resource="https://r",
            client_registration=ClientRegistration(
                client_id="c", client_secret="s"
            ),
            http_client_factory=_client_factory_with(handler),
        )
        assert result.access_token == "at-2"
        assert result.refresh_token == "rt-new"

    async def test_success_without_refresh_token_returns_none(self):
        """OAuth 2.1 §4.3.1 — AS may omit refresh_token; caller keeps prior."""

        def handler(req: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={
                    "access_token": "at-2",
                    "token_type": "Bearer",
                    "expires_in": 3600,
                    # No refresh_token field.
                },
            )

        result = await refresh_access_token(
            _authz_metadata(),
            refresh_token="rt-old",
            resource="https://r",
            client_registration=ClientRegistration(
                client_id="c", client_secret=None
            ),
            http_client_factory=_client_factory_with(handler),
        )
        assert result.access_token == "at-2"
        assert result.refresh_token is None

    async def test_invalid_grant_raises_authorization_required(self):
        def handler(req: httpx.Request) -> httpx.Response:
            return httpx.Response(
                400,
                json={
                    "error": "invalid_grant",
                    "error_description": "Refresh token expired",
                },
            )

        with pytest.raises(
            MCPAuthorizationRequiredError, match="re-authorize"
        ):
            await refresh_access_token(
                _authz_metadata(),
                refresh_token="rt-bad",
                resource="https://r",
                client_registration=ClientRegistration(
                    client_id="c", client_secret="s"
                ),
                http_client_factory=_client_factory_with(handler),
            )

    async def test_resource_parameter_is_sent_on_refresh(self):
        captured: dict[str, str] = {}

        def handler(req: httpx.Request) -> httpx.Response:
            form = dict(
                p.split("=", 1) for p in req.content.decode().split("&")
            )
            captured.update(form)
            return httpx.Response(
                200, json={"access_token": "at", "token_type": "Bearer"}
            )

        await refresh_access_token(
            _authz_metadata(),
            refresh_token="rt",
            resource="https://mcp.example.com/mcp",
            client_registration=ClientRegistration(
                client_id="c", client_secret=None
            ),
            http_client_factory=_client_factory_with(handler),
        )
        assert "resource" in captured


# ---------------------------------------------------------------------------
# Canonical resource URI
# ---------------------------------------------------------------------------


class TestCanonicalResourceURI:
    def test_lowercases_scheme_and_host(self):
        assert (
            canonical_resource_uri("HTTPS://MCP.Example.COM/mcp")
            == "https://mcp.example.com/mcp"
        )

    def test_strips_trailing_slash_on_path(self):
        assert (
            canonical_resource_uri("https://mcp.example.com/mcp/")
            == "https://mcp.example.com/mcp"
        )
        # Root path stays as `/` (or empty), no over-stripping.
        assert canonical_resource_uri("https://mcp.example.com/") in {
            "https://mcp.example.com/",
            "https://mcp.example.com",
        }
