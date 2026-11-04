"""M1-137 (L-07): changing a service's owning team moves the cards of its
live incidents the way a service handoff does.

The 2026-11-04 live retest edited only a service's team from Alpha to Beta;
the incident's effective team changed, but the Alpha Telegram card kept its
original state with no moved-away receipt, because the service route
committed the change and scheduled nothing. The route now schedules the
same edit-only refresh as an incident's service handoff, naming the team the
incident left, for each live incident of the service that no reassignment
holds elsewhere.
"""

# The app, client and auth_headers fixtures come from the paging tests.
# ruff: noqa: F811

from __future__ import annotations

import uuid

import pytest

from backend.bots import notifier
from backend.db.repos import BotConnectorRepo, IncidentRepo
from tests.test_paging import TEST_ORG_ID, app, auth_headers, client  # noqa: F401


@pytest.fixture
def scheduled(monkeypatch):
    """The incident-event deliveries the routes schedule, by keyword."""
    calls: list[dict] = []

    def _record(factory, **kwargs):
        calls.append(kwargs)
        return None

    monkeypatch.setattr(notifier, "schedule_incident_event", _record)
    return calls


async def _team(client, auth_headers, name):
    response = await client.post(
        "/teams",
        json={"name": name, "slug": f"{name.lower()}-{uuid.uuid4().hex[:6]}"},
        headers=auth_headers,
    )
    assert response.status_code == 201, response.text
    return uuid.UUID(response.json()["id"])


async def _incident(client, auth_headers, service_id, title):
    response = await client.post(
        "/incidents",
        json={
            "title": title,
            "description": "503s rising",
            "severity": "high",
            "service_id": str(service_id),
        },
        headers=auth_headers,
    )
    assert response.status_code == 201, response.text
    return uuid.UUID(response.json()["id"])


async def test_a_service_team_change_moves_the_cards_of_its_live_incidents(
    app, client, auth_headers, scheduled
):
    alpha = await _team(client, auth_headers, "Alpha")
    beta = await _team(client, auth_headers, "Beta")
    gamma = await _team(client, auth_headers, "Gamma")
    service = await client.post(
        "/services",
        json={"team_id": str(alpha), "name": "Ratings", "slug": "ratings"},
        headers=auth_headers,
    )
    assert service.status_code == 201, service.text
    service_id = uuid.UUID(service.json()["id"])
    other = await client.post(
        "/services",
        json={"team_id": str(alpha), "name": "Reviews", "slug": "reviews"},
        headers=auth_headers,
    )
    other_id = uuid.UUID(other.json()["id"])

    live = await _incident(client, auth_headers, service_id, "Ratings 500s")
    reassigned = await _incident(client, auth_headers, service_id, "Ratings slow")
    resolved = await _incident(client, auth_headers, service_id, "Ratings old")
    elsewhere = await _incident(client, auth_headers, other_id, "Reviews 500s")
    async with app.state.session_factory() as db:
        # A channel that wants notifications, so the routes schedule events.
        await BotConnectorRepo.create(
            db,
            TEST_ORG_ID,
            name="telegram",
            platform="telegram",
            config={"allowed_chat_ids": ["-100A"]},
            credentials={"bot_token": "BOT"},
            allowed_capabilities=["notifications"],
            status="configured",
            is_enabled=True,
        )
        # One incident reassigned to a team of its own, one already resolved.
        held = await IncidentRepo.get_by_id(db, TEST_ORG_ID, reassigned)
        held.team_id = gamma
        await IncidentRepo.update_status(db, TEST_ORG_ID, resolved, "resolved")
        await db.commit()
    scheduled.clear()

    # A change of the service's name alone moves nothing.
    renamed = await client.put(
        f"/services/{service_id}", json={"name": "Ratings v2"}, headers=auth_headers
    )
    assert renamed.status_code == 200, renamed.text
    assert scheduled == []

    # The team change: the live incident that the service's team handled gets
    # the handoff refresh that names the team it left; the reassigned one,
    # the resolved one and the other service's incident get nothing.
    moved = await client.put(
        f"/services/{service_id}", json={"team_id": str(beta)}, headers=auth_headers
    )
    assert moved.status_code == 200, moved.text
    assert moved.json()["team_id"] == str(beta)
    assert [
        (c["incident_id"], c["event_type"], c["edit_only"], c["previous_team_id"])
        for c in scheduled
    ] == [(live, "incident.updated", True, alpha)]
    assert all(not c.get("previous_team_only") for c in scheduled)
    assert elsewhere not in {c["incident_id"] for c in scheduled}

    # The same team again is no move.
    scheduled.clear()
    same = await client.put(
        f"/services/{service_id}", json={"team_id": str(beta)}, headers=auth_headers
    )
    assert same.status_code == 200, same.text
    assert scheduled == []


async def test_a_service_team_change_pages_nobody(app, client, auth_headers, scheduled):
    alpha = await _team(client, auth_headers, "Alpha")
    beta = await _team(client, auth_headers, "Beta")
    service = await client.post(
        "/services",
        json={"team_id": str(alpha), "name": "Ratings", "slug": "ratings"},
        headers=auth_headers,
    )
    service_id = uuid.UUID(service.json()["id"])
    live = await _incident(client, auth_headers, service_id, "Ratings 500s")
    async with app.state.session_factory() as db:
        await BotConnectorRepo.create(
            db,
            TEST_ORG_ID,
            name="telegram",
            platform="telegram",
            config={"allowed_chat_ids": ["-100A"]},
            credentials={"bot_token": "BOT"},
            allowed_capabilities=["notifications"],
            status="configured",
            is_enabled=True,
        )
        await db.commit()
    scheduled.clear()

    moved = await client.put(
        f"/services/{service_id}", json={"team_id": str(beta)}, headers=auth_headers
    )
    assert moved.status_code == 200, moved.text
    # Only the edit-only refresh: no created, acknowledged or escalation
    # event, so no channel posts a new card and no responder is paged.
    assert [c["event_type"] for c in scheduled] == ["incident.updated"]
    assert scheduled[0]["edit_only"] is True
    async with app.state.session_factory() as db:
        incident = await IncidentRepo.get_by_id(db, TEST_ORG_ID, live)
    assert incident.status == "open" and incident.team_id is None
