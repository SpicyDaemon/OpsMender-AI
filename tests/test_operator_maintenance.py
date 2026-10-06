"""M1-06: an operator's Maintenance Window on their own teams takes effect.

An operator's window is active at once when every target is one of their
teams, or a service one of their teams owns. Global, roster, other teams'
services and mixes stay pending for an admin; admins are always active. Every
named service, team or roster must exist in the workspace.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest

from backend.db.models import MaintenanceWindow
from backend.db.repos import ServiceRepo, TeamRepo
from tests.test_ownership_lifecycle import (
    TEST_ORG_ID,
    World,
    _headers,
    app as _app_fixture,
    client as _client_fixture,
    world as _world_fixture,
)
from tests.test_token_scope import _token

app = pytest.fixture(_app_fixture.__wrapped__)
client = pytest.fixture(_client_fixture.__wrapped__)
world = pytest.fixture(_world_fixture.__wrapped__)

# level3 is on Platform; Data is another team.


async def _setup(w: World) -> dict:
    async with w.app.state.session_factory() as db:
        ids = {}
        for name in ("Platform", "Data"):
            team = await TeamRepo.create(
                db,
                TEST_ORG_ID,
                name=name,
                slug=f"{name.lower()}-{uuid.uuid4().hex[:6]}",
            )
            service = await ServiceRepo.create(
                db,
                TEST_ORG_ID,
                team_id=team.id,
                name=f"{name} API",
                slug=f"{name.lower()}-api-{uuid.uuid4().hex[:6]}",
            )
            ids[name] = (team.id, service.id)
        await TeamRepo.add_member(db, TEST_ORG_ID, ids["Platform"][0], user_id=w.level3)
        await db.commit()
    return ids


def _window(scope_type: str, scope_ids: list[uuid.UUID]) -> dict:
    now = datetime.now(timezone.utc)
    return {
        "name": "Database upgrade",
        "scope_type": scope_type,
        "scope_ids": [str(scope_id) for scope_id in scope_ids],
        "starts_at": (now - timedelta(minutes=1)).isoformat(),
        "ends_at": (now + timedelta(hours=1)).isoformat(),
    }


async def _create(w: World, body: dict, headers) -> dict:
    resp = await w.client.post("/maintenance-windows", json=body, headers=headers)
    assert resp.status_code == 201, resp.text
    async with w.app.state.session_factory() as db:
        saved = await db.get(MaintenanceWindow, uuid.UUID(resp.json()["id"]))
    assert saved.approved is resp.json()["approved"]
    return resp.json()


async def test_an_operators_own_service_or_team_is_active_at_once(world):
    ids = await _setup(world)
    operator = await _headers(world.client, "lc-l3")
    service = await _create(world, _window("service", [ids["Platform"][1]]), operator)
    team = await _create(world, _window("team", [ids["Platform"][0]]), operator)
    assert service["approved"] is True and team["approved"] is True


async def test_anything_else_waits_for_an_admin(world):
    ids = await _setup(world)
    operator = await _headers(world.client, "lc-l3")
    for body in (
        _window("service", [ids["Data"][1]]),
        _window("team", [ids["Data"][0]]),
        _window("service", [ids["Platform"][1], ids["Data"][1]]),
        {**_window("global", []), "target_ids": ["*"]},
    ):
        assert (await _create(world, body, operator))["approved"] is False, body


async def test_an_operator_on_no_team_waits_for_an_admin(world):
    ids = await _setup(world)
    loner = await _headers(world.client, "lc-l2")
    assert (await _create(world, _window("service", [ids["Platform"][1]]), loner))[
        "approved"
    ] is False


async def test_an_operator_token_whose_creator_is_on_no_team_waits(world):
    ids = await _setup(world)
    token = await _token(world, "operator")
    assert (await _create(world, _window("service", [ids["Platform"][1]]), token))[
        "approved"
    ] is False


async def test_admins_are_always_active(world):
    ids = await _setup(world)
    created = await _create(world, _window("service", [ids["Data"][1]]), world.admin)
    assert created["approved"] is True


async def test_unknown_targets_are_refused(world):
    await _setup(world)
    operator = await _headers(world.client, "lc-l3")
    for scope_type in ("service", "team", "roster"):
        resp = await world.client.post(
            "/maintenance-windows",
            json=_window(scope_type, [uuid.uuid4()]),
            headers=operator,
        )
        assert resp.status_code == 422, (scope_type, resp.text)
        assert resp.json()["detail"] == (
            f"Each {scope_type} in a Maintenance Window must exist in this workspace."
        )
