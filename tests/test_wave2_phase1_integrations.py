"""Wave 2 Phase 1 — Zendesk, Freshservice, and Asana adapter tests."""

from __future__ import annotations

import base64
import json
import uuid

import httpx
import pytest

from backend.db.models import IntegrationConnector
from backend.integrations.support import AsanaAdapter, FreshserviceAdapter, ZendeskAdapter


def _connector(
    kind: str,
    *,
    auth_type: str,
    base_url: str | None = None,
    config: dict | None = None,
) -> IntegrationConnector:
    return IntegrationConnector(
        id=uuid.uuid4(),
        org_id=uuid.uuid4(),
        kind=kind,
        name=f"{kind}-test",
        base_url=base_url,
        auth_type=auth_type,
        config=config or {},
        is_enabled=True,
    )


# ─── Zendesk ─────────────────────────────────────────────────────────────────


async def test_zendesk_test_connection_pat():
    seen: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        cred = base64.b64encode(b"agent@acme.com/token:zd-token").decode()
        assert request.headers["authorization"] == f"Basic {cred}"
        assert request.url.path == "/api/v2/users/me.json"
        return httpx.Response(200, json={"user": {"id": 1, "name": "Agent"}})

    transport = httpx.MockTransport(handler)

    def factory():
        return httpx.AsyncClient(transport=transport, timeout=5)

    connector = _connector("zendesk", auth_type="basic", base_url="https://acme.zendesk.com")
    auth = {"email": "agent@acme.com", "token": "zd-token"}
    result = await ZendeskAdapter(http_client_factory=factory).test_connection(connector, auth)
    assert result.ok
    assert "Agent" in result.data["detail"]
    assert len(seen) == 1


async def test_zendesk_test_connection_oauth():
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"] == "Bearer oauth-tok"
        return httpx.Response(200, json={"user": {"name": "OAuth User"}})

    transport = httpx.MockTransport(handler)

    def factory():
        return httpx.AsyncClient(transport=transport, timeout=5)

    connector = _connector("zendesk", auth_type="oauth", base_url="https://acme.zendesk.com")
    auth = {"access_token": "oauth-tok"}
    result = await ZendeskAdapter(http_client_factory=factory).test_connection(connector, auth)
    assert result.ok


async def test_zendesk_get_ticket():
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v2/tickets/42.json"
        return httpx.Response(200, json={"ticket": {"id": 42, "subject": "DB down"}})

    transport = httpx.MockTransport(handler)

    def factory():
        return httpx.AsyncClient(transport=transport, timeout=5)

    connector = _connector("zendesk", auth_type="basic", base_url="https://acme.zendesk.com")
    auth = {"email": "a@b.com", "token": "tok"}
    result = await ZendeskAdapter(http_client_factory=factory).get_ticket(connector, auth, ticket_id=42)
    assert result.ok
    assert result.data["ticket"]["id"] == 42


async def test_zendesk_list_tickets():
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v2/tickets.json"
        assert request.url.params["status"] == "open"
        return httpx.Response(200, json={"tickets": [{"id": 1}], "count": 1})

    transport = httpx.MockTransport(handler)

    def factory():
        return httpx.AsyncClient(transport=transport, timeout=5)

    connector = _connector("zendesk", auth_type="basic", base_url="https://acme.zendesk.com")
    auth = {"email": "a@b.com", "token": "tok"}
    result = await ZendeskAdapter(http_client_factory=factory).list_tickets(
        connector, auth, status="open"
    )
    assert result.ok
    assert result.data["tickets"][0]["id"] == 1


async def test_zendesk_create_ticket():
    seen: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        assert request.method == "POST"
        body = json.loads(request.content)
        assert body["ticket"]["subject"] == "Outage"
        return httpx.Response(201, json={"ticket": {"id": 99, "subject": "Outage"}})

    transport = httpx.MockTransport(handler)

    def factory():
        return httpx.AsyncClient(transport=transport, timeout=5)

    connector = _connector("zendesk", auth_type="basic", base_url="https://acme.zendesk.com")
    auth = {"email": "a@b.com", "token": "tok"}
    result = await ZendeskAdapter(http_client_factory=factory).create_ticket(
        connector, auth, subject="Outage", description="DB is down", priority="urgent"
    )
    assert result.ok
    assert result.data["ticket"]["id"] == 99


async def test_zendesk_update_ticket():
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "PUT"
        assert "/tickets/10.json" in request.url.path
        body = json.loads(request.content)
        assert body["ticket"]["status"] == "solved"
        return httpx.Response(200, json={"ticket": {"id": 10, "status": "solved"}})

    transport = httpx.MockTransport(handler)

    def factory():
        return httpx.AsyncClient(transport=transport, timeout=5)

    connector = _connector("zendesk", auth_type="basic", base_url="https://acme.zendesk.com")
    auth = {"email": "a@b.com", "token": "tok"}
    result = await ZendeskAdapter(http_client_factory=factory).update_ticket(
        connector, auth, ticket_id=10, fields={"status": "solved"}
    )
    assert result.ok


async def test_zendesk_comment_ticket():
    async def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert body["ticket"]["comment"]["body"] == "Noted"
        assert body["ticket"]["comment"]["public"] is True
        return httpx.Response(200, json={"ticket": {"id": 5}})

    transport = httpx.MockTransport(handler)

    def factory():
        return httpx.AsyncClient(transport=transport, timeout=5)

    connector = _connector("zendesk", auth_type="basic", base_url="https://acme.zendesk.com")
    auth = {"email": "a@b.com", "token": "tok"}
    result = await ZendeskAdapter(http_client_factory=factory).comment_ticket(
        connector, auth, ticket_id=5, body="Noted", public=True
    )
    assert result.ok


async def test_zendesk_link_ticket_to_incident():
    seen: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        body = json.loads(request.content)
        comment = body["ticket"]["comment"]["body"]
        assert "INC-123" in comment
        assert "https://ops.example.com/incidents/123" in comment
        assert body["ticket"]["comment"]["public"] is False
        return httpx.Response(200, json={"ticket": {"id": 7}})

    transport = httpx.MockTransport(handler)

    def factory():
        return httpx.AsyncClient(transport=transport, timeout=5)

    connector = _connector("zendesk", auth_type="basic", base_url="https://acme.zendesk.com")
    auth = {"email": "a@b.com", "token": "tok"}
    result = await ZendeskAdapter(http_client_factory=factory).link_ticket_to_incident(
        connector, auth,
        ticket_id=7,
        incident_id="INC-123",
        incident_url="https://ops.example.com/incidents/123",
    )
    assert result.ok


async def test_zendesk_http_error_surfaces():
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": "Couldn't authenticate you"})

    transport = httpx.MockTransport(handler)

    def factory():
        return httpx.AsyncClient(transport=transport, timeout=5)

    connector = _connector("zendesk", auth_type="basic", base_url="https://acme.zendesk.com")
    auth = {"email": "a@b.com", "token": "bad"}
    result = await ZendeskAdapter(http_client_factory=factory).test_connection(connector, auth)
    assert not result.ok
    assert "401" in result.error


async def test_zendesk_missing_base_url_raises():
    connector = _connector("zendesk", auth_type="basic", base_url=None)
    auth = {"email": "a@b.com", "token": "tok"}
    result = await ZendeskAdapter().safe_invoke(
        "test_connection", connector, auth
    )
    assert not result.ok
    assert "base_url" in result.error


# ─── Freshservice ─────────────────────────────────────────────────────────────


async def test_freshservice_test_connection():
    async def handler(request: httpx.Request) -> httpx.Response:
        cred = base64.b64encode(b"fs-key:X").decode()
        assert request.headers["authorization"] == f"Basic {cred}"
        assert "/api/v2/agents/me" in request.url.path
        return httpx.Response(200, json={"agent": {"first_name": "Jane", "last_name": "Ops"}})

    transport = httpx.MockTransport(handler)

    def factory():
        return httpx.AsyncClient(transport=transport, timeout=5)

    connector = _connector("freshservice", auth_type="api_key", base_url="https://acme.freshservice.com")
    auth = {"api_key": "fs-key"}
    result = await FreshserviceAdapter(http_client_factory=factory).test_connection(connector, auth)
    assert result.ok
    assert "Jane" in result.data["detail"]


async def test_freshservice_get_ticket():
    async def handler(request: httpx.Request) -> httpx.Response:
        assert "/api/v2/tickets/55" in request.url.path
        return httpx.Response(200, json={"ticket": {"id": 55, "subject": "Server slow"}})

    transport = httpx.MockTransport(handler)

    def factory():
        return httpx.AsyncClient(transport=transport, timeout=5)

    connector = _connector("freshservice", auth_type="api_key", base_url="https://acme.freshservice.com")
    auth = {"api_key": "fs-key"}
    result = await FreshserviceAdapter(http_client_factory=factory).get_ticket(connector, auth, ticket_id=55)
    assert result.ok
    assert result.data["ticket"]["id"] == 55


async def test_freshservice_list_tickets():
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params.get("priority") == "3"
        return httpx.Response(200, json={"tickets": [{"id": 1}, {"id": 2}]})

    transport = httpx.MockTransport(handler)

    def factory():
        return httpx.AsyncClient(transport=transport, timeout=5)

    connector = _connector("freshservice", auth_type="api_key", base_url="https://acme.freshservice.com")
    auth = {"api_key": "fs-key"}
    result = await FreshserviceAdapter(http_client_factory=factory).list_tickets(
        connector, auth, priority="high"
    )
    assert result.ok
    assert len(result.data["tickets"]) == 2


async def test_freshservice_create_ticket():
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        body = json.loads(request.content)
        assert body["subject"] == "DB down"
        assert body["email"] == "reporter@acme.com"
        return httpx.Response(201, json={"ticket": {"id": 77, "subject": "DB down"}})

    transport = httpx.MockTransport(handler)

    def factory():
        return httpx.AsyncClient(transport=transport, timeout=5)

    connector = _connector("freshservice", auth_type="api_key", base_url="https://acme.freshservice.com")
    auth = {"api_key": "fs-key"}
    result = await FreshserviceAdapter(http_client_factory=factory).create_ticket(
        connector, auth,
        subject="DB down",
        description="Production DB is unresponsive",
        email="reporter@acme.com",
        priority="urgent",
    )
    assert result.ok
    assert result.data["ticket"]["id"] == 77


async def test_freshservice_update_ticket():
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "PUT"
        body = json.loads(request.content)
        assert body["status"] == 4
        return httpx.Response(200, json={"ticket": {"id": 10, "status": 4}})

    transport = httpx.MockTransport(handler)

    def factory():
        return httpx.AsyncClient(transport=transport, timeout=5)

    connector = _connector("freshservice", auth_type="api_key", base_url="https://acme.freshservice.com")
    auth = {"api_key": "fs-key"}
    result = await FreshserviceAdapter(http_client_factory=factory).update_ticket(
        connector, auth, ticket_id=10, fields={"status": 4}
    )
    assert result.ok


async def test_freshservice_comment_ticket():
    async def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert body["body"] == "Root cause identified"
        assert body["private"] is True
        return httpx.Response(201, json={"note": {"id": 3}})

    transport = httpx.MockTransport(handler)

    def factory():
        return httpx.AsyncClient(transport=transport, timeout=5)

    connector = _connector("freshservice", auth_type="api_key", base_url="https://acme.freshservice.com")
    auth = {"api_key": "fs-key"}
    result = await FreshserviceAdapter(http_client_factory=factory).comment_ticket(
        connector, auth, ticket_id=10, body="Root cause identified"
    )
    assert result.ok
    assert result.data["note"]["id"] == 3


async def test_freshservice_link_ticket_to_incident():
    async def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert "INC-456" in body["body"]
        assert "https://ops.example.com/i/456" in body["body"]
        return httpx.Response(201, json={"note": {"id": 8}})

    transport = httpx.MockTransport(handler)

    def factory():
        return httpx.AsyncClient(transport=transport, timeout=5)

    connector = _connector("freshservice", auth_type="api_key", base_url="https://acme.freshservice.com")
    auth = {"api_key": "fs-key"}
    result = await FreshserviceAdapter(http_client_factory=factory).link_ticket_to_incident(
        connector, auth,
        ticket_id=10,
        incident_id="INC-456",
        incident_url="https://ops.example.com/i/456",
    )
    assert result.ok


async def test_freshservice_http_error():
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"description": "Access denied"})

    transport = httpx.MockTransport(handler)

    def factory():
        return httpx.AsyncClient(transport=transport, timeout=5)

    connector = _connector("freshservice", auth_type="api_key", base_url="https://acme.freshservice.com")
    auth = {"api_key": "bad"}
    result = await FreshserviceAdapter(http_client_factory=factory).test_connection(connector, auth)
    assert not result.ok
    assert "403" in result.error


# ─── Asana ────────────────────────────────────────────────────────────────────


async def test_asana_test_connection():
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"] == "Bearer asana-pat"
        assert request.url.path == "/api/1.0/users/me"
        return httpx.Response(200, json={"data": {"gid": "111", "name": "Dev User"}})

    transport = httpx.MockTransport(handler)

    def factory():
        return httpx.AsyncClient(transport=transport, timeout=5)

    connector = _connector("asana", auth_type="pat")
    auth = {"token": "asana-pat"}
    result = await AsanaAdapter(http_client_factory=factory).test_connection(connector, auth)
    assert result.ok
    assert "Dev User" in result.data["detail"]


async def test_asana_test_connection_oauth():
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"] == "Bearer oauth-tok"
        return httpx.Response(200, json={"data": {"name": "OAuth Dev"}})

    transport = httpx.MockTransport(handler)

    def factory():
        return httpx.AsyncClient(transport=transport, timeout=5)

    connector = _connector("asana", auth_type="oauth")
    auth = {"access_token": "oauth-tok"}
    result = await AsanaAdapter(http_client_factory=factory).test_connection(connector, auth)
    assert result.ok


async def test_asana_get_task():
    async def handler(request: httpx.Request) -> httpx.Response:
        assert "/api/1.0/tasks/123" in request.url.path
        return httpx.Response(200, json={"data": {"gid": "123", "name": "Fix login bug"}})

    transport = httpx.MockTransport(handler)

    def factory():
        return httpx.AsyncClient(transport=transport, timeout=5)

    connector = _connector("asana", auth_type="pat")
    auth = {"token": "tok"}
    result = await AsanaAdapter(http_client_factory=factory).get_task(connector, auth, task_id="123")
    assert result.ok
    assert result.data["task"]["name"] == "Fix login bug"


async def test_asana_list_tasks():
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["project"] == "proj-99"
        return httpx.Response(200, json={"data": [{"gid": "1"}, {"gid": "2"}]})

    transport = httpx.MockTransport(handler)

    def factory():
        return httpx.AsyncClient(transport=transport, timeout=5)

    connector = _connector("asana", auth_type="pat")
    auth = {"token": "tok"}
    result = await AsanaAdapter(http_client_factory=factory).list_tasks(
        connector, auth, project_id="proj-99"
    )
    assert result.ok
    assert len(result.data["tasks"]) == 2


async def test_asana_list_tasks_uses_connector_config():
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["project"] == "default-proj"
        return httpx.Response(200, json={"data": []})

    transport = httpx.MockTransport(handler)

    def factory():
        return httpx.AsyncClient(transport=transport, timeout=5)

    connector = _connector("asana", auth_type="pat", config={"project_id": "default-proj"})
    auth = {"token": "tok"}
    result = await AsanaAdapter(http_client_factory=factory).list_tasks(connector, auth)
    assert result.ok


async def test_asana_create_task():
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        body = json.loads(request.content)
        assert body["data"]["name"] == "Investigate outage"
        assert "proj-1" in body["data"]["projects"]
        return httpx.Response(201, json={"data": {"gid": "999", "name": "Investigate outage"}})

    transport = httpx.MockTransport(handler)

    def factory():
        return httpx.AsyncClient(transport=transport, timeout=5)

    connector = _connector("asana", auth_type="pat")
    auth = {"token": "tok"}
    result = await AsanaAdapter(http_client_factory=factory).create_task(
        connector, auth, name="Investigate outage", projects=["proj-1"], notes="See incident"
    )
    assert result.ok
    assert result.data["task"]["gid"] == "999"


async def test_asana_update_task():
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "PUT"
        body = json.loads(request.content)
        assert body["data"]["completed"] is True
        return httpx.Response(200, json={"data": {"gid": "123", "completed": True}})

    transport = httpx.MockTransport(handler)

    def factory():
        return httpx.AsyncClient(transport=transport, timeout=5)

    connector = _connector("asana", auth_type="pat")
    auth = {"token": "tok"}
    result = await AsanaAdapter(http_client_factory=factory).update_task(
        connector, auth, task_id="123", fields={"completed": True}
    )
    assert result.ok


async def test_asana_comment_task():
    async def handler(request: httpx.Request) -> httpx.Response:
        assert "/tasks/123/stories" in request.url.path
        body = json.loads(request.content)
        assert body["data"]["text"] == "Checked logs"
        return httpx.Response(201, json={"data": {"gid": "s1", "text": "Checked logs"}})

    transport = httpx.MockTransport(handler)

    def factory():
        return httpx.AsyncClient(transport=transport, timeout=5)

    connector = _connector("asana", auth_type="pat")
    auth = {"token": "tok"}
    result = await AsanaAdapter(http_client_factory=factory).comment_task(
        connector, auth, task_id="123", text="Checked logs"
    )
    assert result.ok


async def test_asana_link_task_to_incident():
    async def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert "INC-789" in body["data"]["text"]
        assert "https://ops.example.com/i/789" in body["data"]["text"]
        return httpx.Response(201, json={"data": {"gid": "s2"}})

    transport = httpx.MockTransport(handler)

    def factory():
        return httpx.AsyncClient(transport=transport, timeout=5)

    connector = _connector("asana", auth_type="pat")
    auth = {"token": "tok"}
    result = await AsanaAdapter(http_client_factory=factory).link_task_to_incident(
        connector, auth,
        task_id="777",
        incident_id="INC-789",
        incident_url="https://ops.example.com/i/789",
    )
    assert result.ok


async def test_asana_http_error():
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"errors": [{"message": "Not authorized"}]})

    transport = httpx.MockTransport(handler)

    def factory():
        return httpx.AsyncClient(transport=transport, timeout=5)

    connector = _connector("asana", auth_type="pat")
    auth = {"token": "bad"}
    result = await AsanaAdapter(http_client_factory=factory).test_connection(connector, auth)
    assert not result.ok
    assert "401" in result.error


async def test_asana_missing_token_raises():
    connector = _connector("asana", auth_type="pat")
    auth = {}
    result = await AsanaAdapter().safe_invoke("test_connection", connector, auth)
    assert not result.ok
    assert "token" in result.error.lower()


# ─── Registry smoke test ──────────────────────────────────────────────────────


def test_wave2_phase1_kinds_registered():
    import backend.integrations  # noqa: F401 - ensure adapters are loaded
    from backend.integrations.registry import get_adapter, get_kind, list_kinds

    for kind in ("zendesk", "freshservice", "asana"):
        assert get_kind(kind) is not None, f"Kind '{kind}' missing from registry"
        assert get_adapter(kind) is not None, f"Adapter for '{kind}' not registered"

    labels = {k.label for k in list_kinds()}
    assert "Zendesk" in labels
    assert "Freshservice" in labels
    assert "Asana" in labels
