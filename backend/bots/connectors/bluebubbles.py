"""BlueBubbles (iMessage) connector adapter."""

from __future__ import annotations

import secrets
from typing import Any, Mapping

from fastapi import HTTPException, status
import httpx

from backend.db.models import BotConnector
from .base import BotConnectorAdapter, FieldSpec, InboundMessage


class BlueBubblesAdapter:
    """Adapter for BlueBubbles (iMessage relay)."""

    platform = "bluebubbles"

    @classmethod
    def form_schema(cls) -> list[FieldSpec]:
        return [
            FieldSpec(
                name="server_url",
                label="BlueBubbles server URL",
                kind="url",
                group="credentials",
                required=True,
                helper="Base URL of your BlueBubbles server running on a Mac.",
                doc_url="https://bluebubbles.app/server/",
                placeholder="http://192.168.1.10:1234",
            ),
            FieldSpec(
                name="password",
                label="Server password",
                kind="secret",
                group="credentials",
                required=True,
                helper="Password configured in your BlueBubbles server settings.",
            ),
            FieldSpec(
                name="default_chat_id",
                label="Default recipient",
                kind="text",
                group="config",
                required=False,
                helper="Optional. iMessage handle (phone or email) for outbound notifications.",
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
            raise HTTPException(status_code=400, detail="Not a BlueBubbles connector")
            
        # BlueBubbles webhooks are usually protected by a shared password in the payload
        pass

    def parse_inbound(
        self,
        payload: dict[str, Any],
    ) -> InboundMessage | None:
        # BlueBubbles Webhook payload structure:
        # { "type": "new-message", "data": { "text": "...", "handle": { "address": "..." } } }
        event_type = payload.get("type")
        if event_type != "new-message":
            return None
            
        data = payload.get("data") or {}
        text = data.get("text")
        handle = data.get("handle") or {}
        address = handle.get("address")
        
        if not text or not address:
            return None
            
        # Skip messages from self if necessary, but BlueBubbles usually only sends received messages to webhooks
        return InboundMessage(
            chat_id=str(address),
            platform_user_id=str(address),
            text=text.strip(),
        )

    def inline_reply(
        self,
        chat_id: str,
        text: str,
    ) -> dict[str, Any] | None:
        return None

    async def send_message(
        self,
        connector: BotConnector,
        *,
        chat_id: str,
        text: str,
    ) -> tuple[bool, str | None]:
        credentials = connector.credentials or {}
        url = credentials.get("server_url")
        password = credentials.get("password")
        
        if not url or not password:
            return False, "BlueBubbles credentials (server_url, password) not configured"
            
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{url.rstrip('/')}/api/v1/message/text",
                params={"password": password},
                json={
                    "chatGuid": f"iMessage;-;{chat_id}", # Simplified chatGuid
                    "message": text,
                    "method": "apple-script", # or 'private-api'
                },
                timeout=15.0,
            )
            if resp.status_code != 200:
                return False, f"BlueBubbles API error: HTTP {resp.status_code} - {resp.text}"
                
            return True, None
