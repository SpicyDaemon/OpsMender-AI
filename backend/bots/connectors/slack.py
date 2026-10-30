"""Slack connector adapter."""

from __future__ import annotations

import asyncio
import hmac
import hashlib
import time
from typing import Any, Mapping
from urllib.parse import urlsplit

from fastapi import HTTPException, status
import httpx

from backend.db.models import BotConnector
from backend.bots.delivery import DeliveryReceipt, UpdateResult
from backend.bots.incident_card import public_base_url
from backend.paging.slack_cards import build_page_card_blocks
from .base import FieldSpec, InboundMessage
from .credentials import UNREADABLE_HINT, readable


# Slack chat.update errors that mean the original message can no longer be
# edited - the notifier should post a fresh follow-up message instead of
# treating these as hard delivery failures.
_SLACK_UPDATE_FALLBACK_ERRORS = {
    "message_not_found",
    "cant_update_message",
    "edit_window_closed",
    "channel_not_found",
    "is_inactive",
}

# Slack sends a block_actions ``response_url`` on its hooks host (GovSlack
# workspaces use slack-gov.com). Nothing else is ever posted to.
_RESPONSE_URL_HOSTS = {"hooks.slack.com", "hooks.slack-gov.com"}


async def _post_message(
    bot_token: str, payload: dict[str, Any]
) -> tuple[httpx.Response | None, str | None]:
    """Retry only connection setup, before Slack could accept the message.

    A read/write timeout may follow an accepted post, so it must not trigger
    another send. Keep transport exception text out of delivery audits.
    """
    for attempt in range(3):
        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    "https://slack.com/api/chat.postMessage",
                    headers={
                        "Authorization": f"Bearer {bot_token}",
                        "Content-Type": "application/json; charset=utf-8",
                    },
                    json=payload,
                    timeout=10.0,
                )
            return response, None
        except (httpx.ConnectTimeout, httpx.ConnectError, httpx.PoolTimeout) as exc:
            error = f"Slack transport error: {type(exc).__name__}"
            if attempt < 2:
                await asyncio.sleep(0.25 * (attempt + 1))
        except httpx.HTTPError as exc:
            return None, f"Slack transport error: {type(exc).__name__}"
    return None, error


def _bot_token_problem(credentials: Mapping[str, Any]) -> str:
    """Why no bot token is usable: none stored, or one this key cannot open."""
    if credentials.get("bot_token"):
        return f"Slack bot token {UNREADABLE_HINT}"
    return "Slack bot token is not configured"


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
                name="inbound_mode",
                label="How Slack reaches OpsMender",
                kind="select",
                group="config",
                required=False,
                default="http",
                helper=(
                    "Request URLs need a public URL for buttons and commands. "
                    "Socket Mode needs none: OpsMender opens the connection to Slack."
                ),
                doc_url="https://api.slack.com/apis/socket-mode",
                options=(
                    ("http", "Request URLs"),
                    ("socket", "Socket Mode"),
                ),
            ),
            FieldSpec(
                name="app_token_env",
                label="App-level token variable",
                kind="text",
                group="config",
                required=False,
                default="OPSMENDER_SLACK_APP_TOKEN",
                helper=(
                    "Socket Mode only. The name of the environment variable that "
                    "holds the app-level token (xapp-, scope connections:write); "
                    "the token itself is never stored."
                ),
                placeholder="OPSMENDER_SLACK_APP_TOKEN",
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
        signing_secret = readable(credentials.get("signing_secret"))
        if not signing_secret:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=(
                    f"Slack signing secret {UNREADABLE_HINT}"
                    if credentials.get("signing_secret")
                    else "Slack signing secret is not configured"
                ),
            )

        # Slack signature verification: https://api.slack.com/authentication/verifying-requests-from-slack
        timestamp = headers.get("x-slack-request-timestamp") or headers.get(
            "X-Slack-Request-Timestamp"
        )
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
        my_signature = (
            "v0="
            + hmac.new(
                signing_secret.encode("utf-8"),
                sig_basestring.encode("utf-8"),
                hashlib.sha256,
            ).hexdigest()
        )

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
        bot_token = readable(credentials.get("bot_token"))
        if not bot_token:
            return False, _bot_token_problem(credentials)

        resp, error = await _post_message(bot_token, {"channel": chat_id, "text": text})
        if resp is None:
            return False, error
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
        status_update: bool = False,
        facts: list[tuple[str, str]] | None = None,
        acknowledged_by: str | None = None,
    ) -> DeliveryReceipt:
        credentials = connector.credentials or {}
        bot_token = readable(credentials.get("bot_token"))
        if not bot_token:
            return DeliveryReceipt(ok=False, error=_bot_token_problem(credentials))

        payload: dict[str, Any] = {"channel": chat_id, "text": text}
        if incident is not None:
            payload["blocks"] = build_page_card_blocks(
                incident,
                base_url=public_base_url(),
                include_native_actions=native_actions_ready,
                status_update=status_update,
                facts=facts,
                acknowledged_by=acknowledged_by,
            )
        resp, error = await _post_message(bot_token, payload)
        if resp is None:
            return DeliveryReceipt(ok=False, error=error, external_channel_id=chat_id)
        if resp.status_code != 200:
            return DeliveryReceipt(
                ok=False, error=f"Slack API error: HTTP {resp.status_code}"
            )
        data = resp.json()
        if not data.get("ok"):
            return DeliveryReceipt(
                ok=False, error=f"Slack API error: {data.get('error')}"
            )
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
        status_update: bool = False,
        facts: list[tuple[str, str]] | None = None,
        acknowledged_by: str | None = None,
    ) -> UpdateResult:
        """Edit an existing Slack incident message in place via chat.update.

        Re-renders the Block Kit card (when ``incident`` is supplied) so the
        edited message reflects the new lifecycle state and keeps its action
        buttons. Recoverable Slack errors (message gone, edit window closed)
        return ``fallback_to_followup=True`` so the notifier posts a new message.
        """
        credentials = connector.credentials or {}
        bot_token = readable(credentials.get("bot_token"))
        if not bot_token:
            return UpdateResult(ok=False, error=_bot_token_problem(credentials))

        payload: dict[str, Any] = {
            "channel": chat_id,
            "ts": external_message_id,
            "text": text,
        }
        if incident is not None:
            payload["blocks"] = build_page_card_blocks(
                incident,
                base_url=public_base_url(),
                include_native_actions=native_actions_ready,
                status_update=status_update,
                facts=facts,
                acknowledged_by=acknowledged_by,
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

    async def respond_to_interaction(
        self,
        *,
        response_url: str,
        text: str,
    ) -> tuple[bool, str | None]:
        """Tell the person who tapped a card button what happened.

        Slack shows nothing for the HTTP body of a ``block_actions`` request;
        a message posted to the payload's ``response_url`` is what that person
        sees. It is ephemeral (only they see it) and never replaces the card.
        Only Slack's hooks hosts are accepted. Never raises.
        """
        try:
            parts = urlsplit(response_url)
            host = parts.hostname
        except ValueError:  # a malformed URL, such as an unclosed IPv6 bracket
            return False, "Slack response_url is not a Slack hooks URL"
        if parts.scheme != "https" or host not in _RESPONSE_URL_HOSTS:
            return False, "Slack response_url is not a Slack hooks URL"
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.post(
                    response_url,
                    headers={"Content-Type": "application/json; charset=utf-8"},
                    json={
                        "response_type": "ephemeral",
                        "replace_original": False,
                        "text": text,
                    },
                    timeout=10.0,
                )
        except httpx.HTTPError as exc:
            # The type only: the error text can carry the response_url, which
            # anyone could use to post to this person until it expires.
            return False, f"Slack response_url network error: {type(exc).__name__}"
        if resp.status_code != 200:
            return False, f"Slack response_url error: HTTP {resp.status_code}"
        return True, None
