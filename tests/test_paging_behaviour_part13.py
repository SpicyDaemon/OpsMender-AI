"""Paging behaviour (X8, Part 13).

- S-115: reopening a resolved incident pages again, in a new round from the
  first level, or says why nobody was paged.
- S-112: taking an incident someone is actively working needs their OK
  through a takeover request; the owner hears about it and can hand over.
- S-103: a service uses one MCP server.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from backend.db.models import IncidentPage
from backend.db.repos import (
    EscalationChainRepo,
    InAppNotificationRepo,
    IncidentRepo,
    MCPServerRepo,
    ServiceEscalationChainRepo,
    ServiceRepo,
    TeamRepo,
)
from backend.paging import escalation as _esc
from tests.test_ownership_lifecycle import (
    TEST_ORG_ID,
    World,
    _chain,
    _comments,
    _headers,
    _owner,
    _state,
    app as _app_fixture,
    client as _client_fixture,
    world as _world_fixture,
)

app = pytest.fixture(_app_fixture.__wrapped__)
client = pytest.fixture(_client_fixture.__wrapped__)
world = pytest.fixture(_world_fixture.__wrapped__)


async def _service_incident(
    w: World,
    *,
    priority: str = "P1",
    response_mode: str = "page",
    link_chain: bool = True,
) -> uuid.UUID:
    """An incident on a service whose chain pages level1 then level2."""
    chain_id = await _chain(w.app, [w.level1, w.level2])
    async with w.app.state.session_factory() as db:
        chain = await EscalationChainRepo.get_by_id(db, TEST_ORG_ID, chain_id)
        service = await ServiceRepo.create(
            db,
            TEST_ORG_ID,
            team_id=chain.team_id,
            name=f"svc-{uuid.uuid4().hex[:6]}",
            slug=f"svc-{uuid.uuid4().hex[:6]}",
            priority=priority,
        )
        if link_chain:
            await ServiceEscalationChainRepo.link(
                db, TEST_ORG_ID, service_id=service.id, chain_id=chain_id
            )
        incident = await IncidentRepo.create(
            db,
            TEST_ORG_ID,
            title="part 13",
            description="d",
            priority=priority,
            response_mode=response_mode,
            service_id=service.id,
        )
        if link_chain and response_mode == "page":
            await _esc.start_chain(
                db, TEST_ORG_ID, incident_id=incident.id, chain_id=chain_id
            )
        await db.commit()
        return incident.id


async def _pages_in_round(w: World, incident_id, round_: int):
    async with w.app.state.session_factory() as db:
        rows = (
            await db.execute(
                select(IncidentPage).where(
                    IncidentPage.incident_id == incident_id,
                    IncidentPage.channel == "recorded",
                    IncidentPage.round == round_,
                )
            )
        ).scalars()
        return sorted((row.user_id, row.step_index) for row in rows)


async def _own(w: World, incident_id, user_id) -> None:
    async with w.app.state.session_factory() as db:
        await _esc.acknowledge(
            db, TEST_ORG_ID, incident_id=incident_id, assignee_id=user_id
        )
        await db.commit()


async def _patch_status(w: World, incident_id, status: str):
    resp = await w.client.patch(
        f"/incidents/{incident_id}", json={"status": status}, headers=w.admin
    )
    assert resp.status_code == 200, resp.text


# S-115: reopening pages again


async def test_reopening_a_paging_incident_pages_again_in_a_new_round(world):
    incident_id = await _service_incident(world)
    await _own(world, incident_id, world.level2)
    await _patch_status(world, incident_id, "resolved")
    assert (await _state(world.app, incident_id)).status == "cancelled"

    await _patch_status(world, incident_id, "open")

    state = await _state(world.app, incident_id)
    assert state.status == "running"
    assert state.round == 1
    assert state.current_step_index == 0
    assert await _pages_in_round(world, incident_id, 1) == [(world.level1, 0)]
    # Nobody owns it until someone acknowledges again.
    assert await _owner(world.app, incident_id) is None
    assert "Reopened. Paging started again from the first level." in await _comments(
        world.app, incident_id
    )


async def test_bulk_reopen_pages_again(world):
    incident_id = await _service_incident(world)
    await _patch_status(world, incident_id, "resolved")
    resp = await world.client.post(
        "/incidents/bulk",
        json={"action": "reopen", "incident_ids": [str(incident_id)]},
        headers=world.admin,
    )
    assert resp.status_code == 200, resp.text
    state = await _state(world.app, incident_id)
    assert state.status == "running" and state.round == 1
    assert await _pages_in_round(world, incident_id, 1) == [(world.level1, 0)]


async def test_reopen_with_service_handoff_pages_once_and_notes_it(world):
    incident_id = await _service_incident(world)
    await _patch_status(world, incident_id, "resolved")
    next_chain_id = await _chain(world.app, [world.level2, world.level3])
    async with world.app.state.session_factory() as db:
        next_chain = await EscalationChainRepo.get_by_id(db, TEST_ORG_ID, next_chain_id)
        next_service = await ServiceRepo.create(
            db,
            TEST_ORG_ID,
            team_id=next_chain.team_id,
            name=f"next-{uuid.uuid4().hex[:6]}",
            slug=f"next-{uuid.uuid4().hex[:6]}",
            priority="P1",
        )
        await ServiceEscalationChainRepo.link(
            db, TEST_ORG_ID, service_id=next_service.id, chain_id=next_chain_id
        )
        await db.commit()

    response = await world.client.patch(
        f"/incidents/{incident_id}",
        json={
            "status": "open",
            "service_id": str(next_service.id),
            "service_id_set": True,
            "handoff_reason": "Handoff note",
        },
        headers=world.admin,
    )
    assert response.status_code == 200, response.text
    state = await _state(world.app, incident_id)
    assert state.status == "running" and state.round == 1
    assert state.chain_id == next_chain_id
    assert await _pages_in_round(world, incident_id, 1) == [(world.level2, 0)]
    assert "Reopened. Paging started again from the first level." in await _comments(
        world.app, incident_id
    )


async def test_reopening_a_notify_incident_says_nobody_was_paged(world):
    incident_id = await _service_incident(world, priority="P3", response_mode="notify")
    await _patch_status(world, incident_id, "resolved")
    await _patch_status(world, incident_id, "open")
    assert (
        "Reopened. This incident notifies instead of paging, so nobody was paged."
        in await _comments(world.app, incident_id)
    )


async def test_reopening_with_no_matching_chain_says_so(world):
    incident_id = await _service_incident(world, link_chain=False)
    await _patch_status(world, incident_id, "resolved")
    await _patch_status(world, incident_id, "open")
    notes = await _comments(world.app, incident_id)
    assert any(
        note.startswith("Reopened. No escalation chain matches") for note in notes
    )


# S-112: taking an incident someone is working


async def _join_incident_team(w: World, incident_id: uuid.UUID, user_id: uuid.UUID):
    """Only the handling team takes an incident (M1-10)."""
    async with w.app.state.session_factory() as db:
        incident = await IncidentRepo.get_by_id(db, TEST_ORG_ID, incident_id)
        service = await ServiceRepo.get_by_id(db, TEST_ORG_ID, incident.service_id)
        await TeamRepo.add_member(db, TEST_ORG_ID, service.team_id, user_id=user_id)
        await db.commit()


async def test_taking_an_incident_someone_is_working_asks_them_first(world):
    incident_id = await _service_incident(world)
    await _own(world, incident_id, world.level1)
    await _join_incident_team(world, incident_id, world.level2)
    level2 = await _headers(world.client, "lc-l2")

    refused = await world.client.post(
        f"/incidents/{incident_id}/assign", json={}, headers=level2
    )
    assert refused.status_code == 409
    assert "lc-l1 owns this incident" in refused.json()["detail"]
    assert await _owner(world.app, incident_id) == world.level1

    requested = await world.client.post(
        f"/incidents/{incident_id}/take", json={}, headers=level2
    )
    assert requested.status_code == 200, requested.text
    panel = (
        await world.client.get(f"/incidents/{incident_id}/paging", headers=level2)
    ).json()
    assert panel["pending_takeover"]["user_id"] == str(world.level2)
    assert panel["pending_takeover"]["username"] == "lc-l2"
    repeated = await world.client.post(
        f"/incidents/{incident_id}/take", json={}, headers=level2
    )
    assert repeated.status_code == 200, repeated.text
    again = (
        await world.client.get(f"/incidents/{incident_id}/paging", headers=level2)
    ).json()
    assert again["pending_takeover"] == panel["pending_takeover"]
    async with world.app.state.session_factory() as db:
        inbox = await InAppNotificationRepo.list_for_user(db, TEST_ORG_ID, world.level1)
    assert sum(item.event_type == "incident.takeover_requested" for item in inbox) == 1

    level1 = await _headers(world.client, "lc-l1")
    handed = await world.client.post(
        f"/incidents/{incident_id}/take", json={"confirm": True}, headers=level1
    )
    assert handed.status_code == 200, handed.text
    assert await _owner(world.app, incident_id) == world.level2
    panel = (
        await world.client.get(f"/incidents/{incident_id}/paging", headers=level2)
    ).json()
    assert panel["pending_takeover"] is None


async def test_taking_an_unowned_incident_still_works(world):
    incident_id = await _service_incident(world)
    await _join_incident_team(world, incident_id, world.level2)
    level2 = await _headers(world.client, "lc-l2")
    taken = await world.client.post(
        f"/incidents/{incident_id}/assign", json={}, headers=level2
    )
    assert taken.status_code == 200, taken.text
    assert await _owner(world.app, incident_id) == world.level2


async def test_admins_can_still_reassign_to_someone_else(world):
    incident_id = await _service_incident(world)
    await _own(world, incident_id, world.level1)
    # Replacing an owner needs a note (M1-11).
    no_note = await world.client.post(
        f"/incidents/{incident_id}/assign",
        json={"user_id": str(world.level3)},
        headers=world.admin,
    )
    assert no_note.status_code == 422, no_note.text
    assert await _owner(world.app, incident_id) == world.level1
    reassigned = await world.client.post(
        f"/incidents/{incident_id}/assign",
        json={"user_id": str(world.level3), "note": "Covering while lc-l1 is out."},
        headers=world.admin,
    )
    assert reassigned.status_code == 200, reassigned.text
    assert await _owner(world.app, incident_id) == world.level3
    async with world.app.state.session_factory() as db:
        inbox = await InAppNotificationRepo.list_for_user(db, TEST_ORG_ID, world.level1)
    assert any(item.event_type == "incident.reassigned" for item in inbox)


async def test_bulk_acknowledge_skips_incidents_someone_else_holds(world):
    incident_id = await _service_incident(world)
    await _own(world, incident_id, world.level1)
    await _join_incident_team(world, incident_id, world.level2)
    level2 = await _headers(world.client, "lc-l2")
    resp = await world.client.post(
        "/incidents/bulk",
        json={"action": "acknowledge", "incident_ids": [str(incident_id)]},
        headers=level2,
    )
    assert resp.status_code == 200, resp.text
    (item,) = resp.json()["items"]
    assert item["ok"] is False
    assert "Request a takeover" in item["error"]
    assert await _owner(world.app, incident_id) == world.level1


async def test_the_paging_panel_drops_an_expired_request(world):
    incident_id = await _service_incident(world)
    await _own(world, incident_id, world.level1)
    async with world.app.state.session_factory() as db:
        state = await _esc.IncidentChainStateRepo.get_for_incident(
            db, TEST_ORG_ID, incident_id
        )
        state.pending_takeover_user_id = world.level2
        state.pending_takeover_expires_at = datetime.now(timezone.utc) - timedelta(
            seconds=1
        )
        await db.commit()
    panel = (
        await world.client.get(f"/incidents/{incident_id}/paging", headers=world.admin)
    ).json()
    assert panel["pending_takeover"] is None


async def test_teammate_can_force_take_with_a_reason_and_notify_owner(world):
    incident_id = await _service_incident(world)
    await _own(world, incident_id, world.level1)
    level2 = await _headers(world.client, "lc-l2")
    level3 = await _headers(world.client, "lc-l3")
    async with world.app.state.session_factory() as db:
        incident = await IncidentRepo.get_by_id(db, TEST_ORG_ID, incident_id)
        service = await ServiceRepo.get_by_id(db, TEST_ORG_ID, incident.service_id)
        await TeamRepo.add_member(
            db, TEST_ORG_ID, service.team_id, user_id=world.level2
        )
        await db.commit()

    missing_reason = await world.client.post(
        f"/incidents/{incident_id}/take", json={"force": True}, headers=level2
    )
    assert missing_reason.status_code == 422
    blank_reason = await world.client.post(
        f"/incidents/{incident_id}/take",
        json={"force": True, "reason": "  "},
        headers=level2,
    )
    assert blank_reason.status_code == 422
    outsider = await world.client.post(
        f"/incidents/{incident_id}/take",
        json={"force": True, "reason": "Urgent escalation"},
        headers=level3,
    )
    assert outsider.status_code == 403
    # Being on the service team is not enough if the current owner is not.
    denied = await world.client.post(
        f"/incidents/{incident_id}/take",
        json={"force": True, "reason": "Urgent escalation"},
        headers=level2,
    )
    assert denied.status_code == 403
    panel = (
        await world.client.get(f"/incidents/{incident_id}/paging", headers=level2)
    ).json()
    assert panel["can_force_take"] is False
    assert await _owner(world.app, incident_id) == world.level1

    async with world.app.state.session_factory() as db:
        await TeamRepo.add_member(
            db, TEST_ORG_ID, service.team_id, user_id=world.level1
        )
        await db.commit()
    panel = (
        await world.client.get(f"/incidents/{incident_id}/paging", headers=level2)
    ).json()
    assert panel["can_force_take"] is True
    assert (
        await world.client.get(f"/incidents/{incident_id}/paging", headers=level3)
    ).json()["can_force_take"] is False

    requested = await world.client.post(
        f"/incidents/{incident_id}/take", json={}, headers=level2
    )
    assert requested.status_code == 200
    reason = "Emergency database errors are causing rising 5xx responses."
    forced = await world.client.post(
        f"/incidents/{incident_id}/take",
        json={"force": True, "reason": f"  {reason}  "},
        headers=level2,
    )
    assert forced.status_code == 200, forced.text
    assert await _owner(world.app, incident_id) == world.level2
    assert (await _state(world.app, incident_id)).pending_takeover_user_id is None
    assert any(reason in note for note in await _comments(world.app, incident_id))
    async with world.app.state.session_factory() as db:
        inbox = await InAppNotificationRepo.list_for_user(db, TEST_ORG_ID, world.level1)
    notices = [n for n in inbox if n.event_type == "incident.force_takeover"]
    assert len(notices) == 1
    assert reason in notices[0].body


# S-103: one MCP server per service


async def test_a_service_uses_one_mcp_server(world):
    chain_id = await _chain(world.app, [world.level1])
    async with world.app.state.session_factory() as db:
        first = await MCPServerRepo.create(
            db, TEST_ORG_ID, name="k8s", transport="http", url="http://k8s.local/mcp"
        )
        second = await MCPServerRepo.create(
            db, TEST_ORG_ID, name="aws", transport="http", url="http://aws.local/mcp"
        )
        chain = await EscalationChainRepo.get_by_id(db, TEST_ORG_ID, chain_id)
        await db.commit()
    body = {
        "team_id": str(chain.team_id),
        "name": "One MCP",
        "slug": f"one-mcp-{uuid.uuid4().hex[:6]}",
        "mcp_server_ids": [str(first.id), str(second.id)],
    }
    refused = await world.client.post("/services", json=body, headers=world.admin)
    assert refused.status_code == 422
    assert (
        refused.json()["detail"]
        == "A service can use one MCP server for now. Pick one."
    )

    body["mcp_server_ids"] = [str(first.id)]
    created = await world.client.post("/services", json=body, headers=world.admin)
    assert created.status_code == 201, created.text
    updated = await world.client.put(
        f"/services/{created.json()['id']}",
        json={"mcp_server_ids": [str(first.id), str(second.id)]},
        headers=world.admin,
    )
    assert updated.status_code == 422
