"""M1-16: operators add responders only from the team handling the incident.

Admins add anyone. An operator, including an owner from another team, adds
only members of the handling team: the team it was reassigned to, else its
service's team. An incident with no team keeps the any-operator rule. A
refusal names the person and adds nobody; the limit of 3 is unchanged.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import func, select

from backend.db.models import (
    InAppNotification,
    IncidentComment,
    IncidentPage,
    IncidentResponder,
)
from backend.db.repos import TeamRepo
from backend.paging.responders import RESPONDER_CHANNEL
from tests.test_ownership_lifecycle import (
    TEST_ORG_ID,
    World,
    _headers,
    _user,
    app as _app_fixture,
    client as _client_fixture,
    world as _world_fixture,
)
from tests.test_reassign_responders_part15 import _incident_on, _own, _team
from tests.test_token_scope import _token

app = pytest.fixture(_app_fixture.__wrapped__)
client = pytest.fixture(_client_fixture.__wrapped__)
world = pytest.fixture(_world_fixture.__wrapped__)


def _off_team(name: str, team: str) -> str:
    return (
        f"{name} isn't on {team}, the team handling this incident. "
        "Ask an admin to add them."
    )


async def _setup(w: World) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:
    """Platform handles the incident: level1 (paged by its chain) and level3
    are its members. level2 is on Data. Returns the incident and both teams."""
    platform, service, chain = await _team(
        w, "Platform", members=[w.level1, w.level3], levels=[w.level1]
    )
    data, _, _ = await _team(w, "Data", members=[w.level2])
    return await _incident_on(w, service, chain), platform, data


async def _add(w: World, incident_id, user_ids, headers):
    return await w.client.post(
        f"/incidents/{incident_id}/responders",
        json={"user_ids": [str(user_id) for user_id in user_ids]},
        headers=headers,
    )


async def _snapshot(w: World, incident_id: uuid.UUID) -> dict:
    async with w.app.state.session_factory() as db:

        async def count(model, *where) -> int:
            return await db.scalar(select(func.count()).select_from(model).where(*where))

        responders = (
            await db.execute(
                select(IncidentResponder.user_id).where(
                    IncidentResponder.incident_id == incident_id
                )
            )
        ).scalars()
        return {
            "responders": sorted(responders),
            "pages": await count(
                IncidentPage,
                IncidentPage.incident_id == incident_id,
                IncidentPage.channel == RESPONDER_CHANNEL,
            ),
            "notices": await count(
                InAppNotification,
                InAppNotification.incident_id == incident_id,
                InAppNotification.event_type == "incident.responder_requested",
            ),
            "comments": await count(
                IncidentComment, IncidentComment.incident_id == incident_id
            ),
        }


async def test_an_operator_adds_a_teammate(world):
    incident_id, _, _ = await _setup(world)
    before = await _snapshot(world, incident_id)

    added = await _add(
        world, incident_id, [world.level3], await _headers(world.client, "lc-l1")
    )
    assert added.status_code == 201, added.text
    assert [r["username"] for r in added.json()["items"]] == ["lc-l3"]
    after = await _snapshot(world, incident_id)
    assert after["responders"] == [world.level3]
    assert (after["pages"], after["notices"]) == (1, 1)
    assert after["comments"] == before["comments"] + 1


async def test_an_operator_cannot_add_another_teams_member(world):
    incident_id, _, _ = await _setup(world)
    before = await _snapshot(world, incident_id)
    operator = await _headers(world.client, "lc-l1")

    refused = await _add(world, incident_id, [world.level2], operator)
    assert refused.status_code == 403
    assert refused.json()["detail"] == _off_team("lc-l2", "Platform")
    assert await _snapshot(world, incident_id) == before

    # One outsider in the request adds nobody, not even the teammate.
    mixed = await _add(world, incident_id, [world.level3, world.level2], operator)
    assert mixed.status_code == 403
    assert mixed.json()["detail"] == _off_team("lc-l2", "Platform")
    assert await _snapshot(world, incident_id) == before


async def test_an_off_team_owner_adds_only_the_handling_teams_members(world):
    incident_id, _, data = await _setup(world)
    data_mate = await _user(world.app, "tr-data-mate")
    async with world.app.state.session_factory() as db:
        await TeamRepo.add_member(db, TEST_ORG_ID, data, user_id=data_mate)
        await db.commit()
    await _own(world, incident_id, world.level2)
    before = await _snapshot(world, incident_id)
    owner = await _headers(world.client, "lc-l2")

    refused = await _add(world, incident_id, [data_mate], owner)
    assert refused.status_code == 403
    assert refused.json()["detail"] == _off_team("tr-data-mate", "Platform")
    assert await _snapshot(world, incident_id) == before

    added = await _add(world, incident_id, [world.level3], owner)
    assert added.status_code == 201, added.text
    assert (await _snapshot(world, incident_id))["responders"] == [world.level3]


async def test_an_admin_adds_anyone(world):
    incident_id, _, _ = await _setup(world)
    loner = await _user(world.app, "tr-loner")

    added = await _add(world, incident_id, [world.level2, loner], world.admin)
    assert added.status_code == 201, added.text
    assert (await _snapshot(world, incident_id))["responders"] == sorted(
        [world.level2, loner]
    )


async def test_an_operator_token_from_an_admin_follows_the_team_rule(world):
    incident_id, platform, _ = await _setup(world)
    # The token's creator is on the team, so only the candidate rule refuses.
    async with world.app.state.session_factory() as db:
        await TeamRepo.add_member(db, TEST_ORG_ID, platform, user_id=world.admin_id)
        await db.commit()
    before = await _snapshot(world, incident_id)
    operator = await _token(world, "operator")

    refused = await _add(world, incident_id, [world.level2], operator)
    assert refused.status_code == 403
    assert refused.json()["detail"] == _off_team("lc-l2", "Platform")
    assert await _snapshot(world, incident_id) == before

    added = await _add(world, incident_id, [world.level3], operator)
    assert added.status_code == 201, added.text


async def test_a_reassigned_incident_takes_responders_from_its_new_team(world):
    incident_id, _, data = await _setup(world)
    data_mate = await _user(world.app, "tr-data-mate")
    async with world.app.state.session_factory() as db:
        await TeamRepo.add_member(db, TEST_ORG_ID, data, user_id=data_mate)
        await db.commit()
    moved = await world.client.post(
        f"/incidents/{incident_id}/reassign",
        json={"team_id": str(data), "note": "Data owns the orders database"},
        headers=world.admin,
    )
    assert moved.status_code == 200, moved.text
    before = await _snapshot(world, incident_id)
    data_operator = await _headers(world.client, "lc-l2")

    refused = await _add(world, incident_id, [world.level3], data_operator)
    assert refused.status_code == 403
    assert refused.json()["detail"] == _off_team("lc-l3", "Data")
    assert await _snapshot(world, incident_id) == before

    added = await _add(world, incident_id, [data_mate], data_operator)
    assert added.status_code == 201, added.text


async def test_an_incident_with_no_team_keeps_the_any_operator_rule(world):
    await _team(world, "Data", members=[world.level2])
    incident_id = await _incident_on(world, None, None)

    added = await _add(
        world, incident_id, [world.level2], await _headers(world.client, "lc-l1")
    )
    assert added.status_code == 201, added.text
    assert (await _snapshot(world, incident_id))["responders"] == [world.level2]
