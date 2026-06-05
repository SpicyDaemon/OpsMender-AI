"""Part 6 — role-based API authorization for Admin / Operator / Viewer.

Locks the v1 matrix at the backend so access control doesn't rely on hiding UI:
operators and viewers cannot reach admin management endpoints, and viewers
cannot read admin surfaces like config or the user list.
"""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from backend.api.app import create_app
from backend.api.deps import set_session_factory
from backend.db.models import Base


@pytest.fixture
async def client(tmp_path, monkeypatch):
    db_path = tmp_path / "roles.db"
    url = f"sqlite+aiosqlite:///{db_path}"
    engine = create_async_engine(url, echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    set_session_factory(async_sessionmaker(engine, expire_on_commit=False))
    monkeypatch.setenv("OPSMENDER_DATABASE_URL", url)
    monkeypatch.setenv("OPSMENDER_JWT_SECRET", "test-secret-32-chars-long-enough-ok")
    app = create_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c
    await engine.dispose()


async def _headers(client: AsyncClient) -> dict[str, dict[str, str]]:
    """Create admin (first user) + operator + viewer; return auth headers each."""
    await client.post(
        "/auth/register",
        json={"username": "admin", "email": "admin@test.com", "password": "securepass123"},
    )
    admin = (
        await client.post(
            "/auth/login", json={"username": "admin", "password": "securepass123"}
        )
    ).json()["access_token"]
    admin_h = {"Authorization": f"Bearer {admin}"}

    out = {"admin": admin_h}
    for role in ("operator", "viewer"):
        await client.post(
            "/auth/users",
            headers=admin_h,
            json={
                "username": role,
                "email": f"{role}@test.com",
                "role": role,
                "password": "temp-pass-123",
                "require_password_change": False,
            },
        )
        tok = (
            await client.post(
                "/auth/login", json={"username": role, "password": "temp-pass-123"}
            )
        ).json()["access_token"]
        out[role] = {"Authorization": f"Bearer {tok}"}
    return out


@pytest.mark.asyncio
async def test_admin_only_mutations_reject_operator_and_viewer(client):
    h = await _headers(client)

    # PUT /config — admin only.
    assert (await client.put("/config", headers=h["admin"], json={"tier": 2})).status_code == 200
    assert (await client.put("/config", headers=h["operator"], json={"tier": 2})).status_code == 403
    assert (await client.put("/config", headers=h["viewer"], json={"tier": 2})).status_code == 403

    # POST /sla-targets — admin only.
    body = {"name": "t1", "kind": "http", "config": {"url": "https://x.test"}}
    assert (await client.post("/sla-targets", headers=h["admin"], json=body)).status_code == 201
    body["name"] = "t2"
    assert (await client.post("/sla-targets", headers=h["operator"], json=body)).status_code == 403
    assert (await client.post("/sla-targets", headers=h["viewer"], json=body)).status_code == 403

    # POST /auth/users (create user) — admin only.
    nu = {"username": "x", "email": "x@test.com", "role": "viewer", "password": "temp-pass-123"}
    assert (await client.post("/auth/users", headers=h["operator"], json=nu)).status_code == 403
    assert (await client.post("/auth/users", headers=h["viewer"], json=nu)).status_code == 403


@pytest.mark.asyncio
async def test_admin_reads_are_gated(client):
    h = await _headers(client)

    # GET /auth/users — admin + operator, NOT viewer.
    assert (await client.get("/auth/users", headers=h["admin"])).status_code == 200
    assert (await client.get("/auth/users", headers=h["operator"])).status_code == 200
    assert (await client.get("/auth/users", headers=h["viewer"])).status_code == 403

    # GET /config — admin + operator, NOT viewer.
    assert (await client.get("/config", headers=h["admin"])).status_code == 200
    assert (await client.get("/config", headers=h["operator"])).status_code == 200
    assert (await client.get("/config", headers=h["viewer"])).status_code == 403


@pytest.mark.asyncio
async def test_all_roles_can_manage_own_profile(client):
    h = await _headers(client)
    # Self-service profile + password are available to every role.
    for role in ("admin", "operator", "viewer"):
        me = await client.get("/auth/me", headers=h[role])
        assert me.status_code == 200, role
        patch = await client.patch(
            "/auth/me", headers=h[role], json={"first_name": role.title()}
        )
        assert patch.status_code == 200, role
        assert patch.json()["first_name"] == role.title()


@pytest.mark.asyncio
async def test_incident_create_and_actions_rbac(client):
    """Part 1/8: only admin creates incidents; viewer can't act; operator can."""
    h = await _headers(client)
    body = {"title": "Outage", "description": "DB down", "severity": "high"}

    # Create incident — admin only (operator + viewer rejected).
    admin_create = await client.post("/incidents", headers=h["admin"], json=body)
    assert admin_create.status_code == 201, admin_create.text
    incident_id = admin_create.json()["id"]
    assert (await client.post("/incidents", headers=h["operator"], json=body)).status_code == 403
    assert (await client.post("/incidents", headers=h["viewer"], json=body)).status_code == 403

    # Everyone can view the list + detail.
    assert (await client.get("/incidents", headers=h["viewer"])).status_code == 200
    assert (await client.get(f"/incidents/{incident_id}", headers=h["viewer"])).status_code == 200

    # Viewer cannot acknowledge; operator/admin can reach the action.
    assert (await client.post(f"/incidents/{incident_id}/ack", headers=h["viewer"])).status_code == 403
    assert (
        await client.post(f"/incidents/{incident_id}/ack", headers=h["operator"])
    ).status_code != 403

    # Viewer cannot drive escalation-related lifecycle either: takeover (the
    # only operator-initiated escalation handoff) and resolve are both blocked.
    # Escalation itself is time-driven by the engine, never a viewer action.
    assert (
        await client.post(f"/incidents/{incident_id}/take", headers=h["viewer"], json={})
    ).status_code == 403
    assert (
        await client.patch(
            f"/incidents/{incident_id}", headers=h["viewer"], json={"status": "resolved"}
        )
    ).status_code == 403

    # Viewer cannot read AI session internals for the incident.
    assert (
        await client.get(f"/incidents/{incident_id}/sessions", headers=h["viewer"])
    ).status_code == 403
