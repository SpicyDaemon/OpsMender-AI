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


# A provider's own words in an error are kept short: they go to the
# operator, the model and the audit log, and the provider chooses them.
_ERROR_DETAIL_LIMIT = 300


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
            errors = body.get("errors")
            if not detail and isinstance(errors, list):
                # The shape of Confluence's v2 API and of several others:
                # a list of objects with a title, a detail or a message.
                detail = [
                    item.get("title") or item.get("detail") or item.get("message")
                    for item in errors[:3]
                    if isinstance(item, dict)
                ]
                detail = [item for item in detail if item]
    except ValueError:
        pass
    if isinstance(detail, list):
        detail = "; ".join(str(item) for item in detail)
    if detail:
        detail = " ".join(str(detail).split())
        if len(detail) > _ERROR_DETAIL_LIMIT:
            detail = detail[:_ERROR_DETAIL_LIMIT].rstrip() + "..."
    return IntegrationResult.failure(
        f"{provider} HTTP {response.status_code}" + (f": {detail}" if detail else "")
    )
