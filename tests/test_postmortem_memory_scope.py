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
