"""M1-14: a team handoff and a service move each need a note (EC-I04).

A missing or blank note is refused with 422 and nothing moves; a note lands
on the incident's timeline. A save that does not change the service needs no
note.
"""

from __future__ import annotations

import pytest

from tests.test_ownership_lifecycle import (
    _comments,
    _user,
    app as _app_fixture,
    client as _client_fixture,
    world as _world_fixture,
)
from tests.test_reassign_responders_part15 import _incident_on, _incident_row, _team

app = pytest.fixture(_app_fixture.__wrapped__)
client = pytest.fixture(_client_fixture.__wrapped__)
world = pytest.fixture(_world_fixture.__wrapped__)


async def _two_teams(w):
    a1 = await _user(w.app, "hn-a1")
    b1 = await _user(w.app, "hn-b1")
    _, service_a, chain_a = await _team(w, "Platform", members=[a1], levels=[a1])
    team_b, service_b, _ = await _team(w, "Data", members=[b1], levels=[b1])
    incident_id = await _incident_on(w, service_a, chain_a)
    return incident_id, service_a, team_b, service_b


@pytest.mark.parametrize("body", [{}, {"note": ""}, {"note": "   "}])
async def test_team_handoff_without_a_note_is_refused(world, body):
    incident_id, service_a, team_b, _ = await _two_teams(world)
    before = await _comments(world.app, incident_id)

    resp = await world.client.post(
        f"/incidents/{incident_id}/reassign",
        json={"team_id": str(team_b), **body},
        headers=world.admin,
    )

    assert resp.status_code == 422, resp.text
    row = await _incident_row(world, incident_id)
    assert row.team_id is None and row.service_id == service_a
    assert await _comments(world.app, incident_id) == before


async def test_team_handoff_note_lands_on_the_timeline(world):
    incident_id, service_a, team_b, _ = await _two_teams(world)

    resp = await world.client.post(
        f"/incidents/{incident_id}/reassign",
        json={
            "team_id": str(team_b),
            "note": "  The alert comes from the Data pipeline.  ",
        },
        headers=world.admin,
    )

    assert resp.status_code == 200, resp.text
    row = await _incident_row(world, incident_id)
    assert row.team_id == team_b and row.service_id == service_a
    assert any(
        "Note: The alert comes from the Data pipeline." in body
        for body in await _comments(world.app, incident_id)
    )


@pytest.mark.parametrize("body", [{}, {"handoff_reason": ""}, {"handoff_reason": "  "}])
async def test_service_move_without_a_note_is_refused(world, body):
    incident_id, service_a, _, service_b = await _two_teams(world)
    before = await _comments(world.app, incident_id)

    resp = await world.client.patch(
        f"/incidents/{incident_id}",
        json={"service_id": str(service_b), "service_id_set": True, **body},
        headers=world.admin,
    )

    assert resp.status_code == 422, resp.text
    assert "handoff note is required" in resp.json()["detail"]
    assert (await _incident_row(world, incident_id)).service_id == service_a
    assert await _comments(world.app, incident_id) == before


async def test_service_move_note_lands_on_the_timeline(world):
    incident_id, _, _, service_b = await _two_teams(world)

    resp = await world.client.patch(
        f"/incidents/{incident_id}",
        json={
            "service_id": str(service_b),
            "service_id_set": True,
            "handoff_reason": "Data owns this pipeline",
        },
        headers=world.admin,
    )

    assert resp.status_code == 200, resp.text
    assert (await _incident_row(world, incident_id)).service_id == service_b
    assert any(
        "Note: Data owns this pipeline" in body
        for body in await _comments(world.app, incident_id)
    )


async def test_saving_without_a_service_change_needs_no_note(world):
    incident_id, service_a, _, _ = await _two_teams(world)

    resp = await world.client.patch(
        f"/incidents/{incident_id}",
        json={
            "severity": "critical",
            "service_id": str(service_a),
            "service_id_set": True,
        },
        headers=world.admin,
    )

    assert resp.status_code == 200, resp.text
    row = await _incident_row(world, incident_id)
    assert row.severity == "critical" and row.service_id == service_a
