"""Matrix (Element) connector adapter."""

from __future__ import annotations

import secrets
from typing import Any, Mapping

from fastapi import HTTPException, status
import httpx

from backend.db.models import BotConnector
from .base import FieldSpec, InboundMessage


class MatrixAdapter:
    """Adapter for Matrix App Service / Webhook integrations."""

    platform = "matrix"

    @classmethod
    def form_schema(cls) -> list[FieldSpec]:
        return [
            FieldSpec(
                name="webhook_secret",
                label="Webhook shared secret",
                kind="secret",
                group="credentials",
                required=True,
                helper="Random string injected by your relay as the X-OpsMender-Webhook-Secret header.",
            ),
            FieldSpec(
                name="homeserver_url",
                label="Homeserver URL",
                kind="url",
                group="credentials",
                required=True,
                helper="Base URL of your Matrix homeserver.",
                placeholder="https://matrix.example.com",
            ),
            FieldSpec(
                name="access_token",
                label="Bot access token",
                kind="secret",
                group="credentials",
                required=True,
                helper="Access token for the bot's Matrix account. Used to send outbound messages.",
                doc_url="https://spec.matrix.org/latest/client-server-api/#client-authentication",
            ),
            FieldSpec(
                name="default_chat_id",
                label="Default room ID",
                kind="text",
                group="config",
                required=False,
                helper="Optional. Matrix room ID for outbound notifications.",
                placeholder="!abc123:matrix.example.com",
            ),
        ]

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
                detail="Connector is not a Matrix connector",
            )
        if not connector.is_enabled:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Connector is disabled",
            )

        credentials = connector.credentials or {}
        expected_secret = credentials.get("webhook_secret")
        if not expected_secret:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Matrix webhook secret is not configured",
            )

        # We expect a shared secret header, e.g. Authorization: Bearer <secret>
        # or a custom header X-Matrix-Token
        provided = headers.get("authorization") or headers.get("Authorization")
        if provided and provided.startswith("Bearer "):
            provided = provided[len("Bearer "):]
        
        if not provided:
            provided = headers.get("x-matrix-token") or headers.get("X-Matrix-Token")

        if not provided or not secrets.compare_digest(str(expected_secret), provided):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Invalid Matrix webhook secret",
            )

    def parse_inbound(
        self,
        payload: dict[str, Any],
    ) -> InboundMessage | None:
        # Matrix App Service payloads often contain a list of 'events'
        events = payload.get("events")
        if not events or not isinstance(events, list):
            # Fallback to single event if direct
            event = payload
        else:
            # We take the first relevant message event
            event = events[0]

        if event.get("type") != "m.room.message":
            return None
        
        content = event.get("content") or {}
        if content.get("msgtype") != "m.text":
            return None

        chat_id = event.get("room_id")
        user_id = event.get("sender")
        text = content.get("body")

        if not chat_id or not text:
            return None

        return InboundMessage(
            chat_id=str(chat_id),
            platform_user_id=str(user_id) if user_id else None,
            text=text.strip(),
        )

    def inline_reply(
        self,
        chat_id: str,
        text: str,
    ) -> dict[str, Any] | None:
        # Matrix doesn't typically support inline replies in the webhook response
        return None

    async def send_message(
        self,
        connector: BotConnector,
        *,
        chat_id: str,
        text: str,
    ) -> tuple[bool, str | None]:
        credentials = connector.credentials or {}
        access_token = credentials.get("access_token")
        homeserver_url = credentials.get("homeserver_url")
        if not access_token or not homeserver_url:
            return False, "Matrix credentials (access_token, homeserver_url) not configured"

        # Matrix uses a transaction ID in the URL for idempotency
        import uuid
        txn_id = str(uuid.uuid4())
        
        async with httpx.AsyncClient() as client:
            resp = await client.put(
                f"{homeserver_url.rstrip('/')}/_matrix/client/v3/rooms/{chat_id}/send/m.room.message/{txn_id}",
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "Content-Type": "application/json",
                },
                json={
                    "msgtype": "m.text",
                    "body": text,
                    "format": "org.matrix.custom.html",
                    "formatted_body": text.replace("\n", "<br>"), # Simple markdown-to-html fallback
                },
                timeout=10.0,
            )
            if resp.status_code not in (200, 201):
                return False, f"Matrix API error: HTTP {resp.status_code} - {resp.text}"
            
            return True, None
