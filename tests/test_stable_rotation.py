"""M1-37 (O-03, R-13): taking a member off a Roster (removal, deactivation or
demotion to Viewer) keeps everyone else's shifts where they were, and the
removed member's shifts pass to the next member. Demotion to Viewer leaves
every Roster, promotion doesn't restore it, and the confirmation can list the
Rosters and who takes the current shift."""

from __future__ import annotations

import uuid
from datetime import date, datetime, time, timedelta, timezone

import pytest

from backend.db.repos import RosterRepo, TeamRepo
from backend.paging.on_call import OnCallContext, OnCallMember, on_call_at
from tests.test_ownership_lifecycle import (
    TEST_ORG_ID,
    _user,
    app as _app_fixture,
    client as _client_fixture,
    world as _world_fixture,
)

app = pytest.fixture(_app_fixture.__wrapped__)
client = pytest.fixture(_client_fixture.__wrapped__)
world = pytest.fixture(_world_fixture.__wrapped__)

WEEKS = 8


def _week(anchor: date, week: int) -> datetime:
    return datetime.combine(
        anchor + timedelta(days=7 * week, hours=0), time(12), timezone.utc
    )


def test_a_removed_place_passes_its_shifts_to_the_next_member():
    people = [uuid.uuid4() for _ in range(4)]
    anchor = date(2026, 10, 5)

    def schedule(unavailable: set[uuid.UUID]) -> list[uuid.UUID | None]:
        ctx = OnCallContext(
            members=[
                OnCallMember(person, index, available=person not in unavailable)
                for index, person in enumerate(people)
            ],
            coverage_start_time="00:00",
            coverage_end_time="00:00",
            handoff_time="00:00",
            anchor_date=anchor,
        )
        return [on_call_at(ctx, _week(anchor, week)) for week in range(WEEKS)]

    before = schedule(set())
    after = schedule({people[1]})

    assert before == [people[week % 4] for week in range(WEEKS)]
    for week in range(WEEKS):
        if before[week] == people[1]:
            assert after[week] == people[2]  # the next member takes it
        else:
            assert after[week] == before[week]  # nobody else's shift moves
    assert schedule(set(people)) == [None] * WEEKS


async def _rostered(w, *, anchor: date) -> tuple[uuid.UUID, list[uuid.UUID]]:
    """A 24/7 weekly Roster of four: lc-l1, lc-l2, lc-l3 and a fourth."""
    fourth = await _user(w.app, f"rot-{uuid.uuid4().hex[:6]}")
    people = [w.level1, w.level2, w.level3, fourth]
    async with w.app.state.session_factory() as db:
        team = await TeamRepo.create(
            db, TEST_ORG_ID, name="Rotation", slug=f"rotation-{uuid.uuid4().hex[:6]}"
        )
        roster = await RosterRepo.create(
            db,
            TEST_ORG_ID,
            team_id=team.id,
            name=f"Primary {uuid.uuid4().hex[:4]}",
            anchor_date=anchor,
            coverage_start_time="00:00",
            coverage_end_time="00:00",
            handoff_time="00:00",
        )
        for index, person in enumerate(people):
            await TeamRepo.add_member(db, TEST_ORG_ID, team.id, user_id=person)
            await RosterRepo.add_member(
                db,
                TEST_ORG_ID,
                roster_id=roster.id,
                user_id=person,
                position_index=index,
            )
        await db.commit()
        return roster.id, people


async def _schedule(w, roster_id, anchor: date) -> list[str | None]:
    result = []
    for week in range(WEEKS):
        resp = await w.client.get(
            f"/rosters/{roster_id}/on-call",
            params={"at": _week(anchor, week).isoformat()},
            headers=w.admin,
        )
        assert resp.status_code == 200, resp.text
        result.append(resp.json()["user_id"])
    return result


async def _members(w, roster_id) -> list[str]:
    resp = await w.client.get(f"/rosters/{roster_id}/members", headers=w.admin)
    return [row["user_id"] for row in resp.json()["items"]]


@pytest.mark.parametrize("how", ["removed", "deactivated", "demoted"])
async def test_taking_a_member_off_keeps_every_other_shift(world, how):
    anchor = date(2026, 9, 7)
    roster_id, people = await _rostered(world, anchor=anchor)
    before = await _schedule(world, roster_id, anchor)
    gone = people[1]

    if how == "removed":
        resp = await world.client.delete(
            f"/rosters/{roster_id}/members/{gone}", headers=world.admin
        )
        assert resp.status_code == 204, resp.text
    else:
        change = {"is_active": False} if how == "deactivated" else {"role": "viewer"}
        resp = await world.client.patch(
            f"/auth/users/{gone}", json=change, headers=world.admin
        )
        assert resp.status_code == 200, resp.text

    after = await _schedule(world, roster_id, anchor)
    assert str(gone) not in await _members(world, roster_id)
    for week in range(WEEKS):
        if before[week] == str(gone):
            assert after[week] == str(people[2])
        else:
            assert after[week] == before[week]


async def test_promotion_does_not_restore_membership_but_adding_back_does(world):
    anchor = date(2026, 9, 7)
    roster_id, people = await _rostered(world, anchor=anchor)
    before = await _schedule(world, roster_id, anchor)
    gone = people[1]
    await world.client.patch(
        f"/auth/users/{gone}", json={"role": "viewer"}, headers=world.admin
    )

    promoted = await world.client.patch(
        f"/auth/users/{gone}", json={"role": "operator"}, headers=world.admin
    )
    assert promoted.status_code == 200
    assert str(gone) not in await _members(world, roster_id)

    added = await world.client.post(
        f"/rosters/{roster_id}/members",
        json={"user_id": str(gone), "position_index": 9},
        headers=world.admin,
    )
    assert added.status_code == 201, added.text
    assert added.json()["position_index"] == 1  # their old place
    assert await _schedule(world, roster_id, anchor) == before


async def test_a_new_order_starts_the_rotation_afresh(world):
    anchor = date(2026, 9, 7)
    roster_id, people = await _rostered(world, anchor=anchor)
    await world.client.delete(
        f"/rosters/{roster_id}/members/{people[1]}", headers=world.admin
    )

    order = [people[3], people[0], people[2]]
    resp = await world.client.post(
        f"/rosters/{roster_id}/members/reorder",
        json={"ordered_user_ids": [str(p) for p in order]},
        headers=world.admin,
    )

    assert resp.status_code == 200, resp.text
    assert await _schedule(world, roster_id, anchor) == [
        str(order[week % 3]) for week in range(WEEKS)
    ]


async def test_the_confirmation_lists_rosters_and_who_takes_the_shift(world):
    today = datetime.now(timezone.utc).date()
    # One week in: the second member holds the current shift.
    on_shift, people = await _rostered(world, anchor=today - timedelta(days=7))
    # The same people, but the first member holds this week.
    off_shift, _ = await _rostered(world, anchor=today)

    resp = await world.client.get(
        f"/auth/users/{people[1]}/roster-impact", headers=world.admin
    )

    assert resp.status_code == 200, resp.text
    items = {row["roster_id"]: row for row in resp.json()["items"]}
    assert items[str(on_shift)]["on_current_shift"] is True
    assert items[str(on_shift)]["current_shift_taken_by"] == "lc-l3"
    assert items[str(off_shift)]["on_current_shift"] is False
    assert items[str(off_shift)]["current_shift_taken_by"] is None
