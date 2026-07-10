"""Part 6 — incident responder/assignment state in the list + detail response."""

from __future__ import annotations

import uuid

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from backend.api.app import create_app
from backend.api.deps import set_session_factory
from backend.db.models import Base
from backend.db.repos import (
    IncidentAssignmentRepo,
    IncidentPageRepo,
    UserRepo,
)

TEST_ORG_ID = uuid.UUID("00000000-0000-0000-0000-0000000000a6")


@pytest.fixture
async def ctx(tmp_path, monkeypatch):
    url = f"sqlite+aiosqlite:///{tmp_path / 'responder.db'}"
    engine = create_async_engine(url, echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    set_session_factory(factory)
    monkeypatch.setenv("OPSMENDER_DATABASE_URL", url)
    monkeypatch.setenv("OPSMENDER_JWT_SECRET", "test-secret-32-chars-long-enough-ok")
    app = create_app()
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as c:
        yield {"client": c, "factory": factory}
    await engine.dispose()


async def _roles(client: AsyncClient):
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
    h = {"admin": {"Authorization": f"Bearer {admin}"}}
    ids = {}
    for role in ("operator", "viewer"):
        created = await client.post(
            "/auth/users",
            headers=h["admin"],
            json={
                "username": role,
                "email": f"{role}@test.com",
                "role": role,
                "password": "temp-pass-123",
                "require_password_change": False,
                "first_name": role.title(),
                "last_name": "User",
            },
        )
        ids[role] = created.json()["id"]
        tok = (
            await client.post(
                "/auth/login", json={"username": role, "password": "temp-pass-123"}
            )
        ).json()["access_token"]
        h[role] = {"Authorization": f"Bearer {tok}"}
    return h, ids


async def _create_incident(client, headers) -> str:
    suffix = uuid.uuid4().hex[:8]
    team = await client.post(
        "/teams",
        headers=headers,
        json={"name": "Responder Team", "slug": f"responder-team-{suffix}"},
    )
    assert team.status_code == 201, team.text
    service = await client.post(
        "/services",
        headers=headers,
        json={
            "team_id": team.json()["id"],
            "name": "Responder Service",
            "slug": f"responder-service-{suffix}",
        },
    )
    assert service.status_code == 201, service.text
    resp = await client.post(
        "/incidents",
        headers=headers,
        json={
            "title": "T",
            "description": "D",
            "severity": "high",
            "service_id": service.json()["id"],
        },
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


async def _get(client, headers, incident_id) -> dict:
    listing = await client.get("/incidents", headers=headers)
    return next(i for i in listing.json()["items"] if i["id"] == incident_id)


async def _org_of(factory, incident_id: str):
    from backend.db.models import Incident

    async with factory() as db:
        inc = await db.get(Incident, uuid.UUID(incident_id))
        return inc.org_id


@pytest.mark.asyncio
async def test_unassigned_incident(ctx):
    client = ctx["client"]
    h, _ = await _roles(client)
    iid = await _create_incident(client, h["admin"])
    row = await _get(client, h["admin"], iid)
    assert row["responder_state"] == "unassigned"
    assert row["responder_user_id"] is None


@pytest.mark.asyncio
async def test_awaiting_current_escalation_target(ctx):
    client, factory = ctx["client"], ctx["factory"]
    h, ids = await _roles(client)
    iid = await _create_incident(client, h["admin"])
    org = await _org_of(factory, iid)
    async with factory() as db:
        await IncidentPageRepo.create(
            db,
            org,
            incident_id=uuid.UUID(iid),
            user_id=uuid.UUID(ids["operator"]),
            step_index=0,
        )
        await db.commit()
    row = await _get(client, h["admin"], iid)
    assert row["responder_state"] == "awaiting"
    assert row["responder_user_id"] == ids["operator"]
    assert row["responder_display_name"] == "Operator User"


@pytest.mark.asyncio
async def test_escalated_to_next_target(ctx):
    client, factory = ctx["client"], ctx["factory"]
    h, ids = await _roles(client)
    iid = await _create_incident(client, h["admin"])
    org = await _org_of(factory, iid)
    async with factory() as db:
        await IncidentPageRepo.create(
            db,
            org,
            incident_id=uuid.UUID(iid),
            user_id=uuid.UUID(ids["operator"]),
            step_index=0,
        )
        await IncidentPageRepo.create(
            db,
            org,
            incident_id=uuid.UUID(iid),
            user_id=uuid.UUID(ids["operator"]),
            step_index=1,
        )
        await db.commit()
    row = await _get(client, h["admin"], iid)
    assert row["responder_state"] == "escalated"
    assert row["escalated_to_user_id"] == ids["operator"]


@pytest.mark.asyncio
async def test_acknowledged_shows_assigned_responder(ctx):
    client, factory = ctx["client"], ctx["factory"]
    h, ids = await _roles(client)
    iid = await _create_incident(client, h["admin"])
    org = await _org_of(factory, iid)
    async with factory() as db:
        await IncidentAssignmentRepo.assign(
            db,
            org,
            incident_id=uuid.UUID(iid),
            user_id=uuid.UUID(ids["operator"]),
            assigned_by="self_ack",
        )
        await db.commit()
    row = await _get(client, h["admin"], iid)
    assert row["responder_state"] == "assigned"
    assert row["responder_user_id"] == ids["operator"]
    assert row["acknowledged_by_user_id"] == ids["operator"]
    assert row["acknowledged_by_display_name"] == "Operator User"


@pytest.mark.asyncio
async def test_deleted_responder_falls_back(ctx):
    client, factory = ctx["client"], ctx["factory"]
    h, ids = await _roles(client)
    iid = await _create_incident(client, h["admin"])
    org = await _org_of(factory, iid)
    async with factory() as db:
        await IncidentAssignmentRepo.assign(
            db,
            org,
            incident_id=uuid.UUID(iid),
            user_id=uuid.UUID(ids["operator"]),
            assigned_by="self_ack",
        )
        await db.commit()
    # Soft-delete the responder.
    async with factory() as db:
        await UserRepo.soft_delete(db, uuid.UUID(ids["operator"]))
        await db.commit()
    row = await _get(client, h["admin"], iid)
    # The id is preserved but the name resolves to None -> frontend shows
    # "Deleted user <id>".
    assert row["responder_user_id"] == ids["operator"]
    assert row["responder_display_name"] is None


@pytest.mark.asyncio
async def test_viewer_reads_responder_but_cannot_act(ctx):
    client, factory = ctx["client"], ctx["factory"]
    h, ids = await _roles(client)
    iid = await _create_incident(client, h["admin"])
    org = await _org_of(factory, iid)
    async with factory() as db:
        await IncidentAssignmentRepo.assign(
            db,
            org,
            incident_id=uuid.UUID(iid),
            user_id=uuid.UUID(ids["operator"]),
            assigned_by="self_ack",
        )
        await db.commit()
    # Viewer can read responder state.
    row = await _get(client, h["viewer"], iid)
    assert row["responder_state"] == "assigned"
    assert row["responder_user_id"] == ids["operator"]
    # But cannot acknowledge.
    ack = await client.post(f"/incidents/{iid}/ack", headers=h["viewer"])
    assert ack.status_code == 403
