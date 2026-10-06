"""M1-12: only admins and the handling team resolve or reopen an incident.

Admins always; operators on the team handling the incident, whether or not
they own it or respond to it; an incident with no team keeps the any-operator
rule. Responders and owners from another team are refused on every path:
status edits, bulk resolve and reopen, combining it into another incident,
Slack /resolve, chat actions and phone key 3. The paging panel reports
``can_resolve`` for the Resolve button.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import func, select

from backend.bots.actions import (
    IncidentActionError,
    IncidentActionTokenClaims,
    execute_incident_action,
)
from backend.db.models import (
    IncidentAssignment,
    IncidentComment,
    IncidentResponder,
    Session,
)
from backend.db.repos import (
    EscalationChainRepo,
    IncidentChainStateRepo,
    IncidentRepo,
    ServiceEscalationChainRepo,
    ServiceRepo,
    SessionRepo,
    TeamRepo,
)
from backend.paging import escalation as _esc
from backend.paging.reassign import RESOLVE_FORBIDDEN
from tests.test_ownership_lifecycle import (
    TEST_ORG_ID,
    World,
    _chain,
    _headers,
    _incident,
    _slack,
    _user,
    app as _app_fixture,
    client as _client_fixture,
    world as _world_fixture,
)

app = pytest.fixture(_app_fixture.__wrapped__)
client = pytest.fixture(_client_fixture.__wrapped__)
world = pytest.fixture(_world_fixture.__wrapped__)

# In every test: Devops handles the incident. level1 is off the team but its
# chain pages them; level2 is on Data and asked to help as a responder; level3
# is on Devops and not taking part.

STATUS_FORBIDDEN = (
    "Only an admin or a member of this incident's team can change its status. "
    "Ask one of them."
)


async def _devops_incident(w: World) -> uuid.UUID:
    chain_id = await _chain(w.app, [w.level1])
    async with w.app.state.session_factory() as db:
        chain = await EscalationChainRepo.get_by_id(db, TEST_ORG_ID, chain_id)
        await TeamRepo.add_member(db, TEST_ORG_ID, chain.team_id, user_id=w.level3)
        data = await TeamRepo.create(
            db, TEST_ORG_ID, name="Data", slug=f"data-{uuid.uuid4().hex[:6]}"
        )
        await TeamRepo.add_member(db, TEST_ORG_ID, data.id, user_id=w.level2)
        service = await ServiceRepo.create(
            db,
            TEST_ORG_ID,
            team_id=chain.team_id,
            name=f"devops-{uuid.uuid4().hex[:6]}",
            slug=f"devops-{uuid.uuid4().hex[:6]}",
            priority="P1",
        )
        await ServiceEscalationChainRepo.link(
            db, TEST_ORG_ID, service_id=service.id, chain_id=chain_id
        )
        incident = await IncidentRepo.create(
            db,
            TEST_ORG_ID,
            title="devops outage",
            description="d",
            priority="P1",
            response_mode="page",
            service_id=service.id,
        )
        await _esc.start_chain(
            db,
            TEST_ORG_ID,
            incident_id=incident.id,
            chain_id=chain_id,
            at=datetime.now(timezone.utc),
        )
        # Queued AI work that an accepted resolve would cancel.
        await SessionRepo.create(
            db, TEST_ORG_ID, tier=2, incident_id=incident.id, status="queued"
        )
        await db.commit()
    added = await w.client.post(
        f"/incidents/{incident.id}/responders",
        json={"user_ids": [str(w.level2)]},
        headers=w.admin,
    )
    assert added.status_code == 201, added.text
    return incident.id


async def _snapshot(w: World, incident_id: uuid.UUID) -> dict:
    async with w.app.state.session_factory() as db:
        incident = await IncidentRepo.get_by_id(db, TEST_ORG_ID, incident_id)
        state = await IncidentChainStateRepo.get_for_incident(
            db, TEST_ORG_ID, incident_id
        )

        async def count(model, *where) -> int:
            return await db.scalar(
                select(func.count()).select_from(model).where(*where)
            )

        sessions = (
            await db.execute(
                select(Session.status).where(Session.incident_id == incident_id)
            )
        ).scalars()
        return {
            "status": incident.status,
            "chain": None if state is None else (state.status, state.finished_at),
            "assignments": await count(
                IncidentAssignment, IncidentAssignment.incident_id == incident_id
            ),
            "responders": await count(
                IncidentResponder, IncidentResponder.incident_id == incident_id
            ),
            "comments": await count(
                IncidentComment, IncidentComment.incident_id == incident_id
            ),
            "sessions": sorted(sessions),
        }


async def _status(w: World, incident_id: uuid.UUID) -> str:
    return (await _snapshot(w, incident_id))["status"]


def _claims(incident_id: uuid.UUID) -> IncidentActionTokenClaims:
    return IncidentActionTokenClaims(
        org_id=TEST_ORG_ID,
        incident_id=incident_id,
        action="resolve",
        channel_id=None,
        message_id=None,
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
        nonce=uuid.uuid4().hex,
    )


def _voice_path(incident_id: uuid.UUID, user_id: uuid.UUID) -> str:
    from backend.api.routes.voice import encode_voice_ack_token

    token = encode_voice_ack_token(
        org_id=TEST_ORG_ID, incident_id=incident_id, user_id=user_id, summary="Devops"
    )
    return f"/paging/voice/ack/{token}"


async def test_a_responder_from_another_team_cannot_resolve_on_any_path(world):
    incident_id = await _devops_incident(world)
    before = await _snapshot(world, incident_id)
    assert before["responders"] == 1 and before["sessions"] == ["queued"]
    responder = await _headers(world.client, "lc-l2")

    patched = await world.client.patch(
        f"/incidents/{incident_id}", json={"status": "resolved"}, headers=responder
    )
    assert patched.status_code == 403, patched.text
    assert patched.json()["detail"] == STATUS_FORBIDDEN
    bulk = await world.client.post(
        "/incidents/bulk",
        json={"action": "resolve", "incident_ids": [str(incident_id)]},
        headers=responder,
    )
    assert bulk.status_code == 403, bulk.text
    assert bulk.json()["detail"] == RESOLVE_FORBIDDEN
    assert await _snapshot(world, incident_id) == before

    text = await _slack(world, "/resolve", str(incident_id), "U-data", world.level2)
    assert text == f"You can't resolve *devops outage*: {RESOLVE_FORBIDDEN}"
    assert await _snapshot(world, incident_id) == before

    async with world.app.state.session_factory() as db:
        with pytest.raises(IncidentActionError) as refused:
            await execute_incident_action(
                db, claims=_claims(incident_id), actor_user_id=world.level2
            )
        await db.rollback()
    assert str(refused.value) == "actor_cannot_resolve"
    assert await _snapshot(world, incident_id) == before

    phone = await world.client.post(
        _voice_path(incident_id, world.level2), data={"Digits": "3"}
    )
    assert phone.status_code == 200
    assert "Only this incident's team or an admin can resolve it" in phone.text
    assert await _snapshot(world, incident_id) == before


async def test_an_owner_from_another_team_cannot_resolve(world):
    incident_id = await _devops_incident(world)
    paged = await _headers(world.client, "lc-l1")
    acked = await world.client.post(
        f"/incidents/{incident_id}/ack", json={}, headers=paged
    )
    assert acked.status_code == 200, acked.text
    before = await _snapshot(world, incident_id)

    patched = await world.client.patch(
        f"/incidents/{incident_id}", json={"status": "resolved"}, headers=paged
    )
    assert patched.status_code == 403, patched.text
    phone = await world.client.post(
        _voice_path(incident_id, world.level1), data={"Digits": "3"}
    )
    assert "Only this incident's team or an admin can resolve it" in phone.text
    assert await _snapshot(world, incident_id) == before


async def test_a_team_member_who_is_not_taking_part_resolves_and_reopens(world):
    incident_id = await _devops_incident(world)
    member = await _headers(world.client, "lc-l3")
    resolved = await world.client.patch(
        f"/incidents/{incident_id}", json={"status": "resolved"}, headers=member
    )
    assert resolved.status_code == 200, resolved.text
    after = await _snapshot(world, incident_id)
    assert after["status"] == "resolved" and after["sessions"] == ["cancelled"]

    reopened = await world.client.post(
        "/incidents/bulk",
        json={"action": "reopen", "incident_ids": [str(incident_id)]},
        headers=member,
    )
    assert reopened.status_code == 200, reopened.text
    assert await _status(world, incident_id) == "open"

    async with world.app.state.session_factory() as db:
        result = await execute_incident_action(
            db, claims=_claims(incident_id), actor_user_id=world.level3
        )
        await db.commit()
    assert result.status == "resolved"
    assert await _status(world, incident_id) == "resolved"


async def test_a_team_member_resolves_from_slack_and_the_phone(world):
    first = await _devops_incident(world)
    text = await _slack(world, "/resolve", str(first), "U-devops", world.level3)
    assert text == "Marked *devops outage* resolved."
    assert await _status(world, first) == "resolved"

    second = await _devops_incident(world)
    phone = await world.client.post(
        _voice_path(second, world.level3), data={"Digits": "3"}
    )
    assert phone.status_code == 200 and "resolved" in phone.text.lower()
    assert await _status(world, second) == "resolved"


async def test_an_admin_always_resolves(world):
    incident_id = await _devops_incident(world)
    resolved = await world.client.patch(
        f"/incidents/{incident_id}", json={"status": "resolved"}, headers=world.admin
    )
    assert resolved.status_code == 200, resolved.text
    assert await _status(world, incident_id) == "resolved"


async def test_reopening_follows_the_same_rule(world):
    incident_id = await _devops_incident(world)
    resolved = await world.client.patch(
        f"/incidents/{incident_id}", json={"status": "resolved"}, headers=world.admin
    )
    assert resolved.status_code == 200, resolved.text
    before = await _snapshot(world, incident_id)
    outsider = await _headers(world.client, "lc-l2")

    patched = await world.client.patch(
        f"/incidents/{incident_id}", json={"status": "open"}, headers=outsider
    )
    assert patched.status_code == 403, patched.text
    bulk = await world.client.post(
        "/incidents/bulk",
        json={"action": "reopen", "incident_ids": [str(incident_id)]},
        headers=outsider,
    )
    assert bulk.status_code == 403 and bulk.json()["detail"] == RESOLVE_FORBIDDEN
    assert await _snapshot(world, incident_id) == before


async def test_one_incident_off_the_team_stops_a_bulk_resolve(world):
    ours = await _devops_incident(world)
    theirs = await _devops_incident(world)
    async with world.app.state.session_factory() as db:
        # Same service, but the second incident was handed to Data.
        incident = await IncidentRepo.get_by_id(db, TEST_ORG_ID, theirs)
        data = await TeamRepo.create(
            db, TEST_ORG_ID, name="Data 2", slug=f"data-{uuid.uuid4().hex[:6]}"
        )
        incident.team_id = data.id
        other = await IncidentRepo.get_by_id(db, TEST_ORG_ID, ours)
        incident.service_id = other.service_id
        await db.commit()
    before = (await _snapshot(world, ours), await _snapshot(world, theirs))
    member = await _headers(world.client, "lc-l3")
    bulk = await world.client.post(
        "/incidents/bulk",
        json={"action": "resolve", "incident_ids": [str(ours), str(theirs)]},
        headers=member,
    )
    assert bulk.status_code == 403 and bulk.json()["detail"] == RESOLVE_FORBIDDEN
    assert (await _snapshot(world, ours), await _snapshot(world, theirs)) == before


async def test_an_incident_with_no_team_keeps_the_any_operator_rule(world):
    incident_id = await _incident(world.app, await _chain(world.app, [world.level1]))
    outsider = await _headers(world.client, "lc-l2")
    resolved = await world.client.patch(
        f"/incidents/{incident_id}", json={"status": "resolved"}, headers=outsider
    )
    assert resolved.status_code == 200, resolved.text
    assert await _status(world, incident_id) == "resolved"


async def test_the_paging_panel_tells_the_ui_who_can_resolve(world):
    incident_id = await _devops_incident(world)
    viewer = await _user(world.app, "resolve-viewer", role="viewer")
    assert viewer
    expected = {
        "lc-l1": False,
        "lc-l2": False,
        "lc-l3": True,
        "lc-admin": True,
        "resolve-viewer": False,
    }
    for name, can_resolve in expected.items():
        headers = await _headers(world.client, name)
        panel = await world.client.get(
            f"/incidents/{incident_id}/paging", headers=headers
        )
        assert panel.status_code == 200, panel.text
        assert panel.json()["can_resolve"] is can_resolve, name


async def test_combining_closes_an_incident_only_for_its_team(world):
    secondary = await _devops_incident(world)
    primary = await _incident(world.app, await _chain(world.app, [world.level2]))
    before = await _snapshot(world, secondary)
    outsider = await _headers(world.client, "lc-l2")
    refused = await world.client.post(
        f"/incidents/{primary}/combine",
        json={"secondary_ids": [str(secondary)]},
        headers=outsider,
    )
    assert refused.status_code == 403, refused.text
    assert refused.json()["detail"] == (
        'Only an admin or a member of the team handling "devops outage" can '
        "combine it into another incident."
    )
    assert await _snapshot(world, secondary) == before

    member = await _headers(world.client, "lc-l3")
    combined = await world.client.post(
        f"/incidents/{primary}/combine",
        json={"secondary_ids": [str(secondary)]},
        headers=member,
    )
    assert combined.status_code == 200, combined.text
    assert await _status(world, secondary) == "merged"
