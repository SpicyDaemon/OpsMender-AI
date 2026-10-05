"""M1-09: viewers are never paged and never own an incident (EC-P01).

A viewer is whoever People shows as a viewer (``User.role``): paging skips
them for user, team and roster levels, an empty level moves on, and neither
an escalation step nor an assignment can name them.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from backend.db.models import EscalationStep, IncidentAssignment, IncidentPage
from backend.db.repos import (
    EscalationChainRepo,
    EscalationStepRepo,
    IncidentRepo,
    RosterRepo,
    ServiceEscalationChainRepo,
    ServiceRepo,
    TeamRepo,
)
from tests.test_ownership_lifecycle import (
    TEST_ORG_ID,
    _headers,
    _owner,
    _recorded_pages,
    _state,
    _user,
    app as _app_fixture,
    client as _client_fixture,
)

app = pytest.fixture(_app_fixture.__wrapped__)
client = pytest.fixture(_client_fixture.__wrapped__)


async def _admin(app, client) -> dict[str, str]:
    await _user(app, "vp-admin", role="admin")
    return await _headers(client, "vp-admin")


async def _team(app, members: list[uuid.UUID] = ()) -> uuid.UUID:
    async with app.state.session_factory() as db:
        team = await TeamRepo.create(
            db, TEST_ORG_ID, name="Viewers", slug=f"v-{uuid.uuid4().hex[:8]}"
        )
        for user_id in members:
            await TeamRepo.add_member(db, TEST_ORG_ID, team.id, user_id=user_id)
        await db.commit()
        return team.id


async def _service(app, team_id: uuid.UUID, levels) -> uuid.UUID:
    """A P1 service whose chain has one level per (target_type, target_id)."""
    async with app.state.session_factory() as db:
        chain = await EscalationChainRepo.create(
            db, TEST_ORG_ID, team_id=team_id, name=f"c-{uuid.uuid4().hex[:6]}"
        )
        for index, (target_type, target_id) in enumerate(levels):
            await EscalationStepRepo.create(
                db,
                TEST_ORG_ID,
                chain_id=chain.id,
                step_index=index,
                target_type=target_type,
                target_id=target_id,
                timeout_seconds=300,
            )
        service = await ServiceRepo.create(
            db,
            TEST_ORG_ID,
            team_id=team_id,
            name=f"svc-{uuid.uuid4().hex[:6]}",
            slug=f"svc-{uuid.uuid4().hex[:6]}",
            priority="P1",
        )
        await ServiceEscalationChainRepo.link(
            db, TEST_ORG_ID, service_id=service.id, chain_id=chain.id
        )
        await db.commit()
        return service.id


async def _open(app, client, headers, service_id: uuid.UUID) -> uuid.UUID:
    resp = await client.post(
        "/incidents",
        json={
            "title": "Viewer paging",
            "description": "Synthetic paging check",
            "severity": "high",
            "service_id": str(service_id),
        },
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    incident_id = uuid.UUID(resp.json()["id"])
    async with app.state.session_factory() as db:
        saved = await IncidentRepo.get_by_id(db, TEST_ORG_ID, incident_id)
        assert (saved.priority, saved.response_mode) == ("P1", "page")
    return incident_id


async def _paged_users(app, incident_id: uuid.UUID) -> set[uuid.UUID]:
    """Everyone with any page row, recorded or delivery attempt."""
    async with app.state.session_factory() as db:
        rows = await db.execute(
            select(IncidentPage.user_id).where(IncidentPage.incident_id == incident_id)
        )
        return set(rows.scalars())


async def test_team_level_pages_only_admins_and_operators(app, client):
    admin = await _admin(app, client)
    operator = await _user(app, "vp-operator")
    other_admin = await _user(app, "vp-admin-2", role="admin")
    viewer = await _user(app, "vp-viewer", role="viewer")
    team = await _team(app, [operator, other_admin, viewer])
    service = await _service(app, team, [("team", team)])

    incident = await _open(app, client, admin, service)

    assert await _recorded_pages(app, incident) == sorted(
        [(operator, 0), (other_admin, 0)], key=lambda page: str(page[0])
    )
    assert viewer not in await _paged_users(app, incident)
    assert (await _state(app, incident)).current_step_index == 0


async def test_user_level_demoted_on_people_pages_nobody_and_moves_on(app, client):
    admin = await _admin(app, client)
    demoted = await _user(app, "vp-demoted")
    backup = await _user(app, "vp-backup")
    team = await _team(app)
    service = await _service(app, team, [("user", demoted), ("user", backup)])
    resp = await client.patch(
        f"/auth/users/{demoted}", json={"role": "viewer"}, headers=admin
    )
    assert resp.status_code == 200 and resp.json()["role"] == "viewer"

    incident = await _open(app, client, admin, service)

    assert await _recorded_pages(app, incident) == [(backup, 1)]
    assert demoted not in await _paged_users(app, incident)
    state = await _state(app, incident)
    assert state.status == "running" and state.current_step_index == 1


async def test_people_promotion_makes_a_viewer_pageable(app, client):
    admin = await _admin(app, client)
    promoted = await _user(app, "vp-promoted", role="viewer")
    team = await _team(app)
    resp = await client.patch(
        f"/auth/users/{promoted}", json={"role": "operator"}, headers=admin
    )
    assert resp.status_code == 200 and resp.json()["role"] == "operator"
    service = await _service(app, team, [("user", promoted)])

    incident = await _open(app, client, admin, service)

    assert await _recorded_pages(app, incident) == [(promoted, 0)]


async def test_roster_member_demoted_on_shift_leaves_the_level_empty(app, client):
    admin = await _admin(app, client)
    viewer = await _user(app, "vp-roster-on-shift")
    backup = await _user(app, "vp-roster-backup")
    team = await _team(app, [viewer, backup])
    async with app.state.session_factory() as db:
        roster = await RosterRepo.create(
            db,
            TEST_ORG_ID,
            team_id=team,
            name="Always",
            anchor_date=(datetime.now(timezone.utc) - timedelta(days=7)).date(),
            pattern="daily",
            pattern_length=1,
            coverage_start_time="00:00",
            coverage_end_time="00:00",
            handoff_time="00:00",
        )
        await RosterRepo.add_member(
            db, TEST_ORG_ID, roster_id=roster.id, user_id=viewer, position_index=0
        )
        await db.commit()
    service = await _service(app, team, [("roster", roster.id), ("user", backup)])
    # Rosters only take admins and operators; the person is demoted later.
    resp = await client.patch(
        f"/auth/users/{viewer}", json={"role": "viewer"}, headers=admin
    )
    assert resp.status_code == 200 and resp.json()["role"] == "viewer"

    incident = await _open(app, client, admin, service)

    assert await _recorded_pages(app, incident) == [(backup, 1)]
    assert viewer not in await _paged_users(app, incident)


async def test_escalation_step_cannot_target_a_viewer(app, client):
    admin = await _admin(app, client)
    viewer = await _user(app, "vp-step-viewer", role="viewer")
    operator = await _user(app, "vp-step-operator")
    team = await _team(app)
    chain = await client.post(
        "/escalation-chains",
        json={"team_id": str(team), "name": "Viewer target"},
        headers=admin,
    )
    assert chain.status_code == 201, chain.text
    chain_id = chain.json()["id"]

    refused = await client.post(
        f"/escalation-chains/{chain_id}/steps",
        json={
            "step_index": 0,
            "target_type": "user",
            "target_id": str(viewer),
            "timeout_seconds": 300,
        },
        headers=admin,
    )
    assert refused.status_code == 422, refused.text
    assert "Viewers are never paged" in refused.json()["detail"]
    async with app.state.session_factory() as db:
        rows = await db.execute(
            select(EscalationStep).where(EscalationStep.chain_id == uuid.UUID(chain_id))
        )
        assert list(rows.scalars()) == []

    accepted = await client.post(
        f"/escalation-chains/{chain_id}/steps",
        json={
            "step_index": 0,
            "target_type": "user",
            "target_id": str(operator),
            "timeout_seconds": 300,
        },
        headers=admin,
    )
    assert accepted.status_code == 201, accepted.text


async def test_assigning_a_viewer_is_refused(app, client):
    admin = await _admin(app, client)
    operator = await _user(app, "vp-assign-operator")
    viewer = await _user(app, "vp-assign-viewer", role="viewer")
    team = await _team(app, [operator])
    service = await _service(app, team, [("user", operator)])
    incident = await _open(app, client, admin, service)

    refused = await client.post(
        f"/incidents/{incident}/assign",
        json={"user_id": str(viewer)},
        headers=admin,
    )
    assert refused.status_code == 422, refused.text
    assert await _owner(app, incident) is None
    async with app.state.session_factory() as db:
        rows = await db.execute(
            select(IncidentAssignment).where(IncidentAssignment.incident_id == incident)
        )
        assert list(rows.scalars()) == []
    assert (await _state(app, incident)).status == "running"

    accepted = await client.post(
        f"/incidents/{incident}/assign",
        json={"user_id": str(operator)},
        headers=admin,
    )
    assert accepted.status_code == 200, accepted.text
    assert await _owner(app, incident) == operator
