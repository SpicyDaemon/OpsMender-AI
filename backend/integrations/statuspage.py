"""Statuspage component and incident integration adapter."""

from __future__ import annotations

from typing import Any
from urllib.parse import quote

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


class StatuspageAdapter(IntegrationAdapter):
    kind = "statuspage"
    capabilities = (
        IntegrationCapability("test_connection", "Validate status-page access."),
        IntegrationCapability("list_components", "List page components."),
        IntegrationCapability("list_incidents", "List page incidents."),
        IntegrationCapability("get_incident", "Read an incident."),
        IntegrationCapability(
            "create_incident",
            "Create a public status incident.",
            classification="caution",
            mutating=True,
        ),
    )

    def __init__(self, *, http_client_factory: HttpClientFactory | None = None):
        self._factory = http_client_factory or default_http_client

    @staticmethod
    def _base(connector: IntegrationConnector) -> str:
        return (connector.base_url or "https://api.statuspage.io/v1").rstrip("/")

    @staticmethod
    def _headers(auth: dict[str, Any]) -> dict[str, str]:
        token = required(auth.get("api_key") or auth.get("token"), "api_key")
        return {"Authorization": f"OAuth {token}", "Content-Type": "application/json"}

    @staticmethod
    def _page(connector: IntegrationConnector, page_id=None) -> str:
        return required(page_id or connector.config.get("page_id"), "page_id")

    async def _request(self, connector, auth, method, path, **kwargs):
        async with self._factory() as client:
            response = await client.request(
                method,
                f"{self._base(connector)}{path}",
                headers=self._headers(auth),
                **kwargs,
            )
        return (
            (None, response_error("Statuspage", response))
            if response.status_code >= 400
            else (response, None)
        )

    def _path(self, connector, page_id, suffix):
        return f"/pages/{quote(self._page(connector, page_id), safe='')}{suffix}"

    async def test_connection(self, connector, auth):
        response, failure = await self._request(
            connector, auth, "GET", self._path(connector, None, "")
        )
        return failure or IntegrationResult.success(
            detail=f"Status page credentials accepted ({response.status_code})."
        )

    async def list_components(self, connector, auth, page_id=None):
        response, failure = await self._request(
            connector, auth, "GET", self._path(connector, page_id, "/components")
        )
        return failure or IntegrationResult.success(components=response.json())

    async def list_incidents(self, connector, auth, page_id=None):
        response, failure = await self._request(
            connector, auth, "GET", self._path(connector, page_id, "/incidents")
        )
        return failure or IntegrationResult.success(incidents=response.json())

    async def get_incident(self, connector, auth, incident_id, page_id=None):
        response, failure = await self._request(
            connector,
            auth,
            "GET",
            self._path(
                connector,
                page_id,
                f"/incidents/{quote(required(incident_id, 'incident_id'), safe='')}",
            ),
        )
        return failure or IntegrationResult.success(incident=response.json())

    async def create_incident(
        self,
        connector,
        auth,
        name,
        body,
        page_id=None,
        status="investigating",
        impact_override=None,
        component_ids=None,
        deliver_notifications=True,
    ):
        incident: dict[str, Any] = {
            "name": required(name, "name"),
            "body": required(body, "body"),
            "status": status,
            "deliver_notifications": bool(deliver_notifications),
        }
        if impact_override:
            incident["impact_override"] = impact_override
        if component_ids:
            incident["component_ids"] = component_ids
        response, failure = await self._request(
            connector,
            auth,
            "POST",
            self._path(connector, page_id, "/incidents"),
            json={"incident": incident},
        )
        return failure or IntegrationResult.success(incident=response.json())


register_adapter(StatuspageAdapter())
