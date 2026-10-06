"""M1-15: hand several incidents to another team with one shared note.

Every selected incident must exist, be open and be one the person may
reassign, and none may already belong to that team; otherwise nothing moves.
Each incident is handed off exactly as a single Reassign does, keeps its
service, and gets the same note on its timeline.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from backend.db.models import Incident, IncidentComment
from backend.db.repos import IncidentRepo
from tests.test_ownership_lifecycle import (
    TEST_ORG_ID,
    World,
    _headers,
    app as _app_fixture,
    client as _client_fixture,
    world as _world_fixture,
)
from tests.test_reassign_responders_part15 import _incident_on, _team

app = pytest.fixture(_app_fixture.__wrapped__)
client = pytest.fixture(_client_fixture.__wrapped__)
world = pytest.fixture(_world_fixture.__wrapped__)

NOTE = "Data owns the orders pipeline now."

# Platform's chain pages level1; level3 is on Platform. Data and Ops are other
# teams; level2 is on Data.


async def _setup(w: World):
    platform, service, chain = await _team(
        w, "Platform", members=[w.level1, w.level3], levels=[w.level1]
    )
    data, data_service, data_chain = await _team(
        w, "Data", members=[w.level2], levels=[w.level2]
    )
    ops, _, _ = await _team(w, "Ops", members=[])
    return {
        "platform": platform,
        "data": data,
        "ops": ops,
        "platform_incidents": [
            await _incident_on(w, service, chain),
            await _incident_on(w, service, chain),
        ],
        "data_incident": await _incident_on(w, data_service, data_chain),
    }


async def _handoff(w: World, incident_ids, team_id, *, note=NOTE, name="lc-l3"):
    return await w.client.post(
        "/incidents/bulk",
        json={
            "action": "handoff",
            "incident_ids": [str(i) for i in incident_ids],
            "team_id": str(team_id),
            "note": note,
        },
        headers=await _headers(w.client, name),
    )


async def _rows(w: World, incident_ids) -> dict:
    async with w.app.state.session_factory() as db:
        rows = (
            await db.execute(
                select(
                    Incident.id, Incident.team_id, Incident.service_id, Incident.status
                ).where(Incident.id.in_(list(incident_ids)))
            )
        ).all()
        notes = (
            await db.execute(
                select(IncidentComment.incident_id, IncidentComment.body).where(
                    IncidentComment.incident_id.in_(list(incident_ids))
                )
            )
        ).all()
    return {
        "incidents": {
            row.id: (row.team_id, row.service_id, row.status) for row in rows
        },
        "notes": sorted((row.incident_id, row.body) for row in notes),
    }


async def test_two_incidents_move_with_the_same_note_and_keep_their_services(world):
    s = await _setup(world)
    first, second = s["platform_incidents"]
    before = await _rows(world, [first, second])
    resp = await _handoff(world, [first, second], s["data"])
    assert resp.status_code == 200, resp.text
    assert resp.json()["succeeded"] == 2
    after = await _rows(world, [first, second])
    for incident_id in (first, second):
        team_id, service_id, _ = after["incidents"][incident_id]
        assert team_id == s["data"]
        assert service_id == before["incidents"][incident_id][1]
        handoff = [
            body
            for owner, body in after["notes"]
            if owner == incident_id and "Reassigned" in body
        ]
        assert len(handoff) == 1 and handoff[0].endswith(f"Note: {NOTE}")


async def test_one_forbidden_incident_moves_none(world):
    s = await _setup(world)
    ids = [s["platform_incidents"][0], s["data_incident"]]
    before = await _rows(world, ids)
    resp = await _handoff(world, ids, s["ops"])
    assert resp.status_code == 403, resp.text
    # Every incident is checked before any of them is handed off or paged.
    assert resp.json()["detail"] == (
        'Only an admin or a member of its team can hand off "orders db is slow"; '
        "nothing was handed off."
    )
    assert await _rows(world, ids) == before


async def test_a_resolved_incident_moves_none(world):
    s = await _setup(world)
    first, second = s["platform_incidents"]
    async with world.app.state.session_factory() as db:
        await IncidentRepo.update_status(db, TEST_ORG_ID, second, "resolved")
        await db.commit()
    before = await _rows(world, [first, second])
    resp = await _handoff(world, [first, second], s["data"])
    assert resp.status_code == 409, resp.text
    assert (
        resp.json()["detail"]
        == '"orders db is slow" is resolved; nothing was handed off.'
    )
    assert await _rows(world, [first, second]) == before


async def test_a_note_and_a_new_team_are_required(world):
    s = await _setup(world)
    first, second = s["platform_incidents"]
    before = await _rows(world, [first, second])
    blank = await _handoff(world, [first, second], s["data"], note="   ")
    assert blank.status_code == 422, blank.text
    same = await _handoff(world, [first, second], s["platform"])
    assert same.status_code == 409, same.text
    assert "already handles" in same.json()["detail"]
    missing = await world.client.post(
        "/incidents/bulk",
        json={"action": "handoff", "incident_ids": [str(first)], "note": NOTE},
        headers=await _headers(world.client, "lc-l3"),
    )
    assert missing.status_code == 422, missing.text
    assert await _rows(world, [first, second]) == before


async def test_a_handoff_that_fails_partway_moves_none(world, monkeypatch):
    from backend.paging import reassign

    s = await _setup(world)
    first, second = s["platform_incidents"]
    before = await _rows(world, [first, second])
    real = reassign.reassign_to_team
    calls = []

    async def membership_changed(db, org_id, *, incident, **kwargs):
        calls.append(incident.id)
        if len(calls) == 2:
            raise PermissionError("The actor is no longer on this incident's team.")
        return await real(db, org_id, incident=incident, **kwargs)

    monkeypatch.setattr(reassign, "reassign_to_team", membership_changed)
    resp = await _handoff(world, [first, second], s["data"])
    assert resp.status_code == 403, resp.text
    assert resp.json()["detail"] == (
        "You can no longer hand off one of these incidents; nothing was handed off."
    )
    assert calls == [first, second]
    assert await _rows(world, [first, second]) == before


async def test_an_unknown_team_is_not_found(world):
    s = await _setup(world)
    resp = await _handoff(world, s["platform_incidents"], uuid.uuid4())
    assert resp.status_code == 404, resp.text
