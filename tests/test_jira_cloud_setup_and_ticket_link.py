"""M1-140 (I-01, I-04): Jira Cloud says it needs the account email, and an
incident's ticket link says where the ticket stands.

I-01: in the 2026-11-04 live round a valid Jira Cloud API token with the
email field blank drew a bare "Jira HTTP 403": the Jira form marks the email
optional, and without it the adapter sent the token as a bearer token, which
Cloud refuses. The same token with the email worked.

I-04: the incident's integration link carried the ticket's URL and title and
nothing about its status, the ticket carried no way back to the incident,
and an inbound change was not recorded as the status last agreed.
"""

# The app, client and auth_headers fixtures come from the API tests.
# ruff: noqa: F811

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import types
import uuid

import httpx
import pytest

import backend.integrations  # noqa: F401  (registers every adapter)
from backend.db.models import IntegrationConnector
from backend.db.repos import (
    IncidentIntegrationLinkRepo,
    IncidentRepo,
    IntegrationConnectorRepo,
    TicketSyncStateRepo,
)
from backend.integrations.atlassian import (
    BitbucketAdapter,
    ConfluenceAdapter,
    JiraAdapter,
)
from backend.integrations.registry import credential_fields_by_auth
from backend.services.ticket_sync import _create_params
from tests.test_api import TEST_ORG_ID, app, auth_headers, client  # noqa: F401

SECRET = "ticket-secret"
NEEDED = (
    "An Atlassian Cloud API token needs the email of the account it belongs "
    "to. Fill in the email field; leave it blank only for a Server or Data "
    "Center personal access token, with the edition set to match."
)


def _recording(adapter_class):
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"accountId": "1", "results": []})

    adapter = adapter_class(
        http_client_factory=lambda: httpx.AsyncClient(
            transport=httpx.MockTransport(handler)
        )
    )
    return adapter, requests


def _connector(kind: str, base_url: str | None, **config) -> IntegrationConnector:
    return IntegrationConnector(
        kind=kind, name=kind, base_url=base_url, auth_type="pat", config=config
    )


@pytest.mark.parametrize(
    ("adapter_class", "kind"),
    [(JiraAdapter, "jira"), (ConfluenceAdapter, "confluence")],
)
@pytest.mark.parametrize("config", [{"edition": "cloud"}, {}])
async def test_a_cloud_api_token_without_its_email_is_explained_not_sent(
    adapter_class, kind, config
):
    adapter, requests = _recording(adapter_class)
    cloud = _connector(kind, "https://opsmender.atlassian.net", **config)

    refused = await adapter.safe_invoke("test_connection", cloud, {"api_token": "t"})
    assert refused.ok is False
    assert refused.error == NEEDED
    # Nothing was sent: the old path sent the token as a bearer token and
    # Cloud answered 403.
    assert requests == []

    accepted = await adapter.safe_invoke(
        "test_connection", cloud, {"api_token": "t", "email": "ops@example.test"}
    )
    assert accepted.ok is True
    expected = "Basic " + base64.b64encode(b"ops@example.test:t").decode()
    assert [r.headers["Authorization"] for r in requests] == [expected]


@pytest.mark.parametrize(
    "config",
    [
        {"edition": "server"},
        {"edition": "data_center"},
        {},
        # The form saves "cloud" unless the edition is changed: a Data
        # Center site saved that way keeps its bearer token.
        {"edition": "cloud", "api_version": "2"},
    ],
)
async def test_a_site_of_its_own_still_gets_a_bearer_token(config):
    adapter, requests = _recording(JiraAdapter)
    server = _connector("jira", "https://jira.internal.example", **config)
    result = await adapter.safe_invoke("test_connection", server, {"api_token": "t"})
    assert result.ok is True
    assert [r.headers["Authorization"] for r in requests] == ["Bearer t"]


async def test_a_bitbucket_cloud_access_token_is_still_a_bearer_token():
    """Bitbucket shares the Atlassian headers, and its repository and
    workspace access tokens are bearer tokens with no account email."""
    adapter, requests = _recording(BitbucketAdapter)
    cloud = _connector("bitbucket", None, edition="cloud", workspace="acme", repo="svc")
    for action in ("test_connection", "get_repository"):
        result = await adapter.safe_invoke(action, cloud, {"api_token": "t"})
        assert result.ok is True, result.error
    assert [r.headers["Authorization"] for r in requests] == ["Bearer t", "Bearer t"]
    assert str(requests[1].url) == "https://api.bitbucket.org/2.0/repositories/acme/svc"


def test_the_jira_form_says_when_the_email_is_needed():
    email = next(
        field
        for field in credential_fields_by_auth("jira")["pat"]
        if field.name == "email"
    )
    assert email.required is False  # an on-premises token has none
    assert email.helper == (
        "Required for Jira Cloud: the account the API token belongs to. Leave "
        "blank only for a Server or Data Center personal access token."
    )


@pytest.mark.parametrize("kind", ["jira", "servicenow"])
def test_a_ticket_opened_for_an_incident_links_back_to_it(kind, monkeypatch):
    incident = types.SimpleNamespace(
        id=uuid.UUID("11111111-1111-4111-8111-111111111111"),
        title="Payments 500s",
        description="503s rising",
    )

    def description():
        _, params = _create_params(kind, incident)
        return (
            params["fields"]["description"]
            if kind == "servicenow"
            else params["description"]
        )

    # No public address configured: no link, rather than one to the local
    # default that is dead inside the provider.
    monkeypatch.delenv("OPSMENDER_PUBLIC_URL", raising=False)
    assert description() == "503s rising"

    monkeypatch.setenv("OPSMENDER_PUBLIC_URL", "https://ops.example.test/")
    backlink = (
        "OpsMender incident: https://ops.example.test/dashboard/incidents/"
        "detail?id=11111111-1111-4111-8111-111111111111"
    )
    # First, so a length limit in the provider cannot cut it off.
    assert description() == f"{backlink}\n\n503s rising"
    incident.description = None
    assert description() == backlink


async def _seed(app, **connector_fields):
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
            kind="jira",
            name=f"Jira sync {uuid.uuid4()}",
            base_url="https://tickets.example.test",
            auth_type="pat",
            auth={"token": "api-token", "webhook_secret": SECRET},
            config={"ticket_sync_enabled": True, "status_map": {}},
            **{"is_enabled": True, **connector_fields},
        )
        for external_id, reference_type in (("OPS-42", "ticket"), ("abc123", "commit")):
            await IncidentIntegrationLinkRepo.upsert(
                db,
                TEST_ORG_ID,
                incident_id=incident.id,
                connector_id=connector.id,
                reference_type=reference_type,
                external_id=external_id,
                url=f"https://tickets.example.test/browse/{external_id}",
                title=external_id,
                reference_meta={},
            )
        await TicketSyncStateRepo.upsert(
            db,
            TEST_ORG_ID,
            connector_id=connector.id,
            incident_id=incident.id,
            external_ticket_id="OPS-42",
            external_ticket_url="https://tickets.example.test/browse/OPS-42",
            status_map={},
        )
        await db.commit()
        return incident.id, connector.id


async def _delivery(client, connector_id, status_name: str) -> dict:
    raw = json.dumps(
        {"issue": {"key": "OPS-42", "fields": {"status": {"name": status_name}}}},
        separators=(",", ":"),
    ).encode()
    signature = "sha256=" + hmac.new(SECRET.encode(), raw, hashlib.sha256).hexdigest()
    response = await client.post(
        f"/webhooks/ticket-sync/{connector_id}",
        content=raw,
        headers={"Content-Type": "application/json", "X-Hub-Signature": signature},
    )
    assert response.status_code == 200, response.text
    return response.json()


async def _links(client, auth_headers, incident_id) -> dict[str, dict]:
    response = await client.get(
        f"/incidents/{incident_id}/integration-links", headers=auth_headers
    )
    assert response.status_code == 200, response.text
    return {item["external_id"]: item for item in response.json()["items"]}


async def _status(app, incident_id) -> str:
    async with app.state.session_factory() as db:
        return (await IncidentRepo.get_by_id(db, TEST_ORG_ID, incident_id)).status


async def test_the_ticket_link_shows_the_status_last_agreed_with_the_provider(
    app, client, auth_headers
):
    incident_id, connector_id = await _seed(app)

    # Before any sync the ticket is linked and nothing is claimed about it.
    before = await _links(client, auth_headers, incident_id)
    assert before["OPS-42"]["ticket_sync"] == {
        "ticket_status": None,
        "direction": None,
        "synced_at": None,
    }
    # A commit is a link, not a synced ticket.
    assert before["abc123"]["ticket_sync"] is None

    await _delivery(client, connector_id, "In Progress")
    moved = (await _links(client, auth_headers, incident_id))["OPS-42"]["ticket_sync"]
    assert moved["ticket_status"] == "In Progress"
    assert moved["direction"] == "inbound"
    assert moved["synced_at"] is not None

    await _delivery(client, connector_id, "Done")
    done = (await _links(client, auth_headers, incident_id))["OPS-42"]["ticket_sync"]
    assert done["ticket_status"] == "Done"

    async with app.state.session_factory() as db:
        state = await TicketSyncStateRepo.get_by_external_ticket(
            db, TEST_ORG_ID, connector_id, "OPS-42"
        )
        assert state.last_synced_status == "resolved"

        # A connector that no longer syncs reports nothing about the ticket.
        connector = await IntegrationConnectorRepo.get_by_id(
            db, TEST_ORG_ID, connector_id
        )
        connector.config = {**connector.config, "ticket_sync_enabled": False}
        await db.commit()
    stopped = await _links(client, auth_headers, incident_id)
    assert stopped["OPS-42"]["ticket_sync"] is None


async def test_an_unchanged_ticket_does_not_resolve_a_reopened_incident(
    app, client, auth_headers
):
    """The webhook fires on every edit of the ticket, not only on a status
    change. A ticket closed in Jira and an incident reopened here: a later
    edit of the still-Done ticket must not resolve the incident again."""
    incident_id, connector_id = await _seed(app)
    closed = await _delivery(client, connector_id, "Done")
    assert closed["status"] == "resolved" and closed["updated"] is True

    reopened = await client.patch(
        f"/incidents/{incident_id}", headers=auth_headers, json={"status": "open"}
    )
    assert reopened.status_code == 200, reopened.text
    assert await _status(app, incident_id) == "open"

    edited = await _delivery(client, connector_id, "Done")
    assert edited == {
        "incident_id": str(incident_id),
        "status": "open",
        "updated": False,
        "source": "ticket_sync",
    }
    assert await _status(app, incident_id) == "open"

    # The ticket really moving is still followed.
    moved = await _delivery(client, connector_id, "In Progress")
    assert moved["status"] == "in_progress" and moved["updated"] is True


async def test_the_echo_of_an_acknowledgement_push_changes_nothing(
    app, client, auth_headers
):
    """An acknowledgement is pushed to the ticket as In Progress, and Jira
    reports that change back. It is not news: the incident stays open."""
    incident_id, connector_id = await _seed(app)
    async with app.state.session_factory() as db:
        state = await TicketSyncStateRepo.get_by_external_ticket(
            db, TEST_ORG_ID, connector_id, "OPS-42"
        )
        # What an outbound acknowledgement push records.
        await TicketSyncStateRepo.mark_synced(
            db, state, direction="outbound", internal_status="acknowledged"
        )
        await db.commit()

    echo = await _delivery(client, connector_id, "In Progress")
    assert echo["updated"] is False
    assert await _status(app, incident_id) == "open"
    link = (await _links(client, auth_headers, incident_id))["OPS-42"]["ticket_sync"]
    assert link["ticket_status"] == "In Progress"
    assert link["direction"] == "outbound"
