"""M1-11: an active owner is replaced only with consent, force or a noted reassignment.

An active owner (P0 to P3) is replaced only by a confirmed takeover, an
eligible force take with a reason, or an operator of the handling team
assigning another member of that team with a note and a notice to the
previous owner. Admins may assign anyone, with the same note. Only the owner
or an admin releases. M1-50 (O-19): an assignment whose owner changed after
it was checked is refused with 409 and writes nothing.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import select

from backend.db.models import IncidentComment, IncidentPage, InAppNotification
from backend.db.repos import (
    EscalationChainRepo,
    IncidentAssignmentRepo,
    IncidentChainStateRepo,
    IncidentRepo,
    ServiceEscalationChainRepo,
    ServiceRepo,
    TeamRepo,
)
from backend.paging import escalation as _esc
from tests.test_ownership_lifecycle import (
    TEST_ORG_ID,
    World,
    _chain,
    _headers,
    _owner,
    _run,
    _slack,
    _user,
    app as _app_fixture,
    client as _client_fixture,
    world as _world_fixture,
)

app = pytest.fixture(_app_fixture.__wrapped__)
client = pytest.fixture(_client_fixture.__wrapped__)
world = pytest.fixture(_world_fixture.__wrapped__)

NOTE = "Taking this to the database on-call."


async def _team_incident(w: World, *, paging: bool = True) -> uuid.UUID:
    """level2 and level3 are on the team; the chain pages level2. A P2
    incident notifies instead and has no chain."""
    chain_id = await _chain(w.app, [w.level2])
    async with w.app.state.session_factory() as db:
        chain = await EscalationChainRepo.get_by_id(db, TEST_ORG_ID, chain_id)
        for member in (w.level2, w.level3):
            await TeamRepo.add_member(db, TEST_ORG_ID, chain.team_id, user_id=member)
        priority = "P1" if paging else "P2"
        service = await ServiceRepo.create(
            db,
            TEST_ORG_ID,
            team_id=chain.team_id,
            name=f"own-{uuid.uuid4().hex[:6]}",
            slug=f"own-{uuid.uuid4().hex[:6]}",
            priority=priority,
        )
        if paging:
            await ServiceEscalationChainRepo.link(
                db, TEST_ORG_ID, service_id=service.id, chain_id=chain_id
            )
        incident = await IncidentRepo.create(
            db,
            TEST_ORG_ID,
            title="owner protection",
            description="d",
            priority=priority,
            response_mode="page" if paging else "notify",
            service_id=service.id,
        )
        if paging:
            await _esc.start_chain(
                db,
                TEST_ORG_ID,
                incident_id=incident.id,
                chain_id=chain_id,
                at=datetime.now(timezone.utc),
            )
        await db.commit()
        return incident.id


async def _member(w: World, incident_id: uuid.UUID, name: str) -> uuid.UUID:
    user_id = await _user(w.app, name)
    async with w.app.state.session_factory() as db:
        incident = await IncidentRepo.get_by_id(db, TEST_ORG_ID, incident_id)
        service = await ServiceRepo.get_by_id(db, TEST_ORG_ID, incident.service_id)
        await TeamRepo.add_member(db, TEST_ORG_ID, service.team_id, user_id=user_id)
        await db.commit()
    return user_id


async def _owned_by_level2(w: World, incident_id: uuid.UUID) -> None:
    owner = await _headers(w.client, "lc-l2")
    resp = await w.client.post(f"/incidents/{incident_id}/ack", json={}, headers=owner)
    assert resp.status_code == 200, resp.text
    assert await _owner(w.app, incident_id) == w.level2


async def _snapshot(w: World, incident_id: uuid.UUID) -> dict:
    async with w.app.state.session_factory() as db:
        state = await IncidentChainStateRepo.get_for_incident(
            db, TEST_ORG_ID, incident_id
        )
        comments = (
            await db.execute(
                select(IncidentComment.body).where(
                    IncidentComment.incident_id == incident_id
                )
            )
        ).scalars()
        pages = (
            await db.execute(
                select(IncidentPage.user_id, IncidentPage.step_index).where(
                    IncidentPage.incident_id == incident_id,
                    IncidentPage.channel == "recorded",
                )
            )
        ).all()
        notices = (
            await db.execute(
                select(InAppNotification.user_id, InAppNotification.event_type).where(
                    InAppNotification.incident_id == incident_id
                )
            )
        ).all()
        return {
            "owner": await _owner(w.app, incident_id),
            "chain": None
            if state is None
            else (
                state.status,
                state.current_step_index,
                state.pending_takeover_user_id,
            ),
            "comments": sorted(comments),
            "pages": sorted(map(tuple, pages)),
            "notices": sorted(map(tuple, notices)),
        }


async def test_bulk_reassign_to_yourself_over_an_owner_is_refused(world):
    incident_id = await _team_incident(world)
    await _owned_by_level2(world, incident_id)
    before = await _snapshot(world, incident_id)
    teammate = await _headers(world.client, "lc-l3")
    resp = await world.client.post(
        "/incidents/bulk",
        json={
            "action": "reassign",
            "incident_ids": [str(incident_id)],
            "user_id": str(world.level3),
        },
        headers=teammate,
    )
    assert resp.status_code == 200, resp.text
    (item,) = resp.json()["items"]
    assert item["ok"] is False and "Request a takeover" in item["error"]
    assert await _snapshot(world, incident_id) == before


async def test_bulk_acknowledge_for_someone_else_follows_the_assignment_rule(world):
    incident_id = await _team_incident(world)
    await _owned_by_level2(world, incident_id)
    target = await _member(world, incident_id, "po-bulk-target")
    before = await _snapshot(world, incident_id)

    async def bulk(username: str, note: str | None = None) -> dict:
        body = {
            "action": "acknowledge",
            "incident_ids": [str(incident_id)],
            "user_id": str(target),
        }
        if note is not None:
            body["note"] = note
        resp = await world.client.post(
            "/incidents/bulk", json=body, headers=await _headers(world.client, username)
        )
        assert resp.status_code == 200, resp.text
        (item,) = resp.json()["items"]
        return item

    outsider = await bulk("lc-l1", NOTE)
    assert outsider["ok"] is False
    assert "operator of the team handling this incident" in outsider["error"]
    no_note = await bulk("lc-l3")
    assert no_note["ok"] is False and "Add a note" in no_note["error"]
    assert await _snapshot(world, incident_id) == before

    assert (await bulk("lc-l3", NOTE))["ok"] is True
    after = await _snapshot(world, incident_id)
    assert after["owner"] == target
    assert any(NOTE in body and "replacing lc-l2" in body for body in after["comments"])
    assert (world.level2, "incident.reassigned") in after["notices"]


async def test_a_teammate_assigns_another_member_with_a_note(world):
    incident_id = await _team_incident(world)
    await _owned_by_level2(world, incident_id)
    target = await _member(world, incident_id, "po-target")
    teammate = await _headers(world.client, "lc-l3")
    resp = await world.client.post(
        f"/incidents/{incident_id}/assign",
        json={"user_id": str(target), "note": NOTE},
        headers=teammate,
    )
    assert resp.status_code == 200, resp.text
    after = await _snapshot(world, incident_id)
    assert after["owner"] == target
    assert any(NOTE in body and "replacing lc-l2" in body for body in after["comments"])
    assert (world.level2, "incident.reassigned") in after["notices"]
    assert (target, "incident.assigned") in after["notices"]


async def test_replacing_an_owner_needs_a_note(world):
    incident_id = await _team_incident(world)
    await _owned_by_level2(world, incident_id)
    target = await _member(world, incident_id, "po-no-note")
    before = await _snapshot(world, incident_id)
    teammate = await _headers(world.client, "lc-l3")
    for note in (None, "   "):
        body = {"user_id": str(target)}
        if note is not None:
            body["note"] = note
        resp = await world.client.post(
            f"/incidents/{incident_id}/assign", json=body, headers=teammate
        )
        assert resp.status_code == 422, resp.text
        assert "Add a note" in resp.json()["detail"]
        assert await _snapshot(world, incident_id) == before


async def test_assigning_someone_off_the_team_is_refused(world):
    incident_id = await _team_incident(world)
    await _owned_by_level2(world, incident_id)
    before = await _snapshot(world, incident_id)
    teammate = await _headers(world.client, "lc-l3")
    resp = await world.client.post(
        f"/incidents/{incident_id}/assign",
        json={"user_id": str(world.level1), "note": NOTE},
        headers=teammate,
    )
    assert resp.status_code == 403, resp.text
    assert (
        resp.json()["detail"]
        == "Assign it to a member of the team handling this incident."
    )
    assert await _snapshot(world, incident_id) == before


async def test_an_operator_off_the_team_cannot_assign_anyone(world):
    incident_id = await _team_incident(world)
    await _owned_by_level2(world, incident_id)
    before = await _snapshot(world, incident_id)
    outsider = await _headers(world.client, "lc-l1")
    resp = await world.client.post(
        f"/incidents/{incident_id}/assign",
        json={"user_id": str(world.level3), "note": NOTE},
        headers=outsider,
    )
    assert resp.status_code == 403, resp.text
    assert "operator of the team handling this incident" in resp.json()["detail"]
    assert await _snapshot(world, incident_id) == before


async def test_an_admin_may_assign_anyone_with_a_note(world):
    incident_id = await _team_incident(world)
    await _owned_by_level2(world, incident_id)
    resp = await world.client.post(
        f"/incidents/{incident_id}/assign",
        json={"user_id": str(world.level1), "note": NOTE},
        headers=world.admin,
    )
    assert resp.status_code == 200, resp.text
    after = await _snapshot(world, incident_id)
    assert after["owner"] == world.level1
    assert (world.level2, "incident.reassigned") in after["notices"]


async def test_on_a_p2_incident_others_cannot_assign_or_acknowledge(world):
    incident_id = await _team_incident(world, paging=False)
    await _owned_by_level2(world, incident_id)
    before = await _snapshot(world, incident_id)
    teammate = await _headers(world.client, "lc-l3")
    for path in ("assign", "ack"):
        resp = await world.client.post(
            f"/incidents/{incident_id}/{path}", json={}, headers=teammate
        )
        assert resp.status_code == 409, (path, resp.text)
        # No chain to hold a takeover request, so it names the other ways.
        assert resp.json()["detail"] == (
            "lc-l2 owns this incident. Ask them to release it, or use Force take "
            "with a reason."
        )
        assert await _snapshot(world, incident_id) == before


async def test_take_on_an_owned_p2_incident_points_to_force_take(world):
    incident_id = await _team_incident(world, paging=False)
    await _owned_by_level2(world, incident_id)
    before = await _snapshot(world, incident_id)
    teammate = await _headers(world.client, "lc-l3")
    resp = await world.client.post(
        f"/incidents/{incident_id}/take", json={}, headers=teammate
    )
    assert resp.status_code == 409, resp.text
    assert "Use Force take" in resp.json()["detail"]
    assert await _snapshot(world, incident_id) == before


async def test_release_by_another_operator_is_refused(world):
    incident_id = await _team_incident(world)
    await _owned_by_level2(world, incident_id)
    before = await _snapshot(world, incident_id)
    teammate = await _headers(world.client, "lc-l3")
    resp = await world.client.post(
        f"/incidents/{incident_id}/release", headers=teammate
    )
    assert resp.status_code == 403, resp.text
    assert (
        resp.json()["detail"] == "Only the owner or an admin can release this incident."
    )
    # Nothing escalated: same owner, chain state and recorded pages.
    assert await _snapshot(world, incident_id) == before


async def test_the_owner_and_an_admin_can_release(world):
    first = await _team_incident(world)
    await _owned_by_level2(world, first)
    owner = await _headers(world.client, "lc-l2")
    resp = await world.client.post(f"/incidents/{first}/release", headers=owner)
    assert resp.status_code == 204, resp.text
    assert await _owner(world.app, first) is None

    second = await _team_incident(world)
    await _owned_by_level2(world, second)
    resp = await world.client.post(f"/incidents/{second}/release", headers=world.admin)
    assert resp.status_code == 204, resp.text
    assert await _owner(world.app, second) is None


async def test_slack_release_by_another_operator_is_refused(world):
    incident_id = await _team_incident(world)
    await _owned_by_level2(world, incident_id)
    before = await _snapshot(world, incident_id)
    text = await _slack(world, "/release", str(incident_id), "U-mate", world.level3)
    assert text == "Only the owner or an admin can release *owner protection*."
    assert await _snapshot(world, incident_id) == before

    text = await _slack(world, "/release", str(incident_id), "U-owner", world.level2)
    assert text.startswith("Released *owner protection*."), text
    assert await _owner(world.app, incident_id) is None


async def test_slack_on_an_owned_p2_incident_points_to_force_take(world):
    incident_id = await _team_incident(world, paging=False)
    await _owned_by_level2(world, incident_id)
    before = await _snapshot(world, incident_id)
    ack = await _slack(world, "/ack", str(incident_id), "U-mate", world.level3)
    assert ack == (
        "*owner protection* is owned by lc-l2. Ask them to release it, or use "
        "Force take in OpsMender with a reason."
    )
    take = await _slack(world, "/take", str(incident_id), "U-mate", world.level3)
    assert take.startswith("*owner protection* is owned by lc-l2, and without")
    assert take.endswith("use Force take in OpsMender with a reason.")
    assert await _snapshot(world, incident_id) == before


# M1-50 (N-01, O-19): the owner changed between an assignment's check and its
# write.

TAKEN_BY_LEVEL3 = (
    "lc-l3 owns this incident. Ask them to release it, or use Force take with a reason."
)


async def _assignments(
    w: World, incident_id: uuid.UUID
) -> list[tuple[uuid.UUID, bool]]:
    """Every assignment row of the incident as (owner, still active)."""
    async with w.app.state.session_factory() as db:
        rows = await IncidentAssignmentRepo.list_for_incident(
            db, TEST_ORG_ID, incident_id
        )
        return sorted(
            ((row.assigned_to, row.released_at is None) for row in rows), key=str
        )


def _after_the_check(
    monkeypatch, w: World, raced: uuid.UUID, change, result, **kwargs
) -> None:
    """Run ``change`` on ``raced`` in its own session, and commit it, right
    after an assignment of ``raced`` is authorized and before that assignment
    is written, as a request on another connection can."""
    from backend.api.routes import incidents as incident_routes

    authorize = incident_routes._authorize_assignment

    async def authorize_then_change(db, org_id, incident, *args, **kw):
        checked = await authorize(db, org_id, incident, *args, **kw)
        if incident.id == raced:
            assert await _run(w.app, change, incident_id=raced, **kwargs) == result
        return checked

    monkeypatch.setattr(incident_routes, "_authorize_assignment", authorize_then_change)


async def test_acknowledge_replaces_only_the_owner_the_caller_checked(world):
    owned = await _team_incident(world, paging=False)
    await _owned_by_level2(world, owned)
    unowned = await _team_incident(world, paging=False)

    async def assign_level3(incident_id: uuid.UUID, expected) -> str:
        async with world.app.state.session_factory() as db:
            outcome = await _esc.acknowledge(
                db,
                TEST_ORG_ID,
                incident_id=incident_id,
                assignee_id=world.level3,
                actor_id=world.admin_id,
                assigned_by="manual",
                replace_owner=True,
                note=NOTE,
                expected_owner_id=expected,
            )
            await db.commit()
        return outcome.status

    # Someone took it after the check, someone replaced the checked owner, and
    # the checked owner released it: each is refused and nothing changes.
    for incident_id, expected in (
        (owned, None),
        (owned, world.level1),
        (unowned, world.level2),
    ):
        before = (
            await _snapshot(world, incident_id),
            await _assignments(world, incident_id),
        )
        assert await assign_level3(incident_id, expected) == "owner_changed"
        assert (
            await _snapshot(world, incident_id),
            await _assignments(world, incident_id),
        ) == before

    # The owner it was checked against is still replaced.
    assert await assign_level3(owned, world.level2) == "acknowledged"
    assert await _assignments(world, owned) == sorted(
        [(world.level2, False), (world.level3, True)], key=str
    )


async def test_an_assignment_never_replaces_someone_who_took_it_after_the_check(
    world, monkeypatch
):
    incident_id = await _team_incident(world, paging=False)
    _after_the_check(
        monkeypatch,
        world,
        incident_id,
        _esc.handle_takeover_request,
        "assigned",
        requester_id=world.level3,
    )
    resp = await world.client.post(
        f"/incidents/{incident_id}/assign",
        json={"user_id": str(world.level2)},
        headers=world.admin,
    )
    assert resp.status_code == 409, resp.text
    assert resp.json()["detail"] == TAKEN_BY_LEVEL3
    # The taker keeps it: no second row, no note of an assignment, no notice.
    assert await _assignments(world, incident_id) == [(world.level3, True)]
    assert await _snapshot(world, incident_id) == {
        "owner": world.level3,
        "chain": None,
        "comments": ["Acknowledged the incident (via take)."],
        "pages": [],
        "notices": [],
    }


async def test_an_assignment_is_refused_when_the_checked_owner_released_it(
    world, monkeypatch
):
    incident_id = await _team_incident(world, paging=False)
    await _owned_by_level2(world, incident_id)
    _after_the_check(
        monkeypatch,
        world,
        incident_id,
        _esc.release_ownership,
        True,
        actor_id=world.level2,
    )
    resp = await world.client.post(
        f"/incidents/{incident_id}/assign",
        json={"user_id": str(world.level3), "note": NOTE},
        headers=world.admin,
    )
    assert resp.status_code == 409, resp.text
    assert resp.json()["detail"] == (
        "The owner changed while you were assigning this incident. Reload it and "
        "try again."
    )
    # Only the release happened: no new owner, no replacement note, no notice.
    assert await _assignments(world, incident_id) == [(world.level2, False)]
    assert await _snapshot(world, incident_id) == {
        "owner": None,
        "chain": None,
        "comments": [
            "Acknowledged the incident (via web_ui).",
            "Released the incident.",
        ],
        "pages": [],
        "notices": [],
    }


@pytest.mark.parametrize("action", ["acknowledge", "reassign"])
async def test_bulk_refuses_only_the_incident_taken_after_its_check(
    world, monkeypatch, action
):
    raced = await _team_incident(world, paging=False)
    calm = await _team_incident(world, paging=False)
    _after_the_check(
        monkeypatch,
        world,
        raced,
        _esc.handle_takeover_request,
        "assigned",
        requester_id=world.level3,
    )
    resp = await world.client.post(
        "/incidents/bulk",
        json={
            "action": action,
            # The raced incident goes first, so its Take commits before this
            # request has written anything (SQLite allows one writer).
            "incident_ids": [str(raced), str(calm)],
            "user_id": str(world.level2),
        },
        headers=world.admin,
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["items"] == [
        {"incident_id": str(raced), "ok": False, "error": TAKEN_BY_LEVEL3},
        {"incident_id": str(calm), "ok": True, "error": None},
    ]
    assert await _assignments(world, raced) == [(world.level3, True)]
    assert await _snapshot(world, raced) == {
        "owner": world.level3,
        "chain": None,
        "comments": ["Acknowledged the incident (via take)."],
        "pages": [],
        "notices": [],
    }
    assert await _owner(world.app, calm) == world.level2


CLOSED_MEANWHILE = (
    "The incident was resolved or merged while you were assigning it. "
    "Reload it to see where it stands."
)


async def test_an_assignment_is_refused_when_the_incident_closed_after_the_check(
    world, monkeypatch
):
    incident_id = await _team_incident(world, paging=False)
    before = await _snapshot(world, incident_id)
    _after_the_check(
        monkeypatch,
        world,
        incident_id,
        IncidentRepo.update_status,
        None,
        status="resolved",
    )
    resp = await world.client.post(
        f"/incidents/{incident_id}/assign",
        json={"user_id": str(world.level2)},
        headers=world.admin,
    )
    assert resp.status_code == 409, resp.text
    assert resp.json()["detail"] == CLOSED_MEANWHILE
    # Only the resolution happened: no owner, no row, no notice.
    assert await _assignments(world, incident_id) == []
    assert await _snapshot(world, incident_id) == before
    async with world.app.state.session_factory() as db:
        incident = await IncidentRepo.get_by_id(db, TEST_ORG_ID, incident_id)
        assert incident.status == "resolved"


@pytest.mark.parametrize("action", ["acknowledge", "reassign"])
async def test_bulk_refuses_only_the_incident_closed_after_its_check(
    world, monkeypatch, action
):
    raced = await _team_incident(world, paging=False)
    calm = await _team_incident(world, paging=False)
    before = await _snapshot(world, raced)
    _after_the_check(
        monkeypatch,
        world,
        raced,
        IncidentRepo.update_status,
        None,
        status="resolved",
    )
    resp = await world.client.post(
        "/incidents/bulk",
        json={
            "action": action,
            "incident_ids": [str(raced), str(calm)],
            "user_id": str(world.level2),
        },
        headers=world.admin,
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["items"] == [
        {"incident_id": str(raced), "ok": False, "error": CLOSED_MEANWHILE},
        {"incident_id": str(calm), "ok": True, "error": None},
    ]
    assert await _assignments(world, raced) == []
    assert await _snapshot(world, raced) == before
    assert await _owner(world.app, calm) == world.level2
