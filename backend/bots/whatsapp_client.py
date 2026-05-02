"""WhatsApp Cloud API outbound client.

Targets the Meta WhatsApp Business Cloud API:

    POST https://graph.facebook.com/v21.0/{phone_number_id}/messages
    Authorization: Bearer {access_token}
    Content-Type: application/json
    {
      "messaging_product": "whatsapp",
      "to": "<recipient_phone>",
      "type": "text",
      "text": {"body": "<text>"}
    }

Operators configure ``credentials.access_token`` and
``credentials.phone_number_id`` on the connector.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

log = logging.getLogger(__name__)

WHATSAPP_API_BASE = "https://graph.facebook.com/v21.0"


async def send_message(
    *,
    access_token: str,
    phone_number_id: str,
    recipient: str,
    text: str,
    timeout_seconds: float = 10.0,
) -> tuple[bool, str | None]:
    """Send a single WhatsApp text message. Returns ``(ok, error_detail)``."""
    if not access_token:
        return False, "missing access_token"
    if not phone_number_id:
        return False, "missing phone_number_id"

    url = f"{WHATSAPP_API_BASE}/{phone_number_id}/messages"
    headers: dict[str, str] = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
    }
    payload: dict[str, Any] = {
        "messaging_product": "whatsapp",
        "to": recipient,
        "type": "text",
        "text": {"body": text},
    }
    try:
        async with httpx.AsyncClient(timeout=timeout_seconds) as client:
            response = await client.post(url, json=payload, headers=headers)
        if response.status_code >= 400:
            return False, f"http {response.status_code}: {response.text[:200]}"
    except Exception as exc:  # noqa: BLE001
        return False, str(exc)
    return True, None
