"""ServiceNow Table API connector."""

from __future__ import annotations

import base64
from typing import Any
from urllib.parse import quote

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


class ServiceNowAdapter(IntegrationAdapter):
    kind = "servicenow"
    capabilities = (
        IntegrationCapability("test_connection", "Validate ServiceNow access."),
        IntegrationCapability("get_record", "Read a table record."),
        IntegrationCapability(
            "create_record",
            "Create a table record.",
            classification="caution",
            mutating=True,
        ),
        IntegrationCapability(
            "update_record",
            "Update a table record.",
            classification="caution",
            mutating=True,
        ),
    )

    def __init__(self, *, http_client_factory: HttpClientFactory | None = None):
        self._factory = http_client_factory or default_http_client

    @staticmethod
    def _base(connector: IntegrationConnector) -> str:
        root = required(connector.base_url, "base_url").rstrip("/")
        return root if root.endswith("/api/now/table") else root + "/api/now/table"

    @staticmethod
    def _headers(
        connector: IntegrationConnector,
        auth: dict[str, Any],
    ) -> dict[str, str]:
        if connector.auth_type == "oauth":
            token = required(auth.get("access_token"), "access_token")
            authorization = f"Bearer {token}"
        else:
            username = required(auth.get("username"), "username")
            password = required(auth.get("password"), "password")
            encoded = base64.b64encode(f"{username}:{password}".encode()).decode()
            authorization = f"Basic {encoded}"
        return {
            "Accept": "application/json",
            "Authorization": authorization,
            "Content-Type": "application/json",
        }

    @staticmethod
    def _table(connector: IntegrationConnector, table: str | None) -> str:
        return required(table or connector.config.get("table") or "incident", "table")

    async def _request(self, connector, auth, method, path, **kwargs):
        async with self._factory() as client:
            response = await client.request(
                method,
                f"{self._base(connector)}{path}",
                headers=self._headers(connector, auth),
                **kwargs,
            )
        return (
            (None, response_error("ServiceNow", response))
            if response.status_code >= 400
            else (response, None)
        )

    async def test_connection(self, connector, auth):
        table = self._table(connector, None)
        response, failure = await self._request(
            connector,
            auth,
            "GET",
            f"/{quote(table)}",
            params={"sysparm_limit": 1, "sysparm_fields": "sys_id"},
        )
        return failure or IntegrationResult.success(
            detail=f"ServiceNow connection accepted ({response.status_code})."
        )

    async def get_record(self, connector, auth, sys_id, table=None):
        table = self._table(connector, table)
        response, failure = await self._request(
            connector,
            auth,
            "GET",
            f"/{quote(table)}/{quote(required(sys_id, 'sys_id'))}",
        )
        return failure or IntegrationResult.success(
            record=response.json().get("result", {})
        )

    async def create_record(
        self,
        connector,
        auth,
        fields,
        table=None,
        incident_id=None,
    ):
        if not isinstance(fields, dict) or not fields:
            raise ValueError("fields is required")
        table = self._table(connector, table)
        response, failure = await self._request(
            connector,
            auth,
            "POST",
            f"/{quote(table)}",
            json=fields,
        )
        if failure:
            return failure
        record = response.json().get("result", {})
        data: dict[str, Any] = {"record": record}
        ticket_id = record.get("sys_id")
        if incident_id and ticket_id:
            root = required(connector.base_url, "base_url").rstrip("/")
            target = f"{table}.do?sys_id={ticket_id}"
            url = f"{root}/nav_to.do?uri={quote(target, safe='')}"
            data["integration_link"] = {
                "incident_id": incident_id,
                "reference_type": "ticket",
                "external_id": str(ticket_id),
                "url": url,
                "title": record.get("number"),
            }
            data["ticket_sync"] = {
                "incident_id": incident_id,
                "external_ticket_id": str(ticket_id),
                "external_ticket_url": url,
            }
        return IntegrationResult.success(**data)

    async def update_record(self, connector, auth, sys_id, fields, table=None):
        if not isinstance(fields, dict) or not fields:
            raise ValueError("fields is required")
        table = self._table(connector, table)
        response, failure = await self._request(
            connector,
            auth,
            "PATCH",
            f"/{quote(table)}/{quote(required(sys_id, 'sys_id'))}",
            json=fields,
        )
        return failure or IntegrationResult.success(
            record=response.json().get("result", {})
        )

    async def sync_status_out(
        self,
        connector,
        auth,
        ticket_id,
        new_status,
    ):
        return await self.update_record(
            connector,
            auth,
            sys_id=ticket_id,
            fields={"state": new_status},
        )


register_adapter(ServiceNowAdapter())
