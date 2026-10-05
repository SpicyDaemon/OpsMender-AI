"""M1-26: chat actions and role-based notices follow the People role.

People edits ``User.role``; the membership row keeps the role from account
creation. Chat actions and role-targeted notices must use the People role:
a viewer demoted there cannot act from chat, an operator promoted there can,
and "new incident" notices follow the same role.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from backend.db.models import IncidentAssignment, InAppNotification, UserOrganization
from tests.test_ownership_lifecycle import (
    TEST_ORG_ID,
    World,
    _chain,
    _incident,
    _owner,
    _service_with_chain,
    _slack,
    _state,
    _user,
    app as _app_fixture,
    client as _client_fixture,
    world as _world_fixture,
)

app = pytest.fixture(_app_fixture.__wrapped__)
client = pytest.fixture(_client_fixture.__wrapped__)
world = pytest.fixture(_world_fixture.__wrapped__)

REFUSED = "Ask an admin to verify your identity link and role"


async def _set_role(w: World, user_id: uuid.UUID, role: str) -> None:
    resp = await w.client.patch(
        f"/auth/users/{user_id}", json={"role": role}, headers=w.admin
    )
    assert resp.status_code == 200 and resp.json()["role"] == role


async def _membership_role(w: World, user_id: uuid.UUID) -> str:
    async with w.app.state.session_factory() as db:
        return (await db.get(UserOrganization, (user_id, TEST_ORG_ID))).role


async def _assignments(w: World, incident_id: uuid.UUID) -> int:
    async with w.app.state.session_factory() as db:
        rows = await db.execute(
            select(IncidentAssignment).where(
                IncidentAssignment.incident_id == incident_id
            )
        )
        return len(list(rows.scalars()))


async def test_slack_ack_from_viewer_demoted_on_people_changes_nothing(world):
    incident = await _incident(world.app, await _chain(world.app, [world.level1]))
    await _set_role(world, world.level2, "viewer")
    assert await _membership_role(world, world.level2) == "operator"

    text = await _slack(world, "/ack", str(incident), "U-demoted", world.level2)

    assert REFUSED in text
    assert await _owner(world.app, incident) is None
    assert await _assignments(world, incident) == 0
    assert (await _state(world.app, incident)).status == "running"


async def test_slack_ack_from_viewer_promoted_on_people_takes_ownership(world):
    incident = await _incident(world.app, await _chain(world.app, [world.level1]))
    promoted = await _user(world.app, "pr-promoted", role="viewer")
    await _set_role(world, promoted, "operator")
    assert await _membership_role(world, promoted) == "viewer"

    text = await _slack(world, "/ack", str(incident), "U-promoted", promoted)

    assert text.startswith("You acknowledged") or text.startswith("You recorded")
    assert await _owner(world.app, incident) == promoted


async def test_new_incident_notice_follows_people_roles(world):
    demoted = await _user(world.app, "pr-notice-demoted")
    promoted = await _user(world.app, "pr-notice-promoted", role="viewer")
    await _set_role(world, demoted, "viewer")
    await _set_role(world, promoted, "operator")

    service = await _service_with_chain(world, world.level1)
    resp = await world.client.post(
        "/incidents",
        json={
            "title": "Role notice",
            "description": "People role check",
            "severity": "low",
            "service_id": str(service),
        },
        headers=world.admin,
    )
    assert resp.status_code == 201, resp.text

    async with world.app.state.session_factory() as db:
        rows = await db.execute(
            select(InAppNotification.user_id).where(
                InAppNotification.event_type == "incident.created",
                InAppNotification.incident_id == uuid.UUID(resp.json()["id"]),
            )
        )
        recipients = set(rows.scalars())
    assert promoted in recipients
    assert demoted not in recipients
    assert {world.admin_id, world.level1, world.level2, world.level3} <= recipients
