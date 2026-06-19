"""Notion page and document adapter."""

from __future__ import annotations

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


class NotionAdapter(IntegrationAdapter):
    kind = "notion"
    capabilities = (
        IntegrationCapability("test_connection", "Validate Notion access."),
        IntegrationCapability("read_doc", "Read a page as markdown."),
        IntegrationCapability(
            "create_doc",
            "Create a page.",
            classification="caution",
            mutating=True,
        ),
        IntegrationCapability(
            "append_doc",
            "Append content blocks to a page.",
            classification="caution",
            mutating=True,
        ),
    )

    def __init__(self, *, http_client_factory: HttpClientFactory | None = None):
        self._factory = http_client_factory or default_http_client

    @staticmethod
    def _base(connector: IntegrationConnector) -> str:
        return (connector.base_url or "https://api.notion.com/v1").rstrip("/")

    @staticmethod
    def _headers(
        connector: IntegrationConnector,
        auth: dict[str, Any],
    ) -> dict[str, str]:
        token = required(auth.get("access_token") or auth.get("api_key"), "api_key")
        return {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Notion-Version": str(
                connector.config.get("notion_version") or "2026-03-11"
            ),
        }

    async def _request(self, connector, auth, method, path, **kwargs):
        async with self._factory() as client:
            response = await client.request(
                method,
                f"{self._base(connector)}{path}",
                headers=self._headers(connector, auth),
                **kwargs,
            )
        return (
            (None, response_error("Notion", response))
            if response.status_code >= 400
            else (response, None)
        )

    async def test_connection(self, connector, auth):
        response, failure = await self._request(connector, auth, "GET", "/users/me")
        return failure or IntegrationResult.success(
            detail=f"Notion credentials accepted ({response.status_code})."
        )

    async def read_doc(self, connector, auth, page_id):
        response, failure = await self._request(
            connector,
            auth,
            "GET",
            f"/pages/{quote(required(page_id, 'page_id'))}/markdown",
        )
        return failure or IntegrationResult.success(document=response.json())

    async def create_doc(
        self,
        connector,
        auth,
        title,
        parent_page_id=None,
        data_source_id=None,
        markdown=None,
        properties=None,
    ):
        parent_page_id = parent_page_id or connector.config.get("parent_page_id")
        data_source_id = data_source_id or connector.config.get("data_source_id")
        if data_source_id:
            parent = {"type": "data_source_id", "data_source_id": data_source_id}
        else:
            parent = {
                "type": "page_id",
                "page_id": required(parent_page_id, "parent_page_id"),
            }
        payload: dict[str, Any] = {
            "parent": parent,
            "properties": properties
            or {
                "title": {
                    "type": "title",
                    "title": [
                        {
                            "type": "text",
                            "text": {"content": required(title, "title")},
                        }
                    ],
                }
            },
        }
        if markdown:
            payload["markdown"] = markdown
        response, failure = await self._request(
            connector, auth, "POST", "/pages", json=payload
        )
        return failure or IntegrationResult.success(page=response.json())

    async def append_doc(
        self,
        connector,
        auth,
        page_id,
        markdown=None,
        children=None,
    ):
        if children is None:
            text = required(markdown, "markdown")
            children = [
                {
                    "object": "block",
                    "type": "paragraph",
                    "paragraph": {
                        "rich_text": [
                            {
                                "type": "text",
                                "text": {"content": text},
                            }
                        ]
                    },
                }
            ]
        response, failure = await self._request(
            connector,
            auth,
            "PATCH",
            f"/blocks/{quote(required(page_id, 'page_id'))}/children",
            json={"children": children},
        )
        return failure or IntegrationResult.success(blocks=response.json())


register_adapter(NotionAdapter())
