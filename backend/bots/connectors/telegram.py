"""Telegram connector adapter."""

from __future__ import annotations

import secrets
from typing import Any, Mapping

from fastapi import HTTPException, status

from backend.bots.telegram import send_message as telegram_send
from backend.db.models import BotConnector

from .base import BotConnectorAdapter, FieldSpec, InboundMessage


class TelegramAdapter:
    """Adapter for Telegram Bot API webhooks."""

    platform = "telegram"

    @classmethod
    def form_schema(cls) -> list[FieldSpec]:
        return [
            FieldSpec(
                name="bot_token",
                label="Bot token",
                kind="secret",
                group="credentials",
                required=True,
                helper="Issued by @BotFather when you create the bot.",
                doc_url="https://core.telegram.org/bots#botfather",
                placeholder="123456:ABC-...",
            ),
            FieldSpec(
                name="webhook_secret",
                label="Webhook secret token",
                kind="secret",
                group="credentials",
                required=True,
                helper="Random string you set when calling setWebhook; Telegram echoes it back in X-Telegram-Bot-Api-Secret-Token.",
                doc_url="https://core.telegram.org/bots/api#setwebhook",
            ),
            FieldSpec(
                name="default_chat_id",
                label="Default chat ID",
                kind="text",
                group="config",
                required=False,
                helper="Optional. Chat ID used for outbound notifications when no explicit recipient is given.",
                placeholder="-1001234567890",
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
                detail="Connector is not a Telegram connector",
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
                detail="Telegram webhook secret is not configured",
            )

        provided = headers.get("x-telegram-bot-api-secret-token") or headers.get(
            "X-Telegram-Bot-Api-Secret-Token"
        )
        if not provided or not secrets.compare_digest(
            str(expected_secret), provided
        ):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Invalid Telegram webhook secret",
            )

    def parse_inbound(
        self,
        payload: dict[str, Any],
    ) -> InboundMessage | None:
        message = payload.get("message") or payload.get("edited_message") or {}
        chat = message.get("chat") or {}
        chat_id_raw = chat.get("id")
        if chat_id_raw is None:
            return None
        sender = message.get("from") or {}
        sender_id = sender.get("id")
        text = message.get("text")
        return InboundMessage(
            chat_id=str(chat_id_raw),
            platform_user_id=None if sender_id is None else str(sender_id),
            text=text.strip() if isinstance(text, str) else "",
        )

    def inline_reply(
        self,
        chat_id: str,
        text: str,
    ) -> dict[str, Any] | None:
        return {
            "method": "sendMessage",
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "Markdown",
            "disable_web_page_preview": True,
        }

    async def send_message(
        self,
        connector: BotConnector,
        *,
        chat_id: str,
        text: str,
    ) -> tuple[bool, str | None]:
        bot_token = (connector.credentials or {}).get("bot_token")
        return await telegram_send(
            bot_token=str(bot_token) if bot_token else "",
            chat_id=chat_id,
            text=text,
        )
