"""Signal connector adapter (signal-cli-rest-api bridge)."""

from __future__ import annotations

import secrets
from typing import Any, Mapping

from fastapi import HTTPException, status

from backend.bots.signal_client import send_message as signal_send
from backend.db.models import BotConnector

from .base import FieldSpec, InboundMessage


class SignalAdapter:
    """Adapter for inbound webhooks from a signal-cli-rest-api relay.

    The reference ``signal-cli-rest-api`` server does not sign its
    outbound webhooks. OpsMender expects a reverse proxy / relay in front of
    the bridge that injects an ``X-OpsMender-Webhook-Secret`` header matching
    ``credentials.webhook_secret``. Operators who run signal-cli-rest-api
    behind nginx, Caddy, or a small forwarding service can add one
    ``proxy_set_header`` line to satisfy this.

    Inbound payload shape (signal-cli-rest-api ``--receive-mode http``)::

        {
          "envelope": {
            "source": "+15551234567",
            "sourceUuid": "...",
            "dataMessage": {
              "message": "/incidents",
              "groupInfo": {"groupId": "..."}
            }
          }
        }
    """

    platform = "signal"

    @classmethod
    def form_schema(cls) -> list[FieldSpec]:
        return [
            FieldSpec(
                name="service_url",
                label="signal-cli-rest-api URL",
                kind="url",
                group="credentials",
                required=True,
                helper="Base URL of your signal-cli-rest-api server (e.g. http://signal-cli:8080).",
                doc_url="https://github.com/bbernhard/signal-cli-rest-api",
                placeholder="http://signal-cli:8080",
            ),
            FieldSpec(
                name="bot_number",
                label="Bot phone number",
                kind="text",
                group="credentials",
                required=True,
                helper="The registered Signal phone number in E.164 format.",
                placeholder="+15551234567",
            ),
            FieldSpec(
                name="webhook_secret",
                label="Webhook shared secret",
                kind="secret",
                group="credentials",
                required=True,
                helper="Random string. Your reverse proxy must inject it as the X-OpsMender-Webhook-Secret header on inbound calls.",
            ),
            FieldSpec(
                name="default_chat_id",
                label="Default recipient",
                kind="text",
                group="config",
                required=False,
                helper="Optional. Recipient phone (+15551234567) or Signal group ID for outbound notifications.",
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
                detail="Connector is not a Signal connector",
            )
        if not connector.is_enabled:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Connector is disabled",
            )

        credentials = connector.credentials or {}
        expected = credentials.get("webhook_secret")
        if not expected:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Signal webhook secret is not configured",
            )

        provided = headers.get("x-opsmender-webhook-secret") or headers.get(
            "X-OpsMender-Webhook-Secret"
        )
        if not provided or not secrets.compare_digest(str(expected), provided):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Invalid Signal webhook secret",
            )

    def parse_inbound(
        self,
        payload: dict[str, Any],
    ) -> InboundMessage | None:
        envelope = payload.get("envelope") or {}
        data = envelope.get("dataMessage") or {}
        text_raw = data.get("message")
        if not isinstance(text_raw, str):
            return None

        # Group messages target the group-id; 1-1 messages target the
        # source phone / UUID.
        group = data.get("groupInfo") or {}
        group_id = group.get("groupId") if isinstance(group, dict) else None
        source = envelope.get("source") or envelope.get("sourceUuid")

        chat_id = group_id or source
        if not chat_id:
            return None

        return InboundMessage(
            chat_id=str(chat_id),
            platform_user_id=str(source) if source else None,
            text=text_raw.strip(),
        )

    def inline_reply(
        self,
        chat_id: str,
        text: str,
    ) -> dict[str, Any] | None:
        # signal-cli-rest-api does not accept inline reply payloads.
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
        return await signal_send(
            service_url=str(creds.get("service_url") or ""),
            bot_number=str(creds.get("bot_number") or ""),
            chat_id=chat_id,
            text=text,
        )
