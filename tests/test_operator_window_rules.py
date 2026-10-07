"""M1-38 (O-05, R-11): an operator's Maintenance Window tells the covered
teams and admins once when it becomes active, lasts at most 24 hours, and its
creator (or an admin) edits and ends it; other operators can't."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from backend.db.models import InAppNotification, MaintenanceWindow
from backend.db.repos import TeamRepo
from backend.paging.window_notice import announce_started
from tests.test_operator_maintenance import _setup, _window
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

# level3 is on Platform (see _setup); level1 joins Platform here as a teammate;
# level2 stays off it.


async def _teammate(w, ids) -> None:
    async with w.app.state.session_factory() as db:
        await TeamRepo.add_member(db, TEST_ORG_ID, ids["Platform"][0], user_id=w.level1)
        await db.commit()


async def _notices(w) -> dict[uuid.UUID, list[str]]:
    async with w.app.state.session_factory() as db:
        rows = await db.execute(
            select(InAppNotification).where(
                InAppNotification.event_type == "maintenance.started"
            )
        )
        seen: dict[uuid.UUID, list[str]] = {}
        for row in rows.scalars():
            seen.setdefault(row.user_id, []).append(row.title)
        return seen


async def test_an_active_operator_window_tells_the_team_and_admins_once(world):
    ids = await _setup(world)
    await _teammate(world, ids)
    operator = await _headers(world.client, "lc-l3")

    resp = await world.client.post(
        "/maintenance-windows",
        json=_window("service", [ids["Platform"][1]]),
        headers=operator,
    )

    assert resp.status_code == 201, resp.text
    assert resp.json()["start_announced_at"] is not None
    notices = await _notices(world)
    # The teammate and the admin hear; the creator and other teams don't.
    assert set(notices) == {world.level1, world.admin_id}
    assert notices[world.level1] == ["Maintenance window started: Database upgrade"]
    async with world.app.state.session_factory() as db:
        again = await announce_started(db, at=datetime.now(timezone.utc))
        await db.commit()
    assert again == 0 and await _notices(world) == notices


async def test_a_later_window_is_announced_when_it_starts(world):
    ids = await _setup(world)
    await _teammate(world, ids)
    operator = await _headers(world.client, "lc-l3")
    start = datetime.now(timezone.utc) + timedelta(hours=2)
    body = _window("team", [ids["Platform"][0]])
    body.update(
        starts_at=start.isoformat(), ends_at=(start + timedelta(hours=1)).isoformat()
    )

    created = await world.client.post(
        "/maintenance-windows", json=body, headers=operator
    )

    assert created.status_code == 201, created.text
    assert await _notices(world) == {}
    async with world.app.state.session_factory() as db:
        early = await announce_started(db, at=start - timedelta(minutes=1))
        due = await announce_started(db, at=start + timedelta(minutes=1))
        twice = await announce_started(db, at=start + timedelta(minutes=2))
        await db.commit()
    assert (early, due, twice) == (0, 2, 0)
    assert set(await _notices(world)) == {world.level1, world.admin_id}


async def test_an_admins_window_announces_nothing(world):
    ids = await _setup(world)
    await _teammate(world, ids)

    resp = await world.client.post(
        "/maintenance-windows",
        json=_window("service", [ids["Platform"][1]]),
        headers=world.admin,
    )

    assert resp.status_code == 201, resp.text
    assert await _notices(world) == {}


async def test_an_approved_operator_request_announces_on_approval(world):
    ids = await _setup(world)
    await _teammate(world, ids)
    operator = await _headers(world.client, "lc-l3")
    # Another team's service: pending until an admin approves it.
    created = await world.client.post(
        "/maintenance-windows",
        json=_window("service", [ids["Data"][1]]),
        headers=operator,
    )
    assert created.status_code == 201 and created.json()["approved"] is False
    assert await _notices(world) == {}

    approved = await world.client.post(
        f"/maintenance-windows/{created.json()['id']}/approve", headers=world.admin
    )

    assert approved.status_code == 200, approved.text
    # Data has no members here, so only the admin hears.
    assert set(await _notices(world)) == {world.admin_id}


async def test_an_operator_window_lasts_at_most_a_day(world):
    ids = await _setup(world)
    operator = await _headers(world.client, "lc-l3")
    long = _window("service", [ids["Platform"][1]])
    starts = datetime.fromisoformat(long["starts_at"])
    long["ends_at"] = (starts + timedelta(hours=24, minutes=1)).isoformat()

    refused = await world.client.post(
        "/maintenance-windows", json=long, headers=operator
    )
    allowed = await world.client.post(
        "/maintenance-windows", json=long, headers=world.admin
    )

    assert refused.status_code == 422, refused.text
    assert "up to 24 hours" in refused.json()["detail"]
    assert allowed.status_code == 201, allowed.text


async def test_the_creator_edits_and_ends_their_window_and_others_cannot(world):
    ids = await _setup(world)
    creator = await _headers(world.client, "lc-l3")
    other = await _headers(world.client, "lc-l2")
    created = await world.client.post(
        "/maintenance-windows",
        json=_window("service", [ids["Platform"][1]]),
        headers=creator,
    )
    mw_id = created.json()["id"]

    for call in (
        ("PUT", f"/maintenance-windows/{mw_id}", {"name": "Taken"}),
        ("POST", f"/maintenance-windows/{mw_id}/end", None),
        ("DELETE", f"/maintenance-windows/{mw_id}", None),
    ):
        refused = await world.client.request(
            call[0], call[1], json=call[2], headers=other
        )
        assert refused.status_code == 403, (call, refused.text)

    renamed = await world.client.put(
        f"/maintenance-windows/{mw_id}",
        json={"name": "DB upgrade, part 2"},
        headers=creator,
    )
    rescoped = await world.client.put(
        f"/maintenance-windows/{mw_id}",
        json={"scope_ids": [str(ids["Data"][1])]},
        headers=creator,
    )
    stretched = await world.client.put(
        f"/maintenance-windows/{mw_id}",
        json={"ends_at": (datetime.now(timezone.utc) + timedelta(days=3)).isoformat()},
        headers=creator,
    )
    ended = await world.client.post(
        f"/maintenance-windows/{mw_id}/end", headers=creator
    )

    assert renamed.status_code == 200 and renamed.json()["name"] == "DB upgrade, part 2"
    # The editor sends the scope back unchanged: that's fine.
    same_scope = await world.client.put(
        f"/maintenance-windows/{mw_id}",
        json={
            "name": "DB upgrade, part 3",
            "scope_type": "service",
            "scope_id": str(ids["Platform"][1]),
            "scope_ids": [str(ids["Platform"][1])],
        },
        headers=creator,
    )
    assert same_scope.status_code == 200, same_scope.text
    assert rescoped.status_code == 422 and stretched.status_code == 422
    assert ended.status_code == 200, ended.text
    async with world.app.state.session_factory() as db:
        window = await db.get(
            MaintenanceWindow, uuid.UUID(mw_id), populate_existing=True
        )
    ends_at = window.ends_at.replace(tzinfo=window.ends_at.tzinfo or timezone.utc)
    assert ends_at <= datetime.now(timezone.utc)
    deleted = await world.client.delete(
        f"/maintenance-windows/{mw_id}", headers=creator
    )
    assert deleted.status_code == 204


async def test_a_window_that_has_not_started_is_deleted_not_ended(world):
    ids = await _setup(world)
    creator = await _headers(world.client, "lc-l3")
    later = _window("service", [ids["Platform"][1]])
    start = datetime.now(timezone.utc) + timedelta(hours=1)
    later.update(
        starts_at=start.isoformat(), ends_at=(start + timedelta(hours=1)).isoformat()
    )
    created = await world.client.post(
        "/maintenance-windows", json=later, headers=creator
    )

    ended = await world.client.post(
        f"/maintenance-windows/{created.json()['id']}/end", headers=creator
    )

    assert ended.status_code == 409
    assert (
        ended.json()["detail"] == "This window hasn't started yet. Delete it instead."
    )


async def test_an_admin_still_changes_any_window(world):
    ids = await _setup(world)
    await _user(world.app, "lc-l9")
    creator = await _headers(world.client, "lc-l3")
    created = await world.client.post(
        "/maintenance-windows",
        json=_window("service", [ids["Platform"][1]]),
        headers=creator,
    )

    rescoped = await world.client.put(
        f"/maintenance-windows/{created.json()['id']}",
        json={"scope_ids": [str(ids["Data"][1])]},
        headers=world.admin,
    )

    assert rescoped.status_code == 200, rescoped.text
