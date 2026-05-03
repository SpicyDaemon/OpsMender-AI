"""Slack connector adapter."""

from __future__ import annotations

import hmac
import hashlib
import json
import time
from typing import Any, Mapping

from fastapi import HTTPException, status
import httpx

from backend.db.models import BotConnector
from .base import BotConnectorAdapter, InboundMessage


class SlackAdapter:
    """Adapter for Slack Events API webhooks."""

    platform = "slack"

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
                detail="Connector is not a Slack connector",
            )
        if not connector.is_enabled:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Connector is disabled",
            )

        credentials = connector.credentials or {}
        signing_secret = credentials.get("signing_secret")
        if not signing_secret:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Slack signing secret is not configured",
            )

        # Slack signature verification: https://api.slack.com/authentication/verifying-requests-from-slack
        timestamp = headers.get("x-slack-request-timestamp") or headers.get("X-Slack-Request-Timestamp")
        signature = headers.get("x-slack-signature") or headers.get("X-Slack-Signature")

        if not timestamp or not signature:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Missing Slack signature headers",
            )

        # Check for replay attacks (5 minute window)
        if abs(time.time() - int(timestamp)) > 60 * 5:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Slack request timestamp is too old",
            )

        sig_basestring = f"v0:{timestamp}:{raw_body.decode('utf-8')}"
        my_signature = "v0=" + hmac.new(
            signing_secret.encode("utf-8"),
            sig_basestring.encode("utf-8"),
            hashlib.sha256
        ).hexdigest()

        if not hmac.compare_digest(my_signature, signature):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Invalid Slack signature",
            )

    def parse_inbound(
        self,
        payload: dict[str, Any],
    ) -> InboundMessage | None:
        # Handle Slack URL verification challenge
        if payload.get("type") == "url_verification":
            # This is handled in the route, but we return None here to skip dispatcher
            return None

        event = payload.get("event") or {}
        event_type = event.get("type")

        # We only care about message events that are not from bots
        if event_type != "message" or event.get("bot_id"):
            return None

        chat_id = event.get("channel")
        user_id = event.get("user")
        text = event.get("text")

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
        # Slack doesn't typically use inline replies in the webhook response for events API
        # but we could support it for slash commands later.
        return None

    async def send_message(
        self,
        connector: BotConnector,
        *,
        chat_id: str,
        text: str,
    ) -> tuple[bool, str | None]:
        credentials = connector.credentials or {}
        bot_token = credentials.get("bot_token")
        if not bot_token:
            return False, "Slack bot token is not configured"

        async with httpx.AsyncClient() as client:
            resp = await client.post(
                "https://slack.com/api/chat.postMessage",
                headers={
                    "Authorization": f"Bearer {bot_token}",
                    "Content-Type": "application/json; charset=utf-8",
                },
                json={
                    "channel": chat_id,
                    "text": text,
                },
                timeout=10.0,
            )
            if resp.status_code != 200:
                return False, f"Slack API error: HTTP {resp.status_code}"
            
            data = resp.json()
            if not data.get("ok"):
                return False, f"Slack API error: {data.get('error')}"
            
            return True, None
