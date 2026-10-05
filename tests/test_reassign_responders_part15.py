"""Reassign to another team, and add responders (Part 15).

- An incident can be handed to the team it belongs to. That team's Escalation
  Chain pages from the first level, the owner is released, and the handling
  team drives force takeover, filters, reopen paging and channel scope.
- Up to three people can be asked to help. Each is paged once through their
  own routing; their pages never count as chain pages.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from backend.db.models import (
    InAppNotification,
    Incident,
    IncidentPage,
    NotificationEscalation,
)
from backend.db.repos import (
    EscalationChainRepo,
    EscalationStepRepo,
    IncidentRepo,
    MaintenanceWindowRepo,
    NotificationEscalationRepo,
    ServiceEscalationChainRepo,
    ServiceRepo,
    TeamRepo,
    UserRepo,
)
from backend.paging import escalation as _esc
from backend.paging import notification_escalation as _ne
from tests.test_ownership_lifecycle import (
    TEST_ORG_ID,
    World,
    _comments,
    _headers,
    _owner,
    _state,
    _user,
    app as _app_fixture,
    client as _client_fixture,
    world as _world_fixture,
)

app = pytest.fixture(_app_fixture.__wrapped__)
client = pytest.fixture(_client_fixture.__wrapped__)
world = pytest.fixture(_world_fixture.__wrapped__)


async def _team(
    w: World,
    name: str,
    *,
    members: list[uuid.UUID] = (),
    levels: list[uuid.UUID] | None = None,
) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID | None]:
    """A team with members, one service, and a chain paging ``levels``."""
    async with w.app.state.session_factory() as db:
        team = await TeamRepo.create(
            db, TEST_ORG_ID, name=name, slug=f"{name.lower()}-{uuid.uuid4().hex[:6]}"
        )
        for user_id in members:
            await TeamRepo.add_member(db, TEST_ORG_ID, team.id, user_id=user_id)
        service = await ServiceRepo.create(
            db,
            TEST_ORG_ID,
            team_id=team.id,
            name=f"{name.lower()}-svc",
            slug=f"{name.lower()}-svc-{uuid.uuid4().hex[:6]}",
            priority="P1",
        )
        chain_id = None
        if levels:
            chain = await EscalationChainRepo.create(
                db, TEST_ORG_ID, team_id=team.id, name=f"{name} chain"
            )
            for index, user_id in enumerate(levels):
                await EscalationStepRepo.create(
                    db,
                    TEST_ORG_ID,
                    chain_id=chain.id,
                    step_index=index,
                    target_type="user",
                    target_id=user_id,
                    timeout_seconds=60,
                )
            await ServiceEscalationChainRepo.link(
                db, TEST_ORG_ID, service_id=service.id, chain_id=chain.id
            )
            chain_id = chain.id
        await db.commit()
        return team.id, service.id, chain_id


async def _incident_on(
    w: World,
    service_id: uuid.UUID,
    chain_id: uuid.UUID | None,
    *,
    priority: str = "P1",
) -> uuid.UUID:
    mode = "page" if priority in ("P0", "P1") else "notify"
    async with w.app.state.session_factory() as db:
        incident = await IncidentRepo.create(
            db,
            TEST_ORG_ID,
            title="orders db is slow",
            description="d",
            priority=priority,
            response_mode=mode,
            service_id=service_id,
        )
        if chain_id is not None and mode == "page":
            await _esc.start_chain(
                db, TEST_ORG_ID, incident_id=incident.id, chain_id=chain_id
            )
        await db.commit()
        return incident.id


async def _recorded(w: World, incident_id, round_: int):
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


async def _inbox(w: World, user_id, event_type: str) -> list[InAppNotification]:
    async with w.app.state.session_factory() as db:
        return list(
            (
                await db.execute(
                    select(InAppNotification).where(
                        InAppNotification.user_id == user_id,
                        InAppNotification.event_type == event_type,
                    )
                )
            ).scalars()
        )


async def _incident_row(w: World, incident_id) -> Incident:
    async with w.app.state.session_factory() as db:
        return await IncidentRepo.get_by_id(db, TEST_ORG_ID, incident_id)


async def _own(w: World, incident_id, user_id) -> None:
    async with w.app.state.session_factory() as db:
        await _esc.acknowledge(
            db, TEST_ORG_ID, incident_id=incident_id, assignee_id=user_id
        )
        await db.commit()


# ── Reassign ────────────────────────────────────────────────────────────────


async def test_reassign_pages_the_receiving_team_from_the_first_level(world):
    a1 = await _user(world.app, "rs-a1")
    b1 = await _user(world.app, "rs-b1")
    b2 = await _user(world.app, "rs-b2")
    _, service_a, chain_a = await _team(world, "Platform", members=[a1], levels=[a1])
    team_b, _, chain_b = await _team(world, "Data", members=[b1, b2], levels=[b1, b2])
    incident_id = await _incident_on(world, service_a, chain_a)
    await _own(world, incident_id, a1)

    resp = await world.client.post(
        f"/incidents/{incident_id}/reassign",
        json={"team_id": str(team_b), "note": "The orders database is ours."},
        headers=world.admin,
    )

    assert resp.status_code == 200, resp.text
    assert resp.json()["team_name"] == "Data"
    assert resp.json()["service_id"] == str(service_a)
    assert await _owner(world.app, incident_id) is None
    state = await _state(world.app, incident_id)
    assert (state.chain_id, state.round, state.current_step_index, state.status) == (
        chain_b,
        1,
        0,
        "running",
    )
    assert await _recorded(world, incident_id, 1) == [(b1, 0)]
    assert (await _incident_row(world, incident_id)).team_id == team_b
    assert (
        "Reassigned from Platform to Data. Paging Data chain from the first level. "
        "Note: The orders database is ours."
    ) in await _comments(world.app, incident_id)
    notices = await _inbox(world, a1, "incident.reassigned")
    assert len(notices) == 1 and "no longer own it" in notices[0].body


async def test_reassign_permissions(world):
    a1 = await _user(world.app, "rp-a1")
    outsider = await _user(world.app, "rp-out")
    viewer = await _user(world.app, "rp-viewer", role="viewer")
    _, service_a, chain_a = await _team(world, "Platform", members=[a1], levels=[a1])
    team_b, _, _ = await _team(world, "Data", levels=[outsider])
    incident_id = await _incident_on(world, service_a, chain_a)
    body = {"team_id": str(team_b), "note": "Handoff note"}

    for name in ("rp-out", "rp-viewer"):
        headers = await _headers(world.client, name)
        options = await world.client.get(
            f"/incidents/{incident_id}/reassign-options", headers=headers
        )
        moved = await world.client.post(
            f"/incidents/{incident_id}/reassign", json=body, headers=headers
        )
        assert (options.status_code, moved.status_code) == (403, 403), name

    teammate = await _headers(world.client, "rp-a1")
    listed = await world.client.get(
        f"/incidents/{incident_id}/reassign-options", headers=teammate
    )
    assert listed.status_code == 200, listed.text
    assert [o["team_name"] for o in listed.json()["options"]] == ["Data"]
    assert listed.json()["options"][0]["chain_name"] == "Data chain"
    moved = await world.client.post(
        f"/incidents/{incident_id}/reassign", json=body, headers=teammate
    )
    assert moved.status_code == 200, moved.text
    del viewer


async def test_reassign_to_a_team_without_a_chain_says_nobody_was_paged(world):
    a1 = await _user(world.app, "rn-a1")
    _, service_a, chain_a = await _team(world, "Platform", members=[a1], levels=[a1])
    team_c, _, _ = await _team(world, "Quiet")
    incident_id = await _incident_on(world, service_a, chain_a)

    resp = await world.client.post(
        f"/incidents/{incident_id}/reassign",
        json={"team_id": str(team_c), "note": "Handoff note"},
        headers=world.admin,
    )

    assert resp.status_code == 200, resp.text
    assert (await _state(world.app, incident_id)).status == "cancelled"
    assert (
        "Reassigned from Platform to Quiet. Quiet has no active Escalation Chain, "
        "so nobody was paged. Note: Handoff note"
    ) in await _comments(world.app, incident_id)


async def test_reassigning_a_notify_incident_tells_the_team_in_their_inbox(world):
    b1 = await _user(world.app, "ri-b1")
    b2 = await _user(world.app, "ri-b2")
    _, service_a, _ = await _team(world, "Platform")
    team_b, _, _ = await _team(world, "Data", members=[b1, b2], levels=[b1])
    incident_id = await _incident_on(world, service_a, None, priority="P3")

    resp = await world.client.post(
        f"/incidents/{incident_id}/reassign",
        json={"team_id": str(team_b), "note": "Handoff note"},
        headers=world.admin,
    )

    assert resp.status_code == 200, resp.text
    assert await _recorded(world, incident_id, 0) == []
    for member in (b1, b2):
        assert len(await _inbox(world, member, "incident.reassigned")) == 1


async def test_reassigning_back_to_the_service_team_clears_the_reassignment(world):
    a1 = await _user(world.app, "rb-a1")
    team_a, service_a, chain_a = await _team(world, "Platform", levels=[a1])
    team_b, _, _ = await _team(world, "Data", levels=[a1])
    incident_id = await _incident_on(world, service_a, chain_a)

    for team_id in (team_b, team_a):
        resp = await world.client.post(
            f"/incidents/{incident_id}/reassign",
            json={"team_id": str(team_id), "note": "Handoff note"},
            headers=world.admin,
        )
        assert resp.status_code == 200, resp.text

    assert (await _incident_row(world, incident_id)).team_id is None
    same = await world.client.post(
        f"/incidents/{incident_id}/reassign",
        json={"team_id": str(team_a), "note": "Handoff note"},
        headers=world.admin,
    )
    assert same.status_code == 409


async def test_resolved_incidents_cannot_be_reassigned(world):
    a1 = await _user(world.app, "rr-a1")
    _, service_a, chain_a = await _team(world, "Platform", levels=[a1])
    team_b, _, _ = await _team(world, "Data", levels=[a1])
    incident_id = await _incident_on(world, service_a, chain_a)
    closed = await world.client.patch(
        f"/incidents/{incident_id}", json={"status": "resolved"}, headers=world.admin
    )
    assert closed.status_code == 200, closed.text

    resp = await world.client.post(
        f"/incidents/{incident_id}/reassign",
        json={"team_id": str(team_b), "note": "Handoff note"},
        headers=world.admin,
    )
    assert resp.status_code == 409


async def test_the_handling_team_drives_force_take_filters_and_reopen(world):
    a1 = await _user(world.app, "rh-a1")
    a2 = await _user(world.app, "rh-a2")
    b1 = await _user(world.app, "rh-b1")
    b2 = await _user(world.app, "rh-b2")
    team_a, service_a, chain_a = await _team(
        world, "Platform", members=[a1, a2], levels=[a1]
    )
    team_b, _, chain_b = await _team(world, "Data", members=[b1, b2], levels=[b1])
    incident_id = await _incident_on(world, service_a, chain_a)
    moved = await world.client.post(
        f"/incidents/{incident_id}/reassign",
        json={"team_id": str(team_b), "note": "Handoff note"},
        headers=world.admin,
    )
    assert moved.status_code == 200, moved.text
    await _own(world, incident_id, b1)

    b2_panel = await world.client.get(
        f"/incidents/{incident_id}/paging",
        headers=await _headers(world.client, "rh-b2"),
    )
    a2_panel = await world.client.get(
        f"/incidents/{incident_id}/paging",
        headers=await _headers(world.client, "rh-a2"),
    )
    assert b2_panel.json()["can_force_take"] is True
    assert a2_panel.json()["can_force_take"] is False
    assert a2_panel.json()["can_reassign"] is False

    in_b = await world.client.get(f"/incidents?team_id={team_b}", headers=world.admin)
    in_a = await world.client.get(f"/incidents?team_id={team_a}", headers=world.admin)
    assert str(incident_id) in [i["id"] for i in in_b.json()["items"]]
    assert str(incident_id) not in [i["id"] for i in in_a.json()["items"]]

    for status_ in ("resolved", "open"):
        resp = await world.client.patch(
            f"/incidents/{incident_id}", json={"status": status_}, headers=world.admin
        )
        assert resp.status_code == 200, resp.text
    state = await _state(world.app, incident_id)
    assert (state.chain_id, state.status) == (chain_b, "running")
    assert (b1, 0) in await _recorded(world, incident_id, state.round)


async def test_a_service_change_follows_the_same_rule_and_clears_the_team(world):
    a1 = await _user(world.app, "rs2-a1")
    outsider = await _user(world.app, "rs2-out")
    _, service_a, chain_a = await _team(world, "Platform", members=[a1], levels=[a1])
    team_b, service_b, _ = await _team(world, "Data", levels=[a1])
    incident_id = await _incident_on(world, service_a, chain_a)
    await world.client.post(
        f"/incidents/{incident_id}/reassign",
        json={"team_id": str(team_b), "note": "Handoff note"},
        headers=world.admin,
    )

    denied = await world.client.patch(
        f"/incidents/{incident_id}",
        json={"service_id": str(service_b), "service_id_set": True},
        headers=await _headers(world.client, "rs2-out"),
    )
    assert denied.status_code == 403
    moved = await world.client.patch(
        f"/incidents/{incident_id}",
        json={
            "service_id": str(service_b),
            "service_id_set": True,
            "handoff_reason": "It is a Data alert",
        },
        headers=world.admin,
    )
    assert moved.status_code == 200, moved.text
    assert (await _incident_row(world, incident_id)).team_id is None
    assert moved.json()["team_name"] == "Data"
    assert "Moved from platform-svc to data-svc. Note: It is a Data alert" in (
        await _comments(world.app, incident_id)
    )
    del outsider


# ── Responders ──────────────────────────────────────────────────────────────


async def test_up_to_three_responders_are_paged_and_listed(world):
    a1 = await _user(world.app, "ra-a1")
    helpers = [await _user(world.app, f"ra-h{i}") for i in range(4)]
    _, service_a, chain_a = await _team(world, "Platform", members=[a1], levels=[a1])
    incident_id = await _incident_on(world, service_a, chain_a)

    first = await world.client.post(
        f"/incidents/{incident_id}/responders",
        json={"user_ids": [str(helpers[0]), str(helpers[1])], "message": "Check lag"},
        headers=world.admin,
    )
    assert first.status_code == 201, first.text
    assert [r["username"] for r in first.json()["items"]] == ["ra-h0", "ra-h1"]
    assert first.json()["limit"] == 3
    too_many = await world.client.post(
        f"/incidents/{incident_id}/responders",
        json={"user_ids": [str(helpers[2]), str(helpers[3])]},
        headers=world.admin,
    )
    assert too_many.status_code == 409
    assert "You can add 1 more." in too_many.json()["detail"]
    third = await world.client.post(
        f"/incidents/{incident_id}/responders",
        json={"user_ids": [str(helpers[2])]},
        headers=world.admin,
    )
    assert third.status_code == 201, third.text
    full = await world.client.post(
        f"/incidents/{incident_id}/responders",
        json={"user_ids": [str(helpers[3])]},
        headers=world.admin,
    )
    assert full.status_code == 409
    assert "Remove one to add another." in full.json()["detail"]

    async with world.app.state.session_factory() as db:
        requests = (
            (
                await db.execute(
                    select(IncidentPage).where(
                        IncidentPage.incident_id == incident_id,
                        IncidentPage.channel == "responder",
                    )
                )
            )
            .scalars()
            .all()
        )
    assert sorted(page.user_id for page in requests) == sorted(helpers[:3])
    assert all(page.step_index is None and page.chain_id is None for page in requests)
    notice = (await _inbox(world, helpers[0], "incident.responder_requested"))[0]
    assert notice.body == "Check lag"
    assert "Asked ra-h0 and ra-h1 to help. Message: Check lag" in await _comments(
        world.app, incident_id
    )
    panel = await world.client.get(
        f"/incidents/{incident_id}/paging", headers=world.admin
    )
    assert [r["username"] for r in panel.json()["responders"]] == [
        "ra-h0",
        "ra-h1",
        "ra-h2",
    ]
    assert panel.json()["can_manage_responders"] is True


async def test_who_can_be_added_and_who_can_add(world):
    a1 = await _user(world.app, "rw-a1")
    helper = await _user(world.app, "rw-helper")
    viewer = await _user(world.app, "rw-viewer", role="viewer")
    outsider = await _user(world.app, "rw-out")
    _, service_a, chain_a = await _team(world, "Platform", members=[a1], levels=[a1])
    incident_id = await _incident_on(world, service_a, chain_a)
    await _own(world, incident_id, outsider)
    async with world.app.state.session_factory() as db:
        inactive = await UserRepo.create(
            db,
            username="rw-inactive",
            email="rw-inactive@test.com",
            password_hash="x",
            role="operator",
            primary_org_id=TEST_ORG_ID,
        )
        inactive.is_active = False
        await db.commit()

    async def add(user_id, headers=None):
        return await world.client.post(
            f"/incidents/{incident_id}/responders",
            json={"user_ids": [str(user_id)]},
            headers=headers or world.admin,
        )

    assert (await add(viewer)).status_code == 422
    assert (await add(inactive.id)).status_code == 422
    assert (await add(outsider)).status_code == 409  # already the owner
    # The owner can add responders even though they aren't on the team.
    owner_headers = await _headers(world.client, "rw-out")
    assert (await add(helper, owner_headers)).status_code == 201
    assert (await add(helper)).status_code == 409  # already a responder

    stranger = await _user(world.app, "rw-stranger")
    stranger_headers = await _headers(world.client, "rw-stranger")
    assert (await add(stranger, stranger_headers)).status_code == 403
    removed_by_stranger = await world.client.delete(
        f"/incidents/{incident_id}/responders/{helper}", headers=stranger_headers
    )
    assert removed_by_stranger.status_code == 403
    left = await world.client.delete(
        f"/incidents/{incident_id}/responders/{helper}",
        headers=await _headers(world.client, "rw-helper"),
    )
    assert left.status_code == 204
    assert "Left the responders." in await _comments(world.app, incident_id)
    gone = await world.client.delete(
        f"/incidents/{incident_id}/responders/{helper}", headers=world.admin
    )
    assert gone.status_code == 404


async def test_responder_pages_do_not_change_the_awaiting_label(world):
    a1 = await _user(world.app, "rl-a1")
    helper = await _user(world.app, "rl-helper")
    _, service_a, chain_a = await _team(world, "Platform", members=[a1], levels=[a1])
    incident_id = await _incident_on(world, service_a, chain_a)

    added = await world.client.post(
        f"/incidents/{incident_id}/responders",
        json={"user_ids": [str(helper)]},
        headers=world.admin,
    )
    assert added.status_code == 201, added.text
    detail = await world.client.get(f"/incidents/{incident_id}", headers=world.admin)
    assert detail.json()["responder_state"] == "awaiting"
    assert detail.json()["responder_user_id"] == str(a1)


async def test_a_maintenance_window_does_not_block_a_responder_request(world):
    a1 = await _user(world.app, "rm-a1")
    helper = await _user(world.app, "rm-helper")
    _, service_a, chain_a = await _team(world, "Platform", members=[a1], levels=[a1])
    incident_id = await _incident_on(world, service_a, chain_a)
    now = datetime.now(timezone.utc)
    async with world.app.state.session_factory() as db:
        await MaintenanceWindowRepo.create(
            db,
            TEST_ORG_ID,
            name="patching",
            starts_at=now - timedelta(hours=1),
            ends_at=now + timedelta(hours=1),
        )
        await db.commit()

    added = await world.client.post(
        f"/incidents/{incident_id}/responders",
        json={"user_ids": [str(helper)]},
        headers=world.admin,
    )
    assert added.status_code == 201, added.text
    async with world.app.state.session_factory() as db:
        suppressed = (
            (
                await db.execute(
                    select(IncidentPage).where(
                        IncidentPage.incident_id == incident_id,
                        IncidentPage.user_id == helper,
                        IncidentPage.delivery_error == "maintenance_window",
                    )
                )
            )
            .scalars()
            .all()
        )
    assert suppressed == []


async def test_removing_a_responder_stops_their_staged_notifications(world):
    a1 = await _user(world.app, "rx-a1")
    helper = await _user(world.app, "rx-helper")
    _, service_a, chain_a = await _team(world, "Platform", members=[a1], levels=[a1])
    incident_id = await _incident_on(world, service_a, chain_a)
    added = await world.client.post(
        f"/incidents/{incident_id}/responders",
        json={"user_ids": [str(helper)]},
        headers=world.admin,
    )
    assert added.status_code == 201, added.text
    async with world.app.state.session_factory() as db:
        stage = await NotificationEscalationRepo.create(
            db,
            TEST_ORG_ID,
            incident_id=incident_id,
            user_id=helper,
            priority="P1",
            stages=[{"channel_id": "x", "delay_seconds": 60}] * 2,
        )
        await db.commit()
        stage_id = stage.id

    removed = await world.client.delete(
        f"/incidents/{incident_id}/responders/{helper}", headers=world.admin
    )
    assert removed.status_code == 204
    async with world.app.state.session_factory() as db:
        assert (await db.get(NotificationEscalation, stage_id)).status == "cancelled"


async def test_removing_responder_keeps_their_live_chain_notifications(world):
    helper = await _user(world.app, "rx-chain-helper")
    _, service, chain = await _team(world, "ChainHelper", levels=[helper])
    incident_id = await _incident_on(world, service, chain)
    assert await _recorded(world, incident_id, 0) == [(helper, 0)]
    added = await world.client.post(
        f"/incidents/{incident_id}/responders",
        json={"user_ids": [str(helper)]},
        headers=world.admin,
    )
    assert added.status_code == 201, added.text
    async with world.app.state.session_factory() as db:
        stage = await NotificationEscalationRepo.create(
            db,
            TEST_ORG_ID,
            incident_id=incident_id,
            user_id=helper,
            priority="P1",
            stages=[{"channel_id": "x", "delay_seconds": 60}] * 2,
        )
        await db.commit()
        stage_id = stage.id

    removed = await world.client.delete(
        f"/incidents/{incident_id}/responders/{helper}", headers=world.admin
    )
    assert removed.status_code == 204
    async with world.app.state.session_factory() as db:
        assert (await db.get(NotificationEscalation, stage_id)).status == "running"


async def test_paging_the_same_person_again_restarts_a_finished_escalation(world):
    a1 = await _user(world.app, "re-a1")
    _, service_a, chain_a = await _team(world, "Platform", levels=[a1])
    incident_id = await _incident_on(world, service_a, chain_a)
    sent: list[int] = []

    async def sender(db, org_id, **kwargs):
        sent.append(1)
        return "sent", None

    stages = [_ne.Stage(channel_id="x", delay_seconds=60)] * 2
    async with world.app.state.session_factory() as db:
        incident = await IncidentRepo.get_by_id(db, TEST_ORG_ID, incident_id)
        user = await UserRepo.get_by_id(db, a1)
        await _ne.start_escalation(
            db, TEST_ORG_ID, incident=incident, user=user, stages=stages, sender=sender
        )
        # A second page while it runs doesn't double-start.
        await _ne.start_escalation(
            db, TEST_ORG_ID, incident=incident, user=user, stages=stages, sender=sender
        )
        await _ne.stop_escalation(db, TEST_ORG_ID, incident_id=incident_id)
        # Paging them again after it finished starts over.
        await _ne.start_escalation(
            db, TEST_ORG_ID, incident=incident, user=user, stages=stages, sender=sender
        )
        state = await NotificationEscalationRepo.get(
            db, TEST_ORG_ID, incident_id=incident_id, user_id=a1
        )
        await db.commit()
    assert len(sent) == 2
    assert (state.status, state.current_stage) == ("running", 0)
