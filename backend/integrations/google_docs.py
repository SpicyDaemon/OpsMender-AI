"""Read-only Google Docs and Drive export integration adapter."""

from __future__ import annotations

import base64
from typing import Any
from urllib.parse import quote

from backend.integrations.base import (
    IntegrationAdapter,
    IntegrationCapability,
    IntegrationResult,
)
from backend.integrations.google_auth import service_account_access_token
from backend.integrations.http import (
    HttpClientFactory,
    default_http_client,
    required,
    response_error,
)
from backend.integrations.registry import register_adapter

_SCOPES = (
    "https://www.googleapis.com/auth/documents.readonly",
    "https://www.googleapis.com/auth/drive.readonly",
)


class GoogleDocsAdapter(IntegrationAdapter):
    kind = "google_docs"
    capabilities = (
        IntegrationCapability("test_connection", "Validate Google Drive access."),
        IntegrationCapability("read_doc", "Read a Google document structure."),
        IntegrationCapability("export_doc", "Export a Google document."),
    )

    def __init__(self, *, http_client_factory: HttpClientFactory | None = None):
        self._factory = http_client_factory or default_http_client

    @staticmethod
    async def _token(client, connector, auth: dict[str, Any]) -> str:
        if connector.auth_type == "oauth":
            return required(auth.get("access_token"), "access_token")
        return await service_account_access_token(client, auth, scopes=_SCOPES)

    async def _request(self, connector, auth, method, url, **kwargs):
        async with self._factory() as client:
            token = await self._token(client, connector, auth)
            response = await client.request(
                method,
                url,
                headers={"Authorization": f"Bearer {token}"},
                **kwargs,
            )
        return (
            (None, response_error("Google Docs", response))
            if response.status_code >= 400
            else (response, None)
        )

    async def test_connection(self, connector, auth):
        response, failure = await self._request(
            connector,
            auth,
            "GET",
            "https://www.googleapis.com/drive/v3/about",
            params={"fields": "user"},
        )
        return failure or IntegrationResult.success(
            detail=f"Google Drive credentials accepted ({response.status_code})."
        )

    async def read_doc(self, connector, auth, document_id):
        document_id = quote(required(document_id, "document_id"), safe="")
        response, failure = await self._request(
            connector,
            auth,
            "GET",
            f"https://docs.googleapis.com/v1/documents/{document_id}",
        )
        return failure or IntegrationResult.success(document=response.json())

    async def export_doc(
        self,
        connector,
        auth,
        document_id,
        mime_type="text/plain",
    ):
        document_id = quote(required(document_id, "document_id"), safe="")
        mime_type = required(mime_type, "mime_type")
        response, failure = await self._request(
            connector,
            auth,
            "GET",
            f"https://www.googleapis.com/drive/v3/files/{document_id}/export",
            params={"mimeType": mime_type},
        )
        if failure:
            return failure
        if mime_type.startswith("text/") or mime_type in {
            "application/rtf",
            "application/vnd.oasis.opendocument.text",
        }:
            return IntegrationResult.success(
                export={
                    "mime_type": mime_type,
                    "content": response.content.decode("utf-8", errors="replace"),
                }
            )
        return IntegrationResult.success(
            export={
                "mime_type": mime_type,
                "content_base64": base64.b64encode(response.content).decode(),
            }
        )


register_adapter(GoogleDocsAdapter())
