"""Tests for the FastAPI REST layer — Sprint 8.

Uses in-memory SQLite via aiosqlite so no Postgres is needed.
Tests the full API surface: auth, incidents, sessions, audit, config.
"""

from __future__ import annotations

import asyncio
import uuid
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
    IncidentRepo,
    MCPServerRepo,
    ModelConfigRepo,
    SessionRepo,
    UserRepo,
    WebhookTriggerRepo,
    WorkflowProfileRepo,
)

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

    tmp_env = tmp_path / ".env"
    tmp_env.write_text(
        "AIM_TIER=2\n"
        "AIM_LOG_LEVEL=INFO\n"
        "AIM_AUDIT_LOG=./logs/audit.jsonl\n"
        "AIM_JWT_SECRET=test-secret\n"
        "AIM_DATABASE_URL=sqlite+aiosqlite://\n"
        f"AIM_MCP_SERVERS_JSON={json.dumps([])}\n"
    )
    set_env_path(tmp_env)

    application = create_app()
    application.state.session_factory = factory

    # Override the DB dependency to use our in-memory factory
    application.dependency_overrides[get_db] = _override_get_db(factory)

    yield application

    set_env_path(None)
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
async def auth_headers(client: AsyncClient) -> dict[str, str]:
    """Register + login a user and return auth headers."""
    await client.post(
        "/auth/register",
        json={
            "username": "testadmin",
            "email": "admin@test.com",
            "password": "securepass123",
        },
    )
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
async def viewer_headers(client: AsyncClient, auth_headers) -> dict[str, str]:
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
        session = await SessionRepo.create(db, tier=tier)
        request = await ApprovalRequestRepo.create(
            db,
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


# ===========================================================================
# Incidents
# ===========================================================================


class TestIncidents:

    async def test_create_incident(self, client: AsyncClient, auth_headers):
        resp = await client.post(
            "/incidents",
            json={
                "title": "High CPU on api-server",
                "description": "CPU at 95% for 10 minutes",
                "severity": "high",
            },
            headers=auth_headers,
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["title"] == "High CPU on api-server"
        assert data["status"] == "open"
        assert data["severity"] == "high"

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

    async def test_list_incidents(self, client: AsyncClient, auth_headers):
        # Create two incidents
        await client.post(
            "/incidents",
            json={
                "title": "Inc1",
                "description": "d1",
            },
            headers=auth_headers,
        )
        await client.post(
            "/incidents",
            json={
                "title": "Inc2",
                "description": "d2",
            },
            headers=auth_headers,
        )

        resp = await client.get("/incidents", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 2
        assert len(data["items"]) == 2

    async def test_list_incidents_with_status_filter(
        self, client: AsyncClient, auth_headers
    ):
        await client.post(
            "/incidents",
            json={
                "title": "Open",
                "description": "d",
            },
            headers=auth_headers,
        )
        await client.post(
            "/incidents",
            json={
                "title": "Open2",
                "description": "d",
            },
            headers=auth_headers,
        )

        resp = await client.get("/incidents?status=open", headers=auth_headers)
        assert resp.status_code == 200
        assert resp.json()["total"] == 2

        resp = await client.get("/incidents?status=resolved", headers=auth_headers)
        assert resp.json()["total"] == 0

    async def test_get_incident(self, client: AsyncClient, auth_headers):
        create_resp = await client.post(
            "/incidents",
            json={
                "title": "Look me up",
                "description": "d",
            },
            headers=auth_headers,
        )
        inc_id = create_resp.json()["id"]

        resp = await client.get(f"/incidents/{inc_id}", headers=auth_headers)
        assert resp.status_code == 200
        assert resp.json()["title"] == "Look me up"

    async def test_list_sessions_for_incident(self, client: AsyncClient, auth_headers):
        incident = await client.post(
            "/incidents",
            json={
                "title": "Timeline target",
                "description": "d",
            },
            headers=auth_headers,
        )
        incident_id = incident.json()["id"]

        await client.post(
            "/sessions",
            json={
                "tier": 2,
                "incident_id": incident_id,
            },
            headers=auth_headers,
        )
        await client.post(
            "/sessions",
            json={
                "tier": 1,
                "incident_id": incident_id,
            },
            headers=auth_headers,
        )

        other_incident = await client.post(
            "/incidents",
            json={
                "title": "Other",
                "description": "d",
            },
            headers=auth_headers,
        )
        await client.post(
            "/sessions",
            json={
                "tier": 3,
                "incident_id": other_incident.json()["id"],
            },
            headers=auth_headers,
        )

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

    async def test_get_incident_not_found(self, client: AsyncClient, auth_headers):
        fake_id = uuid.uuid4()
        resp = await client.get(f"/incidents/{fake_id}", headers=auth_headers)
        assert resp.status_code == 404

    async def test_list_incidents_pagination(self, client: AsyncClient, auth_headers):
        for i in range(5):
            await client.post(
                "/incidents",
                json={
                    "title": f"Inc-{i}",
                    "description": "d",
                },
                headers=auth_headers,
            )

        resp = await client.get("/incidents?limit=2&offset=0", headers=auth_headers)
        data = resp.json()
        assert len(data["items"]) == 2
        assert data["total"] == 5


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
        inc_resp = await client.post(
            "/incidents",
            json={
                "title": "T",
                "description": "d",
            },
            headers=auth_headers,
        )
        inc_id = inc_resp.json()["id"]

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
                "tier": 3,
            },
            headers=auth_headers,
        )
        sess_id = create_resp.json()["id"]

        resp = await client.get(f"/sessions/{sess_id}", headers=auth_headers)
        assert resp.status_code == 200
        assert resp.json()["tier"] == 3

    async def test_get_session_not_found(self, client: AsyncClient, auth_headers):
        resp = await client.get(f"/sessions/{uuid.uuid4()}", headers=auth_headers)
        assert resp.status_code == 404

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
            entries = await AuditEntryRepo.list_by_session(db, uuid.UUID(session_id))
        assert entries == []

    async def test_create_session_with_incident_autoruns_workflow(
        self, client: AsyncClient, app, auth_headers, monkeypatch
    ):
        app.state.workflow_start_delay_seconds = 0
        published: list[tuple[str, dict[str, object]]] = []

        async def _capture_publish(session_id, message):
            published.append((message.type, dict(message.data)))

        monkeypatch.setattr("backend.api.session_runner.publish", _capture_publish)

        inc_resp = await client.post(
            "/incidents",
            json={
                "title": "API-launched workflow",
                "description": "pods restarting in production",
                "severity": "high",
            },
            headers=auth_headers,
        )
        assert inc_resp.status_code == 201
        inc_id = inc_resp.json()["id"]

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
            entries = await AuditEntryRepo.list_by_session(db, uuid.UUID(session_id))
        entry_types = [entry.entry_type for entry in entries]
        assert "session_start" in entry_types
        assert "session_end" in entry_types

    async def test_session_created_webhook_trigger_fires(
        self, client: AsyncClient, app, auth_headers, monkeypatch
    ):
        deliveries: list[tuple[str, dict[str, object], dict[str, str]]] = []

        async def _post_json(url, *, payload, headers, timeout_seconds=10.0):
            deliveries.append((url, payload, headers))

            class _Resp:
                status_code = 204

                def raise_for_status(self):
                    return None

            return _Resp()

        monkeypatch.setattr("backend.webhooks.service.post_json", _post_json)

        async with app.state.session_factory() as db:
            await WebhookTriggerRepo.create(
                db,
                name="created",
                url="https://hooks.example/session-created",
                event_types=["session.created"],
                headers={"X-AIM-Test": "1"},
                token="abc123",
            )
            await db.commit()

        resp = await client.post("/sessions", json={"tier": 2}, headers=auth_headers)
        assert resp.status_code == 201
        session_id = resp.json()["id"]

        await asyncio.sleep(0.05)

        assert len(deliveries) == 1
        url, payload, headers = deliveries[0]
        assert url == "https://hooks.example/session-created"
        assert payload["event"] == "session.created"
        assert payload["session"]["id"] == session_id
        assert headers["Authorization"] == "Bearer abc123"
        assert headers["X-AIM-Test"] == "1"

    async def test_session_terminal_webhook_trigger_fires(
        self, client: AsyncClient, app, auth_headers, monkeypatch
    ):
        app.state.workflow_start_delay_seconds = 0
        deliveries: list[tuple[str, dict[str, object]]] = []

        async def _post_json(url, *, payload, headers, timeout_seconds=10.0):
            deliveries.append((url, payload))

            class _Resp:
                status_code = 200

                def raise_for_status(self):
                    return None

            return _Resp()

        monkeypatch.setattr("backend.webhooks.service.post_json", _post_json)

        async with app.state.session_factory() as db:
            await WebhookTriggerRepo.create(
                db,
                name="completed",
                url="https://hooks.example/session-complete",
                event_types=["session.completed"],
            )
            incident = await IncidentRepo.create(
                db,
                title="Webhook terminal test",
                description="terminal state hook",
                severity="high",
            )
            await db.commit()
            incident_id = incident.id

        resp = await client.post(
            "/sessions",
            json={"incident_id": str(incident_id), "tier": 2},
            headers=auth_headers,
        )
        assert resp.status_code == 201
        session_id = resp.json()["id"]

        await _wait_for_session_status(
            client,
            session_id,
            auth_headers,
            statuses={"completed"},
        )
        await asyncio.sleep(0.05)

        assert any(
            payload["event"] == "session.completed"
            and payload["session"]["id"] == session_id
            and payload["session"]["status"] == "completed"
            for _, payload in deliveries
        )

    async def test_approval_pause_webhook_trigger_fires(
        self, client: AsyncClient, app, auth_headers, monkeypatch
    ):
        app.state.workflow_start_delay_seconds = 0
        deliveries: list[dict[str, object]] = []

        async def _post_json(url, *, payload, headers, timeout_seconds=10.0):
            deliveries.append(payload)

            class _Resp:
                status_code = 200

                def raise_for_status(self):
                    return None

            return _Resp()

        monkeypatch.setattr("backend.webhooks.service.post_json", _post_json)

        async def _resolve_llm(factory, session):
            from backend.agent.llm import StubLLM

            return StubLLM(
                response=json.dumps(
                    [
                        {
                            "tool_name": "delete_pod",
                            "tool_parameters": {"pod": "api-123"},
                            "justification": "Force approval path",
                        }
                    ]
                )
            )

        monkeypatch.setattr("backend.api.session_runner._resolve_llm", _resolve_llm)

        async with app.state.session_factory() as db:
            await WebhookTriggerRepo.create(
                db,
                name="approval-pause",
                url="https://hooks.example/approval",
                event_types=["session.awaiting_approval"],
            )
            incident = await IncidentRepo.create(
                db,
                title="Approval webhook test",
                description="delete_pod should require approval",
                severity="critical",
            )
            await db.commit()
            incident_id = incident.id

        resp = await client.post(
            "/sessions",
            json={"incident_id": str(incident_id), "tier": 1},
            headers=auth_headers,
        )
        assert resp.status_code == 201
        session_id = resp.json()["id"]

        await _wait_for_session_status(
            client,
            session_id,
            auth_headers,
            statuses={"awaiting_approval", "completed", "failed", "timed_out"},
        )
        await asyncio.sleep(0.05)

        assert any(
            payload["event"] == "session.awaiting_approval"
            and payload["session"]["id"] == session_id
            for payload in deliveries
        )


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
        assert data["ingest_auto_start_enabled"] is False
        assert data["ingest_auto_start_min_severity"] == "critical"
        assert data["ingest_auto_start_source"] is None


# ===========================================================================
# Webhook triggers
# ===========================================================================


class TestWebhookTriggers:

    async def test_create_list_update_delete_and_test_trigger(
        self, client: AsyncClient, app, auth_headers, monkeypatch
    ):
        deliveries: list[tuple[str, dict[str, object], dict[str, str]]] = []

        async def _post_json(url, *, payload, headers, timeout_seconds=10.0):
            deliveries.append((url, payload, headers))

            class _Resp:
                status_code = 202

                def raise_for_status(self):
                    return None

            return _Resp()

        monkeypatch.setattr("backend.webhooks.service.post_json", _post_json)

        create_resp = await client.post(
            "/webhook-triggers",
            json={
                "name": "ops-webhook",
                "url": "https://hooks.example/ops",
                "format": "generic",
                "event_types": ["session.completed", "session.failed"],
                "headers": {"X-Team": "ops"},
                "token": "secret-token",
                "is_active": True,
            },
            headers=auth_headers,
        )
        assert create_resp.status_code == 201
        trigger_id = create_resp.json()["id"]
        assert create_resp.json()["format"] == "generic"
        assert create_resp.json()["has_token"] is True
        assert create_resp.json()["header_names"] == ["X-Team"]

        list_resp = await client.get("/webhook-triggers", headers=auth_headers)
        assert list_resp.status_code == 200
        assert list_resp.json()["total"] == 1

        update_resp = await client.put(
            f"/webhook-triggers/{trigger_id}",
            json={
                "name": "ops-webhook",
                "url": "https://hooks.example/ops-v2",
                "format": "slack",
                "event_types": ["session.completed"],
                "headers": {"X-Team": "platform"},
                "is_active": False,
            },
            headers=auth_headers,
        )
        assert update_resp.status_code == 200
        assert update_resp.json()["url"] == "https://hooks.example/ops-v2"
        assert update_resp.json()["format"] == "slack"
        assert update_resp.json()["is_active"] is False

        test_resp = await client.post(
            f"/webhook-triggers/{trigger_id}/test",
            headers=auth_headers,
        )
        assert test_resp.status_code == 200
        assert test_resp.json()["success"] is True
        assert test_resp.json()["event_type"] == "webhook.test"

        await asyncio.sleep(0.05)
        assert deliveries
        _, payload, headers = deliveries[-1]
        assert payload["text"].startswith("AIM Webhook.Test:")
        assert payload["blocks"]
        assert headers["Authorization"] == "Bearer secret-token"
        assert headers["X-Team"] == "platform"

        delete_resp = await client.delete(
            f"/webhook-triggers/{trigger_id}",
            headers=auth_headers,
        )
        assert delete_resp.status_code == 204

    async def test_update_trigger_preserves_headers_unless_cleared(
        self, client: AsyncClient, auth_headers
    ):
        create_resp = await client.post(
            "/webhook-triggers",
            json={
                "name": "preserve-headers",
                "url": "https://hooks.example/ops",
                "format": "generic",
                "event_types": ["session.completed"],
                "headers": {"X-Team": "ops", "X-Region": "us"},
                "is_active": True,
            },
            headers=auth_headers,
        )
        assert create_resp.status_code == 201
        trigger_id = create_resp.json()["id"]

        update_resp = await client.put(
            f"/webhook-triggers/{trigger_id}",
            json={
                "name": "preserve-headers",
                "url": "https://hooks.example/ops-v2",
                "format": "generic",
                "event_types": ["session.failed"],
                "is_active": True,
            },
            headers=auth_headers,
        )
        assert update_resp.status_code == 200
        assert sorted(update_resp.json()["header_names"]) == ["X-Region", "X-Team"]

        clear_resp = await client.put(
            f"/webhook-triggers/{trigger_id}",
            json={
                "name": "preserve-headers",
                "url": "https://hooks.example/ops-v3",
                "format": "generic",
                "event_types": ["session.failed"],
                "clear_headers": True,
                "is_active": True,
            },
            headers=auth_headers,
        )
        assert clear_resp.status_code == 200
        assert clear_resp.json()["header_names"] == []

    async def test_create_trigger_validates_event_types(
        self, client: AsyncClient, auth_headers
    ):
        resp = await client.post(
            "/webhook-triggers",
            json={
                "name": "bad-trigger",
                "url": "https://hooks.example/bad",
                "format": "generic",
                "event_types": ["session.unknown"],
            },
            headers=auth_headers,
        )
        assert resp.status_code == 400
        assert "Unsupported event types" in resp.json()["detail"]

    async def test_session_created_slack_trigger_formats_payload(
        self, client: AsyncClient, app, auth_headers, monkeypatch
    ):
        deliveries: list[dict[str, object]] = []

        async def _post_json(url, *, payload, headers, timeout_seconds=10.0):
            deliveries.append(payload)

            class _Resp:
                status_code = 200

                def raise_for_status(self):
                    return None

            return _Resp()

        monkeypatch.setattr("backend.webhooks.service.post_json", _post_json)

        async with app.state.session_factory() as db:
            await WebhookTriggerRepo.create(
                db,
                name="slack-created",
                url="https://hooks.slack.com/services/T/B/X",
                format="slack",
                event_types=["session.created"],
            )
            await db.commit()

        resp = await client.post("/sessions", json={"tier": 2}, headers=auth_headers)
        assert resp.status_code == 201

        await asyncio.sleep(0.05)

        assert len(deliveries) == 1
        payload = deliveries[0]
        assert "text" in payload
        assert "blocks" in payload
        assert payload["text"].startswith("AIM Created:")

    async def test_test_trigger_teams_format_uses_text_payload(
        self, client: AsyncClient, app, auth_headers, monkeypatch
    ):
        deliveries: list[dict[str, object]] = []

        async def _post_json(url, *, payload, headers, timeout_seconds=10.0):
            deliveries.append(payload)

            class _Resp:
                status_code = 200

                def raise_for_status(self):
                    return None

            return _Resp()

        monkeypatch.setattr("backend.webhooks.service.post_json", _post_json)

        create_resp = await client.post(
            "/webhook-triggers",
            json={
                "name": "teams-test",
                "url": "https://prod-00.westus.logic.azure.com/workflows/test",
                "format": "teams",
                "event_types": ["session.completed"],
                "is_active": True,
            },
            headers=auth_headers,
        )
        assert create_resp.status_code == 201

        trigger_id = create_resp.json()["id"]
        test_resp = await client.post(
            f"/webhook-triggers/{trigger_id}/test",
            headers=auth_headers,
        )
        assert test_resp.status_code == 200
        assert test_resp.json()["success"] is True

        assert deliveries
        payload = deliveries[-1]
        assert payload.keys() == {"text"}
        assert "Webhook test incident" in payload["text"]

    async def test_test_trigger_sumo_format_uses_log_friendly_json(
        self, client: AsyncClient, app, auth_headers, monkeypatch
    ):
        deliveries: list[dict[str, object]] = []

        async def _post_json(url, *, payload, headers, timeout_seconds=10.0):
            deliveries.append(payload)

            class _Resp:
                status_code = 200

                def raise_for_status(self):
                    return None

            return _Resp()

        monkeypatch.setattr("backend.webhooks.service.post_json", _post_json)

        create_resp = await client.post(
            "/webhook-triggers",
            json={
                "name": "sumo-test",
                "url": "https://endpoint.collection.us2.sumologic.com/receiver/v1/http/test",
                "format": "sumo",
                "event_types": ["session.completed"],
                "is_active": True,
            },
            headers=auth_headers,
        )
        assert create_resp.status_code == 201

        trigger_id = create_resp.json()["id"]
        test_resp = await client.post(
            f"/webhook-triggers/{trigger_id}/test",
            headers=auth_headers,
        )
        assert test_resp.status_code == 200
        assert test_resp.json()["success"] is True

        assert deliveries
        payload = deliveries[-1]
        assert payload["eventType"] == "webhook.test"
        assert payload["source"] == "aim"
        assert payload["message"].startswith("AIM Webhook.Test:")
        assert payload["incidentTitle"] == "Webhook test incident"
        assert payload["sessionStatus"] == "completed"
        assert payload["session"]["id"] == "test-session"


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
            json={
                "tier": 3,
                "logging_level": "DEBUG",
                "ingest_auto_start_enabled": True,
                "ingest_auto_start_min_severity": "high",
                "ingest_auto_start_source": "legacy_alert_vendor",
            },
            headers=auth_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["tier"] == 3
        assert data["logging_level"] == "DEBUG"
        assert data["ingest_auto_start_enabled"] is True
        assert data["ingest_auto_start_min_severity"] == "high"
        assert data["ingest_auto_start_source"] == "legacy_alert_vendor"

    async def test_update_config_allows_clearing_ingest_auto_start_source(
        self, client: AsyncClient, auth_headers
    ):
        await client.put(
            "/config",
            json={"ingest_auto_start_source": "legacy_alert_relay"},
            headers=auth_headers,
        )
        resp = await client.put(
            "/config",
            json={"ingest_auto_start_source": ""},
            headers=auth_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["ingest_auto_start_source"] is None

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
        assert "credentials" not in data

        async with app.state.session_factory() as db:
            stored = await BotConnectorRepo.get_by_id(db, uuid.UUID(connector_id))
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

        delete_resp = await client.delete(
            f"/bot-connectors/{connector_id}",
            headers=auth_headers,
        )
        assert delete_resp.status_code == 204

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
        assert resp.json()["text"] == "This chat is not allowed to use AIM."

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
                title="Worker crash loop",
                description="worker deployment is restarting",
                severity="medium",
            )
            session = await SessionRepo.create(
                db,
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
            session = await SessionRepo.create(db, tier=1)
            approval = await ApprovalRequestRepo.create(
                db,
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
        aim_user_id: str,
    ) -> None:
        resp = await client.post(
            f"/bot-connectors/{connector_id}/user-links",
            json={
                "platform_user_id": platform_user_id,
                "aim_user_id": aim_user_id,
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
            aim_user_id = str(admin.id)
            session = await SessionRepo.create(db, tier=1)
            await SessionRepo.set_status(db, session.id, status="awaiting_approval")
            approval = await ApprovalRequestRepo.create(
                db,
                session_id=session.id,
                action={"tool_name": "scale_deployment"},
                expires_at=datetime.now(timezone.utc) + timedelta(minutes=15),
            )
            await db.commit()
            approval_id = str(approval.id)
            session_id = session.id

        await self._link_user(client, auth_headers, connector_id, "111", aim_user_id)

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
            updated = await ApprovalRequestRepo.get_by_id(db, uuid.UUID(approval_id))
            session = await SessionRepo.get_by_id(db, session_id)
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
        from backend.api.routes import bot_webhooks
        from backend.bots.rate_limit import rate_limiter

        rate_limiter.reset()
        scheduled: list[uuid.UUID] = []

        async def fake_responder(factory, *, session_id, user_message_id, **kwargs):
            scheduled.append(session_id)

        monkeypatch.setattr(
            bot_webhooks, "respond_to_user_message", fake_responder
        )

        from backend.db.repos import UserRepo

        connector_id = await self._create_connector(
            client,
            auth_headers,
            capabilities=["copilot_chat"],
        )
        async with app.state.session_factory() as db:
            admin = await UserRepo.get_by_username(db, "testadmin")
            assert admin is not None
            aim_user_id = str(admin.id)
            session = await SessionRepo.create(db, tier=2)
            await db.commit()
            session_id = session.id

        await self._link_user(client, auth_headers, connector_id, "111", aim_user_id)

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
                await db.execute(
                    select(SessionMessage).where(
                        SessionMessage.session_id == session_id
                    )
                )
            ).scalars().all()
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

    async def test_telegram_webhook_rate_limit(
        self, client: AsyncClient, auth_headers
    ):
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
                await db.execute(
                    select(BotActionAudit).where(
                        BotActionAudit.connector_id == uuid.UUID(connector_id)
                    )
                )
            ).scalars().all()

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
            client, auth_headers, capabilities=["approvals"],
        )
        async with app.state.session_factory() as db:
            session = await SessionRepo.create(db, tier=1)
            approval = await ApprovalRequestRepo.create(
                db,
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
            request = await ApprovalRequestRepo.get_by_id(db, uuid.UUID(approval_id))
            assert request is not None
            assert request.status == "pending"

            entries = (
                await db.execute(
                    select(BotActionAudit).where(
                        BotActionAudit.connector_id == uuid.UUID(connector_id)
                    )
                )
            ).scalars().all()
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
            client, auth_headers, capabilities=["approvals"],
        )
        async with app.state.session_factory() as db:
            viewer = await UserRepo.get_by_username(db, "viewer-bot")
            assert viewer is not None
            session = await SessionRepo.create(db, tier=1)
            approval = await ApprovalRequestRepo.create(
                db,
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
                await db.execute(
                    select(BotActionAudit).where(
                        BotActionAudit.connector_id == uuid.UUID(connector_id)
                    )
                )
            ).scalars().all()
            assert any(
                e.status == "role_denied"
                and "role=viewer" in (e.detail or "")
                for e in entries
            )

    async def test_bot_user_link_crud(
        self, client: AsyncClient, app, auth_headers
    ):
        from backend.db.repos import UserRepo

        connector_id = await self._create_connector(client, auth_headers)
        async with app.state.session_factory() as db:
            admin = await UserRepo.get_by_username(db, "testadmin")
            aim_user_id = str(admin.id)

        # Create
        resp = await client.post(
            f"/bot-connectors/{connector_id}/user-links",
            json={"platform_user_id": "555", "aim_user_id": aim_user_id},
            headers=auth_headers,
        )
        assert resp.status_code == 201
        link = resp.json()
        assert link["platform_user_id"] == "555"
        assert link["aim_username"] == "testadmin"
        assert link["aim_role"] == "admin"

        # Conflict on duplicate
        resp = await client.post(
            f"/bot-connectors/{connector_id}/user-links",
            json={"platform_user_id": "555", "aim_user_id": aim_user_id},
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
        assert data["is_default"] is True

        async with app.state.session_factory() as db:
            default = await ModelConfigRepo.get_default(db)
            assert default is not None
            assert default.name == "primary-openai"

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

    async def test_delete_saved_model_config_admin(
        self, client: AsyncClient, app, auth_headers
    ):
        async with app.state.session_factory() as db:
            cfg = await ModelConfigRepo.create(
                db,
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
            deleted = await ModelConfigRepo.get_by_id(db, config_id)
            assert deleted is None

    async def test_set_default_saved_model_config_admin(
        self, client: AsyncClient, app, auth_headers
    ):
        async with app.state.session_factory() as db:
            first = await ModelConfigRepo.create(
                db,
                name="first",
                provider="openai",
                model_id="gpt-4o",
                is_default=True,
            )
            second = await ModelConfigRepo.create(
                db,
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
            default = await ModelConfigRepo.get_default(db)
            assert default is not None
            assert default.id == second_id


class TestMCPServerAPI:

    async def test_list_mcp_servers(self, client: AsyncClient, app, auth_headers):
        async with app.state.session_factory() as db:
            await MCPServerRepo.create(
                db,
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
            deleted = await MCPServerRepo.get_by_id(db, server_id)
            assert deleted is None

    async def test_test_mcp_server_success(
        self, client: AsyncClient, app, auth_headers, monkeypatch
    ):
        async with app.state.session_factory() as db:
            server = await MCPServerRepo.create(
                db,
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

    async def test_test_mcp_server_failure(
        self, client: AsyncClient, app, auth_headers, monkeypatch
    ):
        async with app.state.session_factory() as db:
            server = await MCPServerRepo.create(
                db,
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
        ws_routes = [
            r
            for r in app.routes
            if hasattr(r, "path") and "/stream" in getattr(r, "path", "")
        ]
        assert len(ws_routes) == 1
        assert ws_routes[0].path == "/sessions/{session_id}/stream"

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
