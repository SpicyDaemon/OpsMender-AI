"""WhatsApp connector adapter (Meta Cloud API).

WhatsApp webhooks arrive via Meta's Webhooks infrastructure. Meta
signs outbound payloads with an HMAC-SHA256 signature in the
``X-Hub-Signature-256`` header using the app secret as the key.
Operators configure ``credentials.app_secret`` to enable verification.

Inbound payload shape (simplified)::

    {
      "object": "whatsapp_business_account",
      "entry": [{
        "id": "<waba-id>",
        "changes": [{
          "value": {
            "messaging_product": "whatsapp",
            "metadata": {"phone_number_id": "...", "display_phone_number": "..."},
            "messages": [{
              "from": "15551234567",
              "id": "wamid.xxx",
              "timestamp": "...",
              "type": "text",
              "text": {"body": "/incidents"}
            }]
          },
          "field": "messages"
        }]
      }]
    }

The adapter also handles the Meta webhook verification challenge
(``GET`` with ``hub.mode=subscribe``, ``hub.challenge``,
``hub.verify_token``), but that is done at the route level since
it's a GET rather than a POST.
"""

from __future__ import annotations

import hashlib
import hmac
from typing import Any, Mapping

from fastapi import HTTPException, status

from backend.bots.whatsapp_client import send_message as whatsapp_send
from backend.db.models import BotConnector

from .base import BotConnectorAdapter, InboundMessage


class WhatsAppAdapter:
    """Adapter for WhatsApp Business Cloud API webhooks."""

    platform = "whatsapp"

    def verify_webhook(
        self,
        connector: BotConnector,
        *,
        headers: Mapping[str, str],
        raw_body: bytes,
    ) -> None:
        if connector.platform != self.platform:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Connector is not a WhatsApp connector",
            )
        if not connector.is_enabled:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Connector is disabled",
            )

        credentials = connector.credentials or {}
        app_secret = credentials.get("app_secret")
        if not app_secret:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="WhatsApp app_secret is not configured",
            )

        signature_header = headers.get("x-hub-signature-256") or headers.get(
            "X-Hub-Signature-256"
        )
        if not signature_header:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Missing X-Hub-Signature-256 header",
            )

        # Meta sends "sha256=<hex-digest>"
        if signature_header.startswith("sha256="):
            provided_hex = signature_header[7:]
        else:
            provided_hex = signature_header

        expected = hmac.new(
            app_secret.encode("utf-8"),
            raw_body,
            hashlib.sha256,
        ).hexdigest()

        if not hmac.compare_digest(expected, provided_hex):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Invalid WhatsApp webhook signature",
            )

    def parse_inbound(
        self,
        payload: dict[str, Any],
    ) -> InboundMessage | None:
        # Walk the Meta webhook envelope to find the first text message.
        entries = payload.get("entry") or []
        if not isinstance(entries, list):
            return None

        for entry in entries:
            changes = entry.get("changes") or []
            if not isinstance(changes, list):
                continue
            for change in changes:
                value = change.get("value") or {}
                messages = value.get("messages") or []
                if not isinstance(messages, list):
                    continue
                for msg in messages:
                    if msg.get("type") != "text":
                        continue
                    text_obj = msg.get("text") or {}
                    body = text_obj.get("body")
                    if not isinstance(body, str):
                        continue
                    sender = msg.get("from") or ""
                    # WhatsApp messages don't have a "chat" concept like
                    # Telegram groups — the sender phone is the chat scope.
                    return InboundMessage(
                        chat_id=str(sender),
                        platform_user_id=str(sender) if sender else None,
                        text=body.strip(),
                    )

        return None

    def inline_reply(
        self,
        chat_id: str,
        text: str,
    ) -> dict[str, Any] | None:
        # WhatsApp Cloud API does not support inline reply payloads.
        # Returning None tells the dispatcher to schedule send_message.
        return None

    async def send_message(
        self,
        connector: BotConnector,
        *,
        chat_id: str,
        text: str,
    ) -> tuple[bool, str | None]:
        creds = connector.credentials or {}
        return await whatsapp_send(
            access_token=str(creds.get("access_token") or ""),
            phone_number_id=str(creds.get("phone_number_id") or ""),
            recipient=chat_id,
            text=text,
        )
