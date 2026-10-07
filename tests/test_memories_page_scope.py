"""M1-19: the memory list tells the page which services someone may file under.

Admins may use any service and Global (``writable_service_ids`` is null);
operators get the services their teams own; viewers get none.
"""

from __future__ import annotations

import pytest

from backend.db.repos import ServiceRepo, TeamRepo
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


async def test_the_list_names_the_services_each_person_may_file_under(world):
    async with world.app.state.session_factory() as db:
        mine = await TeamRepo.create(
            db, TEST_ORG_ID, name="Payments", slug="payments-w"
        )
        other = await TeamRepo.create(db, TEST_ORG_ID, name="Data", slug="data-w")
        await TeamRepo.add_member(db, TEST_ORG_ID, mine.id, user_id=world.level3)
        checkout = await ServiceRepo.create(
            db, TEST_ORG_ID, team_id=mine.id, name="Checkout", slug="checkout-w"
        )
        await ServiceRepo.create(
            db, TEST_ORG_ID, team_id=other.id, name="Warehouse", slug="warehouse-w"
        )
        await db.commit()

    admin = await world.client.get("/memories", headers=world.admin)
    assert admin.status_code == 200, admin.text
    assert admin.json()["writable_service_ids"] is None

    operator = await world.client.get(
        "/memories", headers=await _headers(world.client, "lc-l3")
    )
    assert operator.json()["writable_service_ids"] == [str(checkout.id)]

    lone = await world.client.get(
        "/memories", headers=await _headers(world.client, "lc-l2")
    )
    assert lone.json()["writable_service_ids"] == []
