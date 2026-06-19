"""Zendesk, Freshservice, and Asana native integration adapters."""

from __future__ import annotations

import base64
from typing import Any

import httpx

from backend.db.models import IntegrationConnector
from backend.integrations.base import (
    IntegrationAdapter,
    IntegrationCapability,
    IntegrationResult,
)
from backend.integrations.http import (
    HttpClientFactory,
    default_http_client,
    required,
    response_error,
)
from backend.integrations.registry import register_adapter


# ─── Zendesk ────────────────────────────────────────────────────────────────


def _zendesk_base(connector: IntegrationConnector) -> str:
    base = (connector.base_url or "").rstrip("/")
    if not base:
        raise ValueError("base_url is required (e.g. https://acme.zendesk.com)")
    return base


def _zendesk_headers(
    connector: IntegrationConnector, auth: dict[str, Any]
) -> dict[str, str]:
    if connector.auth_type == "oauth":
        token = required(auth.get("access_token"), "access_token")
        return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    email = required(auth.get("email"), "email")
    token = required(auth.get("token") or auth.get("api_token"), "token")
    cred = base64.b64encode(f"{email}/token:{token}".encode()).decode()
    return {"Authorization": f"Basic {cred}", "Content-Type": "application/json"}


class ZendeskAdapter(IntegrationAdapter):
    kind = "zendesk"
    capabilities = (
        IntegrationCapability("test_connection", "Validate Zendesk credentials."),
        IntegrationCapability(
            "get_ticket",
            "Read a ticket. Parameters: ticket_id.",
        ),
        IntegrationCapability(
            "list_tickets",
            "List tickets. Parameters: optional status, assignee_id, page.",
        ),
        IntegrationCapability(
            "create_ticket",
            "Create a ticket. Parameters: subject, description, optional priority and type.",
            classification="caution",
            mutating=True,
        ),
        IntegrationCapability(
            "update_ticket",
            "Update a ticket. Parameters: ticket_id, fields dict.",
            classification="caution",
            mutating=True,
        ),
        IntegrationCapability(
            "comment_ticket",
            "Add a comment to a ticket. Parameters: ticket_id, body, optional public bool.",
            classification="caution",
            mutating=True,
        ),
        IntegrationCapability(
            "link_ticket_to_incident",
            "Add an OpsMender incident link comment. Parameters: ticket_id, incident_id, incident_url.",
            classification="caution",
            mutating=True,
        ),
    )

    def __init__(self, *, http_client_factory: HttpClientFactory | None = None):
        self._factory = http_client_factory or default_http_client

    async def test_connection(self, connector, auth):
        base = _zendesk_base(connector)
        async with self._factory() as client:
            r = await client.get(
                f"{base}/api/v2/users/me.json",
                headers=_zendesk_headers(connector, auth),
            )
        if r.status_code >= 400:
            return response_error("Zendesk", r)
        user = r.json().get("user", {})
        return IntegrationResult.success(
            detail=f"Zendesk credentials accepted ({user.get('name', 'user')})."
        )

    async def get_ticket(self, connector, auth, ticket_id):
        base = _zendesk_base(connector)
        tid = required(ticket_id, "ticket_id")
        async with self._factory() as client:
            r = await client.get(
                f"{base}/api/v2/tickets/{tid}.json",
                headers=_zendesk_headers(connector, auth),
            )
        if r.status_code >= 400:
            return response_error("Zendesk", r)
        return IntegrationResult.success(ticket=r.json().get("ticket"))

    async def list_tickets(
        self, connector, auth, status=None, assignee_id=None, page=1
    ):
        base = _zendesk_base(connector)
        params: dict[str, Any] = {"page": page, "per_page": 25}
        if status:
            params["status"] = status
        if assignee_id:
            params["assignee_id"] = assignee_id
        async with self._factory() as client:
            r = await client.get(
                f"{base}/api/v2/tickets.json",
                headers=_zendesk_headers(connector, auth),
                params=params,
            )
        if r.status_code >= 400:
            return response_error("Zendesk", r)
        body = r.json()
        return IntegrationResult.success(
            tickets=body.get("tickets", []),
            count=body.get("count"),
            next_page=body.get("next_page"),
        )

    async def create_ticket(
        self, connector, auth, subject, description, priority=None, type=None
    ):
        base = _zendesk_base(connector)
        ticket: dict[str, Any] = {
            "subject": required(subject, "subject"),
            "comment": {"body": required(description, "description")},
        }
        if priority:
            ticket["priority"] = priority
        if type:
            ticket["type"] = type
        async with self._factory() as client:
            r = await client.post(
                f"{base}/api/v2/tickets.json",
                headers=_zendesk_headers(connector, auth),
                json={"ticket": ticket},
            )
        if r.status_code >= 400:
            return response_error("Zendesk", r)
        return IntegrationResult.success(ticket=r.json().get("ticket"))

    async def update_ticket(self, connector, auth, ticket_id, fields):
        base = _zendesk_base(connector)
        tid = required(ticket_id, "ticket_id")
        if not isinstance(fields, dict) or not fields:
            raise ValueError("fields is required")
        async with self._factory() as client:
            r = await client.put(
                f"{base}/api/v2/tickets/{tid}.json",
                headers=_zendesk_headers(connector, auth),
                json={"ticket": fields},
            )
        if r.status_code >= 400:
            return response_error("Zendesk", r)
        return IntegrationResult.success(ticket=r.json().get("ticket"))

    async def comment_ticket(
        self, connector, auth, ticket_id, body, public=True
    ):
        base = _zendesk_base(connector)
        tid = required(ticket_id, "ticket_id")
        comment_body = required(body, "body")
        async with self._factory() as client:
            r = await client.put(
                f"{base}/api/v2/tickets/{tid}.json",
                headers=_zendesk_headers(connector, auth),
                json={"ticket": {"comment": {"body": comment_body, "public": bool(public)}}},
            )
        if r.status_code >= 400:
            return response_error("Zendesk", r)
        return IntegrationResult.success(ticket=r.json().get("ticket"))

    async def link_ticket_to_incident(
        self, connector, auth, ticket_id, incident_id, incident_url
    ):
        body = (
            f"Linked to OpsMender incident {required(incident_id, 'incident_id')}."
            f"\nView: {required(incident_url, 'incident_url')}"
        )
        return await self.comment_ticket(connector, auth, ticket_id, body, public=False)


register_adapter(ZendeskAdapter())


# ─── Freshservice ────────────────────────────────────────────────────────────


def _freshservice_base(connector: IntegrationConnector) -> str:
    base = (connector.base_url or "").rstrip("/")
    if not base:
        raise ValueError("base_url is required (e.g. https://acme.freshservice.com)")
    return base


def _freshservice_headers(
    connector: IntegrationConnector, auth: dict[str, Any]
) -> dict[str, str]:
    api_key = required(auth.get("api_key") or auth.get("token"), "api_key")
    cred = base64.b64encode(f"{api_key}:X".encode()).decode()
    return {"Authorization": f"Basic {cred}", "Content-Type": "application/json"}


_FS_PRIORITY = {"low": 1, "medium": 2, "high": 3, "urgent": 4}


class FreshserviceAdapter(IntegrationAdapter):
    kind = "freshservice"
    capabilities = (
        IntegrationCapability("test_connection", "Validate Freshservice credentials."),
        IntegrationCapability(
            "get_ticket",
            "Read a ticket. Parameters: ticket_id.",
        ),
        IntegrationCapability(
            "list_tickets",
            "List tickets. Parameters: optional status, priority, page.",
        ),
        IntegrationCapability(
            "create_ticket",
            "Create a ticket. Parameters: subject, description, email, optional priority.",
            classification="caution",
            mutating=True,
        ),
        IntegrationCapability(
            "update_ticket",
            "Update a ticket. Parameters: ticket_id, fields dict.",
            classification="caution",
            mutating=True,
        ),
        IntegrationCapability(
            "comment_ticket",
            "Add a note to a ticket. Parameters: ticket_id, body, optional private bool.",
            classification="caution",
            mutating=True,
        ),
        IntegrationCapability(
            "link_ticket_to_incident",
            "Add an OpsMender link note. Parameters: ticket_id, incident_id, incident_url.",
            classification="caution",
            mutating=True,
        ),
    )

    def __init__(self, *, http_client_factory: HttpClientFactory | None = None):
        self._factory = http_client_factory or default_http_client

    async def test_connection(self, connector, auth):
        base = _freshservice_base(connector)
        async with self._factory() as client:
            r = await client.get(
                f"{base}/api/v2/agents/me",
                headers=_freshservice_headers(connector, auth),
            )
        if r.status_code >= 400:
            return response_error("Freshservice", r)
        agent = r.json().get("agent", {})
        name = agent.get("first_name", "") + " " + agent.get("last_name", "")
        return IntegrationResult.success(
            detail=f"Freshservice credentials accepted ({name.strip() or 'user'})."
        )

    async def get_ticket(self, connector, auth, ticket_id):
        base = _freshservice_base(connector)
        tid = required(ticket_id, "ticket_id")
        async with self._factory() as client:
            r = await client.get(
                f"{base}/api/v2/tickets/{tid}",
                headers=_freshservice_headers(connector, auth),
            )
        if r.status_code >= 400:
            return response_error("Freshservice", r)
        return IntegrationResult.success(ticket=r.json().get("ticket"))

    async def list_tickets(
        self, connector, auth, status=None, priority=None, page=1
    ):
        base = _freshservice_base(connector)
        params: dict[str, Any] = {"page": page, "per_page": 25}
        if status:
            params["status"] = status
        if priority:
            params["priority"] = _FS_PRIORITY.get(str(priority).lower(), priority)
        async with self._factory() as client:
            r = await client.get(
                f"{base}/api/v2/tickets",
                headers=_freshservice_headers(connector, auth),
                params=params,
            )
        if r.status_code >= 400:
            return response_error("Freshservice", r)
        body = r.json()
        return IntegrationResult.success(tickets=body.get("tickets", []))

    async def create_ticket(
        self, connector, auth, subject, description, email, priority=None
    ):
        base = _freshservice_base(connector)
        payload: dict[str, Any] = {
            "subject": required(subject, "subject"),
            "description": required(description, "description"),
            "email": required(email, "email"),
            "status": 2,  # Open
        }
        if priority:
            payload["priority"] = _FS_PRIORITY.get(str(priority).lower(), 2)
        async with self._factory() as client:
            r = await client.post(
                f"{base}/api/v2/tickets",
                headers=_freshservice_headers(connector, auth),
                json=payload,
            )
        if r.status_code >= 400:
            return response_error("Freshservice", r)
        return IntegrationResult.success(ticket=r.json().get("ticket"))

    async def update_ticket(self, connector, auth, ticket_id, fields):
        base = _freshservice_base(connector)
        tid = required(ticket_id, "ticket_id")
        if not isinstance(fields, dict) or not fields:
            raise ValueError("fields is required")
        async with self._factory() as client:
            r = await client.put(
                f"{base}/api/v2/tickets/{tid}",
                headers=_freshservice_headers(connector, auth),
                json=fields,
            )
        if r.status_code >= 400:
            return response_error("Freshservice", r)
        return IntegrationResult.success(ticket=r.json().get("ticket"))

    async def comment_ticket(
        self, connector, auth, ticket_id, body, private=True
    ):
        base = _freshservice_base(connector)
        tid = required(ticket_id, "ticket_id")
        async with self._factory() as client:
            r = await client.post(
                f"{base}/api/v2/tickets/{tid}/notes",
                headers=_freshservice_headers(connector, auth),
                json={"body": required(body, "body"), "private": bool(private)},
            )
        if r.status_code >= 400:
            return response_error("Freshservice", r)
        return IntegrationResult.success(note=r.json().get("note"))

    async def link_ticket_to_incident(
        self, connector, auth, ticket_id, incident_id, incident_url
    ):
        body = (
            f"Linked to OpsMender incident {required(incident_id, 'incident_id')}."
            f"\nView: {required(incident_url, 'incident_url')}"
        )
        return await self.comment_ticket(connector, auth, ticket_id, body, private=True)


register_adapter(FreshserviceAdapter())


# ─── Asana ───────────────────────────────────────────────────────────────────


def _asana_base(connector: IntegrationConnector) -> str:
    return (connector.base_url or "https://app.asana.com/api/1.0").rstrip("/")


def _asana_headers(
    connector: IntegrationConnector, auth: dict[str, Any]
) -> dict[str, str]:
    token = required(
        auth.get("access_token") or auth.get("token") or auth.get("pat"), "token"
    )
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


class AsanaAdapter(IntegrationAdapter):
    kind = "asana"
    capabilities = (
        IntegrationCapability("test_connection", "Validate Asana credentials."),
        IntegrationCapability(
            "get_task",
            "Read a task. Parameters: task_id.",
        ),
        IntegrationCapability(
            "list_tasks",
            "List tasks in a project. Parameters: project_id, optional completed.",
        ),
        IntegrationCapability(
            "create_task",
            "Create a task. Parameters: name, projects list, optional notes and assignee.",
            classification="caution",
            mutating=True,
        ),
        IntegrationCapability(
            "update_task",
            "Update a task. Parameters: task_id, fields dict.",
            classification="caution",
            mutating=True,
        ),
        IntegrationCapability(
            "comment_task",
            "Add a comment to a task. Parameters: task_id, text.",
            classification="caution",
            mutating=True,
        ),
        IntegrationCapability(
            "link_task_to_incident",
            "Add an OpsMender incident link story. Parameters: task_id, incident_id, incident_url.",
            classification="caution",
            mutating=True,
        ),
    )

    def __init__(self, *, http_client_factory: HttpClientFactory | None = None):
        self._factory = http_client_factory or default_http_client

    async def test_connection(self, connector, auth):
        base = _asana_base(connector)
        async with self._factory() as client:
            r = await client.get(
                f"{base}/users/me",
                headers=_asana_headers(connector, auth),
            )
        if r.status_code >= 400:
            return response_error("Asana", r)
        user = r.json().get("data", {})
        return IntegrationResult.success(
            detail=f"Asana credentials accepted ({user.get('name', 'user')})."
        )

    async def get_task(self, connector, auth, task_id):
        base = _asana_base(connector)
        tid = required(task_id, "task_id")
        async with self._factory() as client:
            r = await client.get(
                f"{base}/tasks/{tid}",
                headers=_asana_headers(connector, auth),
                params={"opt_fields": "gid,name,notes,completed,assignee,projects"},
            )
        if r.status_code >= 400:
            return response_error("Asana", r)
        return IntegrationResult.success(task=r.json().get("data"))

    async def list_tasks(
        self, connector, auth, project_id=None, completed=False
    ):
        base = _asana_base(connector)
        pid = required(
            project_id or connector.config.get("project_id"), "project_id"
        )
        params = {
            "project": pid,
            "opt_fields": "gid,name,completed,assignee",
            "completed_since": "now" if not completed else "",
        }
        if completed:
            params.pop("completed_since", None)
        async with self._factory() as client:
            r = await client.get(
                f"{base}/tasks",
                headers=_asana_headers(connector, auth),
                params=params,
            )
        if r.status_code >= 400:
            return response_error("Asana", r)
        return IntegrationResult.success(tasks=r.json().get("data", []))

    async def create_task(
        self, connector, auth, name, projects=None, notes=None, assignee=None
    ):
        base = _asana_base(connector)
        task: dict[str, Any] = {"name": required(name, "name")}
        resolved_projects = projects or (
            [connector.config["project_id"]] if connector.config.get("project_id") else None
        )
        if resolved_projects:
            task["projects"] = resolved_projects
        if notes is not None:
            task["notes"] = notes
        if assignee:
            task["assignee"] = assignee
        async with self._factory() as client:
            r = await client.post(
                f"{base}/tasks",
                headers=_asana_headers(connector, auth),
                json={"data": task},
            )
        if r.status_code >= 400:
            return response_error("Asana", r)
        return IntegrationResult.success(task=r.json().get("data"))

    async def update_task(self, connector, auth, task_id, fields):
        base = _asana_base(connector)
        tid = required(task_id, "task_id")
        if not isinstance(fields, dict) or not fields:
            raise ValueError("fields is required")
        async with self._factory() as client:
            r = await client.put(
                f"{base}/tasks/{tid}",
                headers=_asana_headers(connector, auth),
                json={"data": fields},
            )
        if r.status_code >= 400:
            return response_error("Asana", r)
        return IntegrationResult.success(task=r.json().get("data"))

    async def comment_task(self, connector, auth, task_id, text):
        base = _asana_base(connector)
        tid = required(task_id, "task_id")
        async with self._factory() as client:
            r = await client.post(
                f"{base}/tasks/{tid}/stories",
                headers=_asana_headers(connector, auth),
                json={"data": {"text": required(text, "text")}},
            )
        if r.status_code >= 400:
            return response_error("Asana", r)
        return IntegrationResult.success(story=r.json().get("data"))

    async def link_task_to_incident(
        self, connector, auth, task_id, incident_id, incident_url
    ):
        text = (
            f"Linked to OpsMender incident {required(incident_id, 'incident_id')}."
            f" View: {required(incident_url, 'incident_url')}"
        )
        return await self.comment_task(connector, auth, task_id, text)


register_adapter(AsanaAdapter())
