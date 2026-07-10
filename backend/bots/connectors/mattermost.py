"""Mattermost connector adapter."""

from __future__ import annotations

import secrets
from typing import Any, Mapping

from fastapi import HTTPException, status
import httpx

from backend.db.models import BotConnector
from .base import FieldSpec, InboundMessage


class MattermostAdapter:
    """Adapter for Mattermost Outgoing Webhooks."""

    platform = "mattermost"

    @classmethod
    def form_schema(cls) -> list[FieldSpec]:
        return [
            FieldSpec(
                name="webhook_token",
                label="Outgoing webhook token",
                kind="secret",
                group="credentials",
                required=True,
                helper="Token shown when you create the Outgoing Webhook in Mattermost integrations.",
                doc_url="https://developers.mattermost.com/integrate/webhooks/outgoing/",
            ),
            FieldSpec(
                name="service_url",
                label="Mattermost server URL",
                kind="url",
                group="credentials",
                required=True,
                helper="Base URL of your Mattermost server.",
                placeholder="https://mattermost.example.com",
            ),
            FieldSpec(
                name="bot_token",
                label="Bot personal access token",
                kind="secret",
                group="credentials",
                required=True,
                helper="Personal access token of the bot account. Required for outbound posts.",
                doc_url="https://developers.mattermost.com/integrate/reference/personal-access-token/",
            ),
            FieldSpec(
                name="default_chat_id",
                label="Default channel ID",
                kind="text",
                group="config",
                required=False,
                helper="Optional. Channel ID used for outbound notifications.",
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
                detail="Connector is not a Mattermost connector",
            )
        if not connector.is_enabled:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Connector is disabled",
            )

        credentials = connector.credentials or {}
        expected_token = credentials.get("webhook_token")
        if not expected_token:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Mattermost webhook token is not configured",
            )

        # Mattermost usually sends the token in the form-encoded body or as a header
        # We'll check the body first (standard Outgoing Webhook)
        from urllib.parse import parse_qs

        params = parse_qs(raw_body.decode("utf-8"))
        provided = params.get("token", [None])[0]

        if not provided or not secrets.compare_digest(str(expected_token), provided):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Invalid Mattermost webhook token",
            )

    def parse_inbound(
        self,
        payload: dict[str, Any],
    ) -> InboundMessage | None:
        # Mattermost Outgoing Webhooks send form-encoded data, which FastAPI
        # might have already parsed into a dict if we use payload: dict

        chat_id = payload.get("channel_id")
        user_id = payload.get("user_id")
        text = payload.get("text")
        user_name = payload.get("user_name")

        # Skip messages from bots (Mattermost sets user_name to 'mattermost' or similar,
        # but better to check for bot flags if available)
        if not chat_id or not text or user_name == "slackbot":
            return None

        # Reconstruct message (text often includes the trigger word)
        # We just return the text as-is; dispatcher handles command extraction
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
        # Mattermost supports returning a JSON response to the outgoing webhook
        return {
            "text": text,
            "response_type": "ephemeral",  # or "in_channel"
        }

    async def send_message(
        self,
        connector: BotConnector,
        *,
        chat_id: str,
        text: str,
    ) -> tuple[bool, str | None]:
        credentials = connector.credentials or {}
        bot_token = credentials.get("bot_token")
        service_url = credentials.get("service_url")
        if not bot_token or not service_url:
            return (
                False,
                "Mattermost credentials (bot_token, service_url) not configured",
            )

        # Outbound delivery using the Mattermost API
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{service_url.rstrip('/')}/api/v4/posts",
                headers={
                    "Authorization": f"Bearer {bot_token}",
                    "Content-Type": "application/json",
                },
                json={
                    "channel_id": chat_id,
                    "message": text,
                },
                timeout=10.0,
            )
            if resp.status_code not in (200, 201):
                return (
                    False,
                    f"Mattermost API error: HTTP {resp.status_code} - {resp.text}",
                )

            return True, None
