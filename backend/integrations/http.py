"""Small HTTP helpers shared by native integration adapters."""

from __future__ import annotations

from typing import Any, Callable

import httpx

from backend.integrations.base import IntegrationResult

HttpClientFactory = Callable[[], httpx.AsyncClient]


def default_http_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=20, follow_redirects=True)


def required(value: Any, name: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{name} is required")
    return text


def response_error(provider: str, response: httpx.Response) -> IntegrationResult:
    detail = None
    try:
        body = response.json()
        if isinstance(body, dict):
            detail = (
                body.get("message")
                or body.get("error")
                or body.get("error_description")
                or body.get("errorMessages")
            )
    except ValueError:
        pass
    if isinstance(detail, list):
        detail = "; ".join(str(item) for item in detail)
    return IntegrationResult.failure(
        f"{provider} HTTP {response.status_code}" + (f": {detail}" if detail else "")
    )
