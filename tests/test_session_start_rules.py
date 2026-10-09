"""M1-40 (R-26, O-10): starting an AI session with no incident is for admins
only, on the web or with a token; forcing a session past its model's cap is
for the incident's owner and admins."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import func, select

from backend.db.models import Session
from backend.db.repos import IncidentRepo, SessionRepo
from backend.paging.reassign import SESSION_CONTROL_FORBIDDEN
from tests.test_ownership_lifecycle import TEST_ORG_ID, _headers
from tests.test_session_control import (
    _mate_headers,
    _no_ai_run,  # noqa: F401  (autouse: no model workflow runs)
    _owned_incident,
    app as _app_fixture,
    client as _client_fixture,
    world as _world_fixture,
)

app = pytest.fixture(_app_fixture.__wrapped__)
client = pytest.fixture(_client_fixture.__wrapped__)
world = pytest.fixture(_world_fixture.__wrapped__)

NO_INCIDENT = "Only admins can start an AI session without an incident."


async def _token(w, role: str) -> dict[str, str]:
    resp = await w.client.post(
        "/api/v1/api-tokens",
        json={"name": f"start-{role}-{uuid.uuid4().hex[:6]}", "role": role},
        headers=w.admin,
    )
    assert resp.status_code == 201, resp.text
    return {"Authorization": f"Bearer {resp.json()['token']}"}


async def _loose_sessions(w) -> int:
    async with w.app.state.session_factory() as db:
        return await db.scalar(
            select(func.count())
            .select_from(Session)
            .where(Session.incident_id.is_(None))
        )


@pytest.mark.parametrize("who", ["operator", "operator-token", "viewer-token"])
async def test_only_admins_start_a_session_without_an_incident(world, who):
    headers = {
        "operator": lambda: _headers(world.client, "lc-l1"),
        "operator-token": lambda: _token(world, "operator"),
        "viewer-token": lambda: _token(world, "viewer"),
    }[who]

    resp = await world.client.post(
        "/sessions", json={"tier": 2}, headers=await headers()
    )

    assert resp.status_code == 403, resp.text
    if who != "viewer-token":  # a viewer never reaches the session rules
        assert resp.json()["detail"] == NO_INCIDENT
    assert await _loose_sessions(world) == 0


@pytest.mark.parametrize("who", ["admin", "admin-token"])
async def test_an_admin_starts_a_session_without_an_incident(world, who):
    headers = world.admin if who == "admin" else await _token(world, "admin")

    resp = await world.client.post("/sessions", json={"tier": 2}, headers=headers)

    assert resp.status_code == 201, resp.text
    assert await _loose_sessions(world) == 1


async def _fill(w, model_id: uuid.UUID) -> None:
    """Five running sessions on other incidents: the model's cap."""
    async with w.app.state.session_factory() as db:
        for index in range(5):
            other = await IncidentRepo.create(
                db, TEST_ORG_ID, title=f"busy {index}", description="d"
            )
            await SessionRepo.create(
                db,
                TEST_ORG_ID,
                tier=1,
                incident_id=other.id,
                model_config_id=model_id,
                status="active",
            )
        await db.commit()


async def test_forcing_past_the_cap_is_for_the_owner_and_admins(world):
    incident_id, model_id, mate = await _owned_incident(world)
    await _fill(world, model_id)
    body = {"incident_id": str(incident_id), "tier": 1, "force": True}

    refused = await world.client.post(
        "/sessions", json=body, headers=await _mate_headers(world, mate)
    )
    assert refused.status_code == 403, refused.text
    assert refused.json()["detail"] == SESSION_CONTROL_FORBIDDEN

    owner = await _headers(world.client, "lc-l3")
    forced = await world.client.post("/sessions", json=body, headers=owner)
    assert forced.status_code == 201, forced.text
    assert forced.json()["force_started"] is True
