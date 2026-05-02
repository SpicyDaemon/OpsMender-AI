"""signal-cli-rest-api outbound client.

Targets the open-source ``signal-cli-rest-api`` bridge
(https://github.com/bbernhard/signal-cli-rest-api), which exposes a
simple HTTP wrapper around ``signal-cli`` that AIM operators can run as
a sidecar container. The bridge accepts:

    POST {service_url}/v2/send
    {
      "number": "<bot service number>",
      "recipients": ["<phone or group-id>"],
      "message": "<text>"
    }

The bridge does not accept inline reply payloads, so ``SignalAdapter``
returns ``None`` from ``inline_reply`` and the dispatcher schedules a
``send_message`` call instead.
"""

from __future__ import annotations

import logging

import httpx

log = logging.getLogger(__name__)


async def send_message(
    *,
    service_url: str,
    bot_number: str,
    chat_id: str,
    text: str,
    timeout_seconds: float = 10.0,
) -> tuple[bool, str | None]:
    """Send a single Signal message via signal-cli-rest-api.

    Returns ``(ok, error_detail)``.
    """
    if not service_url:
        return False, "missing service_url"
    if not bot_number:
        return False, "missing bot_number"
    url = service_url.rstrip("/") + "/v2/send"
    payload = {
        "number": bot_number,
        "recipients": [chat_id],
        "message": text,
    }
    try:
        async with httpx.AsyncClient(timeout=timeout_seconds) as client:
            response = await client.post(url, json=payload)
        if response.status_code >= 400:
            return False, f"http {response.status_code}: {response.text[:200]}"
    except Exception as exc:  # noqa: BLE001
        return False, str(exc)
    return True, None
