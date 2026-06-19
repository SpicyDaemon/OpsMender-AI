"""Reference HTTP integration adapter used by the shared foundation."""

from __future__ import annotations

from typing import Any, Callable

import httpx

from backend.db.models import IntegrationConnector
from backend.integrations.base import (
    IntegrationAdapter,
    IntegrationCapability,
    IntegrationResult,
)
from backend.integrations.registry import register_adapter


class GenericHTTPAdapter(IntegrationAdapter):
    kind = "custom"
    capabilities = (
        IntegrationCapability(
            action="test_connection",
            description="Probe the configured HTTP endpoint.",
            classification="safe",
        ),
    )

    def __init__(
        self,
        *,
        http_client_factory: Callable[[], httpx.AsyncClient] | None = None,
    ) -> None:
        self._http_client_factory = http_client_factory or (
            lambda: httpx.AsyncClient(timeout=10, follow_redirects=True)
        )

    @staticmethod
    def _headers(
        connector: IntegrationConnector,
        auth: dict[str, Any],
    ) -> dict[str, str]:
        headers = {
            str(key): str(value)
            for key, value in (connector.config.get("headers") or {}).items()
        }
        if connector.auth_type in {"pat", "oauth"}:
            token = auth.get("token") or auth.get("access_token")
            if token:
                headers["Authorization"] = f"Bearer {token}"
        elif connector.auth_type == "api_key":
            token = auth.get("api_key") or auth.get("token")
            if token:
                header = str(auth.get("header") or "X-API-Key")
                headers[header] = str(token)
        return headers

    async def test_connection(
        self,
        connector: IntegrationConnector,
        auth: dict[str, Any],
    ) -> IntegrationResult:
        if not connector.base_url:
            return IntegrationResult.failure("Base URL is required")
        path = str(connector.config.get("health_path") or "")
        url = connector.base_url.rstrip("/") + (
            f"/{path.lstrip('/')}" if path else ""
        )
        request_auth = None
        if connector.auth_type == "basic":
            request_auth = httpx.BasicAuth(
                str(auth.get("username") or ""),
                str(auth.get("password") or ""),
            )
        async with self._http_client_factory() as client:
            response = await client.get(
                url,
                headers=self._headers(connector, auth),
                auth=request_auth,
            )
        if response.status_code >= 400:
            return IntegrationResult.failure(
                f"HTTP {response.status_code} from {url}"
            )
        return IntegrationResult.success(
            detail=f"HTTP {response.status_code}",
            status_code=response.status_code,
        )


register_adapter(GenericHTTPAdapter())
