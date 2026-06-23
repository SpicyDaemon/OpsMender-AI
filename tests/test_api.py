"""Tests for the FastAPI REST layer — Sprint 8.

Uses in-memory SQLite via aiosqlite so no Postgres is needed.
Tests the full API surface: auth, incidents, sessions, audit, config.
"""

from __future__ import annotations

import asyncio
import uuid

TEST_ORG_ID = uuid.UUID("00000000-0000-0000-0000-000000000000")
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
import json

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from backend.api.app import create_app
from backend.api.deps import get_db, set_session_factory
from backend.config_loader import set_env_path
from backend.db.models import Base
from backend.db.repos import (
    AgentTeamProfileRepo,
    ApprovalRequestRepo,
    AuditEntryRepo,
    BotConnectorRepo,
    EscalationChainRepo,
    EscalationStepRepo,
    IncidentAssignmentRepo,
    IncidentChainStateRepo,
    IncidentPageRepo,
    IncidentRepo,
    IngestLogRepo,
    IngestTokenRepo,
    MCPServerRepo,
    ModelConfigRepo,
    OrgEmailSettingsRepo,
    ServiceRepo,
    ServiceEscalationChainRepo,
    SessionMessageRepo,
    SessionRepo,
    SkillRepo,
    TeamRepo,
    UserRepo,
    WorkflowProfileRepo,
)
from backend.mcp.oauth import (
    AuthzServerMetadata,
    ClientRegistration,
    PKCEPair,
    ProtectedResourceMetadata,
    TokenResponse,
    sign_state,
)

SKILL_MD = """---
version: "1"
environment: api-test
operations:
  - tool: kubectl_get_pods
    classification: safe
---
"""

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
async def app(tmp_path):
    """Create a FastAPI app wired to an in-memory SQLite DB."""
    engine = create_async_engine("sqlite+aiosqlite://", echo=False)

    # Create all tables
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    factory = async_sessionmaker(engine, expire_on_commit=False)
    set_session_factory(factory)

    # Multi-tenancy: Ensure the default test organization exists
    async with factory() as db:
        from backend.db.models import Organization

        org = Organization(id=TEST_ORG_ID, name="Test Org", slug="test-org")
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

    # Override the DB dependency to use our in-memory factory
    application.dependency_overrides[get_db] = _override_get_db(factory)

    yield application

    set_env_path(None)
    # Mirror the lifespan shutdown: cancel any in-flight session / background
    # workflow tasks before disposing the engine. Tests don't run the app
    # lifespan, so without this an orphaned Tier 0 workflow task can touch the
    # DB after it's closed ("Cannot operate on a closed database").
    pending = list(getattr(application.state, "session_tasks", set())) + list(
        getattr(application.state, "background_tasks", set())
    )
    for task in pending:
        task.cancel()
    if pending:
        await asyncio.gather(*pending, return_exceptions=True)
    await engine.dispose()


def _override_get_db(factory):
    async def _get_db():
        async with factory() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    return _get_db


@pytest.fixture
async def client(app):
    """Async HTTP client bound to the test app."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest.fixture
async def auth_headers(client: AsyncClient, app) -> dict[str, str]:
    """Register + login a user and return auth headers."""
    await client.post(
        "/auth/register",
        json={
            "username": "testadmin",
            "email": "admin@test.com",
            "password": "securepass123",
        },
    )
    # Manually assign primary_org_id to satisfy tenant isolation requirements
    from backend.db.repos import UserRepo

    async with app.state.session_factory() as db:
        user = await UserRepo.get_by_username(db, "testadmin")
        if user:
            user.primary_org_id = TEST_ORG_ID
            await db.commit()

    resp = await client.post(
        "/auth/login",
        json={
            "username": "testadmin",
            "password": "securepass123",
        },
    )
    token = resp.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
async def viewer_headers(client: AsyncClient, app, auth_headers) -> dict[str, str]:
    """Register a viewer user and return auth headers."""
    await client.post(
        "/auth/register",
        json={
            "username": "viewer1",
            "email": "viewer@test.com",
            "password": "viewerpass123",
            "role": "viewer",
        },
    )
    # Manually assign primary_org_id to satisfy tenant isolation requirements
    from backend.db.repos import UserRepo

    async with app.state.session_factory() as db:
        user = await UserRepo.get_by_username(db, "viewer1")
        if user:
            user.primary_org_id = TEST_ORG_ID
            await db.commit()

    resp = await client.post(
        "/auth/login",
        json={
            "username": "viewer1",
            "password": "viewerpass123",
        },
    )
    token = resp.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


async def _create_approval_request(
    app, *, tier: int = 1, expires_delta_minutes: int = 15
):
    factory = app.state.session_factory
    async with factory() as db:
        session = await SessionRepo.create(db, TEST_ORG_ID, tier=tier)
        request = await ApprovalRequestRepo.create(
            db,
            TEST_ORG_ID,
            session_id=session.id,
            action={"tool_name": "delete_pod", "tool_parameters": {"pod": "api"}},
            justification="Pod is causing the incident",
            expires_at=datetime.now(timezone.utc)
            + timedelta(minutes=expires_delta_minutes),
        )
        await db.commit()
        await db.refresh(session)
        await db.refresh(request)
        return session, request


async def _enable_mfa(client: AsyncClient, headers: dict[str, str]):
    import pyotp

    setup = await client.post("/auth/mfa/setup", headers=headers)
    assert setup.status_code == 200
    setup_data = setup.json()
    code = pyotp.TOTP(setup_data["secret"]).now()
    confirm = await client.post(
        "/auth/mfa/confirm",
        headers=headers,
        json={"totp_code": code},
    )
    assert confirm.status_code == 200
    return setup_data, confirm.json()["recovery_codes"]


async def _ack_incident(
    client: AsyncClient, incident_id: str, headers: dict[str, str]
) -> None:
    """Acknowledge an incident so a Tier 1/2 AI session may be started.

    The ACK gate (sessions route) requires an active assignment before a
    Tier 1/2 session linked to an incident can start.
    """
    resp = await client.post(
        f"/incidents/{incident_id}/ack", json={"via": "web_ui"}, headers=headers
    )
    assert resp.status_code == 200, resp.text


async def _wait_for_session_status(
    client: AsyncClient,
    session_id: str,
    headers: dict[str, str],
    *,
    statuses: set[str],
    timeout_seconds: float = 2.0,
):
    deadline = asyncio.get_running_loop().time() + timeout_seconds
    while True:
        resp = await client.get(f"/sessions/{session_id}", headers=headers)
        assert resp.status_code == 200
        data = resp.json()
        if data["status"] in statuses:
            return data
        if asyncio.get_running_loop().time() >= deadline:
            raise AssertionError(
                f"Session {session_id} did not reach {sorted(statuses)} within {timeout_seconds}s; "
                f"last status was {data['status']}"
            )
        await asyncio.sleep(0.05)


# ===========================================================================
# Health
# ===========================================================================


class TestHealth:
    async def test_health(self, client: AsyncClient):
        resp = await client.get("/health")
        assert resp.status_code == 200
        assert resp.json() == {"status": "ok"}


# ===========================================================================
# Auth
# ===========================================================================


class TestAuth:
    async def test_register_with_email_only_derives_username(
        self, client: AsyncClient
    ):
        first = await client.post(
            "/auth/register",
            json={"email": "person@example.com", "password": "password123"},
        )
        second = await client.post(
            "/auth/register",
            json={"email": "person@elsewhere.com", "password": "password123"},
        )

        assert first.status_code == 201
        assert first.json()["username"] == "person"
        assert second.status_code == 201
        assert second.json()["username"] == "person-2"

    async def test_sso_hint_resolves_email_domain(self, client: AsyncClient, app):
        from backend.db.repos import OrganizationDomainRepo, OrgSSOConfigRepo

        async with app.state.session_factory() as db:
            await OrganizationDomainRepo.create(
                db,
                org_id=TEST_ORG_ID,
                domain="acme.example",
            )
            await OrgSSOConfigRepo.upsert(
                db,
                org_id=TEST_ORG_ID,
                provider="oidc",
                discovery_url="https://id.acme.example/.well-known/openid-configuration",
                client_id="client",
                client_secret_encrypted="encrypted",
            )
            await db.commit()

        response = await client.post(
            "/auth/sso-hint",
            json={"email": "operator@acme.example"},
        )

        assert response.status_code == 200
        assert response.json() == {
            "provider": "oidc",
            "label": "Continue with Test Org SSO",
            "login_path": "/auth/sso/test-org/login",
            "org_slug": "test-org",
        }

    async def test_register_first_user_is_admin(self, client: AsyncClient):
        resp = await client.post(
            "/auth/register",
            json={
                "username": "first",
                "email": "first@test.com",
                "password": "password123",
            },
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["username"] == "first"
        assert data["auth_source"] == "local"
        assert data["role"] == "admin"  # first user auto-admin

    async def test_register_second_user_uses_given_role(self, client: AsyncClient):
        # First user (becomes admin)
        await client.post(
            "/auth/register",
            json={
                "username": "admin1",
                "email": "a1@test.com",
                "password": "password123",
            },
        )
        # Second user (viewer by default)
        resp = await client.post(
            "/auth/register",
            json={
                "username": "user2",
                "email": "u2@test.com",
                "password": "password123",
            },
        )
        assert resp.status_code == 201
        assert resp.json()["role"] == "viewer"

    async def test_register_duplicate_username(self, client: AsyncClient):
        await client.post(
            "/auth/register",
            json={
                "username": "dupuser",
                "email": "dup1@test.com",
                "password": "password123",
            },
        )
        resp = await client.post(
            "/auth/register",
            json={
                "username": "dupuser",
                "email": "dup2@test.com",
                "password": "password123",
            },
        )
        assert resp.status_code == 409

    async def test_register_duplicate_email(self, client: AsyncClient):
        await client.post(
            "/auth/register",
            json={
                "username": "emaildup1",
                "email": "same@test.com",
                "password": "password123",
            },
        )
        resp = await client.post(
            "/auth/register",
            json={
                "username": "emaildup2",
                "email": "same@test.com",
                "password": "password123",
            },
        )
        assert resp.status_code == 409

    async def test_login_success(self, client: AsyncClient):
        await client.post(
            "/auth/register",
            json={
                "username": "logintest",
                "email": "lt@test.com",
                "password": "password123",
            },
        )
        resp = await client.post(
            "/auth/login",
            json={
                "username": "logintest",
                "password": "password123",
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "access_token" in data
        assert data["token_type"] == "bearer"

    async def test_list_users(self, client: AsyncClient, auth_headers: dict[str, str]):
        # auth_headers creates one admin user. Register another.
        await client.post(
            "/auth/register",
            json={
                "username": "other",
                "email": "other@test.com",
                "password": "password123",
                "role": "viewer",
            },
        )
        resp = await client.get("/auth/users", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 2
        assert len(data["items"]) == 2
        usernames = {u["username"] for u in data["items"]}
        assert "other" in usernames
        assert all(item["auth_source"] == "local" for item in data["items"])

    async def test_login_with_email(self, client: AsyncClient):
        """Login route accepts email in the username field."""
        await client.post(
            "/auth/register",
            json={
                "username": "emaillogin",
                "email": "emaillogin@test.com",
                "password": "password123",
            },
        )
        resp = await client.post(
            "/auth/login",
            json={
                "username": "emaillogin@test.com",
                "password": "password123",
            },
        )
        assert resp.status_code == 200
        assert "access_token" in resp.json()

    async def test_login_with_uppercase_email(self, client: AsyncClient):
        """Email lookup falls back to lowercase when as-is misses."""
        await client.post(
            "/auth/register",
            json={
                "username": "caselogin",
                "email": "caselogin@test.com",
                "password": "password123",
            },
        )
        resp = await client.post(
            "/auth/login",
            json={
                "username": "CaseLogin@TEST.com",
                "password": "password123",
            },
        )
        assert resp.status_code == 200

    async def test_login_wrong_password(self, client: AsyncClient):
        await client.post(
            "/auth/register",
            json={
                "username": "wrongpw",
                "email": "wp@test.com",
                "password": "password123",
            },
        )
        resp = await client.post(
            "/auth/login",
            json={
                "username": "wrongpw",
                "password": "wrongpassword",
            },
        )
        assert resp.status_code == 401

    async def test_login_nonexistent_user(self, client: AsyncClient):
        resp = await client.post(
            "/auth/login",
            json={
                "username": "nobody",
                "password": "password123",
            },
        )
        assert resp.status_code == 401

    async def test_me_authenticated(self, client: AsyncClient, auth_headers):
        resp = await client.get("/auth/me", headers=auth_headers)
        assert resp.status_code == 200
        assert resp.json()["username"] == "testadmin"

    async def test_me_unauthenticated(self, client: AsyncClient):
        resp = await client.get("/auth/me")
        assert resp.status_code == 401

    async def test_me_invalid_token(self, client: AsyncClient):
        resp = await client.get(
            "/auth/me", headers={"Authorization": "Bearer invalid-token"}
        )
        assert resp.status_code == 401


class TestSessionDuration:
    """v1 browser session = 7 days (604800s). See OPSMENDER_JWT_EXPIRE_MINUTES."""

    SEVEN_DAYS_SECONDS = 7 * 24 * 60 * 60  # 604800

    def test_default_session_ttl_is_seven_days(self):
        """Config default is 7 days = 10080 minutes = 604800 seconds."""
        from backend.config_loader import AuthConfig

        assert AuthConfig().jwt_expire_minutes == 10080
        assert AuthConfig().jwt_expire_minutes * 60 == self.SEVEN_DAYS_SECONDS

    async def test_login_issues_seven_day_token(self, client: AsyncClient):
        """The token minted at login carries an ~7-day exp window."""
        from backend.api.auth import decode_access_token

        await client.post(
            "/auth/register",
            json={
                "username": "ttluser",
                "email": "ttl@test.com",
                "password": "password123",
            },
        )
        resp = await client.post(
            "/auth/login",
            json={"username": "ttluser", "password": "password123"},
        )
        assert resp.status_code == 200
        payload = decode_access_token(resp.json()["access_token"])
        window = payload["exp"] - payload["iat"]
        # Allow a few seconds of clock slack; must be the 7-day window, not 1h.
        assert abs(window - self.SEVEN_DAYS_SECONDS) <= 5

    async def test_me_accepts_token_within_window(self, client: AsyncClient, app):
        """A current-user check succeeds for a token well inside the window."""
        from datetime import timedelta
        from backend.api.auth import create_access_token

        async with app.state.session_factory() as db:
            user = await UserRepo.create(
                db,
                username="withinwindow",
                email="within@test.com",
                password_hash="x",
                role="viewer",
            )
            user.primary_org_id = TEST_ORG_ID
            await db.commit()
            user_id, role = user.id, user.role

        token = create_access_token(user_id, role, expires_delta=timedelta(days=6))
        resp = await client.get(
            "/auth/me", headers={"Authorization": f"Bearer {token}"}
        )
        assert resp.status_code == 200
        assert resp.json()["username"] == "withinwindow"

    async def test_me_rejects_expired_token(self, client: AsyncClient, app):
        """After the window closes the session is rejected (not infinite)."""
        from datetime import timedelta
        from backend.api.auth import create_access_token

        async with app.state.session_factory() as db:
            user = await UserRepo.create(
                db,
                username="expireduser",
                email="expired@test.com",
                password_hash="x",
                role="viewer",
            )
            user.primary_org_id = TEST_ORG_ID
            await db.commit()
            user_id, role = user.id, user.role

        token = create_access_token(user_id, role, expires_delta=timedelta(seconds=-1))
        resp = await client.get(
            "/auth/me", headers={"Authorization": f"Bearer {token}"}
        )
        assert resp.status_code == 401


class TestMFA:
    async def test_enrollment_encrypts_secret_and_returns_recovery_codes(
        self,
        client: AsyncClient,
        app,
        auth_headers: dict[str, str],
    ):
        setup, recovery_codes = await _enable_mfa(client, auth_headers)
        assert setup["otpauth_url"].startswith("otpauth://totp/")
        assert setup["qr_data_url"].startswith("data:image/png;base64,")
        assert len(recovery_codes) == 8

        from backend.db.repos import UserMFARepo

        async with app.state.session_factory() as db:
            user = await UserRepo.get_by_username(db, "testadmin")
            row = await UserMFARepo.get(db, user.id)
            assert row is not None
            assert row.enabled_at is not None
            assert row.totp_secret_encrypted != setup["secret"]
            assert setup["secret"] not in row.totp_secret_encrypted
            assert len(row.recovery_codes) == 8
            assert all(code not in row.recovery_codes for code in recovery_codes)

    async def test_login_requires_mfa_and_totp_exchanges_challenge(
        self,
        client: AsyncClient,
        auth_headers: dict[str, str],
    ):
        import pyotp

        setup, _ = await _enable_mfa(client, auth_headers)
        login = await client.post(
            "/auth/login",
            json={"username": "testadmin", "password": "securepass123"},
        )
        assert login.status_code == 200
        challenge = login.json()
        assert challenge["mfa_required"] is True
        assert challenge["access_token"] is None

        verify = await client.post(
            "/auth/mfa/verify",
            json={
                "mfa_token": challenge["mfa_token"],
                "totp_code": pyotp.TOTP(setup["secret"]).now(),
            },
        )
        assert verify.status_code == 200
        me = await client.get(
            "/auth/me",
            headers={"Authorization": f"Bearer {verify.json()['access_token']}"},
        )
        assert me.status_code == 200
        assert me.json()["mfa_enabled"] is True

    async def test_recovery_code_is_single_use(
        self,
        client: AsyncClient,
        auth_headers: dict[str, str],
    ):
        _, recovery_codes = await _enable_mfa(client, auth_headers)
        login = await client.post(
            "/auth/login",
            json={"username": "testadmin", "password": "securepass123"},
        )
        first = await client.post(
            "/auth/mfa/verify",
            json={
                "mfa_token": login.json()["mfa_token"],
                "recovery_code": recovery_codes[0],
            },
        )
        assert first.status_code == 200

        login_again = await client.post(
            "/auth/login",
            json={"username": "testadmin", "password": "securepass123"},
        )
        reused = await client.post(
            "/auth/mfa/verify",
            json={
                "mfa_token": login_again.json()["mfa_token"],
                "recovery_code": recovery_codes[0],
            },
        )
        assert reused.status_code == 401

    async def test_expired_mfa_token_is_rejected(
        self,
        client: AsyncClient,
        app,
        auth_headers: dict[str, str],
    ):
        import pyotp
        from backend.api.auth import create_mfa_token

        setup, _ = await _enable_mfa(client, auth_headers)
        async with app.state.session_factory() as db:
            user = await UserRepo.get_by_username(db, "testadmin")
            expired = create_mfa_token(
                user.id,
                user.role,
                expires_delta=timedelta(seconds=-1),
            )
        response = await client.post(
            "/auth/mfa/verify",
            json={
                "mfa_token": expired,
                "totp_code": pyotp.TOTP(setup["secret"]).now(),
            },
        )
        assert response.status_code == 401

    async def test_org_policy_marks_login_for_enrollment(
        self,
        client: AsyncClient,
        app,
        auth_headers: dict[str, str],
    ):
        policy = await client.patch(
            "/admin/org/settings",
            headers=auth_headers,
            json={"mfa_required": True},
        )
        assert policy.status_code == 200
        assert policy.json()["mfa_required"] is True

        login = await client.post(
            "/auth/login",
            json={"username": "testadmin", "password": "securepass123"},
        )
        assert login.status_code == 200
        assert login.json()["access_token"]
        assert login.json()["mfa_enrollment_required"] is True

        me = await client.get(
            "/auth/me",
            headers={"Authorization": f"Bearer {login.json()['access_token']}"},
        )
        assert me.status_code == 200
        assert me.json()["mfa_enrollment_required"] is True


class TestTicketSync:
    async def _seed(
        self,
        app,
        *,
        webhook_secret: str = "ticket-secret",
    ):
        from backend.db.repos import IntegrationConnectorRepo, TicketSyncStateRepo

        async with app.state.session_factory() as db:
            incident = await IncidentRepo.create(
                db,
                TEST_ORG_ID,
                title="Ticket-linked outage",
                description="Linked external ticket",
                severity="high",
            )
            connector = await IntegrationConnectorRepo.create(
                db,
                TEST_ORG_ID,
                kind="jira",
                name=f"Jira sync {uuid.uuid4()}",
                base_url="https://tickets.example.test",
                auth_type="pat",
                auth={"token": "api-token", "webhook_secret": webhook_secret},
                config={
                    "ticket_sync_enabled": True,
                    "status_map": {
                        "open": "To Do",
                        "in_progress": "In Progress",
                        "resolved": "Done",
                    },
                },
                is_enabled=True,
            )
            state = await TicketSyncStateRepo.upsert(
                db,
                TEST_ORG_ID,
                connector_id=connector.id,
                incident_id=incident.id,
                external_ticket_id="OPS-42",
                external_ticket_url="https://tickets.example.test/browse/OPS-42",
                status_map=connector.config["status_map"],
            )
            await db.commit()
            return incident.id, connector.id, state.id

    async def test_outbound_push_runs_after_incident_resolve(
        self,
        client: AsyncClient,
        app,
        auth_headers: dict[str, str],
        monkeypatch,
    ):
        from backend.integrations.base import IntegrationResult
        from backend.services import ticket_sync

        incident_id, _, _ = await self._seed(app)
        calls: list[dict] = []

        class FakeAdapter:
            async def safe_invoke(self, action, connector, auth, parameters):
                calls.append(
                    {
                        "action": action,
                        "connector": connector.id,
                        "auth": auth,
                        "parameters": parameters,
                    }
                )
                return IntegrationResult.success(synced=True)

        monkeypatch.setattr(ticket_sync, "get_adapter", lambda kind: FakeAdapter())
        response = await client.patch(
            f"/incidents/{incident_id}",
            headers=auth_headers,
            json={"status": "resolved"},
        )
        assert response.status_code == 200
        for _ in range(100):
            if calls:
                break
            await asyncio.sleep(0.01)
        assert calls
        assert calls[0]["action"] == "sync_status_out"
        assert calls[0]["parameters"] == {
            "ticket_id": "OPS-42",
            "new_status": "Done",
        }

    async def test_signed_inbound_webhook_updates_incident_and_audits_source(
        self,
        client: AsyncClient,
        app,
    ):
        import hashlib
        import hmac

        from backend.db.repos import IncidentCommentRepo, TicketSyncStateRepo

        incident_id, connector_id, _ = await self._seed(app)
        raw = json.dumps(
            {
                "issue": {
                    "key": "OPS-42",
                    "fields": {"status": {"name": "Done"}},
                }
            },
            separators=(",", ":"),
        ).encode()
        signature = "sha256=" + hmac.new(
            b"ticket-secret",
            raw,
            hashlib.sha256,
        ).hexdigest()
        response = await client.post(
            f"/webhooks/ticket-sync/{connector_id}",
            content=raw,
            headers={
                "Content-Type": "application/json",
                "X-Hub-Signature": signature,
            },
        )
        assert response.status_code == 200
        assert response.json()["status"] == "resolved"
        assert response.json()["source"] == "ticket_sync"

        async with app.state.session_factory() as db:
            incident = await IncidentRepo.get_by_id(db, TEST_ORG_ID, incident_id)
            comments = await IncidentCommentRepo.list_for_incident(
                db,
                TEST_ORG_ID,
                incident_id,
            )
            state = await TicketSyncStateRepo.get_by_external_ticket(
                db,
                TEST_ORG_ID,
                connector_id,
                "OPS-42",
            )
            assert incident.status == "resolved"
            assert comments[-1].source == "ticket_sync"
            assert state.sync_direction == "inbound"

    async def test_invalid_ticket_webhook_signature_is_rejected(
        self,
        client: AsyncClient,
        app,
    ):
        _, connector_id, _ = await self._seed(app)
        response = await client.post(
            f"/webhooks/ticket-sync/{connector_id}",
            json={
                "issue": {
                    "key": "OPS-42",
                    "fields": {"status": {"name": "Done"}},
                }
            },
            headers={"X-Hub-Signature": "sha256=invalid"},
        )
        assert response.status_code == 401

    def test_status_mapping_round_trip(self):
        from backend.services.ticket_sync import normalized_status_map, reverse_status

        mapping = normalized_status_map(
            "jira",
            {
                "open": "Backlog",
                "in_progress": "Working",
                "resolved": "Complete",
            },
        )
        assert mapping["resolved"] == "Complete"
        assert reverse_status(mapping, "working") == "in_progress"
        assert reverse_status(mapping, "Complete") == "resolved"


class TestSessionRevocation:
    async def test_deactivated_user_rejected_with_valid_token(
        self, client: AsyncClient, app
    ):
        """A deactivated user can't keep using a still-valid token."""
        from backend.api.auth import create_access_token

        async with app.state.session_factory() as db:
            user = await UserRepo.create(
                db,
                username="deactivated",
                email="deact@test.com",
                password_hash="x",
                role="operator",
            )
            user.primary_org_id = TEST_ORG_ID
            await db.commit()
            user_id, role = user.id, user.role
            # Deactivate AFTER minting the token below would be more realistic,
            # but is_active is checked per-request so order doesn't matter.
            await UserRepo.update_fields(db, user_id, is_active=False)
            await db.commit()

        token = create_access_token(user_id, role)  # full 7-day token
        resp = await client.get(
            "/auth/me", headers={"Authorization": f"Bearer {token}"}
        )
        assert resp.status_code == 401

    async def test_deleted_user_rejected_with_valid_token(
        self, client: AsyncClient, app
    ):
        """A soft-deleted user can't keep using a still-valid token."""
        from backend.api.auth import create_access_token

        async with app.state.session_factory() as db:
            user = await UserRepo.create(
                db,
                username="deleteduser",
                email="del@test.com",
                password_hash="x",
                role="operator",
            )
            user.primary_org_id = TEST_ORG_ID
            await db.commit()
            user_id, role = user.id, user.role
            await UserRepo.soft_delete(db, user_id)
            await db.commit()

        token = create_access_token(user_id, role)  # full 7-day token
        resp = await client.get(
            "/auth/me", headers={"Authorization": f"Bearer {token}"}
        )
        assert resp.status_code == 401


class TestMyOrganizations:
    async def test_list_my_organizations(self, client: AsyncClient, app, auth_headers):
        resp = await client.get("/auth/me/organizations", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] >= 1
        # The fixture's TEST_ORG_ID should be flagged primary
        primary = [o for o in data["items"] if o["is_primary"]]
        assert len(primary) == 1
        assert primary[0]["id"] == str(TEST_ORG_ID)

    async def test_set_primary_org_member(self, client: AsyncClient, app, auth_headers):
        # Create a second org and link the user
        from backend.db.repos import OrganizationRepo, UserRepo

        async with app.state.session_factory() as db:
            org2 = await OrganizationRepo.create(db, name="Second", slug="second-org")
            user = await UserRepo.get_by_username(db, "testadmin")
            await UserRepo.add_to_organization(
                db, user_id=user.id, org_id=org2.id, role="admin"
            )
            await db.commit()
            org2_id = str(org2.id)

        resp = await client.put(f"/auth/me/primary-org/{org2_id}", headers=auth_headers)
        assert resp.status_code == 200
        assert resp.json()["primary_org_id"] == org2_id

    async def test_set_primary_org_non_member_forbidden(
        self, client: AsyncClient, app, auth_headers
    ):
        from backend.db.repos import OrganizationRepo

        async with app.state.session_factory() as db:
            org3 = await OrganizationRepo.create(db, name="Third", slug="third-org")
            await db.commit()
            org3_id = str(org3.id)

        resp = await client.put(f"/auth/me/primary-org/{org3_id}", headers=auth_headers)
        assert resp.status_code == 403

    async def test_x_org_id_header_member(self, client: AsyncClient, app, auth_headers):
        # User is a member of TEST_ORG_ID; explicitly setting it via header
        # should succeed for org-scoped endpoints.
        headers = {**auth_headers, "X-Org-ID": str(TEST_ORG_ID)}
        resp = await client.get("/incidents", headers=headers)
        assert resp.status_code == 200

    async def test_x_org_id_header_non_member_forbidden(
        self, client: AsyncClient, app, auth_headers
    ):
        from backend.db.repos import OrganizationRepo

        async with app.state.session_factory() as db:
            other = await OrganizationRepo.create(db, name="Other", slug="other-org")
            await db.commit()
            other_id = str(other.id)

        headers = {**auth_headers, "X-Org-ID": other_id}
        resp = await client.get("/incidents", headers=headers)
        assert resp.status_code == 403


class TestOrganizationDeletionGuards:
    async def test_delete_last_organization_is_blocked(
        self, client: AsyncClient, auth_headers
    ):
        resp = await client.delete(
            f"/organizations/{TEST_ORG_ID}", headers=auth_headers
        )
        assert resp.status_code == 409
        assert "last organization" in resp.json()["detail"]

    async def test_delete_active_organization_is_blocked(
        self, client: AsyncClient, app, auth_headers
    ):
        from backend.db.repos import OrganizationRepo

        async with app.state.session_factory() as db:
            await OrganizationRepo.create(db, name="Spare", slug="spare")
            await db.commit()

        resp = await client.delete(
            f"/organizations/{TEST_ORG_ID}", headers=auth_headers
        )
        assert resp.status_code == 409
        assert "Switch to another organization" in resp.json()["detail"]

    async def test_delete_non_active_organization_cascades_memberships(
        self, client: AsyncClient, app, auth_headers
    ):
        from sqlalchemy import select

        from backend.db.models import UserOrganization
        from backend.db.repos import OrganizationRepo, UserRepo

        async with app.state.session_factory() as db:
            org = await OrganizationRepo.create(db, name="Delete Me", slug="delete-me")
            user = await UserRepo.get_by_username(db, "testadmin")
            await UserRepo.add_to_organization(
                db, user_id=user.id, org_id=org.id, role="admin"
            )
            await db.commit()
            org_id = org.id

        resp = await client.delete(f"/organizations/{org_id}", headers=auth_headers)
        assert resp.status_code == 204

        async with app.state.session_factory() as db:
            assert await OrganizationRepo.get_by_id(db, org_id) is None
            link = (
                await db.execute(
                    select(UserOrganization).where(UserOrganization.org_id == org_id)
                )
            ).scalar_one_or_none()
            assert link is None


class TestOrganizationUsersAndDomainsRouteSmoke:
    """Sprint 64 regression — Workspace Settings 'Manage Users' /
    'Domains' click crashed the dashboard with a 500. Both routes
    should return 200 with the expected shape so the modals render."""

    async def test_list_organization_users_returns_expected_shape(
        self, client: AsyncClient, auth_headers
    ):
        # The test admin is already bound to TEST_ORG_ID by the fixture,
        # so the join returns at least one row without extra setup.
        resp = await client.get(
            f"/organizations/{TEST_ORG_ID}/users", headers=auth_headers
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert "items" in data
        assert "total" in data
        assert data["total"] >= 1
        for item in data["items"]:
            # The shape the frontend modal reads: user_id, username,
            # email, role, joined_at. Any missing field crashes the
            # DataTable rowKey accessor and bubbles to the global
            # error boundary.
            assert "user_id" in item
            assert "username" in item
            assert "email" in item
            assert "role" in item
            assert "joined_at" in item

    async def test_list_organization_domains_returns_expected_shape(
        self, client: AsyncClient, auth_headers
    ):
        resp = await client.get(
            f"/organizations/{TEST_ORG_ID}/domains", headers=auth_headers
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert "items" in data
        assert "total" in data
        # Empty by default; the route just needs to not 500.
        assert isinstance(data["items"], list)


class TestDomainIsolation:
    async def test_resolve_unknown_host(self, client: AsyncClient):
        resp = await client.get(
            "/tenant/resolve", headers={"Host": "unknown.example.com"}
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["pinned"] is False
        assert data["host"] == "unknown.example.com"

    async def test_admin_can_create_domain(
        self, client: AsyncClient, app, auth_headers
    ):
        resp = await client.post(
            f"/organizations/{TEST_ORG_ID}/domains",
            json={"domain": "Acme.OpsMender.Example.com", "is_primary": True},
            headers=auth_headers,
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["domain"] == "acme.opsmender.example.com"
        assert data["is_primary"] is True

    async def test_create_domain_validates_hostname(
        self, client: AsyncClient, auth_headers
    ):
        resp = await client.post(
            f"/organizations/{TEST_ORG_ID}/domains",
            json={"domain": "not-a-host"},
            headers=auth_headers,
        )
        assert resp.status_code == 400

    async def test_create_domain_conflict(self, client: AsyncClient, auth_headers):
        await client.post(
            f"/organizations/{TEST_ORG_ID}/domains",
            json={"domain": "dup.example.com"},
            headers=auth_headers,
        )
        resp = await client.post(
            f"/organizations/{TEST_ORG_ID}/domains",
            json={"domain": "dup.example.com"},
            headers=auth_headers,
        )
        assert resp.status_code == 409

    async def test_resolve_pinned_host(self, client: AsyncClient, auth_headers):
        await client.post(
            f"/organizations/{TEST_ORG_ID}/domains",
            json={"domain": "acme.example.com"},
            headers=auth_headers,
        )
        resp = await client.get(
            "/tenant/resolve", headers={"Host": "acme.example.com:8080"}
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["pinned"] is True
        assert data["org_id"] == str(TEST_ORG_ID)

    async def test_host_pin_resolves_org_for_authed_member(
        self, client: AsyncClient, auth_headers
    ):
        await client.post(
            f"/organizations/{TEST_ORG_ID}/domains",
            json={"domain": "acme2.example.com"},
            headers=auth_headers,
        )
        # The user is a member of TEST_ORG_ID — pinned host succeeds.
        headers = {**auth_headers, "Host": "acme2.example.com"}
        resp = await client.get("/incidents", headers=headers)
        assert resp.status_code == 200

    async def test_host_pin_blocks_non_member(
        self, client: AsyncClient, app, auth_headers
    ):
        # Create a second org and pin a host to it; admin user is NOT a member.
        from backend.db.repos import OrganizationRepo, UserRepo

        async with app.state.session_factory() as db:
            other = await OrganizationRepo.create(db, name="Other", slug="other2")
            await db.commit()
            other_id = other.id
            # Remove admin from the other org explicitly is not needed;
            # registration only links them to the test org.

        # Pin the host to the other org via direct DB write.
        from backend.db.repos import OrganizationDomainRepo

        async with app.state.session_factory() as db:
            await OrganizationDomainRepo.create(
                db, org_id=other_id, domain="other.example.com"
            )
            await db.commit()

        headers = {**auth_headers, "Host": "other.example.com"}
        resp = await client.get("/incidents", headers=headers)
        assert resp.status_code == 403

    async def test_host_pin_overrides_x_org_id(
        self, client: AsyncClient, app, auth_headers
    ):
        # Create a second org the user IS a member of.
        from backend.db.repos import OrganizationRepo, UserRepo

        async with app.state.session_factory() as db:
            org2 = await OrganizationRepo.create(db, name="Org2", slug="org-2")
            user = await UserRepo.get_by_username(db, "testadmin")
            team = await TeamRepo.create(
                db,
                TEST_ORG_ID,
                name="Host Pin Team",
                slug=f"host-pin-{uuid.uuid4().hex[:6]}",
                created_by=user.id,
            )
            service = await ServiceRepo.create(
                db,
                TEST_ORG_ID,
                team_id=team.id,
                name="Host Pin Service",
                slug=f"host-pin-service-{uuid.uuid4().hex[:6]}",
            )
            await UserRepo.add_to_organization(
                db, user_id=user.id, org_id=org2.id, role="admin"
            )
            await db.commit()
            org2_id = org2.id

        # Pin a host to TEST_ORG_ID. Send X-Org-ID for org2 — host wins, so
        # the request acts on TEST_ORG_ID.
        await client.post(
            f"/organizations/{TEST_ORG_ID}/domains",
            json={"domain": "primary.example.com"},
            headers=auth_headers,
        )

        # Create an incident under TEST_ORG_ID via the host-pinned route.
        resp = await client.post(
            "/incidents",
            json={
                "title": "host-pinned",
                "description": "x",
                "service_id": str(service.id),
            },
            headers={
                **auth_headers,
                "Host": "primary.example.com",
                "X-Org-ID": str(org2_id),
            },
        )
        assert resp.status_code == 201

        # Listing without X-Org-ID, org2 should NOT see this incident.
        resp = await client.get(
            "/incidents", headers={**auth_headers, "X-Org-ID": str(org2_id)}
        )
        assert resp.status_code == 200
        titles = [i["title"] for i in resp.json()["items"]]
        assert "host-pinned" not in titles

    async def test_set_primary_domain(self, client: AsyncClient, auth_headers):
        r1 = await client.post(
            f"/organizations/{TEST_ORG_ID}/domains",
            json={"domain": "first.example.com"},
            headers=auth_headers,
        )
        r2 = await client.post(
            f"/organizations/{TEST_ORG_ID}/domains",
            json={"domain": "second.example.com"},
            headers=auth_headers,
        )
        d2_id = r2.json()["id"]
        resp = await client.post(
            f"/organizations/{TEST_ORG_ID}/domains/{d2_id}/set-primary",
            headers=auth_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["is_primary"] is True

        listing = await client.get(
            f"/organizations/{TEST_ORG_ID}/domains", headers=auth_headers
        )
        primaries = [d for d in listing.json()["items"] if d["is_primary"]]
        assert len(primaries) == 1
        assert primaries[0]["id"] == d2_id

    async def test_delete_domain(self, client: AsyncClient, auth_headers):
        r = await client.post(
            f"/organizations/{TEST_ORG_ID}/domains",
            json={"domain": "doomed.example.com"},
            headers=auth_headers,
        )
        d_id = r.json()["id"]
        resp = await client.delete(
            f"/organizations/{TEST_ORG_ID}/domains/{d_id}",
            headers=auth_headers,
        )
        assert resp.status_code == 204
        # Resolve no longer pins the host.
        resp = await client.get(
            "/tenant/resolve", headers={"Host": "doomed.example.com"}
        )
        assert resp.json()["pinned"] is False


# ===========================================================================
# Incidents
# ===========================================================================


async def _seed_manual_incident_service(app, label: str = "Manual"):
    async with app.state.session_factory() as db:
        suffix = uuid.uuid4().hex[:8]
        team = await TeamRepo.create(
            db,
            TEST_ORG_ID,
            name=f"{label} Team",
            slug=f"{label.lower().replace(' ', '-')}-team-{suffix}",
            created_by=uuid.uuid4(),
        )
        service = await ServiceRepo.create(
            db,
            TEST_ORG_ID,
            team_id=team.id,
            name=f"{label} Service",
            slug=f"{label.lower().replace(' ', '-')}-service-{suffix}",
        )
        await db.commit()
        return service


async def _create_manual_service_via_api(client, headers, label: str = "Manual") -> str:
    suffix = uuid.uuid4().hex[:8]
    team = await client.post(
        "/teams",
        json={
            "name": f"{label} Team",
            "slug": f"{label.lower().replace(' ', '-')}-team-{suffix}",
        },
        headers=headers,
    )
    assert team.status_code == 201, team.text
    service = await client.post(
        "/services",
        json={
            "team_id": team.json()["id"],
            "name": f"{label} Service",
            "slug": f"{label.lower().replace(' ', '-')}-service-{suffix}",
        },
        headers=headers,
    )
    assert service.status_code == 201, service.text
    return service.json()["id"]


class TestIncidents:
    async def test_manual_incident_requires_service(
        self, client: AsyncClient, auth_headers
    ):
        resp = await client.post(
            "/incidents",
            json={"title": "No service", "description": "Must be rejected"},
            headers=auth_headers,
        )
        assert resp.status_code == 400
        assert resp.json()["detail"] == (
            "Manual incidents must be linked to an active service."
        )

    async def test_manual_incident_rejects_inactive_service(
        self, client: AsyncClient, app, auth_headers
    ):
        service = await _seed_manual_incident_service(app, "Inactive")
        async with app.state.session_factory() as db:
            await ServiceRepo.update(
                db,
                TEST_ORG_ID,
                service.id,
                is_active=False,
            )
            await db.commit()

        resp = await client.post(
            "/incidents",
            json={
                "title": "Inactive service",
                "description": "Must be rejected",
                "service_id": str(service.id),
            },
            headers=auth_headers,
        )
        assert resp.status_code == 400
        assert "active service" in resp.json()["detail"]

    async def test_manual_incident_rejects_service_from_another_org(
        self, client: AsyncClient, app, auth_headers
    ):
        from backend.db.repos import OrganizationRepo

        async with app.state.session_factory() as db:
            other = await OrganizationRepo.create(
                db,
                name="Other Service Org",
                slug=f"other-service-{uuid.uuid4().hex[:6]}",
            )
            team = await TeamRepo.create(
                db,
                other.id,
                name="Other Team",
                slug=f"other-team-{uuid.uuid4().hex[:6]}",
                created_by=uuid.uuid4(),
            )
            service = await ServiceRepo.create(
                db,
                other.id,
                team_id=team.id,
                name="Other Service",
                slug=f"other-svc-{uuid.uuid4().hex[:6]}",
            )
            await db.commit()

        resp = await client.post(
            "/incidents",
            json={
                "title": "Cross-org service",
                "description": "Must be rejected",
                "service_id": str(service.id),
            },
            headers=auth_headers,
        )
        assert resp.status_code == 400
        assert "active service" in resp.json()["detail"]

    async def test_create_incident(self, client: AsyncClient, app, auth_headers):
        async with app.state.session_factory() as db:
            team = await TeamRepo.create(
                db,
                TEST_ORG_ID,
                name="Manual Incident Team",
                slug=f"manual-team-{uuid.uuid4().hex[:6]}",
                created_by=uuid.uuid4(),
            )
            service = await ServiceRepo.create(
                db,
                TEST_ORG_ID,
                team_id=team.id,
                name="Manual Incident Service",
                slug=f"manual-service-{uuid.uuid4().hex[:6]}",
            )
            await db.commit()
        resp = await client.post(
            "/incidents",
            json={
                "title": "High CPU on api-server",
                "description": "CPU at 95% for 10 minutes",
                "severity": "high",
                "service_id": str(service.id),
            },
            headers=auth_headers,
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["title"] == "High CPU on api-server"
        assert data["status"] == "open"
        assert data["severity"] == "high"

    @pytest.mark.parametrize("incident_status", ["open", "in_progress", "resolved"])
    async def test_admin_can_permanently_delete_incident_in_any_status(
        self, incident_status, client: AsyncClient, app, auth_headers
    ):
        async with app.state.session_factory() as db:
            incident = await IncidentRepo.create(
                db,
                TEST_ORG_ID,
                title=f"Delete {incident_status}",
                description="Permanent deletion coverage",
                severity="low",
            )
            await IncidentRepo.update_status(
                db,
                TEST_ORG_ID,
                incident.id,
                incident_status,
            )
            await db.commit()
            incident_id = incident.id

        resp = await client.delete(
            f"/incidents/{incident_id}",
            headers=auth_headers,
        )
        assert resp.status_code == 204, resp.text

        async with app.state.session_factory() as db:
            assert (await IncidentRepo.get_by_id(db, TEST_ORG_ID, incident_id)) is None

    async def test_delete_incident_removes_session_history_and_detaches_ingest_log(
        self, client: AsyncClient, app, auth_headers
    ):
        async with app.state.session_factory() as db:
            incident = await IncidentRepo.create(
                db,
                TEST_ORG_ID,
                title="Incident with history",
                description="Delete all operational history",
                severity="high",
            )
            session = await SessionRepo.create(
                db,
                TEST_ORG_ID,
                tier=1,
                incident_id=incident.id,
            )
            audit = await AuditEntryRepo.create(
                db,
                TEST_ORG_ID,
                session_id=session.id,
                tier=1,
                entry_type="session_start",
            )
            approval = await ApprovalRequestRepo.create(
                db,
                TEST_ORG_ID,
                session_id=session.id,
                action={"tool_name": "restart_service"},
                expires_at=datetime.now(timezone.utc) + timedelta(minutes=15),
            )
            message = await SessionMessageRepo.create(
                db,
                TEST_ORG_ID,
                session_id=session.id,
                role="user",
                content="Investigate this incident",
            )
            token = await IngestTokenRepo.create(
                db,
                TEST_ORG_ID,
                name="delete-history",
                provider="generic",
                token_hash="delete-history-hash",
            )
            ingest_log = await IngestLogRepo.create(
                db,
                TEST_ORG_ID,
                ingest_token_id=token.id,
                provider="generic",
                raw_payload={"title": "Incident with history"},
                incident_id=incident.id,
                dedup_action="created",
            )
            await db.commit()
            ids = {
                "incident": incident.id,
                "session": session.id,
                "audit": audit.id,
                "approval": approval.id,
                "message": message.id,
                "ingest_log": ingest_log.id,
            }

        resp = await client.delete(
            f"/incidents/{ids['incident']}",
            headers=auth_headers,
        )
        assert resp.status_code == 204, resp.text

        async with app.state.session_factory() as db:
            assert await SessionRepo.get_by_id(db, TEST_ORG_ID, ids["session"]) is None
            assert (
                await AuditEntryRepo.list_by_session(db, TEST_ORG_ID, ids["session"])
                == []
            )
            assert (
                await ApprovalRequestRepo.get_by_id(db, TEST_ORG_ID, ids["approval"])
                is None
            )
            assert (
                await SessionMessageRepo.get_by_id(db, TEST_ORG_ID, ids["message"])
                is None
            )
            logs = await IngestLogRepo.list_recent(db, TEST_ORG_ID)
            retained = next(row for row in logs if row.id == ids["ingest_log"])
            assert retained.incident_id is None

    async def test_delete_incident_is_admin_only(
        self, client: AsyncClient, app, auth_headers, viewer_headers
    ):
        from backend.api.auth import create_access_token

        async with app.state.session_factory() as db:
            team = await TeamRepo.create(
                db,
                TEST_ORG_ID,
                name="Protected Incident Team",
                slug=f"protected-team-{uuid.uuid4().hex[:6]}",
                created_by=uuid.uuid4(),
            )
            service = await ServiceRepo.create(
                db,
                TEST_ORG_ID,
                team_id=team.id,
                name="Protected Incident Service",
                slug=f"protected-service-{uuid.uuid4().hex[:6]}",
            )
            await db.commit()
        create_resp = await client.post(
            "/incidents",
            json={
                "title": "Protected",
                "description": "Admin only",
                "service_id": str(service.id),
            },
            headers=auth_headers,
        )
        incident_id = create_resp.json()["id"]

        viewer_resp = await client.delete(
            f"/incidents/{incident_id}",
            headers=viewer_headers,
        )
        assert viewer_resp.status_code == 403

        async with app.state.session_factory() as db:
            operator = await UserRepo.create(
                db,
                username="incident-delete-operator",
                email="incident-delete-operator@test.com",
                password_hash="x",
                role="operator",
            )
            operator.primary_org_id = TEST_ORG_ID
            await db.commit()
        operator_token = create_access_token(operator.id, operator.role)
        operator_resp = await client.delete(
            f"/incidents/{incident_id}",
            headers={"Authorization": f"Bearer {operator_token}"},
        )
        assert operator_resp.status_code == 403

    async def test_delete_incident_returns_404(self, client: AsyncClient, auth_headers):
        resp = await client.delete(
            f"/incidents/{uuid.uuid4()}",
            headers=auth_headers,
        )
        assert resp.status_code == 404

    @pytest.mark.parametrize("tier", [1, 2])
    async def test_fire_test_incident_skips_non_t0_auto_start(
        self,
        tier,
        client: AsyncClient,
        app,
        auth_headers,
        monkeypatch,
        caplog,
    ):
        await client.put(
            "/config",
            json={"tier": tier},
            headers=auth_headers,
        )

        async def _unexpected_create(*args, **kwargs):
            raise AssertionError("non-T0 fire test must not create an AI session")

        monkeypatch.setattr(SessionRepo, "create", _unexpected_create)
        caplog.set_level("INFO", logger="backend.api.routes.incidents")

        resp = await client.post(
            "/incidents/fire-test",
            json={},
            headers=auth_headers,
        )
        assert resp.status_code == 201, resp.text
        data = resp.json()
        assert data["incident"]["external_source"] == "opsmender-test"
        assert data["resolved_tier"] == tier
        # T1/T2 defer the AI session — an operator acknowledges, then starts it.
        assert data["auto_start_status"] == "skipped"
        assert data["auto_start_reason"] == "auto_start_deferred_to_ack"
        assert "start the AI session" in data["message"]

        async with app.state.session_factory() as db:
            sessions = await SessionRepo.list_by_incident(
                db,
                TEST_ORG_ID,
                uuid.UUID(data["incident"]["id"]),
            )
            assert sessions == []

    async def test_fire_test_incident_queues_only_allowed_t0_auto_start(
        self, client: AsyncClient, app, auth_headers, monkeypatch
    ):
        # T0 requires an enabled model before it queues a session.
        async with app.state.session_factory() as db:
            await ModelConfigRepo.create(
                db,
                TEST_ORG_ID,
                name=f"t0-default-{uuid.uuid4().hex[:6]}",
                provider="ollama",
                model_id="t0-default-model",
                is_default=True,
            )
            await db.commit()

        await client.put(
            "/config",
            json={"tier": 0},
            headers=auth_headers,
        )
        scheduled = []

        def _capture_schedule(app, *, org_id, incident_id, tier):
            scheduled.append((org_id, incident_id, tier))

        monkeypatch.setattr(
            "backend.api.routes.incidents.schedule_auto_started_session",
            _capture_schedule,
        )

        resp = await client.post(
            "/incidents/fire-test",
            json={},
            headers=auth_headers,
        )
        assert resp.status_code == 201, resp.text
        data = resp.json()
        assert data["resolved_tier"] == 0
        assert data["auto_start_status"] == "queued"
        assert data["auto_start_reason"] is None
        assert "auto-started under T0" in data["message"]
        assert len(scheduled) == 1
        assert scheduled[0][0] == TEST_ORG_ID
        assert str(scheduled[0][1]) == data["incident"]["id"]
        assert scheduled[0][2] == 0

    async def test_create_test_incident_with_service_and_source(
        self, client: AsyncClient, app, auth_headers
    ):
        async with app.state.session_factory() as db:
            team = await TeamRepo.create(
                db,
                TEST_ORG_ID,
                name="Platform",
                slug="platform",
                created_by=uuid.uuid4(),
            )
            service = await ServiceRepo.create(
                db,
                TEST_ORG_ID,
                team_id=team.id,
                name="checkout-api",
                slug="checkout-api",
            )
            await db.commit()
            service_id = service.id

        resp = await client.post(
            "/incidents",
            json={
                "title": "TEST · synthetic alert for checkout-api",
                "description": "Synthetic alert fired from the dashboard.",
                "severity": "high",
                "service_id": str(service_id),
                "external_id": "test-123",
                "external_source": "opsmender-test",
            },
            headers=auth_headers,
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["external_id"] == "test-123"
        assert data["external_source"] == "opsmender-test"
        assert data["service_name"] == "checkout-api"
        assert data["team_name"] == "Platform"

        async with app.state.session_factory() as db:
            incident = await IncidentRepo.get_by_id(
                db, TEST_ORG_ID, uuid.UUID(data["id"])
            )
            assert incident is not None
            assert incident.service_id == service_id
            assert incident.external_source == "opsmender-test"
            assert incident.external_id == "test-123"

    async def test_update_incident_status_severity_and_service(
        self, client: AsyncClient, app, auth_headers
    ):
        async with app.state.session_factory() as db:
            team = await TeamRepo.create(
                db,
                TEST_ORG_ID,
                name="Payments",
                slug="payments",
                created_by=uuid.uuid4(),
            )
            service = await ServiceRepo.create(
                db,
                TEST_ORG_ID,
                team_id=team.id,
                name="billing-api",
                slug="billing-api",
            )
            await db.commit()
            service_id = service.id

        create_resp = await client.post(
            "/incidents",
            json={
                "title": "Misclassified alert",
                "description": "Needs handoff",
                "severity": "medium",
                "service_id": str(service_id),
            },
            headers=auth_headers,
        )
        incident_id = create_resp.json()["id"]

        resp = await client.patch(
            f"/incidents/{incident_id}",
            json={
                "status": "in_progress",
                "severity": "critical",
                "service_id": str(service_id),
                "service_id_set": True,
                "handoff_reason": "Owned by Payments",
            },
            headers=auth_headers,
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["status"] == "in_progress"
        assert data["severity"] == "critical"
        assert data["service_id"] == str(service_id)
        assert data["service_name"] == "billing-api"
        assert data["team_name"] == "Payments"

    async def test_update_incident_service_restarts_handoff_chain(
        self, client: AsyncClient, app, auth_headers
    ):
        async with app.state.session_factory() as db:
            original_team = await TeamRepo.create(
                db,
                TEST_ORG_ID,
                name="Original",
                slug="original-handoff",
                created_by=uuid.uuid4(),
            )
            target_team = await TeamRepo.create(
                db,
                TEST_ORG_ID,
                name="Target",
                slug="target-handoff",
                created_by=uuid.uuid4(),
            )
            original_service = await ServiceRepo.create(
                db,
                TEST_ORG_ID,
                team_id=original_team.id,
                name="original-api",
                slug="original-api-handoff",
            )
            target_service = await ServiceRepo.create(
                db,
                TEST_ORG_ID,
                team_id=target_team.id,
                name="target-api",
                slug="target-api-handoff",
            )
            original_chain = await EscalationChainRepo.create(
                db,
                TEST_ORG_ID,
                team_id=original_team.id,
                name="Original chain",
            )
            target_chain = await EscalationChainRepo.create(
                db,
                TEST_ORG_ID,
                team_id=target_team.id,
                name="Target chain",
            )
            await EscalationStepRepo.create(
                db,
                TEST_ORG_ID,
                chain_id=original_chain.id,
                step_index=0,
                target_type="user",
                target_id=uuid.uuid4(),
            )
            await EscalationStepRepo.create(
                db,
                TEST_ORG_ID,
                chain_id=target_chain.id,
                step_index=0,
                target_type="user",
                target_id=uuid.uuid4(),
            )
            await ServiceEscalationChainRepo.link(
                db,
                TEST_ORG_ID,
                service_id=original_service.id,
                chain_id=original_chain.id,
            )
            await ServiceEscalationChainRepo.link(
                db,
                TEST_ORG_ID,
                service_id=target_service.id,
                chain_id=target_chain.id,
            )
            await db.commit()
            original_service_id = original_service.id
            target_service_id = target_service.id
            target_chain_id = target_chain.id

        create_resp = await client.post(
            "/incidents",
            json={
                "title": "Needs team handoff",
                "description": "Created on original service",
                "severity": "critical",
                "service_id": str(original_service_id),
            },
            headers=auth_headers,
        )
        assert create_resp.status_code == 201, create_resp.text
        incident_id = uuid.UUID(create_resp.json()["id"])
        async with app.state.session_factory() as db:
            incident = await IncidentRepo.get_by_id(db, TEST_ORG_ID, incident_id)
            assert incident is not None
            incident.response_mode = "page"
            incident.priority = "P1"
            await db.commit()

        resp = await client.patch(
            f"/incidents/{incident_id}",
            json={"service_id": str(target_service_id), "service_id_set": True},
            headers=auth_headers,
        )
        assert resp.status_code == 200, resp.text

        async with app.state.session_factory() as db:
            state = await IncidentChainStateRepo.get_for_incident(
                db, TEST_ORG_ID, incident_id
            )
            assert state is not None
            assert state.chain_id == target_chain_id
            assert state.status == "running"

    async def test_update_incident_viewer_forbidden(
        self, client: AsyncClient, viewer_headers
    ):
        resp = await client.patch(
            f"/incidents/{uuid.uuid4()}",
            json={"status": "resolved"},
            headers=viewer_headers,
        )
        assert resp.status_code == 403

    async def test_resolve_does_not_start_session_or_call_model(
        self, client: AsyncClient, app, auth_headers, monkeypatch
    ):
        """v1 perf guard: resolving an incident must not synchronously create
        an AI session, build a model provider, or call MCP.

        AI sessions in v1 start only when an Admin/Operator explicitly starts
        one; lifecycle updates (resolve) stay local and fast. This locks that
        contract so a future change can't reintroduce a blocking model call on
        the resolve path.
        """
        # Tripwires: if the resolve path tries to build a model provider or
        # open an MCP client, fail loudly instead of silently going slow.
        import backend.llm.factory as llm_factory

        def _boom_llm(*args, **kwargs):  # pragma: no cover - must never run
            raise AssertionError("resolve path must not build a model provider")

        monkeypatch.setattr(llm_factory, "create_llm", _boom_llm)

        session_create_calls = 0
        original_session_create = SessionRepo.create

        async def _counting_session_create(*args, **kwargs):
            nonlocal session_create_calls
            session_create_calls += 1
            return await original_session_create(*args, **kwargs)

        monkeypatch.setattr(
            SessionRepo, "create", staticmethod(_counting_session_create)
        )

        service = await _seed_manual_incident_service(app, "Resolve")
        create_resp = await client.post(
            "/incidents",
            json={
                "title": "TEST · synthetic alert",
                "description": "qa",
                "service_id": str(service.id),
            },
            headers=auth_headers,
        )
        incident_id = create_resp.json()["id"]

        resp = await client.patch(
            f"/incidents/{incident_id}",
            json={"status": "resolved", "service_id_set": False},
            headers=auth_headers,
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["status"] == "resolved"

        # No session was created anywhere during create + resolve.
        assert session_create_calls == 0

        # And none exists for the incident — resolve did not auto-start one.
        sessions_resp = await client.get(
            f"/incidents/{incident_id}/sessions", headers=auth_headers
        )
        assert sessions_resp.status_code == 200
        assert sessions_resp.json()["total"] == 0

    async def test_resolve_notification_delivery_is_fire_and_forget(
        self, client: AsyncClient, app, auth_headers, monkeypatch
    ):
        """A slow/failing notification channel must not break or block the
        resolve request — delivery is scheduled fire-and-forget after commit.
        """
        # Enable a connector that wants incident notifications so the resolve
        # transition actually reaches the delivery scheduler.
        async with app.state.session_factory() as db:
            await BotConnectorRepo.create(
                db,
                TEST_ORG_ID,
                name="qa-slack",
                platform="slack",
                allowed_capabilities=["notifications"],
                status="connected",
                is_enabled=True,
            )
            await db.commit()

        # Make the background delivery coroutine blow up. Because it is
        # scheduled (not awaited) in the request path, resolve must still
        # succeed — proving delivery failure can't break incident resolve.
        import backend.bots.notifier as notifier

        async def _failing_delivery(*args, **kwargs):  # pragma: no cover
            raise RuntimeError("channel delivery is down")

        monkeypatch.setattr(notifier, "deliver_incident_event", _failing_delivery)

        service = await _seed_manual_incident_service(app, "Notify")
        create_resp = await client.post(
            "/incidents",
            json={
                "title": "TEST · synthetic alert",
                "description": "qa",
                "service_id": str(service.id),
            },
            headers=auth_headers,
        )
        incident_id = create_resp.json()["id"]

        resp = await client.patch(
            f"/incidents/{incident_id}",
            json={"status": "resolved", "service_id_set": False},
            headers=auth_headers,
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["status"] == "resolved"

    async def test_create_incident_with_missing_service_returns_400(
        self, client: AsyncClient, auth_headers
    ):
        resp = await client.post(
            "/incidents",
            json={
                "title": "Broken service reference",
                "description": "This should fail.",
                "service_id": str(uuid.uuid4()),
            },
            headers=auth_headers,
        )
        assert resp.status_code == 400
        assert resp.json()["detail"] == (
            "Manual incidents must be linked to an active service."
        )

    async def test_create_incident_viewer_forbidden(
        self, client: AsyncClient, viewer_headers
    ):
        resp = await client.post(
            "/incidents",
            json={
                "title": "Blocked",
                "description": "should fail",
            },
            headers=viewer_headers,
        )
        assert resp.status_code == 403

    async def test_list_incidents(self, client: AsyncClient, app, auth_headers):
        service = await _seed_manual_incident_service(app, "List")
        # Create two incidents
        await client.post(
            "/incidents",
            json={
                "title": "Inc1",
                "description": "d1",
                "service_id": str(service.id),
            },
            headers=auth_headers,
        )
        await client.post(
            "/incidents",
            json={
                "title": "Inc2",
                "description": "d2",
                "service_id": str(service.id),
            },
            headers=auth_headers,
        )

        resp = await client.get("/incidents", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 2
        assert len(data["items"]) == 2
        # No sessions yet → no AI-session indicator.
        for item in data["items"]:
            assert item["ai_session_active"] is False
            assert item["ai_session_status"] is None

    async def test_list_incidents_surfaces_ai_session_state(
        self, client: AsyncClient, app, auth_headers
    ):
        service = await _seed_manual_incident_service(app, "AISession")
        resp = await client.post(
            "/incidents",
            json={"title": "Inc", "description": "d", "service_id": str(service.id)},
            headers=auth_headers,
        )
        incident_id = uuid.UUID(resp.json()["id"])

        # An in-progress session wins over an earlier terminal one.
        async with app.state.session_factory() as db:
            old = await SessionRepo.create(
                db, TEST_ORG_ID, tier=0, incident_id=incident_id
            )
            await SessionRepo.set_status(db, TEST_ORG_ID, old.id, status="failed")
            active = await SessionRepo.create(
                db, TEST_ORG_ID, tier=0, incident_id=incident_id
            )
            await SessionRepo.set_status(
                db, TEST_ORG_ID, active.id, status="awaiting_approval"
            )
            await db.commit()

        data = (await client.get("/incidents", headers=auth_headers)).json()
        item = next(i for i in data["items"] if i["id"] == str(incident_id))
        assert item["ai_session_active"] is True
        assert item["ai_session_status"] == "awaiting_approval"

        # When no session is in progress, the latest status is reported.
        async with app.state.session_factory() as db:
            await SessionRepo.set_status(db, TEST_ORG_ID, active.id, status="completed")
            await db.commit()

        data = (await client.get("/incidents", headers=auth_headers)).json()
        item = next(i for i in data["items"] if i["id"] == str(incident_id))
        assert item["ai_session_active"] is False
        assert item["ai_session_status"] == "completed"

    async def test_resolving_incident_stops_in_progress_sessions(
        self, client: AsyncClient, app, auth_headers
    ):
        service = await _seed_manual_incident_service(app, "ResolveStops")
        resp = await client.post(
            "/incidents",
            json={"title": "Inc", "description": "d", "service_id": str(service.id)},
            headers=auth_headers,
        )
        incident_id = uuid.UUID(resp.json()["id"])
        async with app.state.session_factory() as db:
            running = await SessionRepo.create(
                db, TEST_ORG_ID, tier=0, incident_id=incident_id
            )  # defaults to "active"
            terminal = await SessionRepo.create(
                db, TEST_ORG_ID, tier=0, incident_id=incident_id
            )
            await SessionRepo.set_status(
                db, TEST_ORG_ID, terminal.id, status="completed"
            )
            await db.commit()

        patch = await client.patch(
            f"/incidents/{incident_id}",
            json={"status": "resolved"},
            headers=auth_headers,
        )
        assert patch.status_code == 200

        async with app.state.session_factory() as db:
            running_after = await SessionRepo.get_by_id(db, TEST_ORG_ID, running.id)
            terminal_after = await SessionRepo.get_by_id(db, TEST_ORG_ID, terminal.id)
        # The in-progress session is stopped; the already-terminal one is left as-is.
        assert running_after.status == "stopped"
        assert running_after.ended_at is not None
        assert terminal_after.status == "completed"

    async def test_closed_incident_status_is_rejected(
        self, client: AsyncClient, app, auth_headers
    ):
        service = await _seed_manual_incident_service(app, "NoClosedState")
        resp = await client.post(
            "/incidents",
            json={"title": "Inc", "description": "d", "service_id": str(service.id)},
            headers=auth_headers,
        )
        patch = await client.patch(
            f"/incidents/{resp.json()['id']}",
            json={"status": "closed"},
            headers=auth_headers,
        )
        assert patch.status_code == 422

    async def test_bulk_resolve_stops_in_progress_sessions(
        self, client: AsyncClient, app, auth_headers
    ):
        service = await _seed_manual_incident_service(app, "BulkResolveStops")
        resp = await client.post(
            "/incidents",
            json={"title": "Inc", "description": "d", "service_id": str(service.id)},
            headers=auth_headers,
        )
        incident_id = uuid.UUID(resp.json()["id"])
        async with app.state.session_factory() as db:
            running = await SessionRepo.create(
                db, TEST_ORG_ID, tier=0, incident_id=incident_id
            )
            await db.commit()

        bulk = await client.post(
            "/incidents/bulk",
            json={"action": "resolve", "incident_ids": [str(incident_id)]},
            headers=auth_headers,
        )
        assert bulk.status_code == 200

        async with app.state.session_factory() as db:
            running_after = await SessionRepo.get_by_id(db, TEST_ORG_ID, running.id)
        assert running_after.status == "stopped"

    async def test_list_incidents_supports_case_insensitive_query(
        self, client: AsyncClient, app, auth_headers
    ):
        service = await _seed_manual_incident_service(app, "Query")
        await client.post(
            "/incidents",
            json={
                "title": "Database cluster unreachable",
                "description": "Primary node stopped answering health checks",
                "service_id": str(service.id),
            },
            headers=auth_headers,
        )
        await client.post(
            "/incidents",
            json={
                "title": "Cache pressure",
                "description": "Redis memory usage climbing",
                "service_id": str(service.id),
            },
            headers=auth_headers,
        )

        resp = await client.get("/incidents?q=HEALTH", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 1
        assert data["items"][0]["title"] == "Database cluster unreachable"

    async def test_list_incidents_query_composes_with_status_filter(
        self, client: AsyncClient, app, auth_headers
    ):
        service = await _seed_manual_incident_service(app, "Status Query")
        first = await client.post(
            "/incidents",
            json={
                "title": "API latency spike",
                "description": "p95 latency exceeded budget",
                "service_id": str(service.id),
            },
            headers=auth_headers,
        )
        second = await client.post(
            "/incidents",
            json={
                "title": "API latency spike follow-up",
                "description": "Same subsystem, now resolved",
                "service_id": str(service.id),
            },
            headers=auth_headers,
        )
        first_id = uuid.UUID(first.json()["id"])
        second_id = uuid.UUID(second.json()["id"])

        async with app.state.session_factory() as db:
            await IncidentRepo.update_status(db, TEST_ORG_ID, first_id, "resolved")
            await IncidentRepo.update_status(db, TEST_ORG_ID, second_id, "open")
            await db.commit()

        resp = await client.get(
            "/incidents?status=open&q=latency",
            headers=auth_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 1
        assert data["items"][0]["id"] == str(second_id)

    async def test_list_incidents_with_status_filter(
        self, client: AsyncClient, app, auth_headers
    ):
        service = await _seed_manual_incident_service(app, "Status")
        await client.post(
            "/incidents",
            json={
                "title": "Open",
                "description": "d",
                "service_id": str(service.id),
            },
            headers=auth_headers,
        )
        await client.post(
            "/incidents",
            json={
                "title": "Open2",
                "description": "d",
                "service_id": str(service.id),
            },
            headers=auth_headers,
        )

        resp = await client.get("/incidents?status=open", headers=auth_headers)
        assert resp.status_code == 200
        assert resp.json()["total"] == 2

        resp = await client.get("/incidents?status=resolved", headers=auth_headers)
        assert resp.json()["total"] == 0

    async def test_list_incidents_multi_value_status_is_or(
        self, client: AsyncClient, app, auth_headers
    ):
        """Repeated ?status= params are an OR match (multi-select filter)."""
        service = await _seed_manual_incident_service(app, "Multi Status")
        a = await client.post(
            "/incidents",
            json={
                "title": "Stays open",
                "description": "d",
                "service_id": str(service.id),
            },
            headers=auth_headers,
        )
        b = await client.post(
            "/incidents",
            json={
                "title": "Gets resolved",
                "description": "d",
                "service_id": str(service.id),
            },
            headers=auth_headers,
        )
        await client.post(
            "/incidents/bulk",
            json={"action": "resolve", "incident_ids": [b.json()["id"]]},
            headers=auth_headers,
        )

        # Single-status filters still work.
        assert (
            await client.get("/incidents?status=open", headers=auth_headers)
        ).json()["total"] == 1
        assert (
            await client.get("/incidents?status=resolved", headers=auth_headers)
        ).json()["total"] == 1
        # OR across both statuses returns both incidents.
        both = await client.get(
            "/incidents?status=open&status=resolved", headers=auth_headers
        )
        assert both.status_code == 200
        assert both.json()["total"] == 2
        assert a.json()["id"] in {i["id"] for i in both.json()["items"]}

    async def test_list_incidents_filters_by_team_severity_and_source(
        self, client: AsyncClient, app, auth_headers
    ):
        async with app.state.session_factory() as db:
            platform = await TeamRepo.create(
                db,
                TEST_ORG_ID,
                name="Platform",
                slug="platform-list-filter",
                created_by=uuid.uuid4(),
            )
            payments = await TeamRepo.create(
                db,
                TEST_ORG_ID,
                name="Payments",
                slug="payments-list-filter",
                created_by=uuid.uuid4(),
            )
            platform_service = await ServiceRepo.create(
                db,
                TEST_ORG_ID,
                team_id=platform.id,
                name="api",
                slug="api-list-filter",
            )
            payments_service = await ServiceRepo.create(
                db,
                TEST_ORG_ID,
                team_id=payments.id,
                name="billing",
                slug="billing-list-filter",
            )
            await db.commit()
            platform_team_id = platform.id
            platform_service_id = platform_service.id
            payments_service_id = payments_service.id

        await client.post(
            "/incidents",
            json={
                "title": "Platform ingested critical",
                "description": "d",
                "severity": "critical",
                "service_id": str(platform_service_id),
                "external_source": "opsmender-test",
                "external_id": "team-filter-1",
            },
            headers=auth_headers,
        )
        await client.post(
            "/incidents",
            json={
                "title": "Payments manual low",
                "description": "d",
                "severity": "low",
                "service_id": str(payments_service_id),
            },
            headers=auth_headers,
        )

        resp = await client.get(
            f"/incidents?team_id={platform_team_id}&severity=critical&source=ingested",
            headers=auth_headers,
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["total"] == 1
        assert data["items"][0]["title"] == "Platform ingested critical"

    async def test_get_incident(self, client: AsyncClient, app, auth_headers):
        service = await _seed_manual_incident_service(app, "Get")
        create_resp = await client.post(
            "/incidents",
            json={
                "title": "Look me up",
                "description": "d",
                "service_id": str(service.id),
            },
            headers=auth_headers,
        )
        inc_id = create_resp.json()["id"]

        resp = await client.get(f"/incidents/{inc_id}", headers=auth_headers)
        assert resp.status_code == 200
        assert resp.json()["title"] == "Look me up"

    async def test_list_sessions_for_incident(
        self, client: AsyncClient, app, auth_headers
    ):
        service = await _seed_manual_incident_service(app, "Session List")
        incident = await client.post(
            "/incidents",
            json={
                "title": "Timeline target",
                "description": "d",
                "service_id": str(service.id),
            },
            headers=auth_headers,
        )
        incident_id = incident.json()["id"]

        other_incident = await client.post(
            "/incidents",
            json={
                "title": "Other",
                "description": "d",
                "service_id": str(service.id),
            },
            headers=auth_headers,
        )
        other_id = other_incident.json()["id"]

        # Seed session rows directly — this test only exercises the listing
        # endpoint, so we skip the start gates (ack + tier) and the background
        # workflows that POST /sessions would otherwise spawn.
        async with app.state.session_factory() as db:
            await SessionRepo.create(
                db, TEST_ORG_ID, tier=2, incident_id=uuid.UUID(incident_id)
            )
            await SessionRepo.create(
                db, TEST_ORG_ID, tier=1, incident_id=uuid.UUID(incident_id)
            )
            await SessionRepo.create(
                db, TEST_ORG_ID, tier=2, incident_id=uuid.UUID(other_id)
            )
            await db.commit()

        resp = await client.get(
            f"/incidents/{incident_id}/sessions", headers=auth_headers
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 2
        assert {item["tier"] for item in data["items"]} == {1, 2}
        assert all(item["incident_id"] == incident_id for item in data["items"])

    async def test_list_sessions_for_incident_not_found(
        self, client: AsyncClient, auth_headers
    ):
        resp = await client.get(
            f"/incidents/{uuid.uuid4()}/sessions", headers=auth_headers
        )
        assert resp.status_code == 404

    async def test_get_incident_timeline_interleaves_response_tool_and_evidence(
        self, client: AsyncClient, app, auth_headers
    ):
        service = await _seed_manual_incident_service(app, "Timeline")
        incident_resp = await client.post(
            "/incidents",
            json={
                "title": "Timeline target",
                "description": "CPU burn on api",
                "external_source": "cloudwatch",
                "external_id": "alarm-123",
                "service_id": str(service.id),
            },
            headers=auth_headers,
        )
        assert incident_resp.status_code == 201
        incident_id = uuid.UUID(incident_resp.json()["id"])

        async with app.state.session_factory() as db:
            user = await UserRepo.get_by_username(db, "testadmin")
            assert user is not None

            session = await SessionRepo.create(
                db,
                TEST_ORG_ID,
                incident_id=incident_id,
                tier=2,
                model_provider="openai",
                model_id="gpt-test",
            )
            await AuditEntryRepo.create(
                db,
                TEST_ORG_ID,
                session_id=session.id,
                tier=2,
                entry_type="tool_call_start",
                tool_name="kubectl_get_pods",
                tool_parameters={"namespace": "prod"},
            )
            await AuditEntryRepo.create(
                db,
                TEST_ORG_ID,
                session_id=session.id,
                tier=2,
                entry_type="tool_call_end",
                tool_name="kubectl_get_pods",
                result={"content": [{"type": "text", "text": "ok"}], "isError": False},
                duration_ms=42,
            )
            await SkillRepo.create(
                db,
                TEST_ORG_ID,
                name="timeline-skill",
                content_md=SKILL_MD,
            )
            await IncidentAssignmentRepo.assign(
                db,
                TEST_ORG_ID,
                incident_id=incident_id,
                user_id=user.id,
                assigned_by="self_ack",
            )
            await IncidentPageRepo.create(
                db,
                TEST_ORG_ID,
                incident_id=incident_id,
                user_id=user.id,
                step_index=0,
                channel="slack",
                delivery_status="sent",
            )
            token = await IngestTokenRepo.create(
                db,
                TEST_ORG_ID,
                name="timeline-token",
                provider="cloudwatch",
                token_hash="abc123",
            )
            await IngestLogRepo.create(
                db,
                TEST_ORG_ID,
                ingest_token_id=token.id,
                provider="cloudwatch",
                raw_payload={"alarmName": "HighCPU", "state": "ALARM"},
                incident_id=incident_id,
                dedup_action="created",
            )
            await db.commit()

        resp = await client.get(
            f"/incidents/{incident_id}/timeline",
            headers=auth_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] >= 5
        event_types = {item["event_type"] for item in data["items"]}
        assert "incident_opened" in event_types
        assert "session_started" in event_types
        assert "tool_completed" in event_types
        assert "ownership_assigned" in event_types
        assert "escalation_step_fired" in event_types
        assert "alert_evidence" in event_types
        tool_row = next(
            item for item in data["items"] if item["event_type"] == "tool_completed"
        )
        assert tool_row["tool_name"] == "kubectl_get_pods"
        assert tool_row["safety_class"] == "safe"
        assert tool_row["tier_decision"] == "permitted"
        assert tool_row["metadata"] == {"namespace": "prod"}

    async def test_get_incident_not_found(self, client: AsyncClient, auth_headers):
        fake_id = uuid.uuid4()
        resp = await client.get(f"/incidents/{fake_id}", headers=auth_headers)
        assert resp.status_code == 404

    async def test_list_incidents_pagination(self, client: AsyncClient, auth_headers):
        service_id = await _create_manual_service_via_api(
            client, auth_headers, "Pagination"
        )
        for i in range(5):
            await client.post(
                "/incidents",
                json={
                    "title": f"Inc-{i}",
                    "description": "d",
                    "service_id": service_id,
                },
                headers=auth_headers,
            )

        resp = await client.get("/incidents?limit=2&offset=0", headers=auth_headers)
        data = resp.json()
        assert len(data["items"]) == 2
        assert data["total"] == 5


class TestIncidentPostmortem:
    """Sprint 61 Step 4 — GET / PUT /incidents/{id}/postmortem."""

    async def _create_incident(self, client: AsyncClient, auth_headers) -> str:
        service_id = await _create_manual_service_via_api(
            client, auth_headers, "Postmortem"
        )
        resp = await client.post(
            "/incidents",
            json={
                "title": "Postmortem test incident",
                "description": "Seeded for postmortem authoring tests.",
                "service_id": service_id,
            },
            headers=auth_headers,
        )
        assert resp.status_code == 201
        return resp.json()["id"]

    async def test_get_postmortem_returns_template_for_fresh_incident(
        self, client: AsyncClient, auth_headers
    ):
        incident_id = await self._create_incident(client, auth_headers)
        resp = await client.get(
            f"/incidents/{incident_id}/postmortem", headers=auth_headers
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["incident_id"] == incident_id
        assert data["postmortem_md"] is None
        assert data["postmortem_updated_at"] is None
        # Template surfaces the doc's recommended section headings.
        assert "## Summary" in data["template"]
        assert "## Memory candidates" in data["template"]

    async def test_put_postmortem_stores_markdown_and_stamps_updated_at(
        self, client: AsyncClient, auth_headers
    ):
        incident_id = await self._create_incident(client, auth_headers)
        body = "## Summary\nPostgres ran out of disk."
        resp = await client.put(
            f"/incidents/{incident_id}/postmortem",
            json={"postmortem_md": body},
            headers=auth_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["postmortem_md"] == body
        assert data["postmortem_updated_at"] is not None

        # Re-fetch goes through the read path; same data should come back.
        get_resp = await client.get(
            f"/incidents/{incident_id}/postmortem", headers=auth_headers
        )
        assert get_resp.status_code == 200
        assert get_resp.json()["postmortem_md"] == body

    async def test_put_empty_postmortem_clears_stored_value(
        self, client: AsyncClient, auth_headers
    ):
        incident_id = await self._create_incident(client, auth_headers)
        await client.put(
            f"/incidents/{incident_id}/postmortem",
            json={"postmortem_md": "## Summary\nfoo"},
            headers=auth_headers,
        )
        clear_resp = await client.put(
            f"/incidents/{incident_id}/postmortem",
            json={"postmortem_md": "   "},
            headers=auth_headers,
        )
        assert clear_resp.status_code == 200
        data = clear_resp.json()
        assert data["postmortem_md"] is None
        assert data["postmortem_updated_at"] is None

    async def test_put_postmortem_viewer_forbidden(
        self, client: AsyncClient, auth_headers, viewer_headers
    ):
        incident_id = await self._create_incident(client, auth_headers)
        resp = await client.put(
            f"/incidents/{incident_id}/postmortem",
            json={"postmortem_md": "## Summary\nnope"},
            headers=viewer_headers,
        )
        assert resp.status_code == 403

    async def test_get_postmortem_for_missing_incident_returns_404(
        self, client: AsyncClient, auth_headers
    ):
        resp = await client.get(
            f"/incidents/{uuid.uuid4()}/postmortem", headers=auth_headers
        )
        assert resp.status_code == 404

    _PM_WITH_CANDIDATES = (
        "## Summary\nDB ran out of disk.\n\n"
        "## Memory candidates\n"
        "<!-- one bullet per memory -->\n"
        "- Alert on disk > 80% for the primary.\n"
        "- _placeholder_\n"
        "- Vacuum the audit table weekly.\n"
    )

    async def test_candidates_requires_saved_postmortem(
        self, client: AsyncClient, auth_headers
    ):
        incident_id = await self._create_incident(client, auth_headers)
        resp = await client.post(
            f"/incidents/{incident_id}/postmortem/memory-candidates",
            headers=auth_headers,
        )
        assert resp.status_code == 400

    async def test_candidates_create_pending_memories(
        self, client: AsyncClient, auth_headers
    ):
        incident_id = await self._create_incident(client, auth_headers)
        await client.put(
            f"/incidents/{incident_id}/postmortem",
            json={"postmortem_md": self._PM_WITH_CANDIDATES},
            headers=auth_headers,
        )
        resp = await client.post(
            f"/incidents/{incident_id}/postmortem/memory-candidates",
            headers=auth_headers,
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["created"] == 2  # placeholder skipped
        assert body["skipped"] == 0

        # Created memories are immediately available in the normal memory pool.
        listed = await client.get("/memories", headers=auth_headers)
        titles = {m["title"] for m in listed.json()["items"]}
        assert "Alert on disk > 80% for the primary." in titles
        assert "Vacuum the audit table weekly." in titles

    async def test_candidates_are_idempotent(self, client: AsyncClient, auth_headers):
        incident_id = await self._create_incident(client, auth_headers)
        await client.put(
            f"/incidents/{incident_id}/postmortem",
            json={"postmortem_md": self._PM_WITH_CANDIDATES},
            headers=auth_headers,
        )
        first = await client.post(
            f"/incidents/{incident_id}/postmortem/memory-candidates",
            headers=auth_headers,
        )
        assert first.json()["created"] == 2
        # Re-running skips already-created candidates instead of duplicating.
        second = await client.post(
            f"/incidents/{incident_id}/postmortem/memory-candidates",
            headers=auth_headers,
        )
        assert second.json()["created"] == 0
        assert second.json()["skipped"] == 2

    async def test_candidates_viewer_forbidden(
        self, client: AsyncClient, auth_headers, viewer_headers
    ):
        incident_id = await self._create_incident(client, auth_headers)
        await client.put(
            f"/incidents/{incident_id}/postmortem",
            json={"postmortem_md": self._PM_WITH_CANDIDATES},
            headers=auth_headers,
        )
        resp = await client.post(
            f"/incidents/{incident_id}/postmortem/memory-candidates",
            headers=viewer_headers,
        )
        assert resp.status_code == 403


class TestIncidentComments:
    """v1.2 Phase 4 — operator comments on incidents + timeline surfacing."""

    async def _create_incident(self, client: AsyncClient, auth_headers) -> str:
        service_id = await _create_manual_service_via_api(
            client, auth_headers, "Comments"
        )
        resp = await client.post(
            "/incidents",
            json={
                "title": "Comment test incident",
                "description": "x",
                "service_id": service_id,
            },
            headers=auth_headers,
        )
        assert resp.status_code == 201
        return resp.json()["id"]

    async def test_create_and_list_comment(self, client: AsyncClient, auth_headers):
        incident_id = await self._create_incident(client, auth_headers)
        resp = await client.post(
            f"/incidents/{incident_id}/comments",
            json={"body": "Rolling back the deploy now."},
            headers=auth_headers,
        )
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["body"] == "Rolling back the deploy now."
        assert body["author_label"]  # admin username

        listed = await client.get(
            f"/incidents/{incident_id}/comments", headers=auth_headers
        )
        assert listed.status_code == 200
        assert listed.json()["total"] == 1

    async def test_comment_appears_on_timeline(self, client: AsyncClient, auth_headers):
        incident_id = await self._create_incident(client, auth_headers)
        await client.post(
            f"/incidents/{incident_id}/comments",
            json={"body": "Investigating the spike."},
            headers=auth_headers,
        )
        timeline = await client.get(
            f"/incidents/{incident_id}/timeline", headers=auth_headers
        )
        assert timeline.status_code == 200
        comment_items = [i for i in timeline.json()["items"] if i["lane"] == "comment"]
        assert len(comment_items) == 1
        assert comment_items[0]["body"] == "Investigating the spike."

    async def test_viewer_cannot_comment(
        self, client: AsyncClient, auth_headers, viewer_headers
    ):
        incident_id = await self._create_incident(client, auth_headers)
        resp = await client.post(
            f"/incidents/{incident_id}/comments",
            json={"body": "nope"},
            headers=viewer_headers,
        )
        assert resp.status_code == 403

    async def test_delete_comment(self, client: AsyncClient, auth_headers):
        incident_id = await self._create_incident(client, auth_headers)
        created = await client.post(
            f"/incidents/{incident_id}/comments",
            json={"body": "temp"},
            headers=auth_headers,
        )
        comment_id = created.json()["id"]
        deleted = await client.delete(
            f"/incidents/{incident_id}/comments/{comment_id}",
            headers=auth_headers,
        )
        assert deleted.status_code == 204
        listed = await client.get(
            f"/incidents/{incident_id}/comments", headers=auth_headers
        )
        assert listed.json()["total"] == 0

    async def test_comment_on_missing_incident_404(
        self, client: AsyncClient, auth_headers
    ):
        resp = await client.post(
            f"/incidents/{uuid.uuid4()}/comments",
            json={"body": "x"},
            headers=auth_headers,
        )
        assert resp.status_code == 404


class TestIncidentAutoStartPolicy:
    """v1.1 — tier-driven AI session auto-start (T0 on create; T1/T2 on ACK)."""

    async def _seed_service(self, app, *, with_model: bool = True) -> str:
        from backend.db.repos import ServiceRepo, TeamRepo

        async with app.state.session_factory() as db:
            if with_model:
                await ModelConfigRepo.create(
                    db,
                    TEST_ORG_ID,
                    name=f"m-{uuid.uuid4().hex[:6]}",
                    provider="ollama",
                    model_id="m",
                    is_default=True,
                )
            team = await TeamRepo.create(
                db,
                TEST_ORG_ID,
                name=f"t-{uuid.uuid4().hex[:6]}",
                slug=f"t-{uuid.uuid4().hex[:6]}",
            )
            service = await ServiceRepo.create(
                db,
                TEST_ORG_ID,
                team_id=team.id,
                name="svc",
                slug=f"svc-{uuid.uuid4().hex[:6]}",
            )
            await db.commit()
            return str(service.id)

    def _capture_schedule(self, monkeypatch) -> list:
        scheduled: list = []
        monkeypatch.setattr(
            "backend.api.routes.incidents.schedule_auto_started_session",
            lambda app, *, org_id, incident_id, tier: scheduled.append(
                (incident_id, tier)
            ),
        )
        return scheduled

    async def _set_tier(self, client, auth_headers, tier: int) -> None:
        resp = await client.put("/config", json={"tier": tier}, headers=auth_headers)
        assert resp.status_code == 200, resp.text

    async def _create(self, client, auth_headers, service_id: str):
        return await client.post(
            "/incidents",
            json={
                "title": "auto-start test",
                "description": "x",
                "service_id": service_id,
            },
            headers=auth_headers,
        )

    async def test_t0_manual_create_auto_starts(
        self, client, app, auth_headers, monkeypatch
    ):
        service_id = await self._seed_service(app)
        await self._set_tier(client, auth_headers, 0)
        scheduled = self._capture_schedule(monkeypatch)
        resp = await self._create(client, auth_headers, service_id)
        assert resp.status_code == 201, resp.text
        data = resp.json()
        assert data["resolved_tier"] == 0
        assert data["auto_start_status"] == "queued"
        assert "auto-started under T0" in data["auto_start_message"]
        assert len(scheduled) == 1

    @pytest.mark.parametrize("tier", [1, 2])
    async def test_t1_t2_manual_create_defers_to_ack(
        self, tier, client, app, auth_headers, monkeypatch
    ):
        service_id = await self._seed_service(app)
        await self._set_tier(client, auth_headers, tier)
        scheduled = self._capture_schedule(monkeypatch)
        resp = await self._create(client, auth_headers, service_id)
        assert resp.status_code == 201, resp.text
        data = resp.json()
        assert data["resolved_tier"] == tier
        assert data["auto_start_status"] == "skipped"
        assert data["auto_start_reason"] == "auto_start_deferred_to_ack"
        assert "start the AI session" in data["auto_start_message"]
        assert scheduled == []

    @pytest.mark.parametrize("tier", [1, 2])
    async def test_t1_t2_ack_defers_to_manual_start(
        self, tier, client, app, auth_headers, monkeypatch
    ):
        # New model (ACK then separate Start): acknowledging takes ownership but
        # does NOT auto-start the AI session — the operator starts it explicitly.
        service_id = await self._seed_service(app)
        await self._set_tier(client, auth_headers, tier)
        scheduled = self._capture_schedule(monkeypatch)
        created = await self._create(client, auth_headers, service_id)
        incident_id = created.json()["id"]
        assert scheduled == []  # not started on create

        ack = await client.post(
            f"/incidents/{incident_id}/ack",
            json={"via": "web_ui"},
            headers=auth_headers,
        )
        assert ack.status_code == 200, ack.text
        body = ack.json()
        assert body["auto_start_status"] == "skipped"
        assert body["auto_start_reason"] == "auto_start_deferred_to_manual_start"
        assert body["resolved_tier"] == tier
        assert "Start the AI session" in body["auto_start_message"]
        assert scheduled == []  # ACK never auto-starts

    async def test_viewer_cannot_ack(self, client, app, auth_headers, viewer_headers):
        service_id = await self._seed_service(app)
        created = await self._create(client, auth_headers, service_id)
        incident_id = created.json()["id"]
        resp = await client.post(
            f"/incidents/{incident_id}/ack",
            json={"via": "web_ui"},
            headers=viewer_headers,
        )
        assert resp.status_code == 403

    async def test_ack_never_auto_starts_a_session(
        self, client, app, auth_headers, monkeypatch
    ):
        # New model: ACK never auto-starts (deferred to a manual Start), so it
        # also never spawns a duplicate when a session already exists.
        service_id = await self._seed_service(app)
        await self._set_tier(client, auth_headers, 1)
        scheduled = self._capture_schedule(monkeypatch)
        created = await self._create(client, auth_headers, service_id)
        incident_id = created.json()["id"]
        async with app.state.session_factory() as db:
            await SessionRepo.create(
                db, TEST_ORG_ID, tier=1, incident_id=uuid.UUID(incident_id)
            )
            await db.commit()
        ack = await client.post(
            f"/incidents/{incident_id}/ack",
            json={"via": "web_ui"},
            headers=auth_headers,
        )
        assert ack.status_code == 200
        assert ack.json()["auto_start_status"] == "skipped"
        assert ack.json()["auto_start_reason"] == "auto_start_deferred_to_manual_start"
        assert scheduled == []

    async def test_t0_no_model_fails_but_incident_created(
        self, client, app, auth_headers, monkeypatch
    ):
        # No model seeded → T0 auto-start fails, but the incident is still created.
        service_id = await self._seed_service(app, with_model=False)
        await self._set_tier(client, auth_headers, 0)
        scheduled = self._capture_schedule(monkeypatch)
        resp = await self._create(client, auth_headers, service_id)
        assert resp.status_code == 201, resp.text
        data = resp.json()
        assert data["id"]  # creation succeeded
        assert data["auto_start_status"] == "failed"
        assert data["auto_start_reason"] == "no_enabled_model"
        assert "no enabled model" in data["auto_start_message"]
        assert scheduled == []


class TestIncidentBulkActions:
    """Sprint 50 — POST /incidents/bulk."""

    async def _operator_headers(self, app) -> dict[str, str]:
        from backend.api.auth import create_access_token

        async with app.state.session_factory() as db:
            operator = await UserRepo.create(
                db,
                username=f"bulk-op-{uuid.uuid4().hex[:6]}",
                email=f"bulk-op-{uuid.uuid4().hex[:6]}@test.com",
                password_hash="x",
                role="operator",
            )
            operator.primary_org_id = TEST_ORG_ID
            await db.commit()
        return {
            "Authorization": (
                f"Bearer {create_access_token(operator.id, operator.role)}"
            )
        }

    async def _seed_incidents(
        self, client: AsyncClient, auth_headers, count: int
    ) -> list[str]:
        service_id = await _create_manual_service_via_api(client, auth_headers, "Bulk")
        ids: list[str] = []
        for i in range(count):
            resp = await client.post(
                "/incidents",
                json={
                    "title": f"Bulk incident {i}",
                    "description": f"#{i}",
                    "service_id": service_id,
                },
                headers=auth_headers,
            )
            assert resp.status_code == 201
            ids.append(resp.json()["id"])
        return ids

    async def test_bulk_resolve_marks_all_resolved(
        self, client: AsyncClient, auth_headers, app
    ):
        ids = await self._seed_incidents(client, auth_headers, count=3)
        resp = await client.post(
            "/incidents/bulk",
            json={"action": "resolve", "incident_ids": ids},
            headers=auth_headers,
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["action"] == "resolve"
        assert body["succeeded"] == 3
        assert body["failed"] == 0

        # Verify status flipped on each.
        async with app.state.session_factory() as db:
            for inc_id in ids:
                incident = await IncidentRepo.get_by_id(
                    db, TEST_ORG_ID, uuid.UUID(inc_id)
                )
                assert incident.status == "resolved"

    async def test_bulk_acknowledge_assigns_self_and_progresses(
        self, client: AsyncClient, auth_headers, app
    ):
        ids = await self._seed_incidents(client, auth_headers, count=2)
        resp = await client.post(
            "/incidents/bulk",
            json={"action": "acknowledge", "incident_ids": ids},
            headers=auth_headers,
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["succeeded"] == 2

        async with app.state.session_factory() as db:
            for inc_id in ids:
                incident = await IncidentRepo.get_by_id(
                    db, TEST_ORG_ID, uuid.UUID(inc_id)
                )
                # ack flipped open -> in_progress.
                assert incident.status == "in_progress"

    async def test_bulk_resolve_is_atomic_when_an_incident_is_missing(
        self, client: AsyncClient, auth_headers, app
    ):
        ids = await self._seed_incidents(client, auth_headers, count=2)
        ghost = str(uuid.uuid4())
        resp = await client.post(
            "/incidents/bulk",
            json={"action": "resolve", "incident_ids": [ids[0], ghost, ids[1]]},
            headers=auth_headers,
        )
        assert resp.status_code == 404
        async with app.state.session_factory() as db:
            for incident_id in ids:
                incident = await IncidentRepo.get_by_id(
                    db, TEST_ORG_ID, uuid.UUID(incident_id)
                )
                assert incident.status == "open"

    async def test_bulk_resolve_rejects_mixed_states_atomically(
        self, client: AsyncClient, auth_headers, app
    ):
        ids = await self._seed_incidents(client, auth_headers, count=2)
        async with app.state.session_factory() as db:
            await IncidentRepo.update_status(
                db, TEST_ORG_ID, uuid.UUID(ids[1]), "resolved"
            )
            await db.commit()
        resp = await client.post(
            "/incidents/bulk",
            json={"action": "resolve", "incident_ids": ids},
            headers=auth_headers,
        )
        assert resp.status_code == 409
        async with app.state.session_factory() as db:
            first = await IncidentRepo.get_by_id(db, TEST_ORG_ID, uuid.UUID(ids[0]))
            assert first.status == "open"

    async def test_bulk_reopen_requires_all_resolved(
        self, client: AsyncClient, auth_headers, app
    ):
        ids = await self._seed_incidents(client, auth_headers, count=2)
        async with app.state.session_factory() as db:
            for incident_id in ids:
                await IncidentRepo.update_status(
                    db, TEST_ORG_ID, uuid.UUID(incident_id), "resolved"
                )
            await db.commit()
        resp = await client.post(
            "/incidents/bulk",
            json={"action": "reopen", "incident_ids": ids},
            headers=auth_headers,
        )
        assert resp.status_code == 200
        async with app.state.session_factory() as db:
            for incident_id in ids:
                incident = await IncidentRepo.get_by_id(
                    db, TEST_ORG_ID, uuid.UUID(incident_id)
                )
                assert incident.status == "open"

    async def test_operator_lifecycle_actions_require_one_service(
        self, client: AsyncClient, auth_headers, app
    ):
        operator_headers = await self._operator_headers(app)
        same_service_ids = await self._seed_incidents(client, auth_headers, count=2)
        allowed = await client.post(
            "/incidents/bulk",
            json={"action": "resolve", "incident_ids": same_service_ids},
            headers=operator_headers,
        )
        assert allowed.status_code == 200

        first = await self._seed_incidents(client, auth_headers, count=1)
        second = await self._seed_incidents(client, auth_headers, count=1)
        blocked = await client.post(
            "/incidents/bulk",
            json={"action": "resolve", "incident_ids": [first[0], second[0]]},
            headers=operator_headers,
        )
        assert blocked.status_code == 403

    async def test_bulk_delete_is_admin_only(
        self, client: AsyncClient, auth_headers, app
    ):
        operator_headers = await self._operator_headers(app)
        ids = await self._seed_incidents(client, auth_headers, count=2)
        blocked = await client.post(
            "/incidents/bulk",
            json={"action": "delete", "incident_ids": ids},
            headers=operator_headers,
        )
        assert blocked.status_code == 403
        deleted = await client.post(
            "/incidents/bulk",
            json={"action": "delete", "incident_ids": ids},
            headers=auth_headers,
        )
        assert deleted.status_code == 200
        assert deleted.json()["succeeded"] == 2
        async with app.state.session_factory() as db:
            for incident_id in ids:
                assert (
                    await IncidentRepo.get_by_id(
                        db, TEST_ORG_ID, uuid.UUID(incident_id)
                    )
                    is None
                )

    async def test_bulk_reassign_requires_user_id(
        self, client: AsyncClient, auth_headers
    ):
        ids = await self._seed_incidents(client, auth_headers, count=1)
        resp = await client.post(
            "/incidents/bulk",
            json={"action": "reassign", "incident_ids": ids},
            headers=auth_headers,
        )
        assert resp.status_code == 400

    async def test_bulk_rejects_unknown_action(self, client: AsyncClient, auth_headers):
        ids = await self._seed_incidents(client, auth_headers, count=1)
        resp = await client.post(
            "/incidents/bulk",
            json={"action": "frobnicate", "incident_ids": ids},
            headers=auth_headers,
        )
        # Pydantic enum-pattern validation rejects → 422.
        assert resp.status_code in (400, 422)

    async def test_bulk_caps_at_200_ids(self, client: AsyncClient, auth_headers):
        ids = [str(uuid.uuid4()) for _ in range(201)]
        resp = await client.post(
            "/incidents/bulk",
            json={"action": "resolve", "incident_ids": ids},
            headers=auth_headers,
        )
        # Schema-level max_length = 200.
        assert resp.status_code in (400, 422)


# ===========================================================================
# Sessions
# ===========================================================================


class TestSessions:
    async def test_create_session(self, client: AsyncClient, auth_headers):
        resp = await client.post(
            "/sessions",
            json={
                "tier": 2,
            },
            headers=auth_headers,
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["tier"] == 2
        assert data["status"] == "active"

    async def test_create_session_with_incident(
        self, client: AsyncClient, auth_headers
    ):
        service_id = await _create_manual_service_via_api(
            client, auth_headers, "Session"
        )
        inc_resp = await client.post(
            "/incidents",
            json={
                "title": "T",
                "description": "d",
                "service_id": service_id,
            },
            headers=auth_headers,
        )
        inc_id = inc_resp.json()["id"]
        # Tier 1 sessions require the incident to be acknowledged first.
        await _ack_incident(client, inc_id, auth_headers)

        resp = await client.post(
            "/sessions",
            json={
                "incident_id": inc_id,
                "tier": 1,
            },
            headers=auth_headers,
        )
        assert resp.status_code == 201
        assert resp.json()["incident_id"] == inc_id

    async def test_explicit_saved_model_respects_capacity(
        self, client: AsyncClient, app, auth_headers
    ):
        async with app.state.session_factory() as db:
            model = await ModelConfigRepo.create(
                db,
                TEST_ORG_ID,
                name=f"manual-cap-{uuid.uuid4().hex[:6]}",
                provider="ollama",
                model_id=f"manual-cap-{uuid.uuid4().hex[:6]}",
                max_concurrent_sessions=1,
            )
            await SessionRepo.create(
                db,
                TEST_ORG_ID,
                tier=2,
                model_config_id=model.id,
                model_provider=model.provider,
                model_id=model.model_id,
            )
            await db.commit()

        resp = await client.post(
            "/sessions",
            json={
                "tier": 2,
                "model_provider": model.provider,
                "model_id": model.model_id,
            },
            headers=auth_headers,
        )
        assert resp.status_code == 409
        assert "at capacity" in resp.json()["detail"]

    async def test_explicit_saved_model_persists_config_id(
        self, client: AsyncClient, app, auth_headers
    ):
        async with app.state.session_factory() as db:
            model = await ModelConfigRepo.create(
                db,
                TEST_ORG_ID,
                name=f"manual-unlimited-{uuid.uuid4().hex[:6]}",
                provider="ollama",
                model_id=f"manual-unlimited-{uuid.uuid4().hex[:6]}",
                max_concurrent_sessions=0,
            )
            await db.commit()

        resp = await client.post(
            "/sessions",
            json={
                "tier": 2,
                "model_provider": model.provider,
                "model_id": model.model_id,
            },
            headers=auth_headers,
        )
        assert resp.status_code == 201, resp.text
        assert resp.json()["model_config_id"] == str(model.id)

    async def test_manual_queue_can_be_force_started_with_warning_and_audit(
        self, client: AsyncClient, app, auth_headers
    ):
        app.state.workflow_start_delay_seconds = 3600
        async with app.state.session_factory() as db:
            model = await ModelConfigRepo.create(
                db,
                TEST_ORG_ID,
                name=f"force-cap-{uuid.uuid4().hex[:6]}",
                provider="ollama",
                model_id=f"force-cap-{uuid.uuid4().hex[:6]}",
                max_concurrent_sessions=1,
            )
            team = await TeamRepo.create(
                db,
                TEST_ORG_ID,
                name="Force Queue Team",
                slug=f"force-queue-team-{uuid.uuid4().hex[:6]}",
                created_by=uuid.uuid4(),
            )
            service = await ServiceRepo.create(
                db,
                TEST_ORG_ID,
                team_id=team.id,
                name="Force Queue Service",
                slug=f"force-queue-service-{uuid.uuid4().hex[:6]}",
                priority="P0",
                preferred_model_config_ids=[str(model.id)],
            )
            incident = await IncidentRepo.create(
                db,
                TEST_ORG_ID,
                title="Manual force queue",
                description="all models full",
                priority="P0",
                service_id=service.id,
            )
            await SessionRepo.create(
                db,
                TEST_ORG_ID,
                tier=2,
                model_config_id=model.id,
                model_provider=model.provider,
                model_id=model.model_id,
            )
            await db.commit()

        queued = await client.post(
            "/sessions",
            json={"incident_id": str(incident.id), "tier": 0},
            headers=auth_headers,
        )
        assert queued.status_code == 201, queued.text
        assert queued.json()["status"] == "queued"

        forced = await client.post(
            "/sessions",
            json={"incident_id": str(incident.id), "tier": 0, "force": True},
            headers=auth_headers,
        )
        assert forced.status_code == 201, forced.text
        data = forced.json()
        assert data["id"] == queued.json()["id"]
        assert data["status"] == "active"
        assert data["force_started"] is True
        assert "1/1" in data["capacity_warning"]

        async with app.state.session_factory() as db:
            entries = await AuditEntryRepo.list_by_session(
                db, TEST_ORG_ID, uuid.UUID(data["id"])
            )
        assert any(entry.entry_type == "session_force_start" for entry in entries)

    async def test_session_defaults_to_incident_ingestion_model(
        self, client: AsyncClient, app, auth_headers
    ):
        async with app.state.session_factory() as db:
            model = await ModelConfigRepo.create(
                db,
                TEST_ORG_ID,
                name=f"incident-default-{uuid.uuid4().hex[:6]}",
                provider="ollama",
                model_id="incident-default-model",
            )
            team = await TeamRepo.create(
                db,
                TEST_ORG_ID,
                name="Model Session Team",
                slug=f"model-session-team-{uuid.uuid4().hex[:6]}",
                created_by=uuid.uuid4(),
            )
            service = await ServiceRepo.create(
                db,
                TEST_ORG_ID,
                team_id=team.id,
                name="Model Session Service",
                slug=f"model-session-service-{uuid.uuid4().hex[:6]}",
                preferred_model_config_ids=[str(model.id)],
            )
            await db.commit()

        incident = await client.post(
            "/incidents",
            json={
                "title": "Use ingestion model",
                "description": "Session should inherit it",
                "service_id": str(service.id),
            },
            headers=auth_headers,
        )
        assert incident.status_code == 201, incident.text
        assert incident.json()["ingestion_model_config_id"] == str(model.id)
        await _ack_incident(client, incident.json()["id"], auth_headers)

        session = await client.post(
            "/sessions",
            json={"incident_id": incident.json()["id"], "tier": 2},
            headers=auth_headers,
        )
        assert session.status_code == 201, session.text
        assert session.json()["model_provider"] == "ollama"
        assert session.json()["model_id"] == "incident-default-model"

    async def test_create_session_invalid_incident(
        self, client: AsyncClient, auth_headers
    ):
        resp = await client.post(
            "/sessions",
            json={
                "incident_id": str(uuid.uuid4()),
                "tier": 2,
            },
            headers=auth_headers,
        )
        assert resp.status_code == 404

    async def test_create_session_viewer_forbidden(
        self, client: AsyncClient, viewer_headers
    ):
        resp = await client.post(
            "/sessions",
            json={
                "tier": 2,
            },
            headers=viewer_headers,
        )
        assert resp.status_code == 403

    async def test_get_session(self, client: AsyncClient, auth_headers):
        create_resp = await client.post(
            "/sessions",
            json={
                "tier": 2,
            },
            headers=auth_headers,
        )
        sess_id = create_resp.json()["id"]

        resp = await client.get(f"/sessions/{sess_id}", headers=auth_headers)
        assert resp.status_code == 200
        assert resp.json()["tier"] == 2

    async def test_get_session_not_found(self, client: AsyncClient, auth_headers):
        resp = await client.get(f"/sessions/{uuid.uuid4()}", headers=auth_headers)
        assert resp.status_code == 404

    # Sprint 59 Step 1 — GET /sessions powers the Operations Dashboard's
    # Active sessions + Recent failures Attention Queue cards. Coverage
    # for the new route lives here.
    async def test_list_sessions_returns_all_for_org(
        self, client: AsyncClient, auth_headers
    ):
        for tier in (0, 1, 2):
            await client.post(
                "/sessions",
                json={"tier": tier},
                headers=auth_headers,
            )

        resp = await client.get("/sessions", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert "items" in data and "total" in data
        assert data["total"] == len(data["items"])
        assert data["total"] >= 3
        # Most-recent-first: started_at descending.
        starts = [item["started_at"] for item in data["items"]]
        assert starts == sorted(starts, reverse=True)

    async def test_list_sessions_filters_by_status(
        self, client: AsyncClient, auth_headers
    ):
        # Two sessions; both start as "active".
        await client.post(
            "/sessions",
            json={"tier": 2},
            headers=auth_headers,
        )
        await client.post(
            "/sessions",
            json={"tier": 2},
            headers=auth_headers,
        )

        # status_filter=active should return both.
        active = await client.get(
            "/sessions?status_filter=active",
            headers=auth_headers,
        )
        assert active.status_code == 200
        assert active.json()["total"] >= 2
        for item in active.json()["items"]:
            assert item["status"] == "active"

        # status_filter=failed should return none of them.
        failed = await client.get(
            "/sessions?status_filter=failed",
            headers=auth_headers,
        )
        assert failed.status_code == 200
        for item in failed.json()["items"]:
            assert item["status"] == "failed"

    async def test_list_sessions_requires_auth(self, client: AsyncClient):
        resp = await client.get("/sessions")
        # Any 4xx is fine; the route must not be public.
        assert resp.status_code in (401, 403)

    async def test_tier_0_session_includes_time_limit(
        self, client: AsyncClient, auth_headers
    ):
        resp = await client.post(
            "/sessions",
            json={
                "tier": 0,
            },
            headers=auth_headers,
        )
        assert resp.status_code == 201
        assert resp.json()["tier0_max_session_seconds"] == 600

    async def test_create_session_without_incident_does_not_autorun(
        self, client: AsyncClient, app, auth_headers
    ):
        app.state.workflow_start_delay_seconds = 0

        resp = await client.post("/sessions", json={"tier": 2}, headers=auth_headers)
        assert resp.status_code == 201
        session_id = resp.json()["id"]

        await asyncio.sleep(0.15)
        latest = await client.get(f"/sessions/{session_id}", headers=auth_headers)
        assert latest.status_code == 200
        assert latest.json()["status"] == "active"

        async with app.state.session_factory() as db:
            entries = await AuditEntryRepo.list_by_session(
                db, TEST_ORG_ID, uuid.UUID(session_id)
            )
        assert entries == []

    async def test_create_session_with_incident_autoruns_workflow(
        self, client: AsyncClient, app, auth_headers, monkeypatch
    ):
        app.state.workflow_start_delay_seconds = 0
        published: list[tuple[str, dict[str, object]]] = []

        async def _capture_publish(session_id, message):
            published.append((message.type, dict(message.data)))

        monkeypatch.setattr("backend.api.session_runner.publish", _capture_publish)

        service_id = await _create_manual_service_via_api(
            client, auth_headers, "Workflow"
        )
        inc_resp = await client.post(
            "/incidents",
            json={
                "title": "API-launched workflow",
                "description": "pods restarting in production",
                "severity": "high",
                "service_id": service_id,
            },
            headers=auth_headers,
        )
        assert inc_resp.status_code == 201
        inc_id = inc_resp.json()["id"]
        await _ack_incident(client, inc_id, auth_headers)

        resp = await client.post(
            "/sessions",
            json={"incident_id": inc_id, "tier": 2},
            headers=auth_headers,
        )
        assert resp.status_code == 201
        session_id = resp.json()["id"]

        final = await _wait_for_session_status(
            client,
            session_id,
            auth_headers,
            statuses={"completed", "failed", "timed_out"},
        )
        assert final["status"] == "completed"
        assert final["summary"]

        event_types = [event_type for event_type, _ in published]
        assert "node_transition" in event_types
        assert "session_end" in event_types
        assert any(
            event_type == "node_transition" and data.get("node") == "observe"
            for event_type, data in published
        )
        assert any(
            event_type == "node_transition" and data.get("node") == "summarize"
            for event_type, data in published
        )

        async with app.state.session_factory() as db:
            entries = await AuditEntryRepo.list_by_session(
                db, TEST_ORG_ID, uuid.UUID(session_id)
            )
        entry_types = [entry.entry_type for entry in entries]
        assert "session_start" in entry_types
        assert "session_end" in entry_types

    async def test_create_session_tier12_requires_ack(
        self, client: AsyncClient, auth_headers
    ):
        service_id = await _create_manual_service_via_api(
            client, auth_headers, "AckGate"
        )
        inc_resp = await client.post(
            "/incidents",
            json={"title": "needs ack", "description": "d", "service_id": service_id},
            headers=auth_headers,
        )
        inc_id = inc_resp.json()["id"]

        # Tier 2 + incident, not acknowledged → 409.
        resp = await client.post(
            "/sessions",
            json={"incident_id": inc_id, "tier": 2},
            headers=auth_headers,
        )
        assert resp.status_code == 409
        assert "acknowledge" in resp.json()["detail"].lower()

        # After acknowledging, the start succeeds.
        await _ack_incident(client, inc_id, auth_headers)
        ok = await client.post(
            "/sessions",
            json={"incident_id": inc_id, "tier": 2},
            headers=auth_headers,
        )
        assert ok.status_code == 201

    async def test_stop_running_session(self, client: AsyncClient, auth_headers):
        # No-incident session is created "active" without a background workflow.
        created = await client.post("/sessions", json={"tier": 2}, headers=auth_headers)
        sid = created.json()["id"]

        resp = await client.post(f"/sessions/{sid}/stop", headers=auth_headers)
        assert resp.status_code == 200
        assert resp.json()["status"] == "stopped"

        # Stopping an already-stopped session is a conflict.
        again = await client.post(f"/sessions/{sid}/stop", headers=auth_headers)
        assert again.status_code == 409

    async def test_override_session_converts_tier_in_place(
        self, client: AsyncClient, app, auth_headers
    ):
        app.state.workflow_start_delay_seconds = 0
        # A no-incident Tier 0 session (requested_tier wins) created "active".
        created = await client.post("/sessions", json={"tier": 0}, headers=auth_headers)
        sid = created.json()["id"]

        resp = await client.post(
            f"/sessions/{sid}/override", json={"tier": 1}, headers=auth_headers
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["id"] == sid
        assert body["tier"] == 1
        assert body["status"] == "active"

    async def test_override_cannot_increase_autonomy(
        self, client: AsyncClient, auth_headers
    ):
        created = await client.post("/sessions", json={"tier": 1}, headers=auth_headers)
        sid = created.json()["id"]
        # Tier 0 target is rejected by the schema (tier must be 1 or 2) → 422.
        resp = await client.post(
            f"/sessions/{sid}/override", json={"tier": 0}, headers=auth_headers
        )
        assert resp.status_code == 422
        # Tier 1 → Tier 1 (no reduction) is a 400.
        same = await client.post(
            f"/sessions/{sid}/override", json={"tier": 1}, headers=auth_headers
        )
        assert same.status_code == 400


# ===========================================================================
# Audit
# ===========================================================================


class TestAudit:
    async def _seed_audit(self, client, auth_headers):
        """Create a session and seed audit entries directly via DB."""
        # Create session via API
        resp = await client.post(
            "/sessions",
            json={
                "tier": 2,
            },
            headers=auth_headers,
        )
        return resp.json()["id"]

    async def test_list_audit_empty(self, client: AsyncClient, auth_headers):
        resp = await client.get("/audit", headers=auth_headers)
        assert resp.status_code == 200
        assert resp.json()["total"] == 0

    async def test_list_audit_unauthenticated(self, client: AsyncClient):
        resp = await client.get("/audit")
        assert resp.status_code == 401

    async def test_export_audit_csv_headers_and_columns(
        self, client: AsyncClient, auth_headers
    ):
        resp = await client.get("/audit/export.csv", headers=auth_headers)
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/csv")
        assert "attachment" in resp.headers["content-disposition"]
        assert "opsmender-audit.csv" in resp.headers["content-disposition"]
        # Header row is always present even with zero entries.
        first_line = resp.text.splitlines()[0]
        assert first_line.startswith("timestamp,entry_type,tool_name,tier")

    async def test_export_audit_csv_unauthenticated(self, client: AsyncClient):
        resp = await client.get("/audit/export.csv")
        assert resp.status_code == 401


# ===========================================================================
# Config
# ===========================================================================


class TestConfig:
    async def test_get_config(self, client: AsyncClient, auth_headers):
        resp = await client.get("/config", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert "tier" in data
        assert "mcp_servers" in data
        assert "audit_output" in data
        assert "logging_level" in data

    async def test_get_config_exposes_public_base_url(
        self, client: AsyncClient, auth_headers
    ):
        """v1 paging — /config surfaces the configured public base URL so the
        frontend can render a service's full intake URL. The field is always
        present; it is null when OPSMENDER_PUBLIC_BASE_URL is unset (the browser
        then falls back to window.location.origin)."""
        resp = await client.get("/config", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert "public_base_url" in data
        # No env override in the test harness → null fallback.
        assert data["public_base_url"] is None

    async def test_get_config_exposes_simple_by_default_auth_flags(
        self, client: AsyncClient, auth_headers
    ):
        """Sprint 64 Step 1 — surface the four auth visibility booleans.

        Default install has no SSO/SAML configured and no env flags set,
        so every value is False. The frontend's rule for showing
        advanced auth settings is the disjunction of these three:
        ``advanced_auth_enabled || sso_configured || saml_configured``.
        """
        resp = await client.get("/config", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["multi_org_enabled"] is False
        assert data["advanced_auth_enabled"] is False
        assert data["sso_configured"] is False
        assert data["saml_configured"] is False

    async def test_get_config_marks_sso_configured_when_org_row_exists(
        self, client: AsyncClient, app, auth_headers
    ):
        """An org with a saved OIDC config keeps its settings visible
        even when ``advanced_auth_enabled`` stays off — that's the
        explicit Sprint 64 rule so existing providers keep working."""
        from backend.db.repos import OrgSSOConfigRepo

        async with app.state.session_factory() as db:
            await OrgSSOConfigRepo.upsert(
                db,
                org_id=TEST_ORG_ID,
                provider="oidc",
                discovery_url="https://example.test/.well-known/openid-configuration",
                client_id="client-abc",
                client_secret_encrypted="ciphertext::placeholder",
            )
            await db.commit()

        resp = await client.get("/config", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["sso_configured"] is True
        assert data["saml_configured"] is False
        # Env flag stays off — the existing provider is what unlocks
        # the UI; advanced_auth_enabled doesn't flip just because a
        # row exists.
        assert data["advanced_auth_enabled"] is False

    async def test_get_config_marks_saml_configured_when_org_row_exists(
        self, client: AsyncClient, app, auth_headers
    ):
        from backend.db.repos import OrgSAMLConfigRepo

        async with app.state.session_factory() as db:
            await OrgSAMLConfigRepo.upsert(
                db,
                org_id=TEST_ORG_ID,
                idp_metadata_url="https://idp.example.test/metadata",
            )
            await db.commit()

        resp = await client.get("/config", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["saml_configured"] is True
        assert data["sso_configured"] is False


class TestIncidentMemoryAPI:
    """Sprint 45 Step 6 — /memories CRUD + feedback + per-session memories-used."""

    async def _seed_service(self, app) -> uuid.UUID:
        _, service_id = await self._seed_team_service(app)
        return service_id

    async def _seed_team_service(self, app) -> tuple[uuid.UUID, uuid.UUID]:
        from backend.db.repos import ServiceRepo, TeamRepo

        async with app.state.session_factory() as db:
            team = await TeamRepo.create(
                db, TEST_ORG_ID, name="t1", slug=f"t1-{uuid.uuid4().hex[:6]}"
            )
            service = await ServiceRepo.create(
                db,
                TEST_ORG_ID,
                team_id=team.id,
                name="api",
                slug=f"api-{uuid.uuid4().hex[:6]}",
            )
            await db.commit()
            return team.id, service.id

    async def _operator_headers_for_team(
        self, client: AsyncClient, app, team_id: uuid.UUID
    ) -> dict[str, str]:
        from backend.api.auth import create_access_token

        async with app.state.session_factory() as db:
            operator = await UserRepo.create(
                db,
                username=f"memory-op-{uuid.uuid4().hex[:6]}",
                email=f"memory-op-{uuid.uuid4().hex[:6]}@test.com",
                password_hash="x",
                role="operator",
            )
            operator.primary_org_id = TEST_ORG_ID
            await TeamRepo.add_member(db, TEST_ORG_ID, team_id, user_id=operator.id)
            await db.commit()
        token = create_access_token(operator.id, operator.role)
        return {"Authorization": f"Bearer {token}"}

    async def test_list_empty(self, client: AsyncClient, auth_headers):
        resp = await client.get("/memories", headers=auth_headers)
        assert resp.status_code == 200
        body = resp.json()
        assert body == {"items": [], "total": 0}

    async def test_create_and_get(self, client: AsyncClient, auth_headers, app):
        service_id = await self._seed_service(app)
        resp = await client.post(
            "/memories",
            headers=auth_headers,
            json={
                "title": "Pod OOMKilled",
                "summary_md": "Increase memory limits.",
                "tags": ["k8s", "OOM"],
                "service_id": str(service_id),
            },
        )
        assert resp.status_code == 201
        body = resp.json()
        assert body["title"] == "Pod OOMKilled"
        assert body["service_id"] == str(service_id)
        # Tags get lower-cased + trimmed by the route.
        assert body["tags"] == ["k8s", "oom"]
        assert body["helpful_count"] == 0
        assert body["unhelpful_count"] == 0
        assert body["can_edit"] is True
        assert body["can_delete"] is True
        assert "review_status" not in body
        assert "is_hidden" not in body

        memory_id = body["id"]
        get_resp = await client.get(f"/memories/{memory_id}", headers=auth_headers)
        assert get_resp.status_code == 200
        assert get_resp.json()["id"] == memory_id

    async def test_create_rejects_unknown_service(
        self, client: AsyncClient, auth_headers
    ):
        bogus = str(uuid.uuid4())
        resp = await client.post(
            "/memories",
            headers=auth_headers,
            json={
                "title": "x",
                "summary_md": "y",
                "service_id": bogus,
            },
        )
        assert resp.status_code == 400

    async def test_list_filters_by_service(
        self, client: AsyncClient, auth_headers, app
    ):
        service_a = await self._seed_service(app)
        service_b = await self._seed_service(app)
        for svc, title in [(service_a, "A1"), (service_b, "B1")]:
            await client.post(
                "/memories",
                headers=auth_headers,
                json={
                    "title": title,
                    "summary_md": "x",
                    "service_id": str(svc),
                },
            )
        resp = await client.get(
            f"/memories?service_id={service_a}", headers=auth_headers
        )
        assert resp.status_code == 200
        titles = {m["title"] for m in resp.json()["items"]}
        assert titles == {"A1"}

    async def test_update_changes_fields(self, client: AsyncClient, auth_headers, app):
        service_id = await self._seed_service(app)
        create = await client.post(
            "/memories",
            headers=auth_headers,
            json={
                "title": "orig",
                "summary_md": "orig body",
                "service_id": str(service_id),
            },
        )
        memory_id = create.json()["id"]

        resp = await client.put(
            f"/memories/{memory_id}",
            headers=auth_headers,
            json={
                "title": "updated",
                "summary_md": "new body",
                "tags": ["one", "two"],
            },
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["title"] == "updated"
        assert body["summary_md"] == "new body"
        assert body["tags"] == ["one", "two"]
        # service_id stays untouched when service_id_set is omitted/false.
        assert body["service_id"] == str(service_id)

    async def test_feedback_increments_counter(
        self, client: AsyncClient, auth_headers, app
    ):
        service_id = await self._seed_service(app)
        create = await client.post(
            "/memories",
            headers=auth_headers,
            json={
                "title": "x",
                "summary_md": "y",
                "service_id": str(service_id),
            },
        )
        memory_id = create.json()["id"]

        for _ in range(3):
            resp = await client.post(
                f"/memories/{memory_id}/feedback",
                headers=auth_headers,
                json={"helpful": True},
            )
            assert resp.status_code == 200
        resp = await client.post(
            f"/memories/{memory_id}/feedback",
            headers=auth_headers,
            json={"helpful": False},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["helpful_count"] == 3
        assert body["unhelpful_count"] == 1

    async def test_delete_admin_and_viewer_permissions(
        self, client: AsyncClient, auth_headers, viewer_headers, app
    ):
        service_id = await self._seed_service(app)
        create = await client.post(
            "/memories",
            headers=auth_headers,
            json={
                "title": "x",
                "summary_md": "y",
                "service_id": str(service_id),
            },
        )
        memory_id = create.json()["id"]

        # Viewer denied.
        resp = await client.delete(f"/memories/{memory_id}", headers=viewer_headers)
        assert resp.status_code in {401, 403}

        # Admin succeeds with 204.
        resp = await client.delete(f"/memories/{memory_id}", headers=auth_headers)
        assert resp.status_code == 204

        # 404 on subsequent GET.
        get_resp = await client.get(f"/memories/{memory_id}", headers=auth_headers)
        assert get_resp.status_code == 404

    async def test_operator_can_edit_and_delete_own_team_memory(
        self, client: AsyncClient, auth_headers, app
    ):
        team_id, service_id = await self._seed_team_service(app)
        operator_headers = await self._operator_headers_for_team(client, app, team_id)
        created = await client.post(
            "/memories",
            headers=auth_headers,
            json={
                "title": "team memory",
                "summary_md": "x",
                "service_id": str(service_id),
            },
        )
        memory_id = created.json()["id"]

        listed = await client.get("/memories", headers=operator_headers)
        row = next(m for m in listed.json()["items"] if m["id"] == memory_id)
        assert row["can_edit"] is True
        assert row["can_delete"] is True
        edited = await client.put(
            f"/memories/{memory_id}",
            headers=operator_headers,
            json={"title": "updated by operator"},
        )
        assert edited.status_code == 200
        deleted = await client.delete(
            f"/memories/{memory_id}", headers=operator_headers
        )
        assert deleted.status_code == 204

    async def test_operator_cannot_manage_other_team_or_global_memory(
        self, client: AsyncClient, auth_headers, app
    ):
        operator_team, _ = await self._seed_team_service(app)
        _, other_service = await self._seed_team_service(app)
        operator_headers = await self._operator_headers_for_team(
            client, app, operator_team
        )
        assert (
            await client.post(
                "/memories",
                headers=operator_headers,
                json={
                    "title": "blocked create",
                    "summary_md": "x",
                    "service_id": str(other_service),
                },
            )
        ).status_code == 403
        other = await client.post(
            "/memories",
            headers=auth_headers,
            json={
                "title": "other",
                "summary_md": "x",
                "service_id": str(other_service),
            },
        )
        global_memory = await client.post(
            "/memories",
            headers=auth_headers,
            json={"title": "global", "summary_md": "x"},
        )
        listed = await client.get("/memories", headers=operator_headers)
        listed_ids = {item["id"] for item in listed.json()["items"]}
        assert global_memory.json()["id"] in listed_ids
        assert other.json()["id"] not in listed_ids
        assert (
            await client.get(
                f"/memories/{other.json()['id']}", headers=operator_headers
            )
        ).status_code == 404
        global_detail = await client.get(
            f"/memories/{global_memory.json()['id']}",
            headers=operator_headers,
        )
        assert global_detail.status_code == 200
        assert global_detail.json()["can_edit"] is False
        assert global_detail.json()["can_delete"] is False
        for memory_id in (other.json()["id"], global_memory.json()["id"]):
            assert (
                await client.put(
                    f"/memories/{memory_id}",
                    headers=operator_headers,
                    json={"title": "blocked"},
                )
            ).status_code == 403
            assert (
                await client.delete(f"/memories/{memory_id}", headers=operator_headers)
            ).status_code == 403

    async def test_bulk_delete_is_atomic_and_team_scoped(
        self, client: AsyncClient, auth_headers, app
    ):
        team_id, service_id = await self._seed_team_service(app)
        _, other_service_id = await self._seed_team_service(app)
        operator_headers = await self._operator_headers_for_team(client, app, team_id)
        own_ids = []
        for title in ("one", "two"):
            created = await client.post(
                "/memories",
                headers=auth_headers,
                json={
                    "title": title,
                    "summary_md": "x",
                    "service_id": str(service_id),
                },
            )
            own_ids.append(created.json()["id"])
        other = await client.post(
            "/memories",
            headers=auth_headers,
            json={
                "title": "other",
                "summary_md": "x",
                "service_id": str(other_service_id),
            },
        )
        blocked = await client.post(
            "/memories/bulk-delete",
            headers=operator_headers,
            json={"memory_ids": [own_ids[0], other.json()["id"]]},
        )
        assert blocked.status_code == 403
        assert (
            await client.get(f"/memories/{own_ids[0]}", headers=auth_headers)
        ).status_code == 200

        deleted = await client.post(
            "/memories/bulk-delete",
            headers=operator_headers,
            json={"memory_ids": own_ids},
        )
        assert deleted.status_code == 200
        assert deleted.json() == {"deleted": 2}

    async def test_unauthenticated_rejected(self, client: AsyncClient):
        resp = await client.get("/memories")
        assert resp.status_code in {401, 403}

    async def test_session_memories_used(self, client: AsyncClient, auth_headers, app):
        from backend.db.repos import (
            IncidentMemoryRecallLogRepo,
            IncidentMemoryRepo,
            IncidentRepo,
            SessionRepo,
        )

        service_id = await self._seed_service(app)

        # Seed an incident, a session, and a memory + recall log row.
        async with app.state.session_factory() as db:
            incident = await IncidentRepo.create(
                db,
                TEST_ORG_ID,
                title="t",
                description="d",
                severity="high",
                service_id=service_id,
            )
            session = await SessionRepo.create(
                db,
                TEST_ORG_ID,
                incident_id=incident.id,
                tier=2,
            )
            memory = await IncidentMemoryRepo.create(
                db,
                org_id=TEST_ORG_ID,
                service_id=service_id,
                title="surfaced lesson",
                summary_md="be sure to check x",
                tags=["high"],
            )
            await IncidentMemoryRecallLogRepo.record(
                db,
                memory_id=memory.id,
                session_id=session.id,
                score=4.2,
            )
            await db.commit()
            session_id = session.id
            memory_id = memory.id

        resp = await client.get(
            f"/sessions/{session_id}/memories-used", headers=auth_headers
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] == 1
        assert body["items"][0]["memory"]["id"] == str(memory_id)
        assert body["items"][0]["score"] == pytest.approx(4.2)

    async def test_session_memories_used_unknown_session(
        self, client: AsyncClient, auth_headers
    ):
        bogus = str(uuid.uuid4())
        resp = await client.get(
            f"/sessions/{bogus}/memories-used", headers=auth_headers
        )
        assert resp.status_code == 404


class TestRetentionAPI:
    """Sprint 53 — /retention status, config, and run."""

    async def test_status_returns_defaults_for_fresh_org(
        self, client: AsyncClient, auth_headers
    ):
        resp = await client.get("/retention", headers=auth_headers)
        assert resp.status_code == 200
        body = resp.json()
        assert body["default_ttl_days"] == 90
        # All four configured categories surface with is_default=true.
        categories = {c["category"] for c in body["configs"]}
        assert categories == {
            "audit_entries",
            "ingest_log",
            "incident_memory_recall_log",
            "bot_action_audit",
        }
        for cfg in body["configs"]:
            assert cfg["is_default"] is True
            assert cfg["ttl_days"] == 90
        # Storage panel includes memories as non-prunable.
        storage_categories = {s["category"] for s in body["storage"]}
        assert "incident_memories" in storage_categories
        mem_row = next(
            s for s in body["storage"] if s["category"] == "incident_memories"
        )
        assert mem_row["non_prunable"] is True

    async def test_put_persists_per_category_ttl(
        self, client: AsyncClient, auth_headers
    ):
        resp = await client.put(
            "/retention",
            headers=auth_headers,
            json={
                "configs": [
                    {"category": "audit_entries", "ttl_days": 30},
                    {"category": "ingest_log", "ttl_days": None},
                ]
            },
        )
        assert resp.status_code == 200
        body = resp.json()
        by_cat = {c["category"]: c for c in body["configs"]}
        assert by_cat["audit_entries"]["ttl_days"] == 30
        assert by_cat["audit_entries"]["is_default"] is False
        assert by_cat["ingest_log"]["ttl_days"] is None
        assert by_cat["ingest_log"]["is_default"] is False
        # Untouched categories still show defaults.
        assert by_cat["incident_memory_recall_log"]["is_default"] is True

    async def test_put_rejects_unknown_category(
        self, client: AsyncClient, auth_headers
    ):
        resp = await client.put(
            "/retention",
            headers=auth_headers,
            json={"configs": [{"category": "frobnicate", "ttl_days": 7}]},
        )
        assert resp.status_code == 400

    async def test_put_rejects_zero_ttl(self, client: AsyncClient, auth_headers):
        resp = await client.put(
            "/retention",
            headers=auth_headers,
            json={"configs": [{"category": "audit_entries", "ttl_days": 0}]},
        )
        assert resp.status_code == 400

    async def test_run_returns_per_category_report(
        self, client: AsyncClient, auth_headers
    ):
        resp = await client.post("/retention/run", headers=auth_headers)
        assert resp.status_code == 200
        body = resp.json()
        assert "total_deleted" in body
        assert body["total_errors"] == 0
        categories = {item["category"] for item in body["items"]}
        # All four show up even when there's nothing to delete.
        assert categories == {
            "audit_entries",
            "ingest_log",
            "incident_memory_recall_log",
            "bot_action_audit",
        }

    async def test_viewer_cannot_read_retention(
        self, client: AsyncClient, viewer_headers
    ):
        resp = await client.get("/retention", headers=viewer_headers)
        assert resp.status_code in (401, 403)


class TestSetupChecklist:
    async def test_fresh_org_has_all_false(self, client: AsyncClient, auth_headers):
        resp = await client.get("/config/setup-checklist", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data == {
            "model_configured": False,
            "mcp_server_added": False,
            "skill_defined": False,
            "ingest_token_created": False,
            "paging_service_added": False,
            "all_complete": False,
        }

    async def test_mcp_server_flips_after_create(
        self, client: AsyncClient, auth_headers, app
    ):
        from backend.db.repos import MCPServerRepo

        async with app.state.session_factory() as db:
            await MCPServerRepo.create(
                db,
                TEST_ORG_ID,
                name="probe",
                transport="stdio",
                command="echo",
                is_active=True,
            )
            await db.commit()
        resp = await client.get("/config/setup-checklist", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["mcp_server_added"] is True
        assert data["all_complete"] is False

    async def test_viewer_can_read(self, client: AsyncClient, viewer_headers):
        resp = await client.get("/config/setup-checklist", headers=viewer_headers)
        assert resp.status_code == 200

    async def test_unauthenticated_rejected(self, client: AsyncClient):
        resp = await client.get("/config/setup-checklist")
        assert resp.status_code in {401, 403}


class TestIntegrationConnectors:
    async def test_crud_encrypts_auth_and_never_returns_secret(
        self, client: AsyncClient, app, auth_headers, viewer_headers
    ):
        created = await client.post(
            "/integrations",
            headers=auth_headers,
            json={
                "kind": "custom",
                "name": "Status API",
                "base_url": "https://status.example.test",
                "auth_type": "pat",
                "auth": {"token": "top-secret"},
                "config": {"health_path": "/health"},
                "is_enabled": True,
            },
        )
        assert created.status_code == 201
        body = created.json()
        assert body["has_auth"] is True
        assert body["auth_keys"] == ["token"]
        assert "top-secret" not in created.text

        from backend.db.repos import IntegrationConnectorRepo

        async with app.state.session_factory() as db:
            row = await IntegrationConnectorRepo.get_by_id(
                db, TEST_ORG_ID, uuid.UUID(body["id"])
            )
            assert row is not None
            assert row.auth_encrypted
            assert "top-secret" not in row.auth_encrypted
            assert IntegrationConnectorRepo.decrypt_auth(row)["token"] == "top-secret"

        listed = await client.get("/integrations", headers=auth_headers)
        assert listed.status_code == 200
        assert listed.json()["total"] == 1
        assert "top-secret" not in listed.text

        updated = await client.put(
            f"/integrations/{body['id']}",
            headers=auth_headers,
            json={
                "kind": "custom",
                "name": "Status API",
                "base_url": "https://status.example.test",
                "auth_type": "pat",
                "config": {"health_path": "/ready"},
                "is_enabled": False,
            },
        )
        assert updated.status_code == 200
        assert updated.json()["status"] == "disabled"
        assert updated.json()["has_auth"] is True

        patched = await client.put(
            f"/integrations/{body['id']}",
            headers=auth_headers,
            json={
                "kind": "custom",
                "name": "Status API",
                "base_url": "https://status.example.test",
                "auth_type": "pat",
                "auth": {"secondary": "keep-me"},
                "config": {"health_path": "/ready"},
                "is_enabled": False,
            },
        )
        assert patched.status_code == 200
        assert patched.json()["auth_keys"] == ["secondary", "token"]
        async with app.state.session_factory() as db:
            row = await IntegrationConnectorRepo.get_by_id(
                db, TEST_ORG_ID, uuid.UUID(body["id"])
            )
            assert IntegrationConnectorRepo.decrypt_auth(row) == {
                "secondary": "keep-me",
                "token": "top-secret",
            }

        removed = await client.put(
            f"/integrations/{body['id']}",
            headers=auth_headers,
            json={
                "kind": "custom",
                "name": "Status API",
                "base_url": "https://status.example.test",
                "auth_type": "pat",
                "auth": {"secondary": None},
                "config": {"health_path": "/ready"},
                "is_enabled": False,
            },
        )
        assert removed.status_code == 200
        assert removed.json()["auth_keys"] == ["token"]

        forbidden = await client.get("/integrations", headers=viewer_headers)
        assert forbidden.status_code == 403

        deleted = await client.delete(
            f"/integrations/{body['id']}", headers=auth_headers
        )
        assert deleted.status_code == 204

    async def test_kind_catalog_and_mocked_connection_test(
        self, client: AsyncClient, auth_headers, monkeypatch
    ):
        kinds = await client.get("/integrations/kinds", headers=auth_headers)
        assert kinds.status_code == 200
        custom = next(
            item for item in kinds.json()["items"] if item["kind"] == "custom"
        )
        assert custom["adapter_available"] is True
        assert custom["capabilities"][0]["action"] == "test_connection"
        assert custom["base_url_placeholder"] == "https://service.example.com"
        assert [field["name"] for field in custom["credential_fields"]["pat"]] == [
            "token"
        ]
        assert {field["name"] for field in custom["config_fields"]} == {
            "headers",
            "health_path",
        }
        github = next(
            item for item in kinds.json()["items"] if item["kind"] == "github"
        )
        gitlab = next(
            item for item in kinds.json()["items"] if item["kind"] == "gitlab"
        )
        assert github["adapter_available"] is True
        assert gitlab["adapter_available"] is True
        assert "Enterprise Server" in github["base_url_helper"]
        assert [field["name"] for field in github["credential_fields"]["pat"]] == [
            "token"
        ]
        assert {
            field["name"] for field in github["credential_fields"]["app"]
        } == {"app_id", "installation_id", "private_key", "installation_token"}
        assert {field["name"] for field in github["config_fields"]} == {
            "owner",
            "repo",
            "api_version",
        }
        assert "merge_pull_request" in {
            item["action"] for item in github["capabilities"]
        }
        assert "merge_merge_request" in {
            item["action"] for item in gitlab["capabilities"]
        }
        phase_four = {
            item["kind"]: item
            for item in kinds.json()["items"]
            if item["kind"]
            in {
                "bitbucket",
                "azure_devops",
                "jira",
                "confluence",
                "servicenow",
                "linear",
                "notion",
            }
        }
        assert len(phase_four) == 7
        assert all(item["adapter_available"] for item in phase_four.values())
        assert any(
            capability["always_requires_approval"]
            for capability in phase_four["bitbucket"]["capabilities"]
            if capability["action"] == "merge_pull_request"
        )
        ci_cd = {
            item["kind"]: item
            for item in kinds.json()["items"]
            if item["kind"] in {"jenkins", "circleci", "azure_pipelines"}
        }
        assert len(ci_cd) == 3
        assert all(item["adapter_available"] for item in ci_cd.values())
        assert {
            capability["action"] for capability in ci_cd["jenkins"]["capabilities"]
        } >= {"get_job", "get_build", "trigger_build"}
        automation = {
            item["kind"]: item
            for item in kinds.json()["items"]
            if item["kind"] in {"terraform_cloud", "argocd", "ansible"}
        }
        assert len(automation) == 3
        assert all(item["adapter_available"] for item in automation.values())
        assert {
            capability["action"]
            for capability in automation["terraform_cloud"]["capabilities"]
        } >= {"list_workspaces", "list_runs", "plan", "apply"}
        assert all(
            capability["always_requires_approval"]
            for item in automation.values()
            for capability in item["capabilities"]
            if capability["mutating"]
        )
        phase_five = {
            item["kind"]: item
            for item in kinds.json()["items"]
            if item["kind"] in {"gitea", "google_docs", "statuspage"}
        }
        assert len(phase_five) == 3
        assert all(item["adapter_available"] for item in phase_five.values())
        assert {
            capability["action"]
            for capability in phase_five["google_docs"]["capabilities"]
        } == {"test_connection", "read_doc", "export_doc"}
        assert any(
            capability["always_requires_approval"]
            for capability in phase_five["gitea"]["capabilities"]
            if capability["action"] == "merge_pull_request"
        )

        created = await client.post(
            "/integrations",
            headers=auth_headers,
            json={
                "kind": "custom",
                "name": "Mocked",
                "base_url": "https://mock.example.test",
                "auth_type": "none",
                "config": {},
                "is_enabled": True,
            },
        )
        connector_id = created.json()["id"]

        from backend.integrations.base import IntegrationResult

        class FakeAdapter:
            async def safe_invoke(self, action, connector, auth, parameters=None):
                assert action == "test_connection"
                return IntegrationResult.success(
                    detail="Mock provider accepted credentials."
                )

        monkeypatch.setattr(
            "backend.api.routes.integrations.get_adapter",
            lambda kind: FakeAdapter(),
        )
        tested = await client.post(
            f"/integrations/{connector_id}/test", headers=auth_headers
        )
        assert tested.status_code == 200
        assert tested.json()["success"] is True
        assert "Mock provider" in tested.json()["detail"]

    async def test_incident_integration_links_are_tenant_scoped_and_readable(
        self, client: AsyncClient, app, auth_headers
    ):
        from backend.db.repos import (
            IncidentIntegrationLinkRepo,
            IntegrationConnectorRepo,
        )

        async with app.state.session_factory() as db:
            incident = await IncidentRepo.create(
                db,
                TEST_ORG_ID,
                title="Source link",
                description="Link a commit",
                severity="high",
            )
            connector = await IntegrationConnectorRepo.create(
                db,
                TEST_ORG_ID,
                kind="github",
                name="Repo",
                base_url=None,
                auth_type="pat",
                auth={"token": "secret"},
                config={"owner": "acme", "repo": "api"},
                is_enabled=True,
            )
            await IncidentIntegrationLinkRepo.upsert(
                db,
                TEST_ORG_ID,
                incident_id=incident.id,
                connector_id=connector.id,
                reference_type="commit",
                external_id="abc123",
                url="https://example.test/commit/abc123",
                title="Fix deploy",
                reference_meta={"owner": "acme", "repo": "api"},
            )
            await db.commit()
            incident_id = incident.id

        response = await client.get(
            f"/incidents/{incident_id}/integration-links",
            headers=auth_headers,
        )
        assert response.status_code == 200
        assert response.json()["total"] == 1
        item = response.json()["items"][0]
        assert item["reference_type"] == "commit"
        assert item["external_id"] == "abc123"
        assert item["reference_meta"]["repo"] == "api"


class TestReportsAndEmail:
    async def test_csv_and_pdf_incident_reports(
        self, client: AsyncClient, app, auth_headers
    ):
        async with app.state.session_factory() as db:
            incident = await IncidentRepo.create(
                db,
                TEST_ORG_ID,
                title="Reportable outage",
                description="checkout failed",
                severity="critical",
                priority="P0",
            )
            incident.status = "resolved"
            incident.acknowledged_at = incident.created_at + timedelta(minutes=2)
            incident.updated_at = incident.created_at + timedelta(minutes=10)
            await db.commit()

        start = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
        end = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
        csv_resp = await client.get(
            "/reports/incidents",
            params={"format": "csv", "from": start, "to": end},
            headers=auth_headers,
        )
        assert csv_resp.status_code == 200
        assert "Reportable outage" in csv_resp.text
        assert "mtta_seconds" in csv_resp.text
        assert "mttr_seconds" in csv_resp.text

        pdf_resp = await client.get(
            "/reports/incidents",
            params={"format": "pdf", "from": start, "to": end},
            headers=auth_headers,
        )
        assert pdf_resp.status_code == 200
        assert pdf_resp.content.startswith(b"%PDF")

    async def test_email_settings_encrypt_password_and_send_test(
        self, client: AsyncClient, app, auth_headers, monkeypatch
    ):
        response = await client.put(
            f"/organizations/{TEST_ORG_ID}/email-settings",
            json={
                "host": "smtp.example.com",
                "port": 587,
                "security": "starttls",
                "username": "ops",
                "password": "secret",
                "from_name": "OpsMender",
                "from_address": "ops@example.com",
            },
            headers=auth_headers,
        )
        assert response.status_code == 200
        assert response.json()["has_password"] is True
        async with app.state.session_factory() as db:
            row = await OrgEmailSettingsRepo.get_for_org(db, TEST_ORG_ID)
            assert row is not None
            assert row.password_encrypted != "secret"

        class FakeChannel:
            async def send(self, **kwargs):
                from backend.paging.dispatch import DeliveryAttempt

                return DeliveryAttempt("email", "sent")

        monkeypatch.setattr(
            "backend.api.routes.organizations.build_email_channel",
            lambda settings: FakeChannel(),
        )
        test_resp = await client.post(
            f"/organizations/{TEST_ORG_ID}/email-settings/test",
            json={"recipient": "admin@example.com"},
            headers=auth_headers,
        )
        assert test_resp.status_code == 200
        assert test_resp.json()["success"] is True

    async def test_report_schedule_crud(self, client: AsyncClient, auth_headers):
        create = await client.post(
            "/reports/schedules",
            json={
                "name": "Weekly leadership",
                "cadence": "weekly",
                "recipients": ["lead@example.com"],
                "filters": {"priority": "P0"},
                "format": "pdf",
                "next_run_at": datetime.now(timezone.utc).isoformat(),
                "enabled": True,
            },
            headers=auth_headers,
        )
        assert create.status_code == 201
        schedule_id = create.json()["id"]
        listing = await client.get("/reports/schedules", headers=auth_headers)
        assert listing.status_code == 200
        assert listing.json()["total"] == 1
        deleted = await client.delete(
            f"/reports/schedules/{schedule_id}", headers=auth_headers
        )
        assert deleted.status_code == 204


class TestWorkflowProfiles:
    async def test_create_list_update_delete_workflow_profile(
        self, client: AsyncClient, auth_headers
    ):
        create_resp = await client.post(
            "/workflow-profiles",
            json={
                "name": "fast-track",
                "description": "Skip observe",
                "node_order": [
                    "diagnose",
                    "plan",
                    "tier_gate",
                    "execute",
                    "verify",
                    "summarize",
                ],
                "is_active": True,
                "is_default": True,
            },
            headers=auth_headers,
        )
        assert create_resp.status_code == 201
        profile_id = create_resp.json()["id"]
        assert create_resp.json()["is_default"] is True

        list_resp = await client.get("/workflow-profiles", headers=auth_headers)
        assert list_resp.status_code == 200
        assert list_resp.json()["total"] >= 1

        update_resp = await client.put(
            f"/workflow-profiles/{profile_id}",
            json={
                "name": "fast-track",
                "description": "Skip observe and verify",
                "node_order": ["diagnose", "plan", "tier_gate", "execute", "summarize"],
                "is_active": True,
                "is_default": False,
            },
            headers=auth_headers,
        )
        assert update_resp.status_code == 200
        assert update_resp.json()["node_order"] == [
            "diagnose",
            "plan",
            "tier_gate",
            "execute",
            "summarize",
        ]

        delete_resp = await client.delete(
            f"/workflow-profiles/{profile_id}",
            headers=auth_headers,
        )
        assert delete_resp.status_code == 204

    async def test_list_session_profile_templates(
        self, client: AsyncClient, auth_headers
    ):
        resp = await client.get("/workflow-profiles/templates", headers=auth_headers)
        assert resp.status_code == 200
        body = resp.json()
        keys = {t["key"] for t in body["items"]}
        assert keys == {
            "standard_assisted_response",
            "read_only_investigation",
            "fast_triage",
            "postmortem_builder",
            "high_risk_change_review",
        }
        # Every template has a usable name, description, and node order.
        for t in body["items"]:
            assert t["name"]
            assert t["description"]
            assert len(t["node_order"]) >= 2

    async def test_template_node_order_creates_a_valid_profile(
        self, client: AsyncClient, auth_headers
    ):
        templates = (
            await client.get("/workflow-profiles/templates", headers=auth_headers)
        ).json()["items"]
        tmpl = next(t for t in templates if t["key"] == "read_only_investigation")
        # A template's node order must save cleanly as a real profile.
        resp = await client.post(
            "/workflow-profiles",
            json={
                "name": "From template",
                "description": tmpl["description"],
                "node_order": tmpl["node_order"],
                "is_active": True,
            },
            headers=auth_headers,
        )
        assert resp.status_code == 201, resp.text
        assert resp.json()["node_order"] == tmpl["node_order"]

    async def test_create_workflow_profile_validates_node_order(
        self, client: AsyncClient, auth_headers
    ):
        resp = await client.post(
            "/workflow-profiles",
            json={
                "name": "bad-workflow",
                "node_order": ["plan", "execute", "tier_gate"],
                "is_active": True,
            },
            headers=auth_headers,
        )
        assert resp.status_code == 400
        assert "tier_gate" in resp.json()["detail"]

    async def test_create_session_uses_default_workflow_profile(
        self, client: AsyncClient, app, auth_headers
    ):
        async with app.state.session_factory() as db:
            profile = await WorkflowProfileRepo.create(
                db,
                TEST_ORG_ID,
                name="default-fast-track",
                description="default workflow",
                node_order=["diagnose", "plan", "tier_gate", "execute", "summarize"],
                is_default=True,
            )
            await db.commit()
            profile_id = profile.id

        resp = await client.post("/sessions", json={"tier": 2}, headers=auth_headers)
        assert resp.status_code == 201
        assert resp.json()["workflow_profile_id"] == str(profile_id)


class TestAgentTeamProfiles:
    async def test_create_list_update_delete_agent_team_profile(
        self, client: AsyncClient, auth_headers
    ):
        create_resp = await client.post(
            "/agent-team-profiles",
            json={
                "name": "triage-council",
                "description": "Multi-angle triage",
                "roles": ["incident_commander", "investigator", "skeptic"],
                "is_active": True,
                "is_default": True,
            },
            headers=auth_headers,
        )
        assert create_resp.status_code == 201
        profile_id = create_resp.json()["id"]
        assert create_resp.json()["is_default"] is True

        list_resp = await client.get("/agent-team-profiles", headers=auth_headers)
        assert list_resp.status_code == 200
        assert list_resp.json()["total"] >= 1

        update_resp = await client.put(
            f"/agent-team-profiles/{profile_id}",
            json={
                "name": "triage-council",
                "description": "Multi-angle triage plus remediation",
                "roles": [
                    "incident_commander",
                    "investigator",
                    "skeptic",
                    "remediator",
                ],
                "is_active": True,
                "is_default": False,
            },
            headers=auth_headers,
        )
        assert update_resp.status_code == 200
        assert update_resp.json()["roles"] == [
            "incident_commander",
            "investigator",
            "skeptic",
            "remediator",
        ]

        delete_resp = await client.delete(
            f"/agent-team-profiles/{profile_id}",
            headers=auth_headers,
        )
        assert delete_resp.status_code == 204

    async def test_create_agent_team_profile_validates_roles(
        self, client: AsyncClient, auth_headers
    ):
        resp = await client.post(
            "/agent-team-profiles",
            json={
                "name": "bad-team",
                "roles": ["incident_commander", "incident_commander"],
                "is_active": True,
            },
            headers=auth_headers,
        )
        assert resp.status_code == 400
        assert "duplicate" in resp.json()["detail"].lower()

    async def test_create_session_uses_default_agent_team_profile(
        self, client: AsyncClient, app, auth_headers
    ):
        async with app.state.session_factory() as db:
            profile = await AgentTeamProfileRepo.create(
                db,
                TEST_ORG_ID,
                name="default-triage-team",
                description="default team",
                roles=["incident_commander", "investigator", "skeptic"],
                is_default=True,
            )
            await db.commit()
            profile_id = profile.id

        resp = await client.post("/sessions", json={"tier": 2}, headers=auth_headers)
        assert resp.status_code == 201
        assert resp.json()["agent_team_profile_id"] == str(profile_id)

    async def test_get_config_viewer_forbidden(
        self, client: AsyncClient, viewer_headers
    ):
        resp = await client.get("/config", headers=viewer_headers)
        assert resp.status_code == 403

    async def test_update_config_admin(self, client: AsyncClient, auth_headers):
        resp = await client.put(
            "/config",
            json={"tier": 2, "logging_level": "DEBUG"},
            headers=auth_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["tier"] == 2
        assert data["logging_level"] == "DEBUG"

    async def test_update_config_viewer_forbidden(
        self, client: AsyncClient, viewer_headers
    ):
        resp = await client.put(
            "/config",
            json={
                "tier": 1,
            },
            headers=viewer_headers,
        )
        assert resp.status_code == 403


class TestBotConnectorsAPI:
    async def test_track_lane_round_trips_and_eventbridge_secrets_are_encrypted(
        self, client: AsyncClient, app, auth_headers
    ):
        response = await client.post(
            "/bot-connectors",
            json={
                "name": "soc-events",
                "platform": "eventbridge",
                "credentials": {
                    "region": "us-east-1",
                    "event_bus_name": "security",
                    "access_key_id": "AKIA_TEST",
                    "secret_access_key": "secret",
                },
                "allowed_capabilities": ["notifications"],
                "lanes": ["track"],
                "is_enabled": True,
            },
            headers=auth_headers,
        )
        assert response.status_code == 201
        data = response.json()
        assert data["lanes"] == ["track"]
        connector_id = uuid.UUID(data["id"])
        async with app.state.session_factory() as db:
            stored = await BotConnectorRepo.get_by_id(db, TEST_ORG_ID, connector_id)
            assert stored is not None
            assert stored.credentials["secret_access_key"] != "secret"
            assert stored.credentials["secret_access_key"].startswith("enc:")
            assert stored.credentials["event_bus_name"] != "security"

    async def test_track_lane_rejects_unsupported_platform(
        self, client: AsyncClient, auth_headers
    ):
        response = await client.post(
            "/bot-connectors",
            json={
                "name": "telegram-track",
                "platform": "telegram",
                "allowed_capabilities": ["notifications"],
                "lanes": ["track"],
            },
            headers=auth_headers,
        )
        assert response.status_code == 400
        assert "Track lane currently supports" in response.json()["detail"]

    async def test_discord_supports_respond_and_track_lanes(
        self, client: AsyncClient, auth_headers
    ):
        response = await client.post(
            "/bot-connectors",
            json={
                "name": "discord-status",
                "platform": "discord",
                "config": {"default_chat_id": "123456789012345678"},
                "credentials": {
                    "public_key": "ab" * 32,
                    "bot_token": "discord-token",
                },
                "allowed_capabilities": ["notifications"],
                "lanes": ["respond", "track"],
                "is_enabled": True,
            },
            headers=auth_headers,
        )
        assert response.status_code == 201
        assert response.json()["lanes"] == ["respond", "track"]

    async def test_google_chat_supports_track_and_encrypts_service_account(
        self, client: AsyncClient, app, auth_headers
    ):
        response = await client.post(
            "/bot-connectors",
            json={
                "name": "google-chat-status",
                "platform": "google_chat",
                "config": {"default_chat_id": "spaces/SPACE1"},
                "credentials": {
                    "client_email": "chat@project.iam.gserviceaccount.com",
                    "private_key": "private-key",
                },
                "allowed_capabilities": ["notifications"],
                "lanes": ["respond", "track"],
                "is_enabled": True,
            },
            headers=auth_headers,
        )
        assert response.status_code == 201
        data = response.json()
        assert data["lanes"] == ["respond", "track"]
        assert data["platform_label"] == "Google Chat"
        assert data["platform_capabilities"]["message_update"] is True
        connector_id = uuid.UUID(data["id"])
        async with app.state.session_factory() as db:
            stored = await BotConnectorRepo.get_by_id(
                db, TEST_ORG_ID, connector_id
            )
            assert stored is not None
            assert stored.credentials["client_email"].startswith("enc:")
            assert stored.credentials["private_key"].startswith("enc:")

    async def test_create_list_update_delete_bot_connector(
        self, client: AsyncClient, app, auth_headers
    ):
        create_resp = await client.post(
            "/bot-connectors",
            json={
                "name": "telegram-ops",
                "platform": "telegram",
                "config": {"default_chat_id": "-100123"},
                "credentials": {"bot_token": "secret-token"},
                "allowed_capabilities": ["approvals", "incident_lookup"],
                "is_enabled": True,
            },
            headers=auth_headers,
        )
        assert create_resp.status_code == 201
        data = create_resp.json()
        connector_id = data["id"]
        assert data["status"] == "configured"
        assert data["credential_keys"] == ["bot_token"]
        assert data["has_credentials"] is True
        assert data["team_scope"] == "workspace"
        assert data["team_ids"] == []
        assert data["team_names"] == []
        assert "credentials" not in data

        async with app.state.session_factory() as db:
            stored = await BotConnectorRepo.get_by_id(
                db, TEST_ORG_ID, uuid.UUID(connector_id)
            )
            assert stored is not None
            assert stored.credentials == {"bot_token": "secret-token"}

        list_resp = await client.get("/bot-connectors", headers=auth_headers)
        assert list_resp.status_code == 200
        assert list_resp.json()["total"] == 1

        update_resp = await client.put(
            f"/bot-connectors/{connector_id}",
            json={
                "name": "telegram-ops",
                "platform": "telegram",
                "config": {"default_chat_id": "-100999"},
                "clear_credentials": True,
                "allowed_capabilities": ["notifications"],
                "status": "disabled",
                "is_enabled": False,
            },
            headers=auth_headers,
        )
        assert update_resp.status_code == 200
        updated = update_resp.json()
        assert updated["has_credentials"] is False
        assert updated["credential_keys"] == []
        assert updated["allowed_capabilities"] == ["notifications"]
        assert updated["team_scope"] == "workspace"

        delete_resp = await client.delete(
            f"/bot-connectors/{connector_id}",
            headers=auth_headers,
        )
        assert delete_resp.status_code == 204

    async def test_bot_connector_team_scope_round_trips(
        self, client: AsyncClient, app, auth_headers
    ):
        async with app.state.session_factory() as db:
            team = await TeamRepo.create(
                db, TEST_ORG_ID, name="Platform", slug="platform"
            )
            await db.commit()

        create_resp = await client.post(
            "/bot-connectors",
            json={
                "name": "platform-alerts",
                "platform": "telegram",
                "config": {"default_chat_id": "-100123"},
                "credentials": {"bot_token": "secret-token"},
                "allowed_capabilities": ["notifications"],
                "team_scope": "teams",
                "team_ids": [str(team.id)],
            },
            headers=auth_headers,
        )

        assert create_resp.status_code == 201
        data = create_resp.json()
        assert data["team_scope"] == "teams"
        assert data["team_ids"] == [str(team.id)]
        assert data["team_names"] == ["Platform"]

        list_resp = await client.get("/bot-connectors", headers=auth_headers)
        item = list_resp.json()["items"][0]
        assert item["team_scope"] == "teams"
        assert item["team_names"] == ["Platform"]

    async def test_bot_connector_rejects_empty_team_scope(
        self, client: AsyncClient, auth_headers
    ):
        resp = await client.post(
            "/bot-connectors",
            json={
                "name": "empty-scope",
                "platform": "telegram",
                "allowed_capabilities": ["notifications"],
                "team_scope": "teams",
                "team_ids": [],
            },
            headers=auth_headers,
        )

        assert resp.status_code == 400
        assert "Select at least one team" in resp.json()["detail"]

    async def test_bot_connector_rejects_unknown_capability(
        self, client: AsyncClient, auth_headers
    ):
        resp = await client.post(
            "/bot-connectors",
            json={
                "name": "unsafe-bot",
                "platform": "custom",
                "allowed_capabilities": ["execute_shell"],
            },
            headers=auth_headers,
        )
        assert resp.status_code == 400
        assert "Unsupported capabilities" in resp.json()["detail"]

    async def test_slack_native_actions_require_signing_secret_readiness(
        self, client: AsyncClient, auth_headers
    ):
        configured = await client.post(
            "/bot-connectors",
            json={
                "name": "slack-native-ready",
                "platform": "slack",
                "credentials": {
                    "signing_secret": "signed",
                    "bot_token": "xoxb-test",
                },
                "allowed_capabilities": ["notifications"],
                "is_enabled": True,
                "native_actions_enabled": True,
            },
            headers=auth_headers,
        )
        assert configured.status_code == 201
        assert configured.json()["native_actions_enabled"] is True
        assert configured.json()["callback_status"] == "configured"

        missing_secret = await client.post(
            "/bot-connectors",
            json={
                "name": "slack-native-missing-secret",
                "platform": "slack",
                "credentials": {"bot_token": "xoxb-test"},
                "allowed_capabilities": ["notifications"],
                "is_enabled": True,
                "native_actions_enabled": True,
            },
            headers=auth_headers,
        )
        assert missing_secret.status_code == 201
        assert missing_secret.json()["callback_status"] == "not_configured"

    async def test_teams_native_actions_require_bot_app_id_readiness(
        self, client: AsyncClient, auth_headers
    ):
        base = {
            "platform": "teams",
            "credentials": {
                "tenant_id": "tenant",
                "client_id": "client",
                "client_secret": "secret",
            },
            "allowed_capabilities": ["notifications"],
            "is_enabled": True,
            "native_actions_enabled": True,
        }
        configured = await client.post(
            "/bot-connectors",
            json={
                **base,
                "name": "teams-native-ready",
                "config": {"bot_app_id": "bot-app"},
            },
            headers=auth_headers,
        )
        assert configured.status_code == 201
        assert configured.json()["native_actions_enabled"] is True
        assert configured.json()["callback_status"] == "configured"

        missing_app = await client.post(
            "/bot-connectors",
            json={**base, "name": "teams-native-missing-app"},
            headers=auth_headers,
        )
        assert missing_app.status_code == 201
        assert missing_app.json()["callback_status"] == "not_configured"

    async def test_bot_connector_duplicate_name_conflict(
        self, client: AsyncClient, auth_headers
    ):
        payload = {
            "name": "signal-ops",
            "platform": "signal",
            "allowed_capabilities": ["session_status"],
        }
        first = await client.post(
            "/bot-connectors",
            json=payload,
            headers=auth_headers,
        )
        assert first.status_code == 201

        second = await client.post(
            "/bot-connectors",
            json=payload,
            headers=auth_headers,
        )
        assert second.status_code == 409

    async def test_bot_connector_test_marks_health(
        self, client: AsyncClient, auth_headers
    ):
        create_resp = await client.post(
            "/bot-connectors",
            json={
                "name": "telegram-health",
                "platform": "telegram",
                "credentials": {"bot_token": "secret-token"},
                "allowed_capabilities": ["notifications"],
                "is_enabled": True,
            },
            headers=auth_headers,
        )
        assert create_resp.status_code == 201

        connector_id = create_resp.json()["id"]
        test_resp = await client.post(
            f"/bot-connectors/{connector_id}/test",
            headers=auth_headers,
        )
        assert test_resp.status_code == 200
        data = test_resp.json()
        assert data["success"] is True
        assert data["status"] == "healthy"

        list_resp = await client.get("/bot-connectors", headers=auth_headers)
        item = list_resp.json()["items"][0]
        assert item["status"] == "healthy"
        assert item["last_checked_at"] is not None
        assert item["last_error"] is None

    async def test_bot_connector_test_reports_missing_credentials(
        self, client: AsyncClient, auth_headers
    ):
        create_resp = await client.post(
            "/bot-connectors",
            json={
                "name": "telegram-missing-token",
                "platform": "telegram",
                "allowed_capabilities": ["notifications"],
                "is_enabled": True,
            },
            headers=auth_headers,
        )
        assert create_resp.status_code == 201

        connector_id = create_resp.json()["id"]
        test_resp = await client.post(
            f"/bot-connectors/{connector_id}/test",
            headers=auth_headers,
        )
        assert test_resp.status_code == 200
        data = test_resp.json()
        assert data["success"] is False
        assert data["status"] == "not_configured"
        assert "bot_token" in data["detail"]

    async def test_bot_connector_viewer_forbidden(
        self, client: AsyncClient, viewer_headers
    ):
        resp = await client.get("/bot-connectors", headers=viewer_headers)
        assert resp.status_code == 403

    async def test_list_platform_schemas(self, client: AsyncClient, auth_headers):
        resp = await client.get("/bot-connectors/platforms", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] >= 15
        platforms = {item["platform"] for item in data["items"]}
        for expected in [
            "telegram",
            "slack",
            "discord",
            "google_chat",
            "whatsapp",
            "signal",
            "mattermost",
            "matrix",
            "feishu",
            "dingtalk",
            "wecom",
            "weixin",
            "twilio",
            "email",
            "smtp",
            "homeassistant",
            "bluebubbles",
        ]:
            assert expected in platforms
        for item in data["items"]:
            assert isinstance(item["fields"], list)
            assert len(item["fields"]) > 0

    async def test_get_single_platform_schema(self, client: AsyncClient, auth_headers):
        resp = await client.get(
            "/bot-connectors/platforms/telegram/schema", headers=auth_headers
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["platform"] == "telegram"
        names = {f["name"]: f for f in data["fields"]}
        assert names["bot_token"]["kind"] == "secret"
        assert names["bot_token"]["group"] == "credentials"
        assert names["bot_token"]["required"] is True
        assert names["webhook_secret"]["required"] is True
        assert names["default_chat_id"]["group"] == "config"

    async def test_discord_and_mailgun_platform_copy(
        self, client: AsyncClient, auth_headers
    ):
        discord_resp = await client.get(
            "/bot-connectors/platforms/discord/schema", headers=auth_headers
        )
        assert discord_resp.status_code == 200
        discord = discord_resp.json()
        discord_fields = {field["name"]: field for field in discord["fields"]}
        assert discord_fields["default_chat_id"]["label"] == "Discord Channel ID"
        assert "Snowflake" not in discord_fields["default_chat_id"]["helper"]

        google_chat_resp = await client.get(
            "/bot-connectors/platforms/google_chat/schema", headers=auth_headers
        )
        assert google_chat_resp.status_code == 200
        google_chat = google_chat_resp.json()
        google_fields = {field["name"]: field for field in google_chat["fields"]}
        assert google_fields["default_chat_id"]["label"] == "Google Chat space name"
        assert google_fields["private_key"]["group"] == "credentials"
        assert google_chat["capabilities"]["message_update"] is True

        email_resp = await client.get(
            "/bot-connectors/platforms/email/schema", headers=auth_headers
        )
        assert email_resp.status_code == 200
        email = email_resp.json()
        assert email["label"] == "Mailgun Email"
        assert {field["name"] for field in email["fields"]} == {
            "mailgun_api_key",
            "mailgun_domain",
            "from_email",
            "default_chat_id",
        }

        smtp_resp = await client.get(
            "/bot-connectors/platforms/smtp/schema", headers=auth_headers
        )
        assert smtp_resp.status_code == 200
        smtp = smtp_resp.json()
        assert smtp["label"] == "SMTP Email"
        smtp_fields = {field["name"]: field for field in smtp["fields"]}
        assert smtp_fields["smtp_port"]["default"] == "587"
        assert smtp_fields["security"]["default"] == "starttls"

    async def test_unknown_platform_schema_returns_404(
        self, client: AsyncClient, auth_headers
    ):
        resp = await client.get(
            "/bot-connectors/platforms/no-such-platform/schema",
            headers=auth_headers,
        )
        assert resp.status_code == 404

    async def test_platform_schema_viewer_forbidden(
        self, client: AsyncClient, viewer_headers
    ):
        resp = await client.get("/bot-connectors/platforms", headers=viewer_headers)
        assert resp.status_code == 403


class TestTelegramBotWebhook:
    async def _create_connector(
        self,
        client: AsyncClient,
        auth_headers,
        *,
        config: dict | None = None,
        capabilities: list[str] | None = None,
    ) -> str:
        resp = await client.post(
            "/bot-connectors",
            json={
                "name": f"telegram-{uuid.uuid4()}",
                "platform": "telegram",
                "config": config or {},
                "credentials": {
                    "bot_token": "secret-token",
                    "webhook_secret": "telegram-secret",
                },
                "allowed_capabilities": capabilities or ["incident_lookup"],
                "is_enabled": True,
            },
            headers=auth_headers,
        )
        assert resp.status_code == 201
        return resp.json()["id"]

    async def test_telegram_webhook_incident_lookup(
        self, client: AsyncClient, app, auth_headers
    ):
        connector_id = await self._create_connector(client, auth_headers)
        async with app.state.session_factory() as db:
            incident = await IncidentRepo.create(
                db,
                TEST_ORG_ID,
                title="API latency spike",
                description="p95 latency crossed the SLO threshold.",
                severity="high",
            )
            await db.commit()
            incident_id = str(incident.id)

        resp = await client.post(
            f"/bot-connectors/{connector_id}/telegram/webhook",
            json={
                "message": {
                    "chat": {"id": "-100123"},
                    "text": f"/incident {incident_id}",
                }
            },
            headers={"X-Telegram-Bot-Api-Secret-Token": "telegram-secret"},
        )

        assert resp.status_code == 200
        data = resp.json()
        assert data["method"] == "sendMessage"
        assert data["chat_id"] == "-100123"
        assert "API latency spike" in data["text"]
        assert incident_id in data["text"]

    async def test_telegram_webhook_rejects_invalid_secret(
        self, client: AsyncClient, auth_headers
    ):
        connector_id = await self._create_connector(client, auth_headers)

        resp = await client.post(
            f"/bot-connectors/{connector_id}/telegram/webhook",
            json={"message": {"chat": {"id": "-100123"}, "text": "/incidents"}},
            headers={"X-Telegram-Bot-Api-Secret-Token": "wrong"},
        )

        assert resp.status_code == 403

    async def test_telegram_webhook_enforces_allowed_chat_ids(
        self, client: AsyncClient, auth_headers
    ):
        connector_id = await self._create_connector(
            client,
            auth_headers,
            config={"allowed_chat_ids": ["-100999"]},
        )

        resp = await client.post(
            f"/bot-connectors/{connector_id}/telegram/webhook",
            json={"message": {"chat": {"id": "-100123"}, "text": "/incidents"}},
            headers={"X-Telegram-Bot-Api-Secret-Token": "telegram-secret"},
        )

        assert resp.status_code == 200
        assert resp.json()["text"] == "This chat is not allowed to use OpsMender."

    async def test_telegram_webhook_respects_incident_lookup_capability(
        self, client: AsyncClient, auth_headers
    ):
        connector_id = await self._create_connector(
            client,
            auth_headers,
            capabilities=["notifications"],
        )

        resp = await client.post(
            f"/bot-connectors/{connector_id}/telegram/webhook",
            json={"message": {"chat": {"id": "-100123"}, "text": "/incidents"}},
            headers={"X-Telegram-Bot-Api-Secret-Token": "telegram-secret"},
        )

        assert resp.status_code == 200
        assert "not enabled" in resp.json()["text"]

    async def test_telegram_webhook_session_status(
        self, client: AsyncClient, app, auth_headers
    ):
        connector_id = await self._create_connector(
            client,
            auth_headers,
            capabilities=["session_status"],
        )
        async with app.state.session_factory() as db:
            incident = await IncidentRepo.create(
                db,
                TEST_ORG_ID,
                title="Worker crash loop",
                description="worker deployment is restarting",
                severity="medium",
            )
            session = await SessionRepo.create(
                db,
                TEST_ORG_ID,
                incident_id=incident.id,
                tier=2,
                model_provider="openai",
                model_id="gpt-4o-mini",
            )
            await db.commit()
            session_id = str(session.id)

        resp = await client.post(
            f"/bot-connectors/{connector_id}/telegram/webhook",
            json={
                "message": {
                    "chat": {"id": "-100123"},
                    "text": f"/session {session_id}",
                }
            },
            headers={"X-Telegram-Bot-Api-Secret-Token": "telegram-secret"},
        )

        assert resp.status_code == 200
        text = resp.json()["text"]
        assert session_id in text
        assert "Status: `active`" in text
        assert "Tier: `2`" in text

    async def test_telegram_webhook_lists_pending_approvals(
        self, client: AsyncClient, app, auth_headers
    ):
        connector_id = await self._create_connector(
            client,
            auth_headers,
            capabilities=["approvals"],
        )
        async with app.state.session_factory() as db:
            session = await SessionRepo.create(db, TEST_ORG_ID, tier=1)
            approval = await ApprovalRequestRepo.create(
                db,
                TEST_ORG_ID,
                session_id=session.id,
                action={"tool_name": "restart_deployment"},
                justification="restart unhealthy pods",
                expires_at=datetime.now(timezone.utc) + timedelta(minutes=15),
            )
            await db.commit()
            approval_id = str(approval.id)

        resp = await client.post(
            f"/bot-connectors/{connector_id}/telegram/webhook",
            json={"message": {"chat": {"id": "-100123"}, "text": "/approvals"}},
            headers={"X-Telegram-Bot-Api-Secret-Token": "telegram-secret"},
        )

        assert resp.status_code == 200
        text = resp.json()["text"]
        assert approval_id in text
        assert "restart_deployment" in text

    async def _link_user(
        self,
        client: AsyncClient,
        auth_headers,
        connector_id: str,
        platform_user_id: str,
        opsmender_user_id: str,
    ) -> None:
        resp = await client.post(
            f"/bot-connectors/{connector_id}/user-links",
            json={
                "platform_user_id": platform_user_id,
                "opsmender_user_id": opsmender_user_id,
            },
            headers=auth_headers,
        )
        assert resp.status_code == 201

    async def test_telegram_webhook_can_approve_pending_request(
        self, client: AsyncClient, app, auth_headers
    ):
        from backend.bots.rate_limit import rate_limiter
        from backend.db.repos import UserRepo

        rate_limiter.reset()

        connector_id = await self._create_connector(
            client,
            auth_headers,
            capabilities=["approvals"],
        )
        async with app.state.session_factory() as db:
            admin = await UserRepo.get_by_username(db, "testadmin")
            assert admin is not None
            opsmender_user_id = str(admin.id)
            session = await SessionRepo.create(db, TEST_ORG_ID, tier=1)
            await SessionRepo.set_status(
                db, TEST_ORG_ID, session.id, status="awaiting_approval"
            )
            approval = await ApprovalRequestRepo.create(
                db,
                TEST_ORG_ID,
                session_id=session.id,
                action={"tool_name": "scale_deployment"},
                expires_at=datetime.now(timezone.utc) + timedelta(minutes=15),
            )
            await db.commit()
            approval_id = str(approval.id)
            session_id = session.id

        await self._link_user(
            client, auth_headers, connector_id, "111", opsmender_user_id
        )

        resp = await client.post(
            f"/bot-connectors/{connector_id}/telegram/webhook",
            json={
                "message": {
                    "from": {"id": 111},
                    "chat": {"id": "-100123"},
                    "text": f"/approve {approval_id}",
                }
            },
            headers={"X-Telegram-Bot-Api-Secret-Token": "telegram-secret"},
        )

        assert resp.status_code == 200
        assert "approved" in resp.json()["text"]
        async with app.state.session_factory() as db:
            updated = await ApprovalRequestRepo.get_by_id(
                db, TEST_ORG_ID, uuid.UUID(approval_id)
            )
            session = await SessionRepo.get_by_id(db, TEST_ORG_ID, session_id)
            assert updated is not None
            assert updated.status == "approved"
            assert session is not None
            assert session.status == "active"

    async def test_telegram_webhook_rejects_session_command_without_capability(
        self, client: AsyncClient, auth_headers
    ):
        connector_id = await self._create_connector(
            client,
            auth_headers,
            capabilities=["incident_lookup"],
        )

        resp = await client.post(
            f"/bot-connectors/{connector_id}/telegram/webhook",
            json={"message": {"chat": {"id": "-100123"}, "text": "/sessions"}},
            headers={"X-Telegram-Bot-Api-Secret-Token": "telegram-secret"},
        )

        assert resp.status_code == 200
        assert "Session Status is not enabled" in resp.json()["text"]

    async def test_telegram_webhook_copilot_chat_relay(
        self, client: AsyncClient, app, auth_headers, monkeypatch
    ):
        from backend.bots import dispatcher as bot_dispatcher
        from backend.bots.rate_limit import rate_limiter

        rate_limiter.reset()
        scheduled: list[uuid.UUID] = []

        async def fake_responder(factory, *, session_id, user_message_id, **kwargs):
            scheduled.append(session_id)

        monkeypatch.setattr(bot_dispatcher, "respond_to_user_message", fake_responder)

        from backend.db.repos import UserRepo

        connector_id = await self._create_connector(
            client,
            auth_headers,
            capabilities=["copilot_chat"],
        )
        async with app.state.session_factory() as db:
            admin = await UserRepo.get_by_username(db, "testadmin")
            assert admin is not None
            opsmender_user_id = str(admin.id)
            session = await SessionRepo.create(db, TEST_ORG_ID, tier=2)
            await db.commit()
            session_id = session.id

        await self._link_user(
            client, auth_headers, connector_id, "111", opsmender_user_id
        )

        resp = await client.post(
            f"/bot-connectors/{connector_id}/telegram/webhook",
            json={
                "message": {
                    "from": {"id": 111},
                    "chat": {"id": "-100123"},
                    "text": f"/chat {session_id} restarting api pod now",
                }
            },
            headers={"X-Telegram-Bot-Api-Secret-Token": "telegram-secret"},
        )

        assert resp.status_code == 200
        assert str(session_id) in resp.json()["text"]
        assert scheduled, "respond_to_user_message should have been scheduled"

        from backend.db.models import SessionMessage
        from sqlalchemy import select

        async with app.state.session_factory() as db:
            messages = (
                (
                    await db.execute(
                        select(SessionMessage).where(
                            SessionMessage.session_id == session_id
                        )
                    )
                )
                .scalars()
                .all()
            )
            assert len(messages) == 1
            assert messages[0].role == "user"
            assert "restarting api pod now" in messages[0].content
            assert "[telegram chat -100123]" in messages[0].content

    async def test_telegram_webhook_copilot_chat_requires_capability(
        self, client: AsyncClient, auth_headers
    ):
        from backend.bots.rate_limit import rate_limiter

        rate_limiter.reset()

        connector_id = await self._create_connector(
            client,
            auth_headers,
            capabilities=["incident_lookup"],
        )

        resp = await client.post(
            f"/bot-connectors/{connector_id}/telegram/webhook",
            json={
                "message": {
                    "chat": {"id": "-100123"},
                    "text": f"/chat {uuid.uuid4()} hello",
                }
            },
            headers={"X-Telegram-Bot-Api-Secret-Token": "telegram-secret"},
        )

        assert resp.status_code == 200
        assert "Copilot Chat is not enabled" in resp.json()["text"]

    async def test_telegram_webhook_rate_limit(self, client: AsyncClient, auth_headers):
        from backend.bots.rate_limit import rate_limiter

        rate_limiter.reset()

        connector_id = await self._create_connector(
            client,
            auth_headers,
            config={"rate_limit_per_minute": 2},
        )

        for _ in range(2):
            resp = await client.post(
                f"/bot-connectors/{connector_id}/telegram/webhook",
                json={"message": {"chat": {"id": "-100777"}, "text": "/incidents"}},
                headers={"X-Telegram-Bot-Api-Secret-Token": "telegram-secret"},
            )
            assert resp.status_code == 200
            assert "Rate limit hit" not in resp.json()["text"]

        resp = await client.post(
            f"/bot-connectors/{connector_id}/telegram/webhook",
            json={"message": {"chat": {"id": "-100777"}, "text": "/incidents"}},
            headers={"X-Telegram-Bot-Api-Secret-Token": "telegram-secret"},
        )
        assert resp.status_code == 200
        assert "Rate limit hit" in resp.json()["text"]

    async def test_telegram_webhook_writes_action_audit(
        self, client: AsyncClient, app, auth_headers
    ):
        from backend.bots.rate_limit import rate_limiter
        from backend.db.models import BotActionAudit
        from sqlalchemy import select

        rate_limiter.reset()

        connector_id = await self._create_connector(client, auth_headers)

        # capability_denied
        await client.post(
            f"/bot-connectors/{connector_id}/telegram/webhook",
            json={"message": {"chat": {"id": "-100888"}, "text": "/sessions"}},
            headers={"X-Telegram-Bot-Api-Secret-Token": "telegram-secret"},
        )
        # bad_args
        await client.post(
            f"/bot-connectors/{connector_id}/telegram/webhook",
            json={"message": {"chat": {"id": "-100888"}, "text": "/incident"}},
            headers={"X-Telegram-Bot-Api-Secret-Token": "telegram-secret"},
        )
        # ok
        await client.post(
            f"/bot-connectors/{connector_id}/telegram/webhook",
            json={"message": {"chat": {"id": "-100888"}, "text": "/incidents"}},
            headers={"X-Telegram-Bot-Api-Secret-Token": "telegram-secret"},
        )

        async with app.state.session_factory() as db:
            entries = (
                (
                    await db.execute(
                        select(BotActionAudit).where(
                            BotActionAudit.connector_id == uuid.UUID(connector_id)
                        )
                    )
                )
                .scalars()
                .all()
            )

        statuses = {e.status for e in entries}
        commands = {e.command for e in entries}
        assert "capability_denied" in statuses
        assert "bad_args" in statuses
        assert "ok" in statuses
        assert "/sessions" in commands
        assert "/incidents" in commands

    async def test_telegram_webhook_rejects_unlinked_user_for_mutating_command(
        self, client: AsyncClient, app, auth_headers
    ):
        from backend.bots.rate_limit import rate_limiter

        rate_limiter.reset()

        connector_id = await self._create_connector(
            client,
            auth_headers,
            capabilities=["approvals"],
        )
        async with app.state.session_factory() as db:
            session = await SessionRepo.create(db, TEST_ORG_ID, tier=1)
            approval = await ApprovalRequestRepo.create(
                db,
                TEST_ORG_ID,
                session_id=session.id,
                action={"tool_name": "scale_deployment"},
                expires_at=datetime.now(timezone.utc) + timedelta(minutes=15),
            )
            await db.commit()
            approval_id = str(approval.id)

        resp = await client.post(
            f"/bot-connectors/{connector_id}/telegram/webhook",
            json={
                "message": {
                    "from": {"id": 999},
                    "chat": {"id": "-100123"},
                    "text": f"/approve {approval_id}",
                }
            },
            headers={"X-Telegram-Bot-Api-Secret-Token": "telegram-secret"},
        )
        assert resp.status_code == 200
        assert "not linked" in resp.json()["text"]

        # Approval must still be pending — no mutation occurred.
        async with app.state.session_factory() as db:
            from backend.db.models import BotActionAudit
            from sqlalchemy import select

            request = await ApprovalRequestRepo.get_by_id(
                db, TEST_ORG_ID, uuid.UUID(approval_id)
            )
            assert request is not None
            assert request.status == "pending"

            entries = (
                (
                    await db.execute(
                        select(BotActionAudit).where(
                            BotActionAudit.connector_id == uuid.UUID(connector_id)
                        )
                    )
                )
                .scalars()
                .all()
            )
            assert any(
                e.status == "unauthorized" and "from=999" in (e.detail or "")
                for e in entries
            )

    async def test_telegram_webhook_role_denied_for_viewer(
        self, client: AsyncClient, app, auth_headers
    ):
        from backend.bots.rate_limit import rate_limiter
        from backend.db.repos import UserRepo

        rate_limiter.reset()

        # Register a viewer-role user (not the first user).
        await client.post(
            "/auth/register",
            json={
                "username": "viewer-bot",
                "email": "viewer-bot@test.com",
                "password": "viewerpass123",
                "role": "viewer",
            },
        )

        connector_id = await self._create_connector(
            client,
            auth_headers,
            capabilities=["approvals"],
        )
        async with app.state.session_factory() as db:
            viewer = await UserRepo.get_by_username(db, "viewer-bot")
            assert viewer is not None
            session = await SessionRepo.create(db, TEST_ORG_ID, tier=1)
            approval = await ApprovalRequestRepo.create(
                db,
                TEST_ORG_ID,
                session_id=session.id,
                action={"tool_name": "scale_deployment"},
                expires_at=datetime.now(timezone.utc) + timedelta(minutes=15),
            )
            await db.commit()
            approval_id = str(approval.id)
            viewer_id = str(viewer.id)

        await self._link_user(client, auth_headers, connector_id, "222", viewer_id)

        resp = await client.post(
            f"/bot-connectors/{connector_id}/telegram/webhook",
            json={
                "message": {
                    "from": {"id": 222},
                    "chat": {"id": "-100123"},
                    "text": f"/approve {approval_id}",
                }
            },
            headers={"X-Telegram-Bot-Api-Secret-Token": "telegram-secret"},
        )
        assert resp.status_code == 200
        assert "viewer" in resp.json()["text"]
        assert "cannot run" in resp.json()["text"]

        async with app.state.session_factory() as db:
            from backend.db.models import BotActionAudit
            from sqlalchemy import select

            entries = (
                (
                    await db.execute(
                        select(BotActionAudit).where(
                            BotActionAudit.connector_id == uuid.UUID(connector_id)
                        )
                    )
                )
                .scalars()
                .all()
            )
            assert any(
                e.status == "role_denied" and "role=viewer" in (e.detail or "")
                for e in entries
            )

    async def test_bot_user_link_crud(self, client: AsyncClient, app, auth_headers):
        from backend.db.repos import UserRepo

        connector_id = await self._create_connector(client, auth_headers)
        async with app.state.session_factory() as db:
            admin = await UserRepo.get_by_username(db, "testadmin")
            opsmender_user_id = str(admin.id)

        # Create
        resp = await client.post(
            f"/bot-connectors/{connector_id}/user-links",
            json={"platform_user_id": "555", "opsmender_user_id": opsmender_user_id},
            headers=auth_headers,
        )
        assert resp.status_code == 201
        link = resp.json()
        assert link["platform_user_id"] == "555"
        assert link["opsmender_username"] == "testadmin"
        assert link["opsmender_role"] == "admin"

        # Conflict on duplicate
        resp = await client.post(
            f"/bot-connectors/{connector_id}/user-links",
            json={"platform_user_id": "555", "opsmender_user_id": opsmender_user_id},
            headers=auth_headers,
        )
        assert resp.status_code == 409

        # List
        resp = await client.get(
            f"/bot-connectors/{connector_id}/user-links",
            headers=auth_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["total"] == 1

        # Delete
        resp = await client.delete(
            f"/bot-connectors/{connector_id}/user-links/{link['id']}",
            headers=auth_headers,
        )
        assert resp.status_code == 204

        resp = await client.get(
            f"/bot-connectors/{connector_id}/user-links",
            headers=auth_headers,
        )
        assert resp.json()["total"] == 0


class TestSignalBotWebhook:
    async def _create_signal_connector(
        self,
        client: AsyncClient,
        auth_headers,
        *,
        capabilities=None,
    ) -> str:
        resp = await client.post(
            "/bot-connectors",
            json={
                "name": f"signal-{uuid.uuid4()}",
                "platform": "signal",
                "config": {},
                "credentials": {
                    "service_url": "http://signal-bridge:8080",
                    "bot_number": "+15555550100",
                    "webhook_secret": "sig-secret",
                },
                "allowed_capabilities": capabilities or ["incident_lookup"],
                "is_enabled": True,
            },
            headers=auth_headers,
        )
        assert resp.status_code == 201, resp.text
        return resp.json()["id"]

    async def test_signal_webhook_incident_lookup_replies_via_outbound(
        self, client: AsyncClient, app, auth_headers, monkeypatch
    ):
        from backend.bots.rate_limit import rate_limiter
        from backend.bots.connectors import signal as signal_mod

        rate_limiter.reset()

        sent: list[dict] = []

        async def fake_send(
            *, service_url, bot_number, chat_id, text, timeout_seconds=10.0
        ):
            sent.append({"chat_id": chat_id, "text": text})
            return True, None

        monkeypatch.setattr(signal_mod, "signal_send", fake_send)

        connector_id = await self._create_signal_connector(client, auth_headers)
        async with app.state.session_factory() as db:
            await IncidentRepo.create(
                db,
                TEST_ORG_ID,
                title="DB outage",
                description="conns exhausted",
                severity="critical",
            )
            await db.commit()

        resp = await client.post(
            f"/bot-connectors/{connector_id}/signal/webhook",
            json={
                "envelope": {
                    "source": "+15555550111",
                    "dataMessage": {
                        "message": "/incidents",
                        "groupInfo": {"groupId": "GROUP-1"},
                    },
                }
            },
            headers={"X-OpsMender-Webhook-Secret": "sig-secret"},
        )
        assert resp.status_code == 200
        assert resp.json() == {"ok": True}

        # Outbound delivery is fire-and-forget; give the loop a chance.
        await asyncio.sleep(0)
        await asyncio.sleep(0)

        assert any(s["chat_id"] == "GROUP-1" and "DB outage" in s["text"] for s in sent)

    async def test_signal_webhook_rejects_bad_secret(
        self, client: AsyncClient, auth_headers
    ):
        connector_id = await self._create_signal_connector(client, auth_headers)
        resp = await client.post(
            f"/bot-connectors/{connector_id}/signal/webhook",
            json={"envelope": {"source": "+1", "dataMessage": {"message": "/help"}}},
            headers={"X-OpsMender-Webhook-Secret": "wrong"},
        )
        assert resp.status_code == 403


class TestWhatsAppBotWebhook:
    async def _create_whatsapp_connector(
        self,
        client: AsyncClient,
        auth_headers,
        *,
        capabilities=None,
    ) -> str:
        resp = await client.post(
            "/bot-connectors",
            json={
                "name": f"whatsapp-{uuid.uuid4()}",
                "platform": "whatsapp",
                "config": {},
                "credentials": {
                    "access_token": "WA-TOKEN",
                    "phone_number_id": "123456789",
                    "app_secret": "wa-app-secret",
                    "verify_token": "my-verify-token",
                },
                "allowed_capabilities": capabilities or ["incident_lookup"],
                "is_enabled": True,
            },
            headers=auth_headers,
        )
        assert resp.status_code == 201, resp.text
        return resp.json()["id"]

    @staticmethod
    def _sign(secret: str, body: bytes) -> str:
        import hashlib
        import hmac as _hmac

        digest = _hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
        return f"sha256={digest}"

    @staticmethod
    def _whatsapp_text_payload(sender: str, text: str) -> dict:
        return {
            "object": "whatsapp_business_account",
            "entry": [
                {
                    "id": "WABA-ID",
                    "changes": [
                        {
                            "value": {
                                "messaging_product": "whatsapp",
                                "metadata": {
                                    "phone_number_id": "123456789",
                                    "display_phone_number": "+15550001111",
                                },
                                "messages": [
                                    {
                                        "from": sender,
                                        "id": "wamid.xxx",
                                        "timestamp": "1700000000",
                                        "type": "text",
                                        "text": {"body": text},
                                    }
                                ],
                            },
                            "field": "messages",
                        }
                    ],
                }
            ],
        }

    async def test_whatsapp_webhook_incident_lookup_replies_via_outbound(
        self, client: AsyncClient, app, auth_headers, monkeypatch
    ):
        from backend.bots.rate_limit import rate_limiter
        from backend.bots.connectors import whatsapp as whatsapp_mod

        rate_limiter.reset()

        sent: list[dict] = []

        async def fake_send(
            *, access_token, phone_number_id, recipient, text, timeout_seconds=10.0
        ):
            sent.append({"recipient": recipient, "text": text})
            return True, None

        monkeypatch.setattr(whatsapp_mod, "whatsapp_send", fake_send)

        connector_id = await self._create_whatsapp_connector(client, auth_headers)
        async with app.state.session_factory() as db:
            await IncidentRepo.create(
                db,
                TEST_ORG_ID,
                title="Cache fail",
                description="redis down",
                severity="high",
            )
            await db.commit()

        import json as _json

        payload = self._whatsapp_text_payload("15559998888", "/incidents")
        body = _json.dumps(payload).encode()
        sig = self._sign("wa-app-secret", body)

        resp = await client.post(
            f"/bot-connectors/{connector_id}/whatsapp/webhook",
            content=body,
            headers={
                "X-Hub-Signature-256": sig,
                "Content-Type": "application/json",
            },
        )
        assert resp.status_code == 200
        assert resp.json() == {"ok": True}

        # Outbound delivery is fire-and-forget; give the loop a chance.
        await asyncio.sleep(0)
        await asyncio.sleep(0)

        assert any(
            s["recipient"] == "15559998888" and "Cache fail" in s["text"] for s in sent
        )

    async def test_whatsapp_webhook_rejects_bad_signature(
        self, client: AsyncClient, auth_headers
    ):
        connector_id = await self._create_whatsapp_connector(client, auth_headers)

        import json as _json

        payload = self._whatsapp_text_payload("15559998888", "/help")
        body = _json.dumps(payload).encode()

        resp = await client.post(
            f"/bot-connectors/{connector_id}/whatsapp/webhook",
            content=body,
            headers={
                "X-Hub-Signature-256": "sha256=0000000000000000",
                "Content-Type": "application/json",
            },
        )
        assert resp.status_code == 403

    async def test_whatsapp_verification_challenge(
        self, client: AsyncClient, auth_headers
    ):
        connector_id = await self._create_whatsapp_connector(client, auth_headers)
        resp = await client.get(
            f"/bot-connectors/{connector_id}/whatsapp/webhook",
            params={
                "hub.mode": "subscribe",
                "hub.verify_token": "my-verify-token",
                "hub.challenge": "challenge-12345",
            },
        )
        assert resp.status_code == 200
        assert resp.text == "challenge-12345"

    async def test_whatsapp_verification_wrong_token(
        self, client: AsyncClient, auth_headers
    ):
        connector_id = await self._create_whatsapp_connector(client, auth_headers)
        resp = await client.get(
            f"/bot-connectors/{connector_id}/whatsapp/webhook",
            params={
                "hub.mode": "subscribe",
                "hub.verify_token": "wrong-token",
                "hub.challenge": "challenge-12345",
            },
        )
        assert resp.status_code == 403


class TestModelConfigAPI:
    async def test_list_models(self, client: AsyncClient, auth_headers, monkeypatch):
        monkeypatch.setattr(
            "backend.api.routes.models.ProviderRegistry.discover_models",
            lambda self, **kwargs: [
                {
                    "provider": "openai",
                    "label": "OpenAI",
                    "default_model_id": "gpt-4o",
                    "default_api_key_env_var": "OPENAI_API_KEY",
                    "requires_api_key": True,
                    "requires_base_url": False,
                    "requires_api_version": False,
                    "available": True,
                    "models": ["gpt-4o", "gpt-4o-mini"],
                    "error": None,
                }
            ],
        )

        resp = await client.get("/models?provider=openai", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 1
        assert data["items"][0]["provider"] == "openai"
        assert data["items"][0]["models"] == ["gpt-4o", "gpt-4o-mini"]

    async def test_list_models_passes_bedrock_region_and_profile(
        self, client: AsyncClient, auth_headers, monkeypatch
    ):
        captured_kwargs: dict[str, object] = {}

        def _discover(self, **kwargs):
            captured_kwargs.update(kwargs)
            return []

        monkeypatch.setattr(
            "backend.api.routes.models.ProviderRegistry.discover_models",
            _discover,
        )

        resp = await client.get(
            "/models?provider=bedrock&region=us-east-1&profile=prod",
            headers=auth_headers,
        )
        assert resp.status_code == 200
        assert captured_kwargs["provider"] == "bedrock"
        assert captured_kwargs["provider_meta"] == {
            "region": "us-east-1",
            "profile": "prod",
        }

    async def test_list_models_passes_vertex_project_and_location(
        self, client: AsyncClient, auth_headers, monkeypatch
    ):
        captured_kwargs: dict[str, object] = {}

        def _discover(self, **kwargs):
            captured_kwargs.update(kwargs)
            return []

        monkeypatch.setattr(
            "backend.api.routes.models.ProviderRegistry.discover_models",
            _discover,
        )

        resp = await client.get(
            "/models?provider=vertex_ai&project=opsmender-prod&location=us-central1",
            headers=auth_headers,
        )
        assert resp.status_code == 200
        assert captured_kwargs["provider"] == "vertex_ai"
        assert captured_kwargs["provider_meta"] == {
            "project": "opsmender-prod",
            "location": "us-central1",
        }

    async def test_viewer_can_list_models(
        self, client: AsyncClient, viewer_headers, monkeypatch
    ):
        monkeypatch.setattr(
            "backend.api.routes.models.ProviderRegistry.discover_models",
            lambda self, **kwargs: [],
        )

        resp = await client.get("/models", headers=viewer_headers)
        assert resp.status_code == 200
        assert resp.json() == {"items": [], "total": 0}

    async def test_update_model_config_admin(
        self, client: AsyncClient, app, auth_headers, monkeypatch
    ):
        monkeypatch.setattr(
            "backend.api.routes.config.ProviderRegistry.validate_model_config",
            lambda self, **kwargs: type(
                "_Validation",
                (),
                {"warnings": []},
            )(),
        )

        resp = await client.put(
            "/config/model",
            json={
                "name": "primary-openai",
                "provider": "openai",
                "model_id": "gpt-4o",
                "api_key_env_var": "OPENAI_API_KEY",
                "max_tokens": 8192,
                "temperature": 0.1,
                "max_concurrent_sessions": 3,
            },
            headers=auth_headers,
        )
        assert resp.status_code == 200
        data = resp.json()["config"]
        assert data["name"] == "primary-openai"
        assert data["provider"] == "openai"
        assert data["model_id"] == "gpt-4o"
        assert data["max_tokens"] == 8192
        assert data["temperature"] == 0.1
        assert data["max_concurrent_sessions"] == 3
        assert data["is_default"] is True

        async with app.state.session_factory() as db:
            default = await ModelConfigRepo.get_default(db, TEST_ORG_ID)
            assert default is not None
            assert default.name == "primary-openai"

    async def test_occupied_model_config_allows_cap_change_but_blocks_retarget_and_delete(
        self, client: AsyncClient, app, auth_headers, monkeypatch
    ):
        monkeypatch.setattr(
            "backend.api.routes.models.ProviderRegistry.validate_model_config",
            lambda self, **kwargs: type("_Validation", (), {"warnings": []})(),
        )
        async with app.state.session_factory() as db:
            model = await ModelConfigRepo.create(
                db,
                TEST_ORG_ID,
                name=f"occupied-{uuid.uuid4().hex[:6]}",
                provider="ollama",
                model_id="occupied-model",
                max_concurrent_sessions=1,
            )
            await SessionRepo.create(
                db,
                TEST_ORG_ID,
                tier=2,
                model_config_id=model.id,
                model_provider=model.provider,
                model_id=model.model_id,
            )
            await db.commit()

        cap_update = await client.put(
            f"/models/configs/{model.id}",
            json={
                "name": model.name,
                "provider": model.provider,
                "model_id": model.model_id,
                "max_concurrent_sessions": 2,
            },
            headers=auth_headers,
        )
        assert cap_update.status_code == 200, cap_update.text
        assert cap_update.json()["config"]["max_concurrent_sessions"] == 2

        retarget = await client.put(
            f"/models/configs/{model.id}",
            json={
                "name": model.name,
                "provider": model.provider,
                "model_id": "different-model",
                "max_concurrent_sessions": 2,
            },
            headers=auth_headers,
        )
        assert retarget.status_code == 409

        deleted = await client.delete(
            f"/models/configs/{model.id}",
            headers=auth_headers,
        )
        assert deleted.status_code == 409

    async def test_update_model_config_surfaces_warnings(
        self, client: AsyncClient, auth_headers, monkeypatch
    ):
        monkeypatch.setattr(
            "backend.api.routes.config.ProviderRegistry.validate_model_config",
            lambda self, **kwargs: type(
                "_Validation",
                (),
                {
                    "warnings": [
                        type(
                            "_Warning",
                            (),
                            {
                                "code": "model_not_reported",
                                "message": "Manual model ID saved with warning.",
                            },
                        )()
                    ]
                },
            )(),
        )

        resp = await client.put(
            "/config/model",
            json={
                "provider": "openai",
                "model_id": "gpt-5-custom",
                "api_key_env_var": "OPENAI_API_KEY",
            },
            headers=auth_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["config"]["model_id"] == "gpt-5-custom"
        assert data["config"]["is_default"] is True
        assert len(data["warnings"]) == 1
        assert data["warnings"][0]["code"] == "model_not_reported"

    async def test_update_model_config_persists_provider_meta(
        self, client: AsyncClient, app, auth_headers, monkeypatch
    ):
        monkeypatch.setattr(
            "backend.api.routes.config.ProviderRegistry.validate_model_config",
            lambda self, **kwargs: type("_Validation", (), {"warnings": []})(),
        )

        resp = await client.put(
            "/config/model",
            json={
                "name": "bedrock-primary",
                "provider": "bedrock",
                "model_id": "anthropic.claude-sonnet-4-6",
                "provider_meta": {"region": "us-east-1", "profile": "prod"},
            },
            headers=auth_headers,
        )
        assert resp.status_code == 200
        data = resp.json()["config"]
        assert data["provider_meta"] == {"region": "us-east-1", "profile": "prod"}

        async with app.state.session_factory() as db:
            default = await ModelConfigRepo.get_default(db, TEST_ORG_ID)
            assert default is not None
            assert default.provider_meta == {
                "region": "us-east-1",
                "profile": "prod",
            }

    async def test_update_model_config_persists_vertex_provider_meta(
        self, client: AsyncClient, app, auth_headers, monkeypatch
    ):
        monkeypatch.setattr(
            "backend.api.routes.config.ProviderRegistry.validate_model_config",
            lambda self, **kwargs: type("_Validation", (), {"warnings": []})(),
        )

        resp = await client.put(
            "/config/model",
            json={
                "name": "vertex-primary",
                "provider": "vertex_ai",
                "model_id": "google/gemini-2.5-flash",
                "provider_meta": {
                    "project": "opsmender-prod",
                    "location": "us-central1",
                },
            },
            headers=auth_headers,
        )
        assert resp.status_code == 200
        data = resp.json()["config"]
        assert data["provider_meta"] == {
            "project": "opsmender-prod",
            "location": "us-central1",
        }

        async with app.state.session_factory() as db:
            default = await ModelConfigRepo.get_default(db, TEST_ORG_ID)
            assert default is not None
            assert default.provider_meta == {
                "project": "opsmender-prod",
                "location": "us-central1",
            }

    async def test_update_model_config_validation_error(
        self, client: AsyncClient, auth_headers, monkeypatch
    ):
        def _raise(self, **kwargs):
            raise ValueError("unsupported deployment")

        monkeypatch.setattr(
            "backend.api.routes.config.ProviderRegistry.validate_model_config",
            _raise,
        )

        resp = await client.put(
            "/config/model",
            json={
                "provider": "azure_openai",
                "model_id": "bad-deployment",
                "base_url": "https://example-resource.openai.azure.com/",
                "api_version": "2024-10-21",
            },
            headers=auth_headers,
        )
        assert resp.status_code == 400
        assert "unsupported deployment" in resp.json()["detail"]

    async def test_update_model_config_viewer_forbidden(
        self, client: AsyncClient, viewer_headers
    ):
        resp = await client.put(
            "/config/model",
            json={
                "provider": "openai",
                "model_id": "gpt-4o",
            },
            headers=viewer_headers,
        )
        assert resp.status_code == 403

    async def test_list_saved_model_configs(
        self, client: AsyncClient, app, auth_headers
    ):
        async with app.state.session_factory() as db:
            await ModelConfigRepo.create(
                db,
                TEST_ORG_ID,
                name="openai-primary",
                provider="openai",
                model_id="gpt-4o",
                api_key_env_var="OPENAI_API_KEY",
                is_default=True,
            )
            await db.commit()

        resp = await client.get("/models/configs", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 1
        assert data["items"][0]["name"] == "openai-primary"

    async def test_get_model_bootstrap_status(
        self, client: AsyncClient, app, auth_headers
    ):
        resp = await client.get("/models/bootstrap", headers=auth_headers)
        assert resp.status_code == 200
        assert resp.json() == {
            "needs_setup": True,
            "has_configs": False,
            "has_default": False,
            "default_config": None,
        }

    async def test_get_model_bootstrap_status_reports_default(
        self, client: AsyncClient, app, auth_headers
    ):
        async with app.state.session_factory() as db:
            await ModelConfigRepo.create(
                db,
                TEST_ORG_ID,
                name="default-openai",
                provider="openai",
                model_id="gpt-4o",
                is_default=True,
            )
            await db.commit()

        resp = await client.get("/models/bootstrap", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["needs_setup"] is False
        assert data["has_configs"] is True
        assert data["has_default"] is True
        assert data["default_config"]["name"] == "default-openai"

    async def test_get_model_bootstrap_status_unauthenticated(
        self, client: AsyncClient
    ):
        resp = await client.get("/models/bootstrap")
        assert resp.status_code == 401

    async def test_get_model_bootstrap_status_has_configs_without_default(
        self, client: AsyncClient, app, auth_headers
    ):
        async with app.state.session_factory() as db:
            await ModelConfigRepo.create(
                db,
                TEST_ORG_ID,
                name="unset-primary",
                provider="ollama",
                model_id="llama3.2",
            )
            await db.commit()

        resp = await client.get("/models/bootstrap", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["needs_setup"] is True
        assert data["has_configs"] is True
        assert data["has_default"] is False
        assert data["default_config"] is None

    async def test_create_saved_model_config_admin(
        self, client: AsyncClient, auth_headers, monkeypatch
    ):
        monkeypatch.setattr(
            "backend.api.routes.models.ProviderRegistry.validate_model_config",
            lambda self, **kwargs: type(
                "_Validation",
                (),
                {"warnings": []},
            )(),
        )

        resp = await client.post(
            "/models/configs",
            json={
                "name": "ollama-local",
                "provider": "ollama",
                "model_id": "llama3.2",
                "base_url": "http://localhost:11434",
                "max_tokens": 4096,
                "temperature": 0.0,
            },
            headers=auth_headers,
        )
        assert resp.status_code == 201
        data = resp.json()["config"]
        assert data["name"] == "ollama-local"
        assert data["provider"] == "ollama"

    async def test_create_saved_model_config_bedrock_round_trips_provider_meta(
        self, client: AsyncClient, auth_headers, monkeypatch
    ):
        monkeypatch.setattr(
            "backend.api.routes.models.ProviderRegistry.validate_model_config",
            lambda self, **kwargs: type("_Validation", (), {"warnings": []})(),
        )

        resp = await client.post(
            "/models/configs",
            json={
                "name": "bedrock-shared",
                "provider": "bedrock",
                "model_id": "anthropic.claude-sonnet-4-6",
                "provider_meta": {"region": "us-east-1"},
            },
            headers=auth_headers,
        )
        assert resp.status_code == 201
        data = resp.json()["config"]
        assert data["provider"] == "bedrock"
        assert data["provider_meta"] == {"region": "us-east-1"}

    async def test_create_saved_model_config_vertex_round_trips_provider_meta(
        self, client: AsyncClient, auth_headers, monkeypatch
    ):
        monkeypatch.setattr(
            "backend.api.routes.models.ProviderRegistry.validate_model_config",
            lambda self, **kwargs: type("_Validation", (), {"warnings": []})(),
        )

        resp = await client.post(
            "/models/configs",
            json={
                "name": "vertex-shared",
                "provider": "vertex_ai",
                "model_id": "google/gemini-2.5-flash",
                "provider_meta": {
                    "project": "opsmender-prod",
                    "location": "us-central1",
                },
            },
            headers=auth_headers,
        )
        assert resp.status_code == 201
        data = resp.json()["config"]
        assert data["provider"] == "vertex_ai"
        assert data["provider_meta"] == {
            "project": "opsmender-prod",
            "location": "us-central1",
        }

    async def test_create_saved_model_config_duplicate_name_conflict(
        self, client: AsyncClient, auth_headers, monkeypatch
    ):
        monkeypatch.setattr(
            "backend.api.routes.models.ProviderRegistry.validate_model_config",
            lambda self, **kwargs: type(
                "_Validation",
                (),
                {"warnings": []},
            )(),
        )

        first = await client.post(
            "/models/configs",
            json={
                "name": "shared-name",
                "provider": "ollama",
                "model_id": "llama3.2",
            },
            headers=auth_headers,
        )
        assert first.status_code == 201

        second = await client.post(
            "/models/configs",
            json={
                "name": "shared-name",
                "provider": "openai",
                "model_id": "gpt-4o",
                "api_key_env_var": "OPENAI_API_KEY",
            },
            headers=auth_headers,
        )
        assert second.status_code == 409
        assert "already exists" in second.json()["detail"].lower()

    async def test_create_saved_model_config_viewer_forbidden(
        self, client: AsyncClient, viewer_headers, monkeypatch
    ):
        monkeypatch.setattr(
            "backend.api.routes.models.ProviderRegistry.validate_model_config",
            lambda self, **kwargs: type(
                "_Validation",
                (),
                {"warnings": []},
            )(),
        )

        resp = await client.post(
            "/models/configs",
            json={
                "name": "viewer-blocked",
                "provider": "ollama",
                "model_id": "llama3.2",
            },
            headers=viewer_headers,
        )
        assert resp.status_code == 403

    async def test_create_saved_model_config_validation_error_returns_400(
        self, client: AsyncClient, auth_headers, monkeypatch
    ):
        def _raise(self, **kwargs):
            raise ValueError("azure_openai requires a base_url")

        monkeypatch.setattr(
            "backend.api.routes.models.ProviderRegistry.validate_model_config",
            _raise,
        )

        resp = await client.post(
            "/models/configs",
            json={
                "name": "bad-azure",
                "provider": "azure_openai",
                "model_id": "deploy-gpt4",
                "api_version": "2024-10-21",
            },
            headers=auth_headers,
        )
        assert resp.status_code == 400
        assert "base_url" in resp.json()["detail"]

    async def test_create_saved_model_config_returns_validation_warnings(
        self, client: AsyncClient, auth_headers, monkeypatch
    ):
        monkeypatch.setattr(
            "backend.api.routes.models.ProviderRegistry.validate_model_config",
            lambda self, **kwargs: type(
                "_Validation",
                (),
                {
                    "warnings": [
                        type(
                            "_Warning",
                            (),
                            {
                                "code": "provider_unverified",
                                "message": "Could not verify provider connectivity.",
                            },
                        )()
                    ]
                },
            )(),
        )

        resp = await client.post(
            "/models/configs",
            json={
                "name": "openai-manual",
                "provider": "openai",
                "model_id": "gpt-5-custom",
                "api_key_env_var": "OPENAI_API_KEY",
            },
            headers=auth_headers,
        )

        assert resp.status_code == 201
        data = resp.json()
        assert data["config"]["name"] == "openai-manual"
        assert data["warnings"][0]["code"] == "provider_unverified"

    async def test_update_saved_model_config_admin(
        self, client: AsyncClient, app, auth_headers, monkeypatch
    ):
        monkeypatch.setattr(
            "backend.api.routes.models.ProviderRegistry.validate_model_config",
            lambda self, **kwargs: type(
                "_Validation",
                (),
                {"warnings": []},
            )(),
        )

        async with app.state.session_factory() as db:
            cfg = await ModelConfigRepo.create(
                db,
                TEST_ORG_ID,
                name="openai-primary",
                provider="openai",
                model_id="gpt-4o",
                api_key_env_var="OPENAI_API_KEY",
            )
            await db.commit()
            await db.refresh(cfg)
            config_id = cfg.id

        resp = await client.put(
            f"/models/configs/{config_id}",
            json={
                "name": "openai-primary-v2",
                "provider": "openai",
                "model_id": "gpt-4o-mini",
                "api_key_env_var": "OPENAI_API_KEY",
                "max_tokens": 2048,
                "temperature": 0.3,
            },
            headers=auth_headers,
        )
        assert resp.status_code == 200
        data = resp.json()["config"]
        assert data["name"] == "openai-primary-v2"
        assert data["model_id"] == "gpt-4o-mini"
        assert data["max_tokens"] == 2048

    async def _seed_config(self, app) -> uuid.UUID:
        async with app.state.session_factory() as db:
            cfg = await ModelConfigRepo.create(
                db,
                TEST_ORG_ID,
                name="lm-studio",
                provider="openai_compatible",
                model_id="local-model",
                base_url="http://localhost:1234/v1",
            )
            await db.commit()
            await db.refresh(cfg)
            return cfg.id

    async def test_model_config_test_connection_ok(
        self, client: AsyncClient, app, auth_headers, monkeypatch
    ):
        config_id = await self._seed_config(app)

        class _FakeProvider:
            def complete(self, prompt):
                return "pong"

        monkeypatch.setattr(
            "backend.api.routes.models.create_provider",
            lambda **kwargs: _FakeProvider(),
        )

        resp = await client.post(
            f"/models/configs/{config_id}/test", headers=auth_headers
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert data["latency_ms"] is not None
        assert "pong" in (data["detail"] or "")

    async def test_model_config_test_connection_surfaces_failure(
        self, client: AsyncClient, app, auth_headers, monkeypatch
    ):
        config_id = await self._seed_config(app)

        class _FakeProvider:
            def complete(self, prompt):
                raise RuntimeError("Connection refused to http://localhost:1234/v1")

        monkeypatch.setattr(
            "backend.api.routes.models.create_provider",
            lambda **kwargs: _FakeProvider(),
        )

        resp = await client.post(
            f"/models/configs/{config_id}/test", headers=auth_headers
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is False
        assert "Connection refused" in data["error"]

    async def test_model_config_test_connection_viewer_forbidden(
        self, client: AsyncClient, app, viewer_headers
    ):
        config_id = await self._seed_config(app)
        resp = await client.post(
            f"/models/configs/{config_id}/test", headers=viewer_headers
        )
        assert resp.status_code == 403

    async def test_model_config_test_connection_missing_404(
        self, client: AsyncClient, auth_headers
    ):
        resp = await client.post(
            f"/models/configs/{uuid.uuid4()}/test", headers=auth_headers
        )
        assert resp.status_code == 404

    async def test_delete_saved_model_config_admin(
        self, client: AsyncClient, app, auth_headers
    ):
        async with app.state.session_factory() as db:
            cfg = await ModelConfigRepo.create(
                db,
                TEST_ORG_ID,
                name="delete-me",
                provider="ollama",
                model_id="llama3.2",
            )
            await db.commit()
            await db.refresh(cfg)
            config_id = cfg.id

        resp = await client.delete(
            f"/models/configs/{config_id}",
            headers=auth_headers,
        )
        assert resp.status_code == 204

        async with app.state.session_factory() as db:
            deleted = await ModelConfigRepo.get_by_id(db, TEST_ORG_ID, config_id)
            assert deleted is None

    async def test_set_default_saved_model_config_admin(
        self, client: AsyncClient, app, auth_headers
    ):
        async with app.state.session_factory() as db:
            first = await ModelConfigRepo.create(
                db,
                TEST_ORG_ID,
                name="first",
                provider="openai",
                model_id="gpt-4o",
                is_default=True,
            )
            second = await ModelConfigRepo.create(
                db,
                TEST_ORG_ID,
                name="second",
                provider="ollama",
                model_id="llama3.2",
            )
            await db.commit()
            await db.refresh(first)
            await db.refresh(second)
            second_id = second.id

        resp = await client.post(
            f"/models/configs/{second_id}/set-default",
            headers=auth_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["is_default"] is True

        async with app.state.session_factory() as db:
            default = await ModelConfigRepo.get_default(db, TEST_ORG_ID)
            assert default is not None
            assert default.id == second_id


class TestMCPServerAPI:
    async def test_list_mcp_servers(self, client: AsyncClient, app, auth_headers):
        async with app.state.session_factory() as db:
            await MCPServerRepo.create(
                db,
                TEST_ORG_ID,
                name="k8s",
                transport="stdio",
                command="npx",
                args=["-y", "@anthropic/mcp-server-k8s"],
            )
            await db.commit()

        resp = await client.get("/mcp-servers", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 1
        assert data["items"][0]["name"] == "k8s"
        assert data["items"][0]["has_token"] is False

    async def test_create_mcp_server_admin(self, client: AsyncClient, auth_headers):
        resp = await client.post(
            "/mcp-servers",
            json={
                "name": "sourcebot",
                "transport": "http",
                "url": "https://sb.example.com/api/mcp",
                "token": "secret-token",
                "env_vars": {"DEBUG": "1"},
            },
            headers=auth_headers,
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["name"] == "sourcebot"
        assert data["transport"] == "http"
        assert data["has_token"] is True
        assert "token" not in data

    async def test_create_mcp_server_viewer_forbidden(
        self, client: AsyncClient, viewer_headers
    ):
        resp = await client.post(
            "/mcp-servers",
            json={
                "name": "denied",
                "transport": "stdio",
                "command": "echo",
            },
            headers=viewer_headers,
        )
        assert resp.status_code == 403

    async def test_update_mcp_server_admin(
        self, client: AsyncClient, app, auth_headers
    ):
        async with app.state.session_factory() as db:
            server = await MCPServerRepo.create(
                db,
                TEST_ORG_ID,
                name="remote",
                transport="sse",
                url="http://localhost:8080/sse",
                token="secret",
            )
            await db.commit()
            await db.refresh(server)
            server_id = server.id

        resp = await client.put(
            f"/mcp-servers/{server_id}",
            json={
                "name": "remote-prod",
                "transport": "http",
                "url": "https://mcp.example.com/api/mcp",
                "env_vars": {"DEBUG": "1"},
                "is_active": False,
            },
            headers=auth_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["name"] == "remote-prod"
        assert data["transport"] == "http"
        assert data["is_active"] is False
        assert data["has_token"] is True

    async def test_delete_mcp_server_admin(
        self, client: AsyncClient, app, auth_headers
    ):
        async with app.state.session_factory() as db:
            server = await MCPServerRepo.create(
                db,
                TEST_ORG_ID,
                name="delete-me",
                transport="stdio",
                command="echo",
            )
            await db.commit()
            await db.refresh(server)
            server_id = server.id

        resp = await client.delete(
            f"/mcp-servers/{server_id}",
            headers=auth_headers,
        )
        assert resp.status_code == 204

        async with app.state.session_factory() as db:
            deleted = await MCPServerRepo.get_by_id(db, TEST_ORG_ID, server_id)
            assert deleted is None

    async def test_test_mcp_server_success(
        self, client: AsyncClient, app, auth_headers, monkeypatch
    ):
        async with app.state.session_factory() as db:
            server = await MCPServerRepo.create(
                db,
                TEST_ORG_ID,
                name="k8s",
                transport="stdio",
                command="npx",
            )
            await db.commit()
            await db.refresh(server)
            server_id = server.id

        @asynccontextmanager
        async def _fake_connect(server_cfg):
            class _Session:
                pass

            yield _Session()

        class _Tool:
            def __init__(self, name: str):
                self.name = name

        async def _fake_list_tools(session):
            return [_Tool("get_pods"), _Tool("describe_pod")]

        monkeypatch.setattr("backend.api.routes.mcp_servers.connect", _fake_connect)
        monkeypatch.setattr(
            "backend.api.routes.mcp_servers.list_tools", _fake_list_tools
        )

        resp = await client.post(
            f"/mcp-servers/{server_id}/test",
            headers=auth_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert data["tool_count"] == 2
        assert data["tool_names"] == ["get_pods", "describe_pod"]

        async with app.state.session_factory() as db:
            refreshed = await MCPServerRepo.get_by_id(db, TEST_ORG_ID, server_id)
            assert refreshed is not None
            assert refreshed.last_successful_call_at is not None
            assert refreshed.last_error is None

    async def test_test_mcp_server_failure(
        self, client: AsyncClient, app, auth_headers, monkeypatch
    ):
        async with app.state.session_factory() as db:
            server = await MCPServerRepo.create(
                db,
                TEST_ORG_ID,
                name="broken",
                transport="http",
                url="https://broken.example.com/mcp",
            )
            await db.commit()
            await db.refresh(server)
            server_id = server.id

        @asynccontextmanager
        async def _failing_connect(server_cfg):
            raise RuntimeError("connection refused")
            yield None

        monkeypatch.setattr("backend.api.routes.mcp_servers.connect", _failing_connect)

        resp = await client.post(
            f"/mcp-servers/{server_id}/test",
            headers=auth_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is False
        assert "connection refused" in data["detail"]

        async with app.state.session_factory() as db:
            refreshed = await MCPServerRepo.get_by_id(db, TEST_ORG_ID, server_id)
            assert refreshed is not None
            assert refreshed.last_successful_call_at is None
            assert refreshed.last_error == "connection refused"

    async def test_list_mcp_server_statuses(
        self, client: AsyncClient, app, auth_headers
    ):
        async with app.state.session_factory() as db:
            healthy = await MCPServerRepo.create(
                db,
                TEST_ORG_ID,
                name="healthy",
                transport="stdio",
                command="echo",
            )
            stale = await MCPServerRepo.create(
                db,
                TEST_ORG_ID,
                name="stale",
                transport="stdio",
                command="echo",
            )
            broken = await MCPServerRepo.create(
                db,
                TEST_ORG_ID,
                name="broken",
                transport="stdio",
                command="echo",
            )
            await MCPServerRepo.mark_connection_success(
                db,
                TEST_ORG_ID,
                healthy.id,
                at=datetime.now(timezone.utc) - timedelta(minutes=2),
            )
            await MCPServerRepo.mark_connection_success(
                db,
                TEST_ORG_ID,
                stale.id,
                at=datetime.now(timezone.utc) - timedelta(minutes=30),
            )
            await MCPServerRepo.mark_connection_failure(
                db,
                TEST_ORG_ID,
                broken.id,
                error="timed out",
            )
            await db.commit()

        resp = await client.get("/mcp-servers/status", headers=auth_headers)
        assert resp.status_code == 200
        items = {item["server_id"]: item for item in resp.json()["items"]}
        assert items[str(healthy.id)]["status"] == "healthy"
        assert items[str(stale.id)]["status"] == "stale"
        assert items[str(broken.id)]["status"] == "error"
        assert items[str(broken.id)]["last_error"] == "timed out"

    async def test_oauth_start_returns_authorize_url(
        self, client: AsyncClient, app, auth_headers, monkeypatch
    ):
        async with app.state.session_factory() as db:
            server = await MCPServerRepo.create(
                db,
                TEST_ORG_ID,
                name="github",
                transport="http",
                url="https://mcp.example.com/api/mcp",
                env_vars={"OPSMENDER_MCP_OAUTH_SCOPES": "repo read:user"},
            )
            await db.commit()
            await db.refresh(server)
            server_id = server.id

        async def _discover(url):
            assert url == "https://mcp.example.com/api/mcp"
            return ProtectedResourceMetadata(
                resource="https://mcp.example.com/api/mcp",
                authorization_servers=["https://auth.example.com"],
            )

        async def _fetch(issuer):
            assert issuer == "https://auth.example.com"
            return AuthzServerMetadata(
                issuer="https://auth.example.com",
                authorization_endpoint="https://auth.example.com/authorize",
                token_endpoint="https://auth.example.com/token",
                registration_endpoint="https://auth.example.com/register",
                code_challenge_methods_supported=["S256"],
            )

        async def _register(metadata, *, redirect_uris):
            assert metadata.issuer == "https://auth.example.com"
            assert redirect_uris == ["http://test/mcp-servers/oauth/callback"]
            return ClientRegistration(client_id="client-1", client_secret=None)

        monkeypatch.setattr(
            "backend.api.routes.mcp_servers.discover_protected_resource_metadata",
            _discover,
        )
        monkeypatch.setattr(
            "backend.api.routes.mcp_servers.fetch_authz_server_metadata", _fetch
        )
        monkeypatch.setattr(
            "backend.api.routes.mcp_servers.register_client_dynamically", _register
        )
        monkeypatch.setattr(
            "backend.api.routes.mcp_servers.generate_pkce_pair",
            lambda: PKCEPair(code_verifier="verifier", code_challenge="challenge"),
        )

        resp = await client.get(
            f"/mcp-servers/oauth/start?id={server_id}",
            headers=auth_headers,
        )
        assert resp.status_code == 200
        url = resp.json()["authorize_url"]
        assert url.startswith("https://auth.example.com/authorize?")
        assert "client_id=client-1" in url
        assert "code_challenge=challenge" in url
        assert "resource=https%3A%2F%2Fmcp.example.com%2Fapi%2Fmcp" in url
        assert "scope=repo%20read%3Auser" in url

    async def test_oauth_start_rejects_stdio_server(
        self, client: AsyncClient, app, auth_headers
    ):
        async with app.state.session_factory() as db:
            server = await MCPServerRepo.create(
                db,
                TEST_ORG_ID,
                name="local",
                transport="stdio",
                command="npx",
            )
            await db.commit()
            await db.refresh(server)
            server_id = server.id

        resp = await client.get(
            f"/mcp-servers/oauth/start?id={server_id}",
            headers=auth_headers,
        )
        assert resp.status_code == 400

    async def test_oauth_callback_persists_encrypted_tokens(
        self, client: AsyncClient, app, monkeypatch
    ):
        async with app.state.session_factory() as db:
            server = await MCPServerRepo.create(
                db,
                TEST_ORG_ID,
                name="github",
                transport="http",
                url="https://mcp.example.com/api/mcp",
            )
            await db.commit()
            await db.refresh(server)
            server_id = server.id

        async def _fetch(issuer):
            assert issuer == "https://auth.example.com"
            return AuthzServerMetadata(
                issuer="https://auth.example.com",
                authorization_endpoint="https://auth.example.com/authorize",
                token_endpoint="https://auth.example.com/token",
                registration_endpoint="https://auth.example.com/register",
                code_challenge_methods_supported=["S256"],
            )

        async def _exchange(metadata, **kwargs):
            assert kwargs["code"] == "auth-code"
            assert kwargs["code_verifier"] == "verifier"
            assert kwargs["resource"] == "https://mcp.example.com/api/mcp"
            assert kwargs["client_registration"].client_id == "client-1"
            return TokenResponse(
                access_token="access-token",
                token_type="Bearer",
                expires_in=3600,
                refresh_token="refresh-token",
                scope=["repo"],
            )

        monkeypatch.setattr(
            "backend.api.routes.mcp_servers.fetch_authz_server_metadata", _fetch
        )
        monkeypatch.setattr("backend.api.routes.mcp_servers.exchange_code", _exchange)

        state = sign_state(
            server_id=str(server_id),
            issuer="https://auth.example.com",
            code_verifier="verifier",
            resource="https://mcp.example.com/api/mcp",
            org_id=str(TEST_ORG_ID),
            client_id="client-1",
        )
        resp = await client.get(
            "/mcp-servers/oauth/callback",
            params={
                "code": "auth-code",
                "state": state,
                "iss": "https://auth.example.com",
            },
            follow_redirects=False,
        )
        assert resp.status_code == 302
        assert "mcp_oauth=ok" in resp.headers["location"]

        async with app.state.session_factory() as db:
            from backend.db.repos import MCPServerOAuthTokenRepo

            row = await MCPServerOAuthTokenRepo.get_for_server(
                db, TEST_ORG_ID, server_id
            )
            assert row is not None
            assert row.access_token_encrypted != "access-token"
            access, refresh = await MCPServerOAuthTokenRepo.read_plaintext(row)
            assert access == "access-token"
            assert refresh == "refresh-token"
            assert row.scopes == ["repo"]

    async def test_oauth_callback_rejects_issuer_mismatch(
        self, client: AsyncClient, app
    ):
        async with app.state.session_factory() as db:
            server = await MCPServerRepo.create(
                db,
                TEST_ORG_ID,
                name="github",
                transport="http",
                url="https://mcp.example.com/api/mcp",
            )
            await db.commit()
            await db.refresh(server)
            server_id = server.id

        state = sign_state(
            server_id=str(server_id),
            issuer="https://auth.example.com",
            code_verifier="verifier",
            resource="https://mcp.example.com/api/mcp",
            org_id=str(TEST_ORG_ID),
            client_id="client-1",
        )
        resp = await client.get(
            "/mcp-servers/oauth/callback",
            params={
                "code": "auth-code",
                "state": state,
                "iss": "https://evil.example.com",
            },
            follow_redirects=False,
        )
        assert resp.status_code == 302
        assert "mcp_oauth=error" in resp.headers["location"]
        assert "iss" in resp.headers["location"].lower()


# ===========================================================================
# Approvals
# ===========================================================================


class TestApprovals:
    async def test_list_approvals(self, client: AsyncClient, app, auth_headers):
        _, request = await _create_approval_request(app)

        resp = await client.get("/approvals", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 1
        assert data["items"][0]["id"] == str(request.id)

    async def test_list_approvals_filtered_by_status(
        self, client: AsyncClient, app, auth_headers
    ):
        _, request = await _create_approval_request(app)

        resp = await client.get("/approvals?status=pending", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 1
        assert data["items"][0]["status"] == "pending"
        assert data["items"][0]["id"] == str(request.id)

    async def test_approve_request(self, client: AsyncClient, app, auth_headers):
        _, request = await _create_approval_request(app)

        resp = await client.post(
            f"/approvals/{request.id}/approve",
            headers=auth_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "approved"
        assert data["resolved_by"] is not None

    async def test_reject_request(self, client: AsyncClient, app, auth_headers):
        _, request = await _create_approval_request(app)

        resp = await client.post(
            f"/approvals/{request.id}/reject",
            headers=auth_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "rejected"

    async def test_redirect_request_stores_guidance(
        self, client: AsyncClient, app, auth_headers
    ):
        _, request = await _create_approval_request(app)
        resp = await client.post(
            f"/approvals/{request.id}/redirect",
            json={"guidance": "drain the node first, then restart the pod"},
            headers=auth_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "redirected"
        assert data["resolution_note"] == "drain the node first, then restart the pod"

    async def test_redirect_requires_guidance(
        self, client: AsyncClient, app, auth_headers
    ):
        _, request = await _create_approval_request(app)
        resp = await client.post(
            f"/approvals/{request.id}/redirect",
            json={"guidance": ""},
            headers=auth_headers,
        )
        assert resp.status_code == 422

    async def test_extend_request_resets_approval_hold(
        self, client: AsyncClient, app, auth_headers
    ):
        _, request = await _create_approval_request(app)
        original_expiry = request.expires_at

        resp = await client.post(
            f"/approvals/{request.id}/extend",
            headers=auth_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "pending"
        assert data["extension_count"] == 1
        original_utc = (
            original_expiry.replace(tzinfo=timezone.utc)
            if original_expiry.tzinfo is None
            else original_expiry
        )
        assert datetime.fromisoformat(data["expires_at"]) > original_utc

    async def test_viewer_cannot_approve(
        self, client: AsyncClient, app, viewer_headers
    ):
        _, request = await _create_approval_request(app)

        resp = await client.post(
            f"/approvals/{request.id}/approve",
            headers=viewer_headers,
        )
        assert resp.status_code == 403

    async def test_expired_request_cannot_be_approved(
        self, client: AsyncClient, app, auth_headers
    ):
        _, request = await _create_approval_request(app, expires_delta_minutes=-1)

        resp = await client.post(
            f"/approvals/{request.id}/approve",
            headers=auth_headers,
        )
        assert resp.status_code == 409
        assert "expired" in resp.json()["detail"].lower()


# ===========================================================================
# WebSocket
# ===========================================================================


class TestWebSocket:
    async def test_ws_endpoint_exists(self, app):
        """Verify the WebSocket route is registered in the app."""
        ws_paths = {
            getattr(r, "path", "")
            for r in app.routes
            if hasattr(r, "path") and "/stream" in getattr(r, "path", "")
        }
        # Per-session session stream + per-user notification stream.
        assert "/sessions/{session_id}/stream" in ws_paths
        assert "/notifications/stream" in ws_paths

    async def test_ws_publish_channel(self):
        """Test the in-memory pub/sub channel."""
        import asyncio
        from backend.api.routes.ws import get_channel, publish, remove_channel
        from backend.api.schemas import WSMessage

        session_id = uuid.uuid4()
        queue = get_channel(session_id)

        msg = WSMessage(type="node_transition", data={"node": "observe"})
        await publish(session_id, msg)

        result = await asyncio.wait_for(queue.get(), timeout=1.0)
        assert result["type"] == "node_transition"
        assert result["data"]["node"] == "observe"

        remove_channel(session_id, queue)

    async def test_ws_publish_no_subscribers(self):
        """Publishing to a session with no listeners should not error."""
        from backend.api.routes.ws import publish
        from backend.api.schemas import WSMessage

        msg = WSMessage(type="session_end", data={})
        await publish(uuid.uuid4(), msg)  # should not raise

    async def test_approval_resolution_publishes_ws_event(
        self, client: AsyncClient, app, auth_headers
    ):
        import asyncio
        from backend.api.routes.ws import get_channel, remove_channel

        session, request = await _create_approval_request(app)
        queue = get_channel(session.id)
        try:
            resp = await client.post(
                f"/approvals/{request.id}/approve",
                headers=auth_headers,
            )
            assert resp.status_code == 200

            msg = await asyncio.wait_for(queue.get(), timeout=1.0)
            assert msg["type"] == "approval_resolved"
            assert msg["data"]["id"] == str(request.id)
            assert msg["data"]["status"] == "approved"
        finally:
            remove_channel(session.id, queue)


class TestResolveLLM:
    """Regression coverage for session_runner._resolve_llm."""

    async def test_resolve_llm_recovers_base_url_for_openai_compatible(self, app):
        """A session storing only provider+model_id must recover the full
        ModelConfig (base_url etc.) so openai_compatible doesn't fail at start."""
        from backend.api.session_runner import _resolve_llm
        from backend.llm.providers import OpenAICompatibleProvider

        factory = app.state.session_factory
        async with factory() as db:
            await ModelConfigRepo.create(
                db,
                TEST_ORG_ID,
                name="LM Studio",
                provider="openai_compatible",
                model_id="local-model",
                base_url="http://localhost:1234/v1",
            )
            session = await SessionRepo.create(
                db,
                TEST_ORG_ID,
                tier=0,
                model_provider="openai_compatible",
                model_id="local-model",
            )
            await db.commit()

        llm = await _resolve_llm(factory, session)
        assert isinstance(llm.provider, OpenAICompatibleProvider)
        assert llm.provider.base_url == "http://localhost:1234/v1"


class TestIncidentCombine:
    """v1.2 — Combine (merge) incidents."""

    async def _mk_incident(self, client, headers, service_id, title):
        resp = await client.post(
            "/incidents",
            json={"title": title, "description": "d", "service_id": str(service_id)},
            headers=headers,
        )
        assert resp.status_code == 201
        return resp.json()["id"]

    async def test_combine_folds_secondaries_into_primary(
        self, client: AsyncClient, app, auth_headers
    ):
        service = await _seed_manual_incident_service(app, "Combine")
        primary = await self._mk_incident(client, auth_headers, service.id, "Primary")
        sec1 = await self._mk_incident(client, auth_headers, service.id, "Dup A")
        sec2 = await self._mk_incident(client, auth_headers, service.id, "Dup B")

        # A comment on a secondary should move to the primary.
        await client.post(
            f"/incidents/{sec1}/comments",
            json={"body": "note on dup A"},
            headers=auth_headers,
        )
        # A running session on a secondary should be stopped by the combine.
        async with app.state.session_factory() as db:
            running = await SessionRepo.create(
                db, TEST_ORG_ID, tier=0, incident_id=uuid.UUID(sec1)
            )
            await db.commit()

        resp = await client.post(
            f"/incidents/{primary}/combine",
            json={"secondary_ids": [sec1, sec2], "note": "same root cause"},
            headers=auth_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert set(data["merged_incident_ids"]) == {sec1, sec2}
        assert data["moved_comments"] == 1
        assert data["stopped_sessions"] == 1

        async with app.state.session_factory() as db:
            for sid in (sec1, sec2):
                inc = await IncidentRepo.get_by_id(db, TEST_ORG_ID, uuid.UUID(sid))
                assert inc.status == "merged"
                assert str(inc.merged_into_incident_id) == primary
            running_after = await SessionRepo.get_by_id(db, TEST_ORG_ID, running.id)
            assert running_after.status == "stopped"

        # The moved comment + two system notes + the operator note live on primary.
        comments = (
            await client.get(f"/incidents/{primary}/comments", headers=auth_headers)
        ).json()
        bodies = [c["body"] for c in comments["items"]]
        assert any("note on dup A" in b for b in bodies)
        assert any("Combined incident" in b for b in bodies)
        assert any("same root cause" in b for b in bodies)

    async def test_merged_incidents_hidden_from_default_list(
        self, client: AsyncClient, app, auth_headers
    ):
        service = await _seed_manual_incident_service(app, "CombineHide")
        primary = await self._mk_incident(client, auth_headers, service.id, "Primary")
        sec = await self._mk_incident(client, auth_headers, service.id, "Dup")
        await client.post(
            f"/incidents/{primary}/combine",
            json={"secondary_ids": [sec]},
            headers=auth_headers,
        )

        default_list = (await client.get("/incidents", headers=auth_headers)).json()
        ids = {i["id"] for i in default_list["items"]}
        assert sec not in ids
        assert primary in ids

        # Explicitly requesting merged surfaces them.
        merged_list = (
            await client.get("/incidents?status=merged", headers=auth_headers)
        ).json()
        assert sec in {i["id"] for i in merged_list["items"]}

        # The primary's merged-children endpoint lists the secondary.
        children = (
            await client.get(f"/incidents/{primary}/merged", headers=auth_headers)
        ).json()
        assert {i["id"] for i in children["items"]} == {sec}

    async def test_combine_rejects_self_and_unknown_and_merged(
        self, client: AsyncClient, app, auth_headers
    ):
        service = await _seed_manual_incident_service(app, "CombineBad")
        primary = await self._mk_incident(client, auth_headers, service.id, "Primary")
        sec = await self._mk_incident(client, auth_headers, service.id, "Dup")

        # self
        r = await client.post(
            f"/incidents/{primary}/combine",
            json={"secondary_ids": [primary]},
            headers=auth_headers,
        )
        assert r.status_code == 400

        # unknown secondary
        r = await client.post(
            f"/incidents/{primary}/combine",
            json={"secondary_ids": [str(uuid.uuid4())]},
            headers=auth_headers,
        )
        assert r.status_code == 404

        # already-merged secondary → 409 on a second combine
        await client.post(
            f"/incidents/{primary}/combine",
            json={"secondary_ids": [sec]},
            headers=auth_headers,
        )
        r = await client.post(
            f"/incidents/{primary}/combine",
            json={"secondary_ids": [sec]},
            headers=auth_headers,
        )
        assert r.status_code == 409

    async def test_combine_viewer_forbidden(
        self, client: AsyncClient, app, auth_headers, viewer_headers
    ):
        service = await _seed_manual_incident_service(app, "CombineRbac")
        primary = await self._mk_incident(client, auth_headers, service.id, "Primary")
        sec = await self._mk_incident(client, auth_headers, service.id, "Dup")
        r = await client.post(
            f"/incidents/{primary}/combine",
            json={"secondary_ids": [sec]},
            headers=viewer_headers,
        )
        assert r.status_code == 403


class TestNotifications:
    """In-app notification center (the bell) HTTP endpoints."""

    @staticmethod
    async def _seed(app, user_username: str, *, count: int, title_prefix: str = "N"):
        """Create *count* notifications directly for the named user."""
        from backend.db.repos import InAppNotificationRepo, UserRepo

        async with app.state.session_factory() as db:
            user = await UserRepo.get_by_username(db, user_username)
            ids = []
            for i in range(count):
                n = await InAppNotificationRepo.create(
                    db,
                    TEST_ORG_ID,
                    user.id,
                    event_type="incident.assigned",
                    category="incident",
                    title=f"{title_prefix}{i}",
                    link="/dashboard/incidents/x",
                )
                ids.append(n.id)
            await db.commit()
            return ids

    async def test_list_empty(self, client: AsyncClient, auth_headers):
        r = await client.get("/notifications", headers=auth_headers)
        assert r.status_code == 200
        body = r.json()
        assert body == {"items": [], "total": 0, "unread": 0}

    async def test_list_and_unread_count(self, client: AsyncClient, app, auth_headers):
        await self._seed(app, "testadmin", count=3)
        r = await client.get("/notifications", headers=auth_headers)
        body = r.json()
        assert body["total"] == 3
        assert body["unread"] == 3
        assert len(body["items"]) == 3
        # newest first
        assert body["items"][0]["title"] == "N2"

        rc = await client.get("/notifications/unread-count", headers=auth_headers)
        assert rc.json() == {"unread": 3}

    async def test_mark_read_then_unread(self, client: AsyncClient, app, auth_headers):
        ids = await self._seed(app, "testadmin", count=2)
        r = await client.post(f"/notifications/{ids[0]}/read", headers=auth_headers)
        assert r.status_code == 204
        rc = await client.get("/notifications/unread-count", headers=auth_headers)
        assert rc.json()["unread"] == 1
        # unread_only filter
        r = await client.get(
            "/notifications", params={"unread_only": True}, headers=auth_headers
        )
        assert r.json()["total"] == 2  # total ignores the filter
        assert len(r.json()["items"]) == 1
        # flip back to unread
        r = await client.post(
            f"/notifications/{ids[0]}/read",
            params={"read": False},
            headers=auth_headers,
        )
        assert r.status_code == 204
        rc = await client.get("/notifications/unread-count", headers=auth_headers)
        assert rc.json()["unread"] == 2

    async def test_mark_all_read(self, client: AsyncClient, app, auth_headers):
        await self._seed(app, "testadmin", count=4)
        r = await client.post("/notifications/read-all", headers=auth_headers)
        assert r.status_code == 200
        assert r.json() == {"updated": 4}
        rc = await client.get("/notifications/unread-count", headers=auth_headers)
        assert rc.json()["unread"] == 0

    async def test_delete(self, client: AsyncClient, app, auth_headers):
        ids = await self._seed(app, "testadmin", count=2)
        r = await client.delete(f"/notifications/{ids[0]}", headers=auth_headers)
        assert r.status_code == 204
        r = await client.get("/notifications", headers=auth_headers)
        assert r.json()["total"] == 1

    async def test_mark_read_missing_404(self, client: AsyncClient, auth_headers):
        import uuid as _uuid

        r = await client.post(
            f"/notifications/{_uuid.uuid4()}/read", headers=auth_headers
        )
        assert r.status_code == 404

    async def test_user_isolation(
        self, client: AsyncClient, app, auth_headers, viewer_headers
    ):
        # Notifications belong to the admin; the viewer must not see or touch them.
        ids = await self._seed(app, "testadmin", count=2)
        r = await client.get("/notifications", headers=viewer_headers)
        assert r.json()["total"] == 0
        # viewer cannot mark the admin's notification read (scoped → 404)
        r = await client.post(f"/notifications/{ids[0]}/read", headers=viewer_headers)
        assert r.status_code == 404
        r = await client.delete(f"/notifications/{ids[0]}", headers=viewer_headers)
        assert r.status_code == 404

    async def test_requires_auth(self, client: AsyncClient):
        r = await client.get("/notifications")
        assert r.status_code == 401


class TestNotificationEventHooks:
    """Phase 2: incident events raise in-app notifications for recipients."""

    @staticmethod
    async def _seed_incident(app, title="Disk pressure"):
        from backend.db.repos import IncidentRepo

        async with app.state.session_factory() as db:
            inc = await IncidentRepo.create(
                db, TEST_ORG_ID, title=title, description="seeded"
            )
            await db.commit()
            return inc.id

    @staticmethod
    async def _viewer_id(app):
        from backend.db.repos import UserRepo

        async with app.state.session_factory() as db:
            return (await UserRepo.get_by_username(db, "viewer1")).id

    async def test_assign_to_other_notifies(
        self, client: AsyncClient, app, auth_headers, viewer_headers
    ):
        inc_id = await self._seed_incident(app)
        viewer_id = await self._viewer_id(app)
        r = await client.post(
            f"/incidents/{inc_id}/assign",
            json={"user_id": str(viewer_id)},
            headers=auth_headers,
        )
        assert r.status_code == 200
        nr = await client.get("/notifications", headers=viewer_headers)
        body = nr.json()
        assert body["unread"] == 1
        item = body["items"][0]
        assert item["event_type"] == "incident.assigned"
        assert item["category"] == "incident"
        assert item["link"] == f"/dashboard/incidents/{inc_id}"
        assert item["incident_id"] == str(inc_id)

    async def test_self_ack_is_silent(self, client: AsyncClient, app, auth_headers):
        inc_id = await self._seed_incident(app)
        # admin assigns themselves → no self-notification
        r = await client.post(
            f"/incidents/{inc_id}/assign", json={}, headers=auth_headers
        )
        assert r.status_code == 200
        nr = await client.get("/notifications/unread-count", headers=auth_headers)
        assert nr.json()["unread"] == 0

    async def test_combine_notifies_secondary_assignee(
        self, client: AsyncClient, app, auth_headers, viewer_headers
    ):
        primary = await self._seed_incident(app, "Primary")
        secondary = await self._seed_incident(app, "Secondary")
        viewer_id = await self._viewer_id(app)
        # assign the secondary to the viewer
        await client.post(
            f"/incidents/{secondary}/assign",
            json={"user_id": str(viewer_id)},
            headers=auth_headers,
        )
        # drain the assignment notification
        await client.post("/notifications/read-all", headers=viewer_headers)
        # admin combines secondary into primary
        r = await client.post(
            f"/incidents/{primary}/combine",
            json={"secondary_ids": [str(secondary)]},
            headers=auth_headers,
        )
        assert r.status_code == 200
        nr = await client.get(
            "/notifications", params={"unread_only": True}, headers=viewer_headers
        )
        items = nr.json()["items"]
        assert len(items) == 1
        assert items[0]["event_type"] == "incident.combined"
        assert items[0]["link"] == f"/dashboard/incidents/{primary}"


class TestNotificationMentions:
    """Phase 3: @mention in an incident comment notifies the mentioned user."""

    async def test_mention_notifies(
        self, client: AsyncClient, app, auth_headers, viewer_headers
    ):
        from backend.db.repos import IncidentRepo

        async with app.state.session_factory() as db:
            inc = await IncidentRepo.create(
                db, TEST_ORG_ID, title="Latency spike", description="x"
            )
            await db.commit()
            inc_id = inc.id
        # admin comments mentioning viewer1
        r = await client.post(
            f"/incidents/{inc_id}/comments",
            json={"body": "Can you take a look @viewer1?"},
            headers=auth_headers,
        )
        assert r.status_code in (200, 201)
        nr = await client.get("/notifications", headers=viewer_headers)
        items = nr.json()["items"]
        assert len(items) == 1
        assert items[0]["event_type"] == "mention.comment"
        assert items[0]["category"] == "mention"
        assert items[0]["link"] == f"/dashboard/incidents/{inc_id}"

    async def test_no_self_mention(self, client: AsyncClient, app, auth_headers):
        from backend.db.repos import IncidentRepo

        async with app.state.session_factory() as db:
            inc = await IncidentRepo.create(
                db, TEST_ORG_ID, title="Self", description="x"
            )
            await db.commit()
            inc_id = inc.id
        await client.post(
            f"/incidents/{inc_id}/comments",
            json={"body": "note to self @testadmin"},
            headers=auth_headers,
        )
        nr = await client.get("/notifications/unread-count", headers=auth_headers)
        assert nr.json()["unread"] == 0


class TestNotificationPreferences:
    """Phase 5: per-category mute + quiet-hours preferences."""

    async def test_defaults(self, client: AsyncClient, auth_headers):
        r = await client.get("/notifications/preferences", headers=auth_headers)
        assert r.status_code == 200
        body = r.json()
        assert body["muted_categories"] == []
        assert body["quiet_hours"] is None
        assert set(body["categories"]) == {
            "incident",
            "approval",
            "session",
            "mention",
            "reliability",
            "account",
        }

    async def test_set_mute_and_quiet_hours(self, client: AsyncClient, auth_headers):
        r = await client.put(
            "/notifications/preferences",
            json={
                "muted_categories": ["session", "incident"],
                "quiet_hours": {
                    "enabled": True,
                    "start": "22:00",
                    "end": "07:00",
                    "tz": "UTC",
                },
            },
            headers=auth_headers,
        )
        assert r.status_code == 200
        body = r.json()
        # canonical order preserved (incident before session)
        assert body["muted_categories"] == ["incident", "session"]
        assert body["quiet_hours"]["enabled"] is True
        # persists
        r = await client.get("/notifications/preferences", headers=auth_headers)
        assert r.json()["muted_categories"] == ["incident", "session"]

    async def test_invalid_category_422(self, client: AsyncClient, auth_headers):
        r = await client.put(
            "/notifications/preferences",
            json={"muted_categories": ["bogus"]},
            headers=auth_headers,
        )
        assert r.status_code == 422

    async def test_bad_quiet_hours_422(self, client: AsyncClient, auth_headers):
        r = await client.put(
            "/notifications/preferences",
            json={"quiet_hours": {"enabled": True, "start": "9am", "end": "5pm"}},
            headers=auth_headers,
        )
        assert r.status_code == 422

    async def test_mute_suppresses_notification(
        self, client: AsyncClient, app, auth_headers, viewer_headers
    ):
        # viewer mutes the "incident" category, then admin assigns them an incident
        r = await client.put(
            "/notifications/preferences",
            json={"muted_categories": ["incident"]},
            headers=viewer_headers,
        )
        assert r.status_code == 200
        from backend.db.repos import IncidentRepo, UserRepo

        async with app.state.session_factory() as db:
            viewer = await UserRepo.get_by_username(db, "viewer1")
            inc = await IncidentRepo.create(
                db, TEST_ORG_ID, title="Muted", description="x"
            )
            await db.commit()
            inc_id, viewer_id = inc.id, viewer.id
        await client.post(
            f"/incidents/{inc_id}/assign",
            json={"user_id": str(viewer_id)},
            headers=auth_headers,
        )
        nr = await client.get("/notifications/unread-count", headers=viewer_headers)
        assert nr.json()["unread"] == 0


class TestNotificationAccountEvents:
    """Phase 4: account events (role change) notify the affected user."""

    async def test_role_change_notifies(
        self, client: AsyncClient, app, auth_headers, viewer_headers
    ):
        from backend.db.repos import UserRepo

        async with app.state.session_factory() as db:
            viewer = await UserRepo.get_by_username(db, "viewer1")
            viewer_id = viewer.id
        r = await client.patch(
            f"/auth/users/{viewer_id}",
            json={"role": "operator"},
            headers=auth_headers,
        )
        assert r.status_code == 200
        nr = await client.get("/notifications", headers=viewer_headers)
        items = nr.json()["items"]
        assert len(items) == 1
        assert items[0]["event_type"] == "account.role_changed"
        assert items[0]["category"] == "account"

    async def test_no_op_role_change_silent(
        self, client: AsyncClient, app, auth_headers, viewer_headers
    ):
        from backend.db.repos import UserRepo

        async with app.state.session_factory() as db:
            viewer_id = (await UserRepo.get_by_username(db, "viewer1")).id
        # set the same role (viewer) → no change, no notification
        r = await client.patch(
            f"/auth/users/{viewer_id}",
            json={"role": "viewer"},
            headers=auth_headers,
        )
        assert r.status_code == 200
        nr = await client.get("/notifications/unread-count", headers=viewer_headers)
        assert nr.json()["unread"] == 0
