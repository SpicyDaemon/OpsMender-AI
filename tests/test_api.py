"""Tests for the FastAPI REST layer — Sprint 8.

Uses in-memory SQLite via aiosqlite so no Postgres is needed.
Tests the full API surface: auth, incidents, sessions, audit, config.
"""

from __future__ import annotations

import uuid
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
    ApprovalRequestRepo,
    AuditEntryRepo,
    IncidentRepo,
    ModelConfigRepo,
    SessionRepo,
    UserRepo,
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
    await client.post("/auth/register", json={
        "username": "testadmin",
        "email": "admin@test.com",
        "password": "securepass123",
    })
    resp = await client.post("/auth/login", json={
        "username": "testadmin",
        "password": "securepass123",
    })
    token = resp.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
async def viewer_headers(client: AsyncClient, auth_headers) -> dict[str, str]:
    """Register a viewer user and return auth headers."""
    await client.post("/auth/register", json={
        "username": "viewer1",
        "email": "viewer@test.com",
        "password": "viewerpass123",
        "role": "viewer",
    })
    resp = await client.post("/auth/login", json={
        "username": "viewer1",
        "password": "viewerpass123",
    })
    token = resp.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


async def _create_approval_request(app, *, tier: int = 1, expires_delta_minutes: int = 15):
    factory = app.state.session_factory
    async with factory() as db:
        session = await SessionRepo.create(db, tier=tier)
        request = await ApprovalRequestRepo.create(
            db,
            session_id=session.id,
            action={"tool_name": "delete_pod", "tool_parameters": {"pod": "api"}},
            justification="Pod is causing the incident",
            expires_at=datetime.now(timezone.utc) + timedelta(minutes=expires_delta_minutes),
        )
        await db.commit()
        await db.refresh(session)
        await db.refresh(request)
        return session, request


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
        resp = await client.post("/auth/register", json={
            "username": "first",
            "email": "first@test.com",
            "password": "password123",
        })
        assert resp.status_code == 201
        data = resp.json()
        assert data["username"] == "first"
        assert data["role"] == "admin"  # first user auto-admin

    async def test_register_second_user_uses_given_role(self, client: AsyncClient):
        # First user (becomes admin)
        await client.post("/auth/register", json={
            "username": "admin1",
            "email": "a1@test.com",
            "password": "password123",
        })
        # Second user (viewer by default)
        resp = await client.post("/auth/register", json={
            "username": "user2",
            "email": "u2@test.com",
            "password": "password123",
        })
        assert resp.status_code == 201
        assert resp.json()["role"] == "viewer"

    async def test_register_duplicate_username(self, client: AsyncClient):
        await client.post("/auth/register", json={
            "username": "dupuser",
            "email": "dup1@test.com",
            "password": "password123",
        })
        resp = await client.post("/auth/register", json={
            "username": "dupuser",
            "email": "dup2@test.com",
            "password": "password123",
        })
        assert resp.status_code == 409

    async def test_register_duplicate_email(self, client: AsyncClient):
        await client.post("/auth/register", json={
            "username": "emaildup1",
            "email": "same@test.com",
            "password": "password123",
        })
        resp = await client.post("/auth/register", json={
            "username": "emaildup2",
            "email": "same@test.com",
            "password": "password123",
        })
        assert resp.status_code == 409

    async def test_login_success(self, client: AsyncClient):
        await client.post("/auth/register", json={
            "username": "logintest",
            "email": "lt@test.com",
            "password": "password123",
        })
        resp = await client.post("/auth/login", json={
            "username": "logintest",
            "password": "password123",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert "access_token" in data
        assert data["token_type"] == "bearer"

    async def test_login_wrong_password(self, client: AsyncClient):
        await client.post("/auth/register", json={
            "username": "wrongpw",
            "email": "wp@test.com",
            "password": "password123",
        })
        resp = await client.post("/auth/login", json={
            "username": "wrongpw",
            "password": "wrongpassword",
        })
        assert resp.status_code == 401

    async def test_login_nonexistent_user(self, client: AsyncClient):
        resp = await client.post("/auth/login", json={
            "username": "nobody",
            "password": "password123",
        })
        assert resp.status_code == 401

    async def test_me_authenticated(self, client: AsyncClient, auth_headers):
        resp = await client.get("/auth/me", headers=auth_headers)
        assert resp.status_code == 200
        assert resp.json()["username"] == "testadmin"

    async def test_me_unauthenticated(self, client: AsyncClient):
        resp = await client.get("/auth/me")
        assert resp.status_code == 401

    async def test_me_invalid_token(self, client: AsyncClient):
        resp = await client.get("/auth/me", headers={
            "Authorization": "Bearer invalid-token"
        })
        assert resp.status_code == 401


# ===========================================================================
# Incidents
# ===========================================================================

class TestIncidents:

    async def test_create_incident(self, client: AsyncClient, auth_headers):
        resp = await client.post("/incidents", json={
            "title": "High CPU on api-server",
            "description": "CPU at 95% for 10 minutes",
            "severity": "high",
        }, headers=auth_headers)
        assert resp.status_code == 201
        data = resp.json()
        assert data["title"] == "High CPU on api-server"
        assert data["status"] == "open"
        assert data["severity"] == "high"

    async def test_create_incident_viewer_forbidden(
        self, client: AsyncClient, viewer_headers
    ):
        resp = await client.post("/incidents", json={
            "title": "Blocked", "description": "should fail",
        }, headers=viewer_headers)
        assert resp.status_code == 403

    async def test_list_incidents(self, client: AsyncClient, auth_headers):
        # Create two incidents
        await client.post("/incidents", json={
            "title": "Inc1", "description": "d1",
        }, headers=auth_headers)
        await client.post("/incidents", json={
            "title": "Inc2", "description": "d2",
        }, headers=auth_headers)

        resp = await client.get("/incidents", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 2
        assert len(data["items"]) == 2

    async def test_list_incidents_with_status_filter(
        self, client: AsyncClient, auth_headers
    ):
        await client.post("/incidents", json={
            "title": "Open", "description": "d",
        }, headers=auth_headers)
        await client.post("/incidents", json={
            "title": "Open2", "description": "d",
        }, headers=auth_headers)

        resp = await client.get("/incidents?status=open", headers=auth_headers)
        assert resp.status_code == 200
        assert resp.json()["total"] == 2

        resp = await client.get("/incidents?status=resolved", headers=auth_headers)
        assert resp.json()["total"] == 0

    async def test_get_incident(self, client: AsyncClient, auth_headers):
        create_resp = await client.post("/incidents", json={
            "title": "Look me up", "description": "d",
        }, headers=auth_headers)
        inc_id = create_resp.json()["id"]

        resp = await client.get(f"/incidents/{inc_id}", headers=auth_headers)
        assert resp.status_code == 200
        assert resp.json()["title"] == "Look me up"

    async def test_get_incident_not_found(self, client: AsyncClient, auth_headers):
        fake_id = uuid.uuid4()
        resp = await client.get(f"/incidents/{fake_id}", headers=auth_headers)
        assert resp.status_code == 404

    async def test_list_incidents_pagination(self, client: AsyncClient, auth_headers):
        for i in range(5):
            await client.post("/incidents", json={
                "title": f"Inc-{i}", "description": "d",
            }, headers=auth_headers)

        resp = await client.get("/incidents?limit=2&offset=0", headers=auth_headers)
        data = resp.json()
        assert len(data["items"]) == 2
        assert data["total"] == 5


# ===========================================================================
# Sessions
# ===========================================================================

class TestSessions:

    async def test_create_session(self, client: AsyncClient, auth_headers):
        resp = await client.post("/sessions", json={
            "tier": 2,
        }, headers=auth_headers)
        assert resp.status_code == 201
        data = resp.json()
        assert data["tier"] == 2
        assert data["status"] == "active"

    async def test_create_session_with_incident(self, client: AsyncClient, auth_headers):
        inc_resp = await client.post("/incidents", json={
            "title": "T", "description": "d",
        }, headers=auth_headers)
        inc_id = inc_resp.json()["id"]

        resp = await client.post("/sessions", json={
            "incident_id": inc_id,
            "tier": 1,
        }, headers=auth_headers)
        assert resp.status_code == 201
        assert resp.json()["incident_id"] == inc_id

    async def test_create_session_invalid_incident(
        self, client: AsyncClient, auth_headers
    ):
        resp = await client.post("/sessions", json={
            "incident_id": str(uuid.uuid4()),
            "tier": 2,
        }, headers=auth_headers)
        assert resp.status_code == 404

    async def test_create_session_viewer_forbidden(
        self, client: AsyncClient, viewer_headers
    ):
        resp = await client.post("/sessions", json={
            "tier": 2,
        }, headers=viewer_headers)
        assert resp.status_code == 403

    async def test_get_session(self, client: AsyncClient, auth_headers):
        create_resp = await client.post("/sessions", json={
            "tier": 3,
        }, headers=auth_headers)
        sess_id = create_resp.json()["id"]

        resp = await client.get(f"/sessions/{sess_id}", headers=auth_headers)
        assert resp.status_code == 200
        assert resp.json()["tier"] == 3

    async def test_get_session_not_found(self, client: AsyncClient, auth_headers):
        resp = await client.get(
            f"/sessions/{uuid.uuid4()}", headers=auth_headers
        )
        assert resp.status_code == 404


# ===========================================================================
# Audit
# ===========================================================================

class TestAudit:

    async def _seed_audit(self, client, auth_headers):
        """Create a session and seed audit entries directly via DB."""
        # Create session via API
        resp = await client.post("/sessions", json={
            "tier": 2,
        }, headers=auth_headers)
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

    async def test_get_config_viewer_forbidden(
        self, client: AsyncClient, viewer_headers
    ):
        resp = await client.get("/config", headers=viewer_headers)
        assert resp.status_code == 403

    async def test_update_config_admin(self, client: AsyncClient, auth_headers):
        resp = await client.put("/config", json={
            "tier": 3,
            "logging_level": "DEBUG",
        }, headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["tier"] == 3
        assert data["logging_level"] == "DEBUG"

    async def test_update_config_viewer_forbidden(
        self, client: AsyncClient, viewer_headers
    ):
        resp = await client.put("/config", json={
            "tier": 1,
        }, headers=viewer_headers)
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
            lambda self, **kwargs: None,
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
        data = resp.json()
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

    async def test_viewer_cannot_approve(self, client: AsyncClient, app, viewer_headers):
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
