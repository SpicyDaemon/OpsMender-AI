"""Part 6 - incident responder/assignment state in the list + detail response."""

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


async def _get(client, headers, incident_id, *, status=None) -> dict:
    listing = await client.get(
        "/incidents", headers=headers, params={"status": status} if status else None
    )
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


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["resolved", "merged"])
@pytest.mark.parametrize("step", [0, 1])
@pytest.mark.parametrize("surface", ["list", "detail", "cards"])
async def test_terminal_pages_are_history_not_waiting_responders(
    ctx, status, step, surface
):
    import json

    from backend.bots.connectors.discord import build_discord_incident_payload
    from backend.bots.incident_card import incident_card_facts
    from backend.bots.notifier import _resolve_incident_responder
    from backend.db.repos import IncidentRepo
    from backend.paging.slack_cards import build_page_card_blocks
    from backend.paging.teams_cards import build_workflows_card

    client, factory = ctx["client"], ctx["factory"]
    h, ids = await _roles(client)
    iid = await _create_incident(client, h["admin"])
    org = await _org_of(factory, iid)
    async with factory() as db:
        page = await IncidentPageRepo.create(
            db,
            org,
            incident_id=uuid.UUID(iid),
            user_id=uuid.UUID(ids["operator"]),
            step_index=step,
        )
        page_history = (
            page.id,
            page.sent_at.replace(tzinfo=None),
            page.delivery_status,
        )
        incident = await IncidentRepo.get_by_id(db, org, uuid.UUID(iid))
        incident.status = status
        await db.commit()

    if surface == "cards":
        async with factory() as db:
            incident = await IncidentRepo.get_by_id(db, org, uuid.UUID(iid))
            responder = await _resolve_incident_responder(db, org, incident.id)
            assert responder["responder_state"] == "unassigned"
            assert responder["responder_display_name"] is None
            assert responder["escalation_level"] == step
            facts = incident_card_facts(
                incident,
                responder=responder,
                service_name="Service",
                team_name="Team",
                org_name="Org",
            )
            assert ("Responder", "Unassigned") in facts
            payloads = [
                build_page_card_blocks(
                    incident,
                    base_url="https://ops.example.com",
                    include_native_actions=True,
                    facts=facts,
                ),
                build_discord_incident_payload(
                    "Incident closed",
                    incident=incident,
                    native_actions_ready=True,
                    facts=facts,
                ),
                build_workflows_card(
                    incident,
                    headline="Incident closed",
                    facts=facts,
                    base_url="https://ops.example.com",
                    action_links={},
                    acknowledged_by=None,
                    exhausted=False,
                ),
            ]
            for payload in payloads:
                encoded = json.dumps(payload)
                assert "Awaiting Operator User" not in encoded
                assert "Escalated to Operator User" not in encoded
    else:
        if surface == "list":
            row = await _get(client, h["admin"], iid, status=status)
        else:
            result = await client.get(f"/incidents/{iid}", headers=h["admin"])
            assert result.status_code == 200
            row = result.json()
        assert row["responder_state"] == "unassigned"
        assert row["responder_user_id"] is None
        assert row["responder_display_name"] is None
        assert row["escalated_to_user_id"] == ids["operator"]

    async with factory() as db:
        pages = await IncidentPageRepo.list_for_incident(db, org, uuid.UUID(iid))
        assert len(pages) == 1
        assert (
            pages[0].id,
            pages[0].sent_at.replace(tzinfo=None),
            pages[0].delivery_status,
        ) == page_history


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["resolved", "merged"])
async def test_terminal_incident_preserves_existing_owner_and_acknowledgement(
    ctx, status
):
    from backend.bots.notifier import _resolve_incident_responder
    from backend.db.repos import IncidentRepo

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
        incident = await IncidentRepo.get_by_id(db, org, uuid.UUID(iid))
        incident.status = status
        await db.commit()
    row = await _get(client, h["admin"], iid, status=status)
    assert row["responder_state"] == "assigned"
    assert row["acknowledged_by_user_id"] == ids["operator"]
    async with factory() as db:
        snapshot = await _resolve_incident_responder(db, org, uuid.UUID(iid))
        assert snapshot["responder_state"] == "assigned"
        assert snapshot["acknowledged_by_display_name"] == "Operator User"


@pytest.mark.asyncio
async def test_reopened_incident_awaits_the_new_page(ctx):
    from backend.bots.notifier import _resolve_incident_responder
    from backend.db.repos import IncidentRepo

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
            step_index=1,
        )
        incident = await IncidentRepo.get_by_id(db, org, uuid.UUID(iid))
        incident.status = "resolved"
        await db.commit()
    result = await client.patch(
        f"/incidents/{iid}",
        headers=h["admin"],
        json={"status": "open"},
    )
    assert result.status_code == 200
    async with factory() as db:
        await IncidentPageRepo.create(
            db,
            org,
            incident_id=uuid.UUID(iid),
            user_id=uuid.UUID(ids["viewer"]),
            step_index=0,
            round=1,
        )
        await db.commit()
    row = await _get(client, h["admin"], iid)
    assert row["responder_state"] == "awaiting"
    assert row["responder_user_id"] == ids["viewer"]
    async with factory() as db:
        snapshot = await _resolve_incident_responder(db, org, uuid.UUID(iid))
        assert snapshot["responder_state"] == "awaiting"
        assert snapshot["responder_display_name"] == "Viewer User"


@pytest.mark.asyncio
@pytest.mark.parametrize("channel", ["responder", "email"])
async def test_card_target_ignores_non_chain_page_rows(ctx, channel):
    from backend.bots.notifier import _resolve_incident_responder

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
            step_index=1,
        )
        await IncidentPageRepo.create(
            db,
            org,
            incident_id=uuid.UUID(iid),
            user_id=uuid.UUID(ids["viewer"]),
            step_index=None,
            channel=channel,
        )
        await db.commit()
    row = await _get(client, h["admin"], iid)
    assert row["responder_state"] == "escalated"
    assert row["responder_display_name"] == "Operator User"
    async with factory() as db:
        snapshot = await _resolve_incident_responder(db, org, uuid.UUID(iid))
        assert snapshot["responder_state"] == row["responder_state"]
        assert snapshot["responder_display_name"] == "Operator User"
        assert snapshot["escalation_level"] == 1
        assert (
            len(await IncidentPageRepo.list_for_incident(db, org, uuid.UUID(iid))) == 2
        )
