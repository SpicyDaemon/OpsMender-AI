"""Tests for Slack / Discord OAuth flow on bot connectors (Sprint 31 Steps 5–6).

We test the surface Opsmender owns:
- AppConfig env wiring + per-platform is_enabled gate.
- State JWT sign/verify (TTL, tampering).
- Authorize URL builder shape.
- Route guards: 404 unknown platform, 503 OAuth disabled, 400 platform
  mismatch, callback handling of state errors / provider errors.
- Code exchange — Slack & Discord — with httpx mocked.

Hitting the real Slack/Discord token endpoints is left to an
integration suite (would need network + valid client credentials).
"""

from __future__ import annotations

import json
import time
import uuid
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from backend.api.app import create_app
from backend.api.deps import get_db, set_session_factory
from backend.auth import bot_oauth as oauth_mod
from backend.auth.bot_oauth import (
    SUPPORTED_PLATFORMS,
    build_authorize_url,
    exchange_code,
    is_platform_enabled,
    sign_state,
    verify_state,
)
from backend.config_loader import AppConfig, BotOAuthConfig, set_env_path
from backend.db.models import Base, BotConnector, Organization
from backend.db.repos import UserRepo

TEST_ORG_ID = uuid.UUID("00000000-0000-0000-0000-0000000000aa")


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------


class TestBotOAuthConfig:
    def test_disabled_by_default(self):
        cfg = BotOAuthConfig()
        assert cfg.is_enabled("slack") is False
        assert cfg.is_enabled("discord") is False
        assert cfg.is_enabled("telegram") is False

    def test_slack_requires_both_id_and_secret(self):
        only_id = BotOAuthConfig(slack_client_id="abc")
        assert only_id.is_enabled("slack") is False
        only_secret = BotOAuthConfig(slack_client_secret="xyz")
        assert only_secret.is_enabled("slack") is False
        full = BotOAuthConfig(slack_client_id="abc", slack_client_secret="xyz")
        assert full.is_enabled("slack") is True

    def test_load_from_env(self, monkeypatch, tmp_path):
        tmp_env = tmp_path / ".env"
        tmp_env.write_text(
            "OPSMENDER_JWT_SECRET=test\n"
            "OPSMENDER_DATABASE_URL=sqlite+aiosqlite://\n"
            "OPSMENDER_SLACK_OAUTH_CLIENT_ID=slack-id\n"
            "OPSMENDER_SLACK_OAUTH_CLIENT_SECRET=slack-secret\n"
            "OPSMENDER_DISCORD_OAUTH_CLIENT_ID=discord-id\n"
            "OPSMENDER_DISCORD_OAUTH_CLIENT_SECRET=discord-secret\n"
        )
        set_env_path(tmp_env)
        try:
            cfg = AppConfig.load()
            assert cfg.bot_oauth.slack_client_id == "slack-id"
            assert cfg.bot_oauth.slack_client_secret == "slack-secret"
            assert cfg.bot_oauth.discord_client_id == "discord-id"
            assert cfg.bot_oauth.is_enabled("slack") is True
            assert cfg.bot_oauth.is_enabled("discord") is True
        finally:
            set_env_path(None)


# ---------------------------------------------------------------------------
# State JWT
# ---------------------------------------------------------------------------


class TestStateJWT:
    @pytest.fixture(autouse=True)
    def _env(self, tmp_path):
        tmp_env = tmp_path / ".env"
        tmp_env.write_text(
            "OPSMENDER_JWT_SECRET=state-secret\n"
            "OPSMENDER_DATABASE_URL=sqlite+aiosqlite://\n"
        )
        set_env_path(tmp_env)
        yield
        set_env_path(None)

    def test_sign_then_verify_roundtrips(self):
        token = sign_state(
            connector_id="abc",
            platform="slack",
            org_id="org-1",
            user_id="user-1",
        )
        claims = verify_state(token)
        assert claims["sub"] == "abc"
        assert claims["plat"] == "slack"
        assert claims["org"] == "org-1"
        assert claims["uid"] == "user-1"
        assert claims["aud"] == "opsmender-bot-oauth"

    def test_tampered_token_rejected(self):
        token = sign_state(
            connector_id="abc",
            platform="slack",
            org_id="org-1",
            user_id="user-1",
        )
        tampered = token[:-3] + ("xyz" if not token.endswith("xyz") else "abc")
        with pytest.raises(ValueError):
            verify_state(tampered)

    def test_expired_token_rejected(self, monkeypatch):
        # Sign with iat/exp in the past so it's already expired.
        real_time = time.time
        monkeypatch.setattr(oauth_mod.time, "time", lambda: real_time() - 10_000)
        token = sign_state(
            connector_id="abc",
            platform="slack",
            org_id="org-1",
            user_id="user-1",
        )
        monkeypatch.setattr(oauth_mod.time, "time", real_time)
        with pytest.raises(ValueError):
            verify_state(token)


# ---------------------------------------------------------------------------
# Authorize URL
# ---------------------------------------------------------------------------


class TestAuthorizeUrl:
    SLACK_CFG = BotOAuthConfig(
        slack_client_id="slack-id",
        slack_client_secret="slack-secret",
    )
    DISCORD_CFG = BotOAuthConfig(
        discord_client_id="discord-id",
        discord_client_secret="discord-secret",
    )

    def test_slack_authorize_url(self):
        url = build_authorize_url(
            platform="slack",
            state="state-abc",
            redirect_uri="https://opsmender.example.com/cb",
            cfg=self.SLACK_CFG,
        )
        assert url.startswith("https://slack.com/oauth/v2/authorize?")
        assert "client_id=slack-id" in url
        assert "state=state-abc" in url
        assert "scope=" in url
        assert "redirect_uri=https%3A%2F%2Fopsmender.example.com%2Fcb" in url

    def test_discord_authorize_url(self):
        url = build_authorize_url(
            platform="discord",
            state="state-xyz",
            redirect_uri="https://opsmender.example.com/cb",
            cfg=self.DISCORD_CFG,
        )
        assert url.startswith("https://discord.com/api/oauth2/authorize?")
        assert "client_id=discord-id" in url
        assert "scope=bot+applications.commands" in url
        assert "response_type=code" in url

    def test_unsupported_platform_raises(self):
        with pytest.raises(ValueError):
            build_authorize_url(
                platform="telegram",
                state="s",
                redirect_uri="https://x/y",
                cfg=self.SLACK_CFG,
            )

    def test_missing_creds_raises(self):
        with pytest.raises(ValueError):
            build_authorize_url(
                platform="slack",
                state="s",
                redirect_uri="https://x/y",
                cfg=BotOAuthConfig(),
            )


# ---------------------------------------------------------------------------
# Code exchange
# ---------------------------------------------------------------------------


def _mock_httpx_response(*, payload: dict, status: int = 200):
    return httpx.Response(status_code=status, json=payload)


class TestCodeExchange:
    SLACK_CFG = BotOAuthConfig(
        slack_client_id="slack-id",
        slack_client_secret="slack-secret",
    )
    DISCORD_CFG = BotOAuthConfig(
        discord_client_id="discord-id",
        discord_client_secret="discord-secret",
    )

    async def test_slack_success(self):
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda req: _mock_httpx_response(
                    payload={
                        "ok": True,
                        "access_token": "xoxb-test-token",
                        "team": {"name": "Acme"},
                    }
                )
            )
        ) as client:
            result = await exchange_code(
                platform="slack",
                code="auth-code",
                redirect_uri="https://opsmender.example.com/cb",
                client=client,
                cfg=self.SLACK_CFG,
            )
        assert result.credentials == {"bot_token": "xoxb-test-token"}
        assert "Acme" in result.detail

    async def test_slack_rejects_non_ok(self):
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda req: _mock_httpx_response(
                    payload={"ok": False, "error": "invalid_code"}
                )
            )
        ) as client:
            with pytest.raises(ValueError, match="invalid_code"):
                await exchange_code(
                    platform="slack",
                    code="bad",
                    redirect_uri="https://opsmender.example.com/cb",
                    client=client,
                    cfg=self.SLACK_CFG,
                )

    async def test_discord_success(self):
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda req: _mock_httpx_response(
                    payload={
                        "access_token": "discord-token",
                        "guild": {"name": "Opsmender Guild"},
                    }
                )
            )
        ) as client:
            result = await exchange_code(
                platform="discord",
                code="auth-code",
                redirect_uri="https://opsmender.example.com/cb",
                client=client,
                cfg=self.DISCORD_CFG,
            )
        assert result.credentials == {"bot_token": "discord-token"}
        assert "Opsmender Guild" in result.detail


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@pytest.fixture
async def app(tmp_path):
    engine = create_async_engine("sqlite+aiosqlite://", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    factory = async_sessionmaker(engine, expire_on_commit=False)
    set_session_factory(factory)

    async with factory() as db:
        org = Organization(id=TEST_ORG_ID, name="OAuth Org", slug="oauth-org")
        db.add(org)
        await db.commit()

    tmp_env = tmp_path / ".env"
    tmp_env.write_text(
        "OPSMENDER_TIER=2\n"
        "OPSMENDER_LOG_LEVEL=INFO\n"
        "OPSMENDER_AUDIT_LOG=./logs/audit.jsonl\n"
        "OPSMENDER_JWT_SECRET=test-secret\n"
        "OPSMENDER_DATABASE_URL=sqlite+aiosqlite://\n"
        f"OPSMENDER_MCP_SERVERS_JSON={json.dumps([])}\n"
    )
    set_env_path(tmp_env)

    application = create_app()
    application.state.session_factory = factory

    async def _override_db():
        async with factory() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    application.dependency_overrides[get_db] = _override_db
    yield application
    set_env_path(None)
    await engine.dispose()


@pytest.fixture
async def client(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest.fixture
async def auth_headers(client, app):
    await client.post(
        "/auth/register",
        json={
            "username": "oauthadmin",
            "email": "oauthadmin@test.com",
            "password": "securepass123",
        },
    )
    async with app.state.session_factory() as db:
        user = await UserRepo.get_by_username(db, "oauthadmin")
        if user:
            user.primary_org_id = TEST_ORG_ID
            if not await UserRepo.is_member(db, user.id, TEST_ORG_ID):
                await UserRepo.add_to_organization(
                    db, user_id=user.id, org_id=TEST_ORG_ID, role="admin"
                )
            await db.commit()

    resp = await client.post(
        "/auth/login",
        json={"username": "oauthadmin", "password": "securepass123"},
    )
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


@pytest.fixture
async def slack_connector_id(client, app, auth_headers):
    async with app.state.session_factory() as db:
        connector = BotConnector(
            org_id=TEST_ORG_ID,
            name="slack-oauth-test",
            platform="slack",
            config={},
            credentials={},
            allowed_capabilities=["notifications"],
            status="not_configured",
            is_enabled=True,
        )
        db.add(connector)
        await db.commit()
        await db.refresh(connector)
        return connector.id


class TestStartRoute:
    async def test_unknown_platform_returns_404(self, client, auth_headers, slack_connector_id):
        resp = await client.get(
            f"/bot-connectors/oauth/telegram/start?connector_id={slack_connector_id}",
            headers=auth_headers,
        )
        assert resp.status_code == 404

    async def test_oauth_disabled_returns_503(
        self, client, auth_headers, slack_connector_id
    ):
        # No OPSMENDER_SLACK_OAUTH_CLIENT_ID in the test env → 503.
        resp = await client.get(
            f"/bot-connectors/oauth/slack/start?connector_id={slack_connector_id}",
            headers=auth_headers,
        )
        assert resp.status_code == 503
        assert "OPSMENDER_SLACK_OAUTH_CLIENT_ID" in resp.text

    async def test_start_returns_authorize_url(
        self, client, auth_headers, slack_connector_id, monkeypatch
    ):
        monkeypatch.setenv("OPSMENDER_SLACK_OAUTH_CLIENT_ID", "slack-test-id")
        monkeypatch.setenv("OPSMENDER_SLACK_OAUTH_CLIENT_SECRET", "slack-test-secret")
        resp = await client.get(
            f"/bot-connectors/oauth/slack/start?connector_id={slack_connector_id}",
            headers=auth_headers,
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["authorize_url"].startswith(
            "https://slack.com/oauth/v2/authorize?"
        )
        assert "state=" in body["authorize_url"]
        assert "client_id=slack-test-id" in body["authorize_url"]

    async def test_connector_platform_mismatch_returns_400(
        self, client, app, auth_headers, monkeypatch
    ):
        monkeypatch.setenv("OPSMENDER_SLACK_OAUTH_CLIENT_ID", "x")
        monkeypatch.setenv("OPSMENDER_SLACK_OAUTH_CLIENT_SECRET", "y")
        async with app.state.session_factory() as db:
            telegram = BotConnector(
                org_id=TEST_ORG_ID,
                name="tg-not-slack",
                platform="telegram",
                config={},
                credentials={},
                allowed_capabilities=["notifications"],
                status="not_configured",
                is_enabled=True,
            )
            db.add(telegram)
            await db.commit()
            await db.refresh(telegram)
            tid = telegram.id
        resp = await client.get(
            f"/bot-connectors/oauth/slack/start?connector_id={tid}",
            headers=auth_headers,
        )
        assert resp.status_code == 400

    async def test_unauthorized_without_token(self, client, slack_connector_id):
        resp = await client.get(
            f"/bot-connectors/oauth/slack/start?connector_id={slack_connector_id}",
        )
        assert resp.status_code == 401


class TestCallbackRoute:
    async def test_callback_writes_credentials_and_redirects(
        self, client, app, slack_connector_id, monkeypatch
    ):
        monkeypatch.setenv("OPSMENDER_SLACK_OAUTH_CLIENT_ID", "x")
        monkeypatch.setenv("OPSMENDER_SLACK_OAUTH_CLIENT_SECRET", "y")

        state = sign_state(
            connector_id=str(slack_connector_id),
            platform="slack",
            org_id=str(TEST_ORG_ID),
            user_id="any",
        )

        async def _fake_exchange(**kwargs):
            from backend.auth.bot_oauth import OAuthResult

            return OAuthResult(
                credentials={"bot_token": "xoxb-fake"},
                detail="Connected to Slack workspace 'Acme'",
            )

        with patch(
            "backend.api.routes.bot_oauth.exchange_code", new=_fake_exchange
        ):
            resp = await client.get(
                f"/bot-connectors/oauth/slack/callback?code=abc&state={state}",
                follow_redirects=False,
            )

        assert resp.status_code == 302
        loc = resp.headers["location"]
        assert loc.startswith("http://test/dashboard/config?")
        assert "bot_oauth=ok" in loc
        assert "Acme" in loc

        async with app.state.session_factory() as db:
            refreshed = await db.get(BotConnector, slack_connector_id)
            assert refreshed is not None
            assert refreshed.credentials == {"bot_token": "xoxb-fake"}
            assert refreshed.status == "configured"

    async def test_callback_bad_state_redirects_with_error(
        self, client, slack_connector_id, monkeypatch
    ):
        monkeypatch.setenv("OPSMENDER_SLACK_OAUTH_CLIENT_ID", "x")
        monkeypatch.setenv("OPSMENDER_SLACK_OAUTH_CLIENT_SECRET", "y")
        resp = await client.get(
            "/bot-connectors/oauth/slack/callback?code=abc&state=not-a-real-jwt",
            follow_redirects=False,
        )
        assert resp.status_code == 302
        loc = resp.headers["location"]
        assert "bot_oauth=error" in loc

    async def test_callback_provider_error_redirects(self, client, monkeypatch):
        monkeypatch.setenv("OPSMENDER_SLACK_OAUTH_CLIENT_ID", "x")
        monkeypatch.setenv("OPSMENDER_SLACK_OAUTH_CLIENT_SECRET", "y")
        resp = await client.get(
            "/bot-connectors/oauth/slack/callback?error=access_denied",
            follow_redirects=False,
        )
        assert resp.status_code == 302
        loc = resp.headers["location"]
        assert "bot_oauth=error" in loc
        assert "access_denied" in loc


# ---------------------------------------------------------------------------
# Schema endpoint exposes oauth_enabled
# ---------------------------------------------------------------------------


class TestSchemaExposesOAuth:
    async def test_oauth_enabled_flag_reflects_env(
        self, client, auth_headers, monkeypatch
    ):
        # Default test env: OAuth not configured → flag is False.
        resp = await client.get(
            "/bot-connectors/platforms/slack/schema", headers=auth_headers
        )
        assert resp.status_code == 200
        assert resp.json()["oauth_enabled"] is False

        monkeypatch.setenv("OPSMENDER_SLACK_OAUTH_CLIENT_ID", "x")
        monkeypatch.setenv("OPSMENDER_SLACK_OAUTH_CLIENT_SECRET", "y")
        resp = await client.get(
            "/bot-connectors/platforms/slack/schema", headers=auth_headers
        )
        assert resp.json()["oauth_enabled"] is True


def test_supported_platforms_constant():
    assert set(SUPPORTED_PLATFORMS) == {"slack", "discord"}
