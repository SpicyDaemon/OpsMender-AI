"""Tests for the Microsoft Graph app-only OAuth helper (Sprint 37 step 1)."""

from __future__ import annotations

import time

import httpx
import pytest

from backend.auth import graph_oauth
from backend.auth.graph_oauth import (
    GraphOAuthError,
    GraphToken,
    acquire_app_only_token,
    reset_token_cache,
    verify_graph_credentials,
)


TENANT = "11111111-1111-1111-1111-111111111111"
CLIENT = "22222222-2222-2222-2222-222222222222"
SECRET = "secret-value"


@pytest.fixture(autouse=True)
def _reset_cache():
    reset_token_cache()
    yield
    reset_token_cache()


def _token_response_handler(
    *,
    access_token: str = "tok-abc",
    expires_in: int = 3600,
    status_code: int = 200,
    error: dict | None = None,
):
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        if status_code != 200:
            return httpx.Response(status_code, json=error or {"error": "bad"})
        return httpx.Response(
            200,
            json={
                "access_token": access_token,
                "expires_in": expires_in,
                "token_type": "Bearer",
            },
        )

    return handler, captured


def _factory_for(handler):
    transport = httpx.MockTransport(handler)

    def factory():
        return httpx.AsyncClient(transport=transport)

    return factory


class TestAcquireAppOnlyToken:
    async def test_requires_all_three_inputs(self):
        with pytest.raises(GraphOAuthError):
            await acquire_app_only_token(
                tenant_id="", client_id=CLIENT, client_secret=SECRET
            )
        with pytest.raises(GraphOAuthError):
            await acquire_app_only_token(
                tenant_id=TENANT, client_id="", client_secret=SECRET
            )
        with pytest.raises(GraphOAuthError):
            await acquire_app_only_token(
                tenant_id=TENANT, client_id=CLIENT, client_secret=""
            )

    async def test_happy_path_returns_token_and_caches(self):
        handler, captured = _token_response_handler(access_token="abc")
        factory = _factory_for(handler)

        tok = await acquire_app_only_token(
            tenant_id=TENANT,
            client_id=CLIENT,
            client_secret=SECRET,
            http_client_factory=factory,
        )
        assert isinstance(tok, GraphToken)
        assert tok.access_token == "abc"
        assert tok.token_type == "Bearer"
        assert not tok.is_expired()
        # POSTed to the right URL with the expected form fields.
        assert len(captured) == 1
        req = captured[0]
        assert TENANT in str(req.url)
        body = req.content.decode("utf-8")
        assert "grant_type=client_credentials" in body
        assert f"client_id={CLIENT}" in body
        assert "scope=https" in body  # graph .default

        # Second call uses the cache — no new request.
        tok2 = await acquire_app_only_token(
            tenant_id=TENANT,
            client_id=CLIENT,
            client_secret=SECRET,
            http_client_factory=factory,
        )
        assert tok2.access_token == "abc"
        assert len(captured) == 1

    async def test_force_refresh_bypasses_cache(self):
        handler, captured = _token_response_handler(access_token="abc")
        factory = _factory_for(handler)
        await acquire_app_only_token(
            tenant_id=TENANT,
            client_id=CLIENT,
            client_secret=SECRET,
            http_client_factory=factory,
        )
        await acquire_app_only_token(
            tenant_id=TENANT,
            client_id=CLIENT,
            client_secret=SECRET,
            http_client_factory=factory,
            force_refresh=True,
        )
        assert len(captured) == 2

    async def test_expired_cached_token_is_refetched(self):
        handler, captured = _token_response_handler(
            access_token="fresh", expires_in=3600
        )
        factory = _factory_for(handler)
        # Pre-populate with an already-expired token.
        graph_oauth._token_cache[(TENANT, CLIENT)] = GraphToken(
            access_token="stale",
            expires_at=time.time() - 10,
            token_type="Bearer",
        )
        tok = await acquire_app_only_token(
            tenant_id=TENANT,
            client_id=CLIENT,
            client_secret=SECRET,
            http_client_factory=factory,
        )
        assert tok.access_token == "fresh"
        assert len(captured) == 1

    async def test_azure_error_response_raises(self):
        handler, _ = _token_response_handler(
            status_code=401,
            error={
                "error": "invalid_client",
                "error_description": "Client secret expired",
            },
        )
        factory = _factory_for(handler)
        with pytest.raises(GraphOAuthError) as excinfo:
            await acquire_app_only_token(
                tenant_id=TENANT,
                client_id=CLIENT,
                client_secret=SECRET,
                http_client_factory=factory,
            )
        assert "Client secret expired" in str(excinfo.value)


class TestVerifyGraphCredentials:
    async def test_success_path(self):
        def handler(request: httpx.Request) -> httpx.Response:
            if "/oauth2/v2.0/token" in str(request.url):
                return httpx.Response(
                    200,
                    json={
                        "access_token": "t",
                        "expires_in": 3600,
                        "token_type": "Bearer",
                    },
                )
            if "/v1.0/organization" in str(request.url):
                assert request.headers["Authorization"] == "Bearer t"
                return httpx.Response(200, json={"value": [{"displayName": "Acme"}]})
            return httpx.Response(404)

        factory = _factory_for(handler)
        ok, err = await verify_graph_credentials(
            tenant_id=TENANT,
            client_id=CLIENT,
            client_secret=SECRET,
            http_client_factory=factory,
        )
        assert ok is True
        assert err is None

    async def test_token_failure_returns_error(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                401,
                json={
                    "error": "invalid_client",
                    "error_description": "Bad secret",
                },
            )

        factory = _factory_for(handler)
        ok, err = await verify_graph_credentials(
            tenant_id=TENANT,
            client_id=CLIENT,
            client_secret="wrong",
            http_client_factory=factory,
        )
        assert ok is False
        assert "Bad secret" in (err or "")

    async def test_graph_failure_after_token_returns_error(self):
        def handler(request: httpx.Request) -> httpx.Response:
            if "/oauth2/v2.0/token" in str(request.url):
                return httpx.Response(
                    200,
                    json={
                        "access_token": "t",
                        "expires_in": 3600,
                        "token_type": "Bearer",
                    },
                )
            return httpx.Response(403, json={"error": {"code": "Forbidden"}})

        factory = _factory_for(handler)
        ok, err = await verify_graph_credentials(
            tenant_id=TENANT,
            client_id=CLIENT,
            client_secret=SECRET,
            http_client_factory=factory,
        )
        assert ok is False
        assert "403" in (err or "")


class TestTeamsAdapterRegistration:
    def test_teams_adapter_registered(self):
        from backend.bots.connectors import get_adapter, list_platforms

        assert "teams" in list_platforms()
        adapter = get_adapter("teams")
        assert adapter is not None
        assert adapter.platform == "teams"

    def test_teams_form_schema_has_required_credentials(self):
        from backend.bots.connectors import get_adapter

        schema = get_adapter("teams").form_schema()
        required = {f.name for f in schema if f.required}
        assert {"tenant_id", "client_id", "client_secret"}.issubset(required)
