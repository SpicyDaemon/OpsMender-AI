"""Home Assistant connector adapter."""

from __future__ import annotations

import secrets
from typing import Any, Mapping

from fastapi import HTTPException
import httpx

from backend.db.models import BotConnector
from .base import FieldSpec, InboundMessage


class HomeAssistantAdapter:
    """Adapter for Home Assistant (HASS) Actionable Notifications."""

    platform = "homeassistant"

    @classmethod
    def form_schema(cls) -> list[FieldSpec]:
        return [
            FieldSpec(
                name="webhook_secret",
                label="Webhook shared secret",
                kind="secret",
                group="credentials",
                required=True,
                helper="Random string. Your HASS automation must send it as the X-OpsMender-Webhook-Secret header.",
            ),
            FieldSpec(
                name="service_url",
                label="Home Assistant URL",
                kind="url",
                group="credentials",
                required=True,
                helper="Base URL of your Home Assistant instance.",
                placeholder="https://homeassistant.local:8123",
            ),
            FieldSpec(
                name="access_token",
                label="Long-lived access token",
                kind="secret",
                group="credentials",
                required=True,
                helper="From your HASS user profile → Long-Lived Access Tokens.",
                doc_url="https://www.home-assistant.io/docs/authentication/#your-account-profile",
            ),
            FieldSpec(
                name="default_chat_id",
                label="Default notify service",
                kind="text",
                group="config",
                required=False,
                helper="Optional. notify.<service> target used for outbound notifications.",
                placeholder="notify.mobile_app_pixel",
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
                status_code=400, detail="Not a Home Assistant connector"
            )

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
            return (
                False,
                "Home Assistant credentials (access_token, service_url) not configured",
            )

        # Deliver via HASS persistent_notification or notify service
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{url.rstrip('/')}/api/services/notify/persistent_notification",
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json",
                },
                json={
                    "title": "OpsMender Incident Update",
                    "message": text,
                },
                timeout=10.0,
            )
            if resp.status_code != 200:
                return (
                    False,
                    f"Home Assistant API error: HTTP {resp.status_code} - {resp.text}",
                )

            return True, None
