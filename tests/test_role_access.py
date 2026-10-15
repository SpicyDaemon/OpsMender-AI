"""Part 6 - role-based API authorization for Admin / Operator / Viewer.

Locks the v1 matrix at the backend so access control doesn't rely on hiding UI:
operators and viewers cannot reach admin management endpoints, and viewers
cannot read admin surfaces like config or the user list.
"""

from __future__ import annotations

import uuid

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
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as c:
        yield c
    await engine.dispose()


async def _headers(client: AsyncClient) -> dict[str, dict[str, str]]:
    """Create admin (first user) + operator + viewer; return auth headers each."""
    await client.post(
        "/auth/register",
        json={
            "username": "admin",
            "email": "admin@test.com",
            "password": "securepass123",
        },
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

    # PUT /config - admin only.
    assert (
        await client.put("/config", headers=h["admin"], json={"tier": 2})
    ).status_code == 200
    assert (
        await client.put("/config", headers=h["operator"], json={"tier": 2})
    ).status_code == 403
    assert (
        await client.put("/config", headers=h["viewer"], json={"tier": 2})
    ).status_code == 403

    # POST /sla-targets - admin only.
    body = {"name": "t1", "kind": "http", "config": {"url": "https://x.test"}}
    assert (
        await client.post("/sla-targets", headers=h["admin"], json=body)
    ).status_code == 201
    body["name"] = "t2"
    assert (
        await client.post("/sla-targets", headers=h["operator"], json=body)
    ).status_code == 403
    assert (
        await client.post("/sla-targets", headers=h["viewer"], json=body)
    ).status_code == 403

    # POST /auth/users (create user) - admin only.
    nu = {
        "username": "x",
        "email": "x@test.com",
        "role": "viewer",
        "password": "temp-pass-123",
    }
    assert (
        await client.post("/auth/users", headers=h["operator"], json=nu)
    ).status_code == 403
    assert (
        await client.post("/auth/users", headers=h["viewer"], json=nu)
    ).status_code == 403


@pytest.mark.asyncio
async def test_admin_reads_are_gated(client):
    h = await _headers(client)

    # GET /auth/users - admin + operator, NOT viewer.
    assert (await client.get("/auth/users", headers=h["admin"])).status_code == 200
    assert (await client.get("/auth/users", headers=h["operator"])).status_code == 200
    assert (await client.get("/auth/users", headers=h["viewer"])).status_code == 403

    # GET /config - admin + operator, NOT viewer.
    assert (await client.get("/config", headers=h["admin"])).status_code == 200
    assert (await client.get("/config", headers=h["operator"])).status_code == 200
    assert (await client.get("/config", headers=h["viewer"])).status_code == 403

    # Session internals remain operator-only even though the dashboard now
    # skips this optional widget for viewers.
    assert (await client.get("/sessions", headers=h["viewer"])).status_code == 403


@pytest.mark.asyncio
async def test_workspace_identity_is_member_readable_but_scoped(client):
    h = await _headers(client)
    me = (await client.get("/auth/me", headers=h["viewer"])).json()
    org_id = me["primary_org_id"]

    for role in ("admin", "operator", "viewer"):
        response = await client.get(f"/organizations/{org_id}", headers=h[role])
        assert response.status_code == 200, role
        assert response.json()["id"] == org_id

    other_org = uuid.uuid4()
    for role in ("admin", "operator", "viewer"):
        assert (
            await client.get(f"/organizations/{other_org}", headers=h[role])
        ).status_code == 404

    assert (
        await client.put(
            f"/organizations/{org_id}",
            headers=h["viewer"],
            json={"name": "Nope"},
        )
    ).status_code == 403


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
    suffix = uuid.uuid4().hex[:8]
    team = await client.post(
        "/teams",
        headers=h["admin"],
        json={"name": "RBAC Team", "slug": f"rbac-team-{suffix}"},
    )
    service = await client.post(
        "/services",
        headers=h["admin"],
        json={
            "team_id": team.json()["id"],
            "name": "RBAC Service",
            "slug": f"rbac-service-{suffix}",
        },
    )
    body = {
        "title": "Outage",
        "description": "DB down",
        "severity": "high",
        "service_id": service.json()["id"],
    }

    # Create incident - admin only (operator + viewer rejected).
    admin_create = await client.post("/incidents", headers=h["admin"], json=body)
    assert admin_create.status_code == 201, admin_create.text
    incident_id = admin_create.json()["id"]
    assert (
        await client.post("/incidents", headers=h["operator"], json=body)
    ).status_code == 403
    assert (
        await client.post("/incidents", headers=h["viewer"], json=body)
    ).status_code == 403

    # Everyone can view the list + detail.
    assert (await client.get("/incidents", headers=h["viewer"])).status_code == 200
    assert (
        await client.get(f"/incidents/{incident_id}", headers=h["viewer"])
    ).status_code == 200

    # Viewer cannot acknowledge; operator/admin can reach the action.
    assert (
        await client.post(f"/incidents/{incident_id}/ack", headers=h["viewer"])
    ).status_code == 403
    assert (
        await client.post(f"/incidents/{incident_id}/ack", headers=h["operator"])
    ).status_code != 403

    # Viewer cannot drive escalation-related lifecycle either: takeover (the
    # only operator-initiated escalation handoff) and resolve are both blocked.
    # Escalation itself is time-driven by the engine, never a viewer action.
    assert (
        await client.post(
            f"/incidents/{incident_id}/take", headers=h["viewer"], json={}
        )
    ).status_code == 403
    assert (
        await client.patch(
            f"/incidents/{incident_id}",
            headers=h["viewer"],
            json={"status": "resolved"},
        )
    ).status_code == 403

    # Viewer cannot read AI session internals for the incident.
    assert (
        await client.get(f"/incidents/{incident_id}/sessions", headers=h["viewer"])
    ).status_code == 403


@pytest.mark.asyncio
async def test_viewer_api_reads_match_o07(client):
    """M1-55: the reads O-07 (narrowed by O-17) gives a Viewer, with real rows.

    Each refused read answers 200 for an operator, so the 403 comes from the
    role and not from a missing row or a wrong path.
    """
    h = await _headers(client)
    suffix = uuid.uuid4().hex[:8]

    async def admin_creates(path: str, body: dict) -> str:
        response = await client.post(path, headers=h["admin"], json=body)
        assert response.status_code == 201, (path, response.text)
        return response.json()["id"]

    team_id = await admin_creates(
        "/teams", {"name": "Scope Team", "slug": f"scope-team-{suffix}"}
    )
    service_id = await admin_creates(
        "/services",
        {"team_id": team_id, "name": "Scope Service", "slug": f"scope-svc-{suffix}"},
    )
    chain_id = await admin_creates(
        "/escalation-chains", {"team_id": team_id, "name": "Scope Chain"}
    )
    roster_id = await admin_creates(
        "/rosters",
        {"team_id": team_id, "name": "Scope Roster", "anchor_date": "2026-10-12"},
    )
    memory_id = await admin_creates(
        "/memories",
        {
            "title": "Scope memory",
            "summary_md": "Restart the pool.",
            "service_id": service_id,
        },
    )
    incident_id = await admin_creates(
        "/incidents",
        {
            "title": "Scope outage",
            "description": "Checkout down",
            "severity": "high",
            "service_id": service_id,
        },
    )

    # Lists a Viewer reads: 200, with the row made above in the answer.
    for path, row_id in (
        ("/incidents", incident_id),
        ("/escalation-chains", chain_id),
        ("/rosters", roster_id),
        ("/teams", team_id),
        ("/services", service_id),
        ("/memories", memory_id),
    ):
        response = await client.get(path, headers=h["viewer"])
        assert response.status_code == 200, (path, response.text)
        assert row_id in {item["id"] for item in response.json()["items"]}, path

    incident = await client.get(f"/incidents/{incident_id}", headers=h["viewer"])
    assert incident.status_code == 200, incident.text
    assert incident.json()["id"] == incident_id

    # Activity carries the admin's changes, such as the team made above.
    activity = await client.get(
        "/audit", headers=h["viewer"], params={"entry_type": "admin_change"}
    )
    assert activity.status_code == 200, activity.text
    assert team_id in {
        item["tool_parameters"]["entity_id"] for item in activity.json()["items"]
    }

    # The Viewer's own Inbox, with a notice addressed to them.
    from backend.api.deps import get_current_session_factory
    from backend.notifications import CATEGORY_INCIDENT, emit_notification

    me = (await client.get("/auth/me", headers=h["viewer"])).json()
    async with get_current_session_factory()() as db:
        notice = await emit_notification(
            db,
            uuid.UUID(me["primary_org_id"]),
            uuid.UUID(me["id"]),
            event_type="incident.assigned",
            category=CATEGORY_INCIDENT,
            title="Scope notice",
            incident_id=uuid.UUID(incident_id),
        )
        await db.commit()
    assert notice is not None
    inbox = await client.get("/notifications", headers=h["viewer"])
    assert inbox.status_code == 200, inbox.text
    assert str(notice.id) in {item["id"] for item in inbox.json()["items"]}

    # Reads O-07 keeps for admins and operators.
    for path in (
        f"/incidents/{incident_id}/timeline",
        f"/incidents/{incident_id}/comments",
        "/reports/incidents",
        "/api/v1/analytics/response",
        "/sessions",
        f"/escalation-chains/{chain_id}/calendar",
    ):
        refused = await client.get(path, headers=h["viewer"])
        assert refused.status_code == 403, (path, refused.text)
        allowed = await client.get(path, headers=h["operator"])
        assert allowed.status_code == 200, (path, allowed.text)
