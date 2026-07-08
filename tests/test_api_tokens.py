from __future__ import annotations

import asyncio
import json
import uuid

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from backend.api.app import create_app
from backend.api.deps import get_db, set_session_factory
from backend.api.routes.ws import notifications_stream, session_stream
from backend.config_loader import set_env_path
from backend.db.models import Base, Organization
from backend.db.repos import UserRepo

TEST_ORG_ID = uuid.UUID("00000000-0000-0000-0000-000000000000")


@pytest.fixture
async def app(tmp_path):
    engine = create_async_engine("sqlite+aiosqlite://", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    factory = async_sessionmaker(engine, expire_on_commit=False)
    set_session_factory(factory)

    async with factory() as db:
        db.add(Organization(id=TEST_ORG_ID, name="Test Org", slug="test-org"))
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
    application.dependency_overrides[get_db] = _override_get_db(factory)

    yield application

    set_env_path(None)
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
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest.fixture
async def admin_headers(client: AsyncClient, app) -> dict[str, str]:
    await client.post(
        "/auth/register",
        json={
            "username": "api-admin",
            "email": "api-admin@test.com",
            "password": "securepass123",
        },
    )
    async with app.state.session_factory() as db:
        user = await UserRepo.get_by_username(db, "api-admin")
        assert user is not None
        user.primary_org_id = TEST_ORG_ID
        await db.commit()

    resp = await client.post(
        "/auth/login",
        json={"username": "api-admin", "password": "securepass123"},
    )
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


async def _create_token(
    client: AsyncClient,
    admin_headers: dict[str, str],
    *,
    name: str,
    role: str,
) -> dict:
    resp = await client.post(
        "/api/v1/api-tokens",
        headers=admin_headers,
        json={"name": name, "role": role},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def _bearer(secret: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {secret}"}


async def test_token_role_ceiling_and_viewer_read_only(client, admin_headers):
    operator = await _create_token(
        client, admin_headers, name="operator-script", role="operator"
    )
    viewer = await _create_token(
        client, admin_headers, name="viewer-script", role="viewer"
    )

    assert (await client.get("/incidents", headers=_bearer(operator["token"]))).status_code == 200
    create_user = await client.post(
        "/auth/users",
        headers=_bearer(operator["token"]),
        json={
            "username": "blocked-user",
            "email": "blocked-user@test.com",
            "role": "viewer",
            "password": "securepass123",
        },
    )
    assert create_user.status_code == 403

    assert (await client.get("/incidents", headers=_bearer(viewer["token"]))).status_code == 200
    create_incident = await client.post(
        "/incidents",
        headers=_bearer(viewer["token"]),
        json={
            "title": "Viewer token cannot create",
            "description": "read-only token",
            "severity": "high",
            "service_id": str(uuid.uuid4()),
        },
    )
    assert create_incident.status_code == 403


async def test_revoked_token_rejects_immediately(client, admin_headers):
    token = await _create_token(client, admin_headers, name="revoke-me", role="operator")
    assert (await client.get("/incidents", headers=_bearer(token["token"]))).status_code == 200

    revoke = await client.delete(
        f"/api/v1/api-tokens/{token['id']}",
        headers=admin_headers,
    )
    assert revoke.status_code == 204

    rejected = await client.get("/incidents", headers=_bearer(token["token"]))
    assert rejected.status_code == 401


async def test_secret_never_returns_from_list_or_audit(client, admin_headers):
    token = await _create_token(client, admin_headers, name="no-leak", role="viewer")

    listed = await client.get("/api/v1/api-tokens", headers=admin_headers)
    assert listed.status_code == 200
    body = listed.json()
    assert "token" not in body["items"][0]
    assert "token_hash" not in body["items"][0]
    assert token["token"] not in json.dumps(body)

    audit = await client.get(
        "/audit?tool_name=api_token_create",
        headers=admin_headers,
    )
    assert audit.status_code == 200
    audit_text = json.dumps(audit.json())
    assert token["token"] not in audit_text
    assert "token_hash" not in audit_text


async def test_denylist_rejects_admin_token_on_self_service_and_ws(
    client, app, admin_headers
):
    token = await _create_token(client, admin_headers, name="admin-script", role="admin")
    headers = _bearer(token["token"])

    assert (await client.get("/auth/me", headers=headers)).status_code == 401
    assert (await client.get("/auth/mfa/status", headers=headers)).status_code == 401
    assert (
        await client.get("/users/me/notification-preferences", headers=headers)
    ).status_code == 401

    session_ws = _FakeWebSocket()
    await session_stream(session_ws, uuid.uuid4(), token=token["token"])
    assert session_ws.closed_code == 4401
    assert not session_ws.accepted

    notifications_ws = _FakeWebSocket()
    await notifications_stream(notifications_ws, token=token["token"])
    assert notifications_ws.closed_code == 4401
    assert not notifications_ws.accepted


async def test_last_used_at_updates_once_per_minute(client, admin_headers):
    token = await _create_token(client, admin_headers, name="usage-clock", role="operator")
    before = await client.get("/api/v1/api-tokens", headers=admin_headers)
    row = next(item for item in before.json()["items"] if item["id"] == token["id"])
    assert row["last_used_at"] is None

    assert (await client.get("/incidents", headers=_bearer(token["token"]))).status_code == 200
    after_first = await client.get("/api/v1/api-tokens", headers=admin_headers)
    first_used = next(
        item for item in after_first.json()["items"] if item["id"] == token["id"]
    )["last_used_at"]
    assert first_used is not None

    assert (await client.get("/incidents", headers=_bearer(token["token"]))).status_code == 200
    after_second = await client.get("/api/v1/api-tokens", headers=admin_headers)
    second_used = next(
        item for item in after_second.json()["items"] if item["id"] == token["id"]
    )["last_used_at"]
    assert second_used == first_used


async def test_token_auth_audit_attribution(client, admin_headers):
    parent = await _create_token(client, admin_headers, name="parent-admin", role="admin")
    child = await _create_token(
        client,
        _bearer(parent["token"]),
        name="child-viewer",
        role="viewer",
    )
    assert child["token"].startswith("omk_")

    audit = await client.get(
        "/audit?tool_name=api_token_create",
        headers=admin_headers,
    )
    assert audit.status_code == 200
    child_rows = [
        row
        for row in audit.json()["items"]
        if row["tool_parameters"].get("name") == "child-viewer"
    ]
    assert child_rows
    assert child_rows[0]["tool_parameters"]["actor"] == "api-token:parent-admin"


class _FakeWebSocket:
    def __init__(self) -> None:
        self.closed_code: int | None = None
        self.accepted = False

    async def close(self, code: int) -> None:
        self.closed_code = code

    async def accept(self) -> None:
        self.accepted = True
