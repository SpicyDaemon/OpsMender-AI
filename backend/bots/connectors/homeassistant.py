"""Home Assistant connector adapter."""

from __future__ import annotations

import secrets
from typing import Any, Mapping

from fastapi import HTTPException, status
import httpx

from backend.db.models import BotConnector
from .base import BotConnectorAdapter, InboundMessage


class HomeAssistantAdapter:
    """Adapter for Home Assistant (HASS) Actionable Notifications."""

    platform = "homeassistant"

    def verify_webhook(
        self,
        connector: BotConnector,
        *,
        headers: Mapping[str, str],
        raw_body: bytes,
    ) -> None:
        if connector.platform != self.platform:
            raise HTTPException(status_code=400, detail="Not a Home Assistant connector")
            
        credentials = connector.credentials or {}
        expected_secret = credentials.get("webhook_secret")
        if not expected_secret:
            return

        # Simple shared secret verification
        provided = headers.get("x-hass-secret") or headers.get("X-Hass-Secret")
        if not provided or not secrets.compare_digest(str(expected_secret), provided):
            raise HTTPException(status_code=403, detail="Invalid Home Assistant secret")

    def parse_inbound(
        self,
        payload: dict[str, Any],
    ) -> InboundMessage | None:
        # HASS Actionable Notifications send a 'action' or 'text'
        chat_id = payload.get("source") or payload.get("entity_id") or "hass-default"
        text = payload.get("action") or payload.get("message")
        user_id = payload.get("user_id")
        
        if not text:
            return None
            
        return InboundMessage(
            chat_id=str(chat_id),
            platform_user_id=str(user_id) if user_id else str(chat_id),
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
        token = credentials.get("access_token")
        url = credentials.get("service_url")
        
        if not token or not url:
            return False, "Home Assistant credentials (access_token, service_url) not configured"
            
        # Deliver via HASS persistent_notification or notify service
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{url.rstrip('/')}/api/services/notify/persistent_notification",
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json",
                },
                json={
                    "title": "AIM Incident Update",
                    "message": text,
                },
                timeout=10.0,
            )
            if resp.status_code != 200:
                return False, f"Home Assistant API error: HTTP {resp.status_code} - {resp.text}"
                
            return True, None
