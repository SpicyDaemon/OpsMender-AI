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
from backend.bots.delivery import DeliveryReceipt, UpdateResult
from backend.paging.slack_cards import build_page_card_blocks
from .base import BotConnectorAdapter, FieldSpec, InboundMessage


# Slack chat.update errors that mean the original message can no longer be
# edited — the notifier should post a fresh follow-up message instead of
# treating these as hard delivery failures.
_SLACK_UPDATE_FALLBACK_ERRORS = {
    "message_not_found",
    "cant_update_message",
    "edit_window_closed",
    "channel_not_found",
    "is_inactive",
}


class SlackAdapter:
    """Adapter for Slack Events API webhooks."""

    platform = "slack"

    @classmethod
    def form_schema(cls) -> list[FieldSpec]:
        return [
            FieldSpec(
                name="signing_secret",
                label="Signing secret",
                kind="secret",
                group="credentials",
                required=True,
                helper="From Slack app settings → Basic Information → App Credentials.",
                doc_url="https://api.slack.com/authentication/verifying-requests-from-slack",
            ),
            FieldSpec(
                name="bot_token",
                label="Bot user OAuth token",
                kind="secret",
                group="credentials",
                required=True,
                helper="Starts with xoxb-. From OAuth & Permissions after installing the app to your workspace.",
                doc_url="https://api.slack.com/authentication/token-types#bot",
                placeholder="xoxb-...",
            ),
            FieldSpec(
                name="default_chat_id",
                label="Default channel ID",
                kind="text",
                group="config",
                required=False,
                helper="Optional. Slack channel ID (e.g. C01ABCD2EF3) used for outbound notifications.",
                placeholder="C01ABCD2EF3",
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

    async def send_incident_update(
        self,
        connector: BotConnector,
        *,
        chat_id: str,
        text: str,
        incident=None,
        native_actions_ready: bool = False,
    ) -> DeliveryReceipt:
        credentials = connector.credentials or {}
        bot_token = credentials.get("bot_token")
        if not bot_token:
            return DeliveryReceipt(ok=False, error="Slack bot token is not configured")

        payload: dict[str, Any] = {"channel": chat_id, "text": text}
        if incident is not None:
            payload["blocks"] = build_page_card_blocks(
                incident,
                base_url=None,
                include_native_actions=native_actions_ready,
            )
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                "https://slack.com/api/chat.postMessage",
                headers={
                    "Authorization": f"Bearer {bot_token}",
                    "Content-Type": "application/json; charset=utf-8",
                },
                json=payload,
                timeout=10.0,
            )
        if resp.status_code != 200:
            return DeliveryReceipt(ok=False, error=f"Slack API error: HTTP {resp.status_code}")
        data = resp.json()
        if not data.get("ok"):
            return DeliveryReceipt(ok=False, error=f"Slack API error: {data.get('error')}")
        ts = data.get("ts")
        return DeliveryReceipt(
            ok=True,
            external_channel_id=str(data.get("channel") or chat_id),
            external_message_id=str(ts) if ts else None,
            # The message can be edited in place later via chat.update as long
            # as we captured its timestamp.
            can_update=bool(ts),
        )

    async def update_incident_update(
        self,
        connector: BotConnector,
        *,
        chat_id: str,
        text: str,
        external_message_id: str,
        external_thread_id: str | None = None,
        incident=None,
        native_actions_ready: bool = False,
    ) -> UpdateResult:
        """Edit an existing Slack incident message in place via chat.update.

        Re-renders the Block Kit card (when ``incident`` is supplied) so the
        edited message reflects the new lifecycle state and keeps its action
        buttons. Recoverable Slack errors (message gone, edit window closed)
        return ``fallback_to_followup=True`` so the notifier posts a new message.
        """
        credentials = connector.credentials or {}
        bot_token = credentials.get("bot_token")
        if not bot_token:
            return UpdateResult(ok=False, error="Slack bot token is not configured")

        payload: dict[str, Any] = {
            "channel": chat_id,
            "ts": external_message_id,
            "text": text,
        }
        if incident is not None:
            payload["blocks"] = build_page_card_blocks(
                incident,
                base_url=None,
                include_native_actions=native_actions_ready,
            )

        try:
            async with httpx.AsyncClient() as client:
                resp = await client.post(
                    "https://slack.com/api/chat.update",
                    headers={
                        "Authorization": f"Bearer {bot_token}",
                        "Content-Type": "application/json; charset=utf-8",
                    },
                    json=payload,
                    timeout=10.0,
                )
        except httpx.HTTPError as exc:
            return UpdateResult(
                ok=False, error=f"network: {exc}", fallback_to_followup=True
            )

        if resp.status_code != 200:
            return UpdateResult(
                ok=False,
                error=f"Slack API error: HTTP {resp.status_code}",
                fallback_to_followup=True,
            )
        data = resp.json()
        if not data.get("ok"):
            error = data.get("error")
            return UpdateResult(
                ok=False,
                error=f"Slack API error: {error}",
                fallback_to_followup=error in _SLACK_UPDATE_FALLBACK_ERRORS,
            )
        return UpdateResult(
            ok=True,
            receipt=DeliveryReceipt(
                ok=True,
                external_channel_id=str(data.get("channel") or chat_id),
                external_message_id=str(data.get("ts") or external_message_id),
                external_thread_id=external_thread_id,
                can_update=True,
            ),
        )
