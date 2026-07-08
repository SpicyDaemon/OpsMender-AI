"""Tests for the Teams bot-activity endpoint (Sprint 37 step 4)."""

from __future__ import annotations

import time
import uuid
from contextlib import asynccontextmanager

import httpx
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from jose import jwt as jose_jwt
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from backend.api.app import create_app
from backend.api.deps import get_db, set_mcp_pool, set_session_factory
from backend.auth import bot_framework
from backend.config_loader import set_env_path
from backend.db.models import Base, Incident, Organization
from backend.db.repos import (
    BotConnectorRepo,
    BotUserLinkRepo,
    IncidentRepo,
    NativeActionInvocationRepo,
    UserRepo,
)
from backend.paging.teams_cards import (
    ACTION_ACK,
    ACTION_START_AI_SESSION,
    ACTION_RESOLVE,
    ACTION_VIEW,
)


TEST_ORG_ID = uuid.UUID("00000000-0000-0000-0000-000000000bbb")
BOT_APP_ID = "11111111-1111-1111-1111-111111111111"


# ---------------------------------------------------------------------------
# Test JWT key material — generated once per session.
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def signing_key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def _public_jwk(private_key, kid: str = "test-kid") -> dict:
    public_numbers = private_key.public_key().public_numbers()

    def _b64u_int(n: int) -> str:
        import base64

        b = n.to_bytes((n.bit_length() + 7) // 8, "big")
        return base64.urlsafe_b64encode(b).rstrip(b"=").decode("ascii")

    return {
        "kty": "RSA",
        "use": "sig",
        "alg": "RS256",
        "kid": kid,
        "n": _b64u_int(public_numbers.n),
        "e": _b64u_int(public_numbers.e),
    }


def _sign_jwt(
    private_key,
    *,
    audience: str = BOT_APP_ID,
    issuer: str = bot_framework.BOT_FRAMEWORK_ISSUER,
    kid: str = "test-kid",
    expires_in: int = 600,
) -> str:
    pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    now = int(time.time())
    claims = {
        "iss": issuer,
        "aud": audience,
        "iat": now,
        "exp": now + expires_in,
        "appid": audience,
    }
    return jose_jwt.encode(
        claims, pem, algorithm="RS256", headers={"kid": kid}
    )


@pytest.fixture
def jwks_transport_factory(signing_key):
    """Returns a callable that builds an `httpx.MockTransport` serving
    the Bot Framework OpenID config and JWKS using ``signing_key``'s
    public counterpart."""

    def make():
        def handler(request: httpx.Request) -> httpx.Response:
            path = request.url.path
            if path.endswith("/openidconfiguration"):
                return httpx.Response(
                    200,
                    json={
                        "issuer": bot_framework.BOT_FRAMEWORK_ISSUER,
                        "jwks_uri": "https://login.botframework.com/v1/keys",
                    },
                )
            if path.endswith("/keys"):
                return httpx.Response(
                    200, json={"keys": [_public_jwk(signing_key)]}
                )
            return httpx.Response(404)

        return httpx.MockTransport(handler)

    return make


# ---------------------------------------------------------------------------
# App fixture
# ---------------------------------------------------------------------------


@pytest.fixture
async def app(tmp_path, jwks_transport_factory, monkeypatch):
    bot_framework.reset_caches()
    transport = jwks_transport_factory()

    def patched_client():
        return httpx.AsyncClient(transport=transport)

    monkeypatch.setattr(
        bot_framework, "_default_http_client", patched_client
    )

    db_path = tmp_path / "teams-paging.db"
    database_url = f"sqlite+aiosqlite:///{db_path}"
    engine = create_async_engine(database_url, echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        session.add(
            Organization(id=TEST_ORG_ID, name="Teams Org", slug="teams-org")
        )
        await session.commit()
    set_session_factory(factory)

    tmp_env = tmp_path / ".env"
    tmp_env.write_text(
        "OPSMENDER_TIER=2\n"
        "OPSMENDER_LOG_LEVEL=INFO\n"
        "OPSMENDER_AUDIT_LOG=./logs/audit.jsonl\n"
        "OPSMENDER_JWT_SECRET=test-secret\n"
        f"OPSMENDER_DATABASE_URL={database_url}\n"
        "OPSMENDER_MCP_SERVERS_JSON=[]\n"
    )
    set_env_path(tmp_env)

    application = create_app()
    application.state.session_factory = factory

    class _Pool:
        async def get_server(self, *a, **kw):
            return object()

        @asynccontextmanager
        async def connect(self, *a, **kw):
            class _S:
                pass

            yield _S()

    set_mcp_pool(_Pool())

    async def _get_db():
        async with factory() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    application.dependency_overrides[get_db] = _get_db
    yield application

    set_env_path(None)
    bot_framework.reset_caches()
    await engine.dispose()


@pytest.fixture
async def client(app):
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as c:
        yield c


async def _seed_teams_connector(app, bot_app_id=BOT_APP_ID):
    async with app.state.session_factory() as db:
        connector = await BotConnectorRepo.create(
            db,
            TEST_ORG_ID,
            name="teams-test",
            platform="teams",
            credentials={
                "tenant_id": "tenant",
                "client_id": bot_app_id,
                "client_secret": "secret",
            },
            config={"bot_app_id": bot_app_id},
            allowed_capabilities=["paging"],
            status="configured",
            is_enabled=True,
            native_actions_enabled=True,
        )
        await db.commit()
        return connector


async def _seed_user_and_link(
    app,
    *,
    connector_id,
    aad_oid="aad-user-1",
    role="operator",
):
    async with app.state.session_factory() as db:
        user = await UserRepo.create(
            db,
            username=f"u-{aad_oid}",
            email=f"{aad_oid}@x.com",
            password_hash="x",
            role=role,
            primary_org_id=TEST_ORG_ID,
        )
        await UserRepo.add_to_organization(
            db,
            user_id=user.id,
            org_id=TEST_ORG_ID,
            role=role,
        )
        link = await BotUserLinkRepo.create(
            db,
            TEST_ORG_ID,
            connector_id=connector_id,
            platform_user_id=aad_oid,
            opsmender_user_id=user.id,
        )
        await db.commit()
        return user, link


async def _seed_incident(app, *, title="boom") -> Incident:
    async with app.state.session_factory() as db:
        incident = await IncidentRepo.create(
            db,
            TEST_ORG_ID,
            title=title,
            description="things broke",
            severity="high",
            priority="P1",
            response_mode="page",
        )
        await db.commit()
        return incident


def _activity_payload(
    *, action: str, incident_id, aad_oid: str = "aad-user-1"
):
    return {
        "id": "activity-1",
        "type": "invoke",
        "from": {"id": "29:abc", "aadObjectId": aad_oid},
        "recipient": {"id": f"28:{BOT_APP_ID}"},
        "conversation": {"id": "19:incident-chat@thread.v2"},
        "value": {"action": action, "incident_id": str(incident_id)},
    }


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestTeamsActivityEndpoint:
    async def test_rejects_missing_authorization(self, client, app):
        await _seed_teams_connector(app)
        incident = await _seed_incident(app)
        body = _activity_payload(action=ACTION_ACK, incident_id=incident.id)
        resp = await client.post("/bot/teams/activity", json=body)
        assert resp.status_code == 403

    async def test_rejects_bad_audience(self, client, app, signing_key):
        await _seed_teams_connector(app)
        incident = await _seed_incident(app)
        body = _activity_payload(action=ACTION_ACK, incident_id=incident.id)
        token = _sign_jwt(signing_key, audience="someone-else")
        resp = await client.post(
            "/bot/teams/activity",
            json=body,
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 403

    async def test_ack_button_routes_through_engine(
        self, client, app, signing_key
    ):
        connector = await _seed_teams_connector(app)
        await _seed_user_and_link(app, connector_id=connector.id)
        incident = await _seed_incident(app, title="ack-me")

        token = _sign_jwt(signing_key)
        body = _activity_payload(action=ACTION_ACK, incident_id=incident.id)
        resp = await client.post(
            "/bot/teams/activity",
            json=body,
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200, resp.text
        text = resp.json()["text"]
        assert "ack-me" in text
        assert "acknowledged" in text or "recorded" in text
        async with app.state.session_factory() as db:
            reloaded = await BotConnectorRepo.get_by_id(
                db, TEST_ORG_ID, connector.id
            )
            assert reloaded.callback_status == "verified"

    async def test_unlinked_user_gets_friendly_reply(
        self, client, app, signing_key
    ):
        await _seed_teams_connector(app)
        incident = await _seed_incident(app)
        token = _sign_jwt(signing_key)
        body = _activity_payload(
            action=ACTION_ACK,
            incident_id=incident.id,
            aad_oid="stranger",
        )
        resp = await client.post(
            "/bot/teams/activity",
            json=body,
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        assert "isn't linked" in resp.json()["text"]

    async def test_resolve_marks_incident_resolved(
        self, client, app, signing_key
    ):
        connector = await _seed_teams_connector(app)
        await _seed_user_and_link(app, connector_id=connector.id)
        incident = await _seed_incident(app, title="bye")
        token = _sign_jwt(signing_key)
        body = _activity_payload(
            action=ACTION_RESOLVE, incident_id=incident.id
        )
        resp = await client.post(
            "/bot/teams/activity",
            json=body,
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        async with app.state.session_factory() as db:
            reloaded = await IncidentRepo.get_by_id(
                db, TEST_ORG_ID, incident.id
            )
            assert reloaded.status == "resolved"

    async def test_viewer_cannot_mutate(
        self, client, app, signing_key
    ):
        connector = await _seed_teams_connector(app)
        await _seed_user_and_link(
            app,
            connector_id=connector.id,
            aad_oid="aad-viewer",
            role="viewer",
        )
        incident = await _seed_incident(app)
        body = _activity_payload(
            action=ACTION_RESOLVE,
            incident_id=incident.id,
            aad_oid="aad-viewer",
        )
        resp = await client.post(
            "/bot/teams/activity",
            json=body,
            headers={"Authorization": f"Bearer {_sign_jwt(signing_key)}"},
        )
        assert resp.status_code == 200
        assert "role cannot perform" in resp.json()["text"]
        async with app.state.session_factory() as db:
            reloaded = await IncidentRepo.get_by_id(
                db, TEST_ORG_ID, incident.id
            )
            assert reloaded.status != "resolved"

    async def test_duplicate_activity_is_deduplicated(
        self, client, app, signing_key
    ):
        connector = await _seed_teams_connector(app)
        await _seed_user_and_link(app, connector_id=connector.id)
        incident = await _seed_incident(app)
        body = _activity_payload(
            action=ACTION_START_AI_SESSION,
            incident_id=incident.id,
        )
        headers = {"Authorization": f"Bearer {_sign_jwt(signing_key)}"}
        first = await client.post("/bot/teams/activity", json=body, headers=headers)
        second = await client.post("/bot/teams/activity", json=body, headers=headers)
        assert first.status_code == second.status_code == 200
        async with app.state.session_factory() as db:
            invocation = await NativeActionInvocationRepo.get_by_key(
                db,
                TEST_ORG_ID,
                connector.id,
                "activity-1",
            )
            assert invocation is not None
            assert invocation.status == "applied"

    async def test_view_action_short_circuits(
        self, client, app, signing_key
    ):
        connector = await _seed_teams_connector(app)
        await _seed_user_and_link(app, connector_id=connector.id)
        incident = await _seed_incident(app)
        token = _sign_jwt(signing_key)
        body = _activity_payload(
            action=ACTION_VIEW, incident_id=incident.id
        )
        resp = await client.post(
            "/bot/teams/activity",
            json=body,
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        # View just acks; no state change.
        assert "Opening OpsMender" in resp.json()["text"]


class TestBotFrameworkJWT:
    """Unit tests for the JWT verifier."""

    async def test_missing_authorization_rejected(self, jwks_transport_factory):
        from backend.auth.bot_framework import (
            BotFrameworkAuthError,
            verify_bot_framework_token,
        )

        bot_framework.reset_caches()
        with pytest.raises(BotFrameworkAuthError):
            await verify_bot_framework_token(
                authorization=None,
                expected_audience=BOT_APP_ID,
            )

    async def test_malformed_header_rejected(self, jwks_transport_factory):
        from backend.auth.bot_framework import (
            BotFrameworkAuthError,
            verify_bot_framework_token,
        )

        bot_framework.reset_caches()
        with pytest.raises(BotFrameworkAuthError):
            await verify_bot_framework_token(
                authorization="not-bearer",
                expected_audience=BOT_APP_ID,
            )

    async def test_valid_token_returns_claims(
        self, jwks_transport_factory, signing_key
    ):
        from backend.auth.bot_framework import verify_bot_framework_token

        bot_framework.reset_caches()
        transport = jwks_transport_factory()

        def factory():
            return httpx.AsyncClient(transport=transport)

        token = _sign_jwt(signing_key)
        claims = await verify_bot_framework_token(
            authorization=f"Bearer {token}",
            expected_audience=BOT_APP_ID,
            http_client_factory=factory,
        )
        assert claims["aud"] == BOT_APP_ID
        assert claims["iss"] == bot_framework.BOT_FRAMEWORK_ISSUER
