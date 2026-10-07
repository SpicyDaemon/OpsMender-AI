"""M1-28: memory candidates from a postmortem follow the memory scope.

Operators file memories only under services their teams own; Global memories
need an admin. Saving a postmortem's memory candidates creates memories under
the incident's service (Global when it has none), so the same rule applies.
"""

from __future__ import annotations

import pytest
from sqlalchemy import func, select

from backend.db.models import IncidentMemory
from backend.db.repos import IncidentRepo, ServiceRepo, TeamRepo
from tests.test_ownership_lifecycle import (
    TEST_ORG_ID,
    _headers,
    _user,
    app as _app_fixture,
    client as _client_fixture,
    world as _world_fixture,
)

app = pytest.fixture(_app_fixture.__wrapped__)
client = pytest.fixture(_client_fixture.__wrapped__)
world = pytest.fixture(_world_fixture.__wrapped__)

POSTMORTEM = (
    "## Summary\nThe queue backed up.\n\n"
    "## Memory candidates\n"
    "- Drain the queue before a deploy.\n"
    "- Alert when the queue passes 10k.\n"
)


async def _incident(w, service_id) -> str:
    async with w.app.state.session_factory() as db:
        incident = await IncidentRepo.create(
            db,
            TEST_ORG_ID,
            title="queue backed up",
            description="d",
            service_id=service_id,
        )
        await IncidentRepo.set_postmortem(db, TEST_ORG_ID, incident.id, POSTMORTEM)
        await db.commit()
        return str(incident.id)


async def _memories(w) -> int:
    async with w.app.state.session_factory() as db:
        return (await db.execute(select(func.count(IncidentMemory.id)))).scalar_one()


@pytest.fixture
async def services(world):
    async with world.app.state.session_factory() as db:
        mine = await TeamRepo.create(db, TEST_ORG_ID, name="Payments", slug="pay-pm")
        other = await TeamRepo.create(db, TEST_ORG_ID, name="Data", slug="data-pm")
        await TeamRepo.add_member(db, TEST_ORG_ID, mine.id, user_id=world.level3)
        checkout = await ServiceRepo.create(
            db, TEST_ORG_ID, team_id=mine.id, name="Checkout", slug="checkout-pm"
        )
        warehouse = await ServiceRepo.create(
            db, TEST_ORG_ID, team_id=other.id, name="Warehouse", slug="warehouse-pm"
        )
        await db.commit()
        return checkout.id, warehouse.id


async def _save(w, incident_id, headers):
    return await w.client.post(
        f"/incidents/{incident_id}/postmortem/memory-candidates", headers=headers
    )


async def test_an_operator_saves_candidates_for_their_teams_service(world, services):
    checkout, _warehouse = services
    saved = await _save(
        world, await _incident(world, checkout), await _headers(world.client, "lc-l3")
    )
    assert saved.status_code == 200, saved.text
    assert saved.json()["created"] == 2


async def test_an_operator_cannot_save_candidates_for_another_teams_service(
    world, services
):
    _checkout, warehouse = services
    incident_id = await _incident(world, warehouse)
    refused = await _save(world, incident_id, await _headers(world.client, "lc-l3"))
    assert refused.status_code == 403, refused.text
    assert refused.json()["detail"] == (
        "Operators can assign memories only to services owned by their teams."
    )
    assert await _memories(world) == 0
    # An admin still can.
    assert (await _save(world, incident_id, world.admin)).json()["created"] == 2


async def test_an_operator_cannot_save_global_candidates(world, services):
    incident_id = await _incident(world, None)
    refused = await _save(world, incident_id, await _headers(world.client, "lc-l3"))
    assert refused.status_code == 403, refused.text
    assert refused.json()["detail"] == "Global memories require an Admin."
    assert await _memories(world) == 0


WRITE_REFUSED = (
    "Only an admin or a member of this incident's team can write its postmortem. "
    "Ask one of them."
)


REWRITTEN = "## Summary\nRewritten.\n"


async def _write(w, incident_id, headers):
    return await w.client.put(
        f"/incidents/{incident_id}/postmortem",
        json={"postmortem_md": REWRITTEN},
        headers=headers,
    )


async def _read(w, incident_id, headers):
    resp = await w.client.get(f"/incidents/{incident_id}/postmortem", headers=headers)
    assert resp.status_code == 200, resp.text
    return resp.json()


async def test_only_admins_and_the_handling_team_write_the_postmortem(world, services):
    checkout, warehouse = services
    operator = await _headers(world.client, "lc-l3")
    theirs = await _incident(world, warehouse)
    before = await _read(world, theirs, operator)
    refused = await _write(world, theirs, operator)
    assert refused.status_code == 403, refused.text
    assert refused.json()["detail"] == WRITE_REFUSED
    seen = await _read(world, theirs, operator)
    assert (
        seen["postmortem_md"] == before["postmortem_md"] and seen["can_edit"] is False
    )
    # An admin writes any incident's postmortem.
    assert (await _write(world, theirs, world.admin)).status_code == 200
    assert (await _read(world, theirs, world.admin))["can_edit"] is True
    # The handling team's operator writes their own incident's postmortem.
    own = await _incident(world, checkout)
    assert (await _read(world, own, operator))["can_edit"] is True
    written = await _write(world, own, operator)
    assert written.status_code == 200, written.text
    assert written.json()["can_edit"] is True
    # With no team on the incident, any operator writes, as with resolving.
    unowned = await _incident(world, None)
    assert (await _write(world, unowned, operator)).status_code == 200


async def test_a_viewer_reads_the_postmortem_without_editing(world, services):
    checkout, _warehouse = services
    await _user(world.app, "pm-viewer", role="viewer")
    viewer = await _headers(world.client, "pm-viewer")
    incident_id = await _incident(world, checkout)
    assert (await _read(world, incident_id, viewer))["can_edit"] is False
    assert (await _write(world, incident_id, viewer)).status_code == 403
