"""M1-139 (I-03): an inbound ticket status becomes a status an incident
can hold.

The 2026-11-04 live round moved a real Jira ticket to In Progress. The
signed delivery was accepted and the linked incident stored ``acknowledged``,
which is not an incident status (open, in_progress, resolved, merged): the
default map sends both ``acknowledged`` and ``in_progress`` to "In Progress"
for the outbound direction, and the reverse lookup returned the first match.
"""

# The app and client fixtures come from the API tests.
# ruff: noqa: F811

from __future__ import annotations

import hashlib
import hmac
import json
import uuid

import pytest

from backend.db.repos import (
    IncidentRepo,
    IntegrationConnectorRepo,
    TicketSyncStateRepo,
)
from backend.services.ticket_sync import (
    DEFAULT_STATUS_MAPS,
    INCIDENT_STATUSES,
    normalized_status_map,
    reverse_status,
)
from tests.test_api import TEST_ORG_ID, app, client  # noqa: F401

SECRET = "ticket-secret"


async def _seed(app, kind: str, auth: dict, status_map: dict | None = None):
    async with app.state.session_factory() as db:
        incident = await IncidentRepo.create(
            db,
            TEST_ORG_ID,
            title="Ticket-linked outage",
            description="Linked external ticket",
            severity="high",
        )
        connector = await IntegrationConnectorRepo.create(
            db,
            TEST_ORG_ID,
            kind=kind,
            name=f"{kind} sync {uuid.uuid4()}",
            base_url="https://tickets.example.test",
            auth_type="pat" if kind == "jira" else "basic",
            auth=auth,
            # No status map of its own: the kind's default map applies.
            config={"ticket_sync_enabled": True, "status_map": status_map or {}},
            is_enabled=True,
        )
        await TicketSyncStateRepo.upsert(
            db,
            TEST_ORG_ID,
            connector_id=connector.id,
            incident_id=incident.id,
            external_ticket_id="OPS-42",
            external_ticket_url="https://tickets.example.test/browse/OPS-42",
            status_map=status_map or {},
        )
        await db.commit()
        return incident.id, connector.id


async def _jira_delivery(client, connector_id, status_name: str):
    raw = json.dumps(
        {"issue": {"key": "OPS-42", "fields": {"status": {"name": status_name}}}},
        separators=(",", ":"),
    ).encode()
    signature = "sha256=" + hmac.new(SECRET.encode(), raw, hashlib.sha256).hexdigest()
    return await client.post(
        f"/webhooks/ticket-sync/{connector_id}",
        content=raw,
        headers={
            "Content-Type": "application/json",
            "X-Hub-Signature-256": signature,
        },
    )


async def _status(app, incident_id) -> str:
    async with app.state.session_factory() as db:
        incident = await IncidentRepo.get_by_id(db, TEST_ORG_ID, incident_id)
        return incident.status


async def test_a_jira_ticket_moved_to_in_progress_puts_the_incident_in_progress(
    app, client
):
    incident_id, connector_id = await _seed(
        app, "jira", {"token": "api-token", "webhook_secret": SECRET}
    )

    # The live round's delivery, on the default map.
    moved = await _jira_delivery(client, connector_id, "In Progress")
    assert moved.status_code == 200, moved.text
    assert moved.json()["status"] == "in_progress"
    assert moved.json()["updated"] is True
    assert await _status(app, incident_id) == "in_progress"

    # The same delivery again changes nothing.
    again = await _jira_delivery(client, connector_id, "In Progress")
    assert again.status_code == 200
    assert again.json()["updated"] is False
    assert await _status(app, incident_id) == "in_progress"

    done = await _jira_delivery(client, connector_id, "Done")
    assert done.json()["status"] == "resolved"
    assert await _status(app, incident_id) == "resolved"

    # A ticket status the map does not name still writes nothing.
    unknown = await _jira_delivery(client, connector_id, "Blocked")
    assert unknown.status_code == 422
    assert await _status(app, incident_id) == "resolved"


async def test_a_servicenow_ticket_moved_to_in_progress_does_the_same(app, client):
    incident_id, connector_id = await _seed(
        app,
        "servicenow",
        {"username": "u", "password": "p", "webhook_token": "sn-token"},
    )
    response = await client.post(
        f"/webhooks/ticket-sync/{connector_id}",
        params={"webhook_token": "sn-token"},
        json={"sys_id": "OPS-42", "state": "2"},
    )
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "in_progress"
    assert await _status(app, incident_id) == "in_progress"


@pytest.mark.parametrize("kind", sorted(DEFAULT_STATUS_MAPS))
def test_no_default_map_reverses_to_a_status_an_incident_cannot_hold(kind):
    mapping = normalized_status_map(kind, None)
    assert "acknowledged" in mapping  # still pushed outward
    for external in set(mapping.values()):
        assert reverse_status(mapping, external) in INCIDENT_STATUSES


def test_a_ticket_status_only_the_acknowledgement_maps_to_reads_as_in_progress():
    mapping = normalized_status_map(
        "jira",
        {
            "open": "Backlog",
            "acknowledged": "Triaged",
            "in_progress": "Working",
            "resolved": "Complete",
        },
    )
    # Nobody is recorded as having acknowledged the incident: the ticket's
    # progress is all that is known.
    assert reverse_status(mapping, "triaged") == "in_progress"
    assert reverse_status(mapping, "Working") == "in_progress"
    assert reverse_status(mapping, "Backlog") == "open"
    assert reverse_status(mapping, "Complete") == "resolved"
    assert reverse_status(mapping, "Blocked") is None


def test_a_ticket_status_two_incident_statuses_map_to_takes_the_earlier_one():
    mapping = normalized_status_map(
        "jira", {"open": "Active", "in_progress": "Active", "acknowledged": "Active"}
    )
    assert reverse_status(mapping, "Active") == "open"
