"""M1-10: only the handling team takes or acknowledges an incident.

Operators take or acknowledge only incidents of the team handling them;
people the Escalation Chain paged in its current run may acknowledge; admins
always; an incident with no team keeps the any-operator rule. Every path is
covered: assign to self, acknowledge, bulk acknowledge, the takeover request,
Slack /ack and /take, chat actions and phone key 1.
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
from backend.db.models import IncidentAssignment, IncidentComment, Session
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
from backend.paging.reassign import TAKE_FORBIDDEN
from tests.test_ownership_lifecycle import (
    TEST_ORG_ID,
    World,
    _chain,
    _headers,
    _incident,
    _owner,
    _slack,
    _user,
    app as _app_fixture,
    client as _client_fixture,
    world as _world_fixture,
)

app = pytest.fixture(_app_fixture.__wrapped__)
client = pytest.fixture(_client_fixture.__wrapped__)
world = pytest.fixture(_world_fixture.__wrapped__)

# In every test: level1 is off the team but paged by the chain, level2 is off
# the team and not paged, level3 is on the team.


async def _team_incident(w: World) -> uuid.UUID:
    chain_id = await _chain(w.app, [w.level1])
    async with w.app.state.session_factory() as db:
        chain = await EscalationChainRepo.get_by_id(db, TEST_ORG_ID, chain_id)
        await TeamRepo.add_member(db, TEST_ORG_ID, chain.team_id, user_id=w.level3)
        service = await ServiceRepo.create(
            db,
            TEST_ORG_ID,
            team_id=chain.team_id,
            name=f"take-{uuid.uuid4().hex[:6]}",
            slug=f"take-{uuid.uuid4().hex[:6]}",
            priority="P1",
        )
        await ServiceEscalationChainRepo.link(
            db, TEST_ORG_ID, service_id=service.id, chain_id=chain_id
        )
        incident = await IncidentRepo.create(
            db,
            TEST_ORG_ID,
            title="team take",
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
        # Queued AI work that an accepted acknowledgement would cancel.
        await SessionRepo.create(
            db, TEST_ORG_ID, tier=2, incident_id=incident.id, status="queued"
        )
        await db.commit()
        return incident.id


async def _snapshot(w: World, incident_id: uuid.UUID) -> dict:
    async with w.app.state.session_factory() as db:
        assignments = (
            await db.execute(
                select(IncidentAssignment.assigned_to, IncidentAssignment.released_at)
                .where(IncidentAssignment.incident_id == incident_id)
                .order_by(IncidentAssignment.assigned_at)
            )
        ).all()
        state = await IncidentChainStateRepo.get_for_incident(
            db, TEST_ORG_ID, incident_id
        )
        comments = (
            await db.execute(
                select(func.count())
                .select_from(IncidentComment)
                .where(IncidentComment.incident_id == incident_id)
            )
        ).scalar_one()
        sessions = (
            await db.execute(
                select(Session.status).where(Session.incident_id == incident_id)
            )
        ).scalars()
        incident = await IncidentRepo.get_by_id(db, TEST_ORG_ID, incident_id)
        return {
            "assignments": [tuple(row) for row in assignments],
            "chain": (
                state.status,
                state.current_step_index,
                state.pending_takeover_user_id,
            ),
            "comments": comments,
            "sessions": sorted(sessions),
            "status": incident.status,
        }


async def test_an_off_team_operator_cannot_take_on_any_web_path(world):
    incident_id = await _team_incident(world)
    before = await _snapshot(world, incident_id)
    outsider = await _headers(world.client, "lc-l2")

    for path in ("assign", "ack", "take"):
        resp = await world.client.post(
            f"/incidents/{incident_id}/{path}", json={}, headers=outsider
        )
        assert resp.status_code == 403, (path, resp.text)
        assert resp.json()["detail"] == TAKE_FORBIDDEN
        assert await _snapshot(world, incident_id) == before, path

    bulk = await world.client.post(
        "/incidents/bulk",
        json={"action": "acknowledge", "incident_ids": [str(incident_id)]},
        headers=outsider,
    )
    assert bulk.status_code == 200, bulk.text
    (item,) = bulk.json()["items"]
    assert item["ok"] is False and item["error"] == TAKE_FORBIDDEN
    assert await _snapshot(world, incident_id) == before
    assert before["assignments"] == [] and before["sessions"] == ["queued"]


async def test_an_off_team_operator_cannot_ask_the_owner_to_hand_over(world):
    incident_id = await _team_incident(world)
    member = await _headers(world.client, "lc-l3")
    owned = await world.client.post(
        f"/incidents/{incident_id}/ack", json={}, headers=member
    )
    assert owned.status_code == 200, owned.text
    before = await _snapshot(world, incident_id)

    outsider = await _headers(world.client, "lc-l2")
    resp = await world.client.post(
        f"/incidents/{incident_id}/take", json={}, headers=outsider
    )
    assert resp.status_code == 403 and resp.json()["detail"] == TAKE_FORBIDDEN
    assert await _snapshot(world, incident_id) == before
    assert before["chain"][2] is None
    assert await _owner(world.app, incident_id) == world.level3


async def test_someone_the_chain_paged_can_acknowledge(world):
    incident_id = await _team_incident(world)
    paged = await _headers(world.client, "lc-l1")
    resp = await world.client.post(
        f"/incidents/{incident_id}/ack", json={}, headers=paged
    )
    assert resp.status_code == 200, resp.text
    assert await _owner(world.app, incident_id) == world.level1
    assert (await _snapshot(world, incident_id))["sessions"] == ["cancelled"]


async def test_a_team_member_and_an_admin_can_take(world):
    first = await _team_incident(world)
    member = await _headers(world.client, "lc-l3")
    taken = await world.client.post(
        f"/incidents/{first}/assign", json={}, headers=member
    )
    assert taken.status_code == 200, taken.text
    assert await _owner(world.app, first) == world.level3

    second = await _team_incident(world)
    acked = await world.client.post(
        f"/incidents/{second}/ack", json={}, headers=world.admin
    )
    assert acked.status_code == 200, acked.text
    assert await _owner(world.app, second) == world.admin_id


async def test_an_incident_with_no_team_keeps_the_any_operator_rule(world):
    incident_id = await _incident(world.app, await _chain(world.app, [world.level1]))
    outsider = await _headers(world.client, "lc-l2")
    resp = await world.client.post(
        f"/incidents/{incident_id}/ack", json={}, headers=outsider
    )
    assert resp.status_code == 200, resp.text
    assert await _owner(world.app, incident_id) == world.level2


async def test_a_page_from_an_earlier_run_does_not_count(world):
    incident_id = await _team_incident(world)
    async with world.app.state.session_factory() as db:
        state = await IncidentChainStateRepo.get_for_incident(
            db, TEST_ORG_ID, incident_id, for_update=True
        )
        state.round += 1  # a new run that has not paged level1 yet
        await db.commit()
    before = await _snapshot(world, incident_id)
    paged_before = await _headers(world.client, "lc-l1")
    resp = await world.client.post(
        f"/incidents/{incident_id}/ack", json={}, headers=paged_before
    )
    assert resp.status_code == 403 and resp.json()["detail"] == TAKE_FORBIDDEN
    assert await _snapshot(world, incident_id) == before


async def test_the_paging_panel_tells_the_ui_who_can_take(world):
    incident_id = await _team_incident(world)
    viewer = await _user(world.app, "take-viewer", role="viewer")
    assert viewer
    expected = {
        "lc-l1": True,
        "lc-l2": False,
        "lc-l3": True,
        "lc-admin": True,
        "take-viewer": False,
    }
    for name, can_take in expected.items():
        headers = await _headers(world.client, name)
        panel = await world.client.get(
            f"/incidents/{incident_id}/paging", headers=headers
        )
        assert panel.status_code == 200, panel.text
        assert panel.json()["can_take"] is can_take, name


async def test_slack_ack_and_take_from_an_off_team_operator_change_nothing(world):
    incident_id = await _team_incident(world)
    before = await _snapshot(world, incident_id)
    for command in ("/ack", "/take"):
        text = await _slack(
            world, command, str(incident_id), "U-outsider", world.level2
        )
        assert TAKE_FORBIDDEN in text, (command, text)
        assert await _snapshot(world, incident_id) == before, command

    text = await _slack(world, "/ack", str(incident_id), "U-paged", world.level1)
    assert text.startswith("You acknowledged") or text.startswith("You recorded")
    assert await _owner(world.app, incident_id) == world.level1


async def test_chat_acknowledge_from_an_off_team_operator_is_refused(world):
    incident_id = await _team_incident(world)
    before = await _snapshot(world, incident_id)

    def claims() -> IncidentActionTokenClaims:
        return IncidentActionTokenClaims(
            org_id=TEST_ORG_ID,
            incident_id=incident_id,
            action="acknowledge",
            channel_id=None,
            message_id=None,
            expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
            nonce=uuid.uuid4().hex,
        )

    async with world.app.state.session_factory() as db:
        with pytest.raises(IncidentActionError) as refused:
            await execute_incident_action(
                db, claims=claims(), actor_user_id=world.level2
            )
        await db.rollback()
    assert str(refused.value) == "actor_not_on_team"
    assert await _snapshot(world, incident_id) == before

    async with world.app.state.session_factory() as db:
        result = await execute_incident_action(
            db, claims=claims(), actor_user_id=world.level1
        )
        await db.commit()
    assert result.status == "acknowledged"
    assert await _owner(world.app, incident_id) == world.level1


async def test_phone_key_1_from_an_off_team_operator_is_refused(world):
    from backend.api.routes.voice import encode_voice_ack_token

    incident_id = await _team_incident(world)
    before = await _snapshot(world, incident_id)

    def path(user_id: uuid.UUID) -> str:
        token = encode_voice_ack_token(
            org_id=TEST_ORG_ID,
            incident_id=incident_id,
            user_id=user_id,
            summary="Team take",
        )
        return f"/paging/voice/ack/{token}"

    refused = await world.client.post(path(world.level2), data={"Digits": "1"})
    assert refused.status_code == 200
    assert "Only this incident's team" in refused.text
    assert await _snapshot(world, incident_id) == before

    accepted = await world.client.post(path(world.level1), data={"Digits": "1"})
    assert "Incident acknowledged" in accepted.text
    assert await _owner(world.app, incident_id) == world.level1
