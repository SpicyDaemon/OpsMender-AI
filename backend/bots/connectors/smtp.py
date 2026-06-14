"""Outbound-only SMTP Email connector adapter."""

from __future__ import annotations

import asyncio
from email.message import EmailMessage
import smtplib
from typing import Any, Mapping

from fastapi import HTTPException, status

from backend.db.models import BotConnector
from .base import FieldSpec, InboundMessage


class SMTPEmailAdapter:
    """Deliver Notification Channel messages through a customer SMTP server."""

    platform = "smtp"

    @classmethod
    def form_schema(cls) -> list[FieldSpec]:
        return [
            FieldSpec(
                name="smtp_host",
                label="SMTP host",
                group="credentials",
                required=True,
                helper="Hostname provided by your cloud email service or mail infrastructure.",
                placeholder="smtp.example.com",
            ),
            FieldSpec(
                name="smtp_port",
                label="SMTP port",
                group="credentials",
                required=True,
                default="587",
                helper="Usually 587 for STARTTLS, 465 for implicit TLS, or 25 for an internal relay.",
                placeholder="587",
            ),
            FieldSpec(
                name="security",
                label="Connection security",
                kind="select",
                group="credentials",
                required=True,
                default="starttls",
                options=(
                    ("starttls", "STARTTLS"),
                    ("ssl", "Implicit TLS / SSL"),
                    ("none", "None (trusted internal relay only)"),
                ),
            ),
            FieldSpec(
                name="smtp_username",
                label="SMTP username",
                group="credentials",
                required=False,
                helper="Optional for trusted relays; commonly required by hosted providers.",
            ),
            FieldSpec(
                name="smtp_password",
                label="SMTP password",
                kind="secret",
                group="credentials",
                required=False,
                helper="Optional for trusted relays. Use an app password or provider SMTP credential where supported.",
            ),
            FieldSpec(
                name="from_email",
                label="From address",
                group="credentials",
                required=True,
                placeholder="opsmender@example.com",
            ),
            FieldSpec(
                name="default_chat_id",
                label="Default recipient",
                group="config",
                required=False,
                helper="Optional. Recipient email used for outbound notifications.",
                placeholder="oncall@example.com",
            ),
        ]

    def verify_webhook(
        self,
        connector: BotConnector,
        *,
        headers: Mapping[str, str],
        raw_body: bytes,
    ) -> None:
        raise HTTPException(
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
            detail="SMTP Email is outbound-only and does not accept callbacks",
        )

    def parse_inbound(self, payload: dict[str, Any]) -> InboundMessage | None:
        return None

    def inline_reply(self, chat_id: str, text: str) -> dict[str, Any] | None:
        return None

    @staticmethod
    def _send(connector: BotConnector, *, chat_id: str, text: str) -> None:
        credentials = connector.credentials or {}
        host = str(credentials.get("smtp_host") or "").strip()
        from_email = str(credentials.get("from_email") or "").strip()
        security_mode = str(credentials.get("security") or "starttls").lower()
        if security_mode not in {"starttls", "ssl", "none"}:
            raise ValueError("SMTP security must be starttls, ssl, or none")
        try:
            port = int(credentials.get("smtp_port") or 587)
        except (TypeError, ValueError) as exc:
            raise ValueError("SMTP port must be a number") from exc
        if not host or not from_email:
            raise ValueError("SMTP host and from address are required")

        message = EmailMessage()
        message["From"] = from_email
        message["To"] = chat_id
        message["Subject"] = "OpsMender Incident Update"
        message.set_content(text)

        client_type = smtplib.SMTP_SSL if security_mode == "ssl" else smtplib.SMTP
        with client_type(host, port, timeout=10) as client:
            if security_mode == "starttls":
                client.starttls()
            username = credentials.get("smtp_username")
            password = credentials.get("smtp_password")
            if username:
                client.login(str(username), str(password or ""))
            client.send_message(message)

    async def send_message(
        self,
        connector: BotConnector,
        *,
        chat_id: str,
        text: str,
    ) -> tuple[bool, str | None]:
        try:
            await asyncio.to_thread(self._send, connector, chat_id=chat_id, text=text)
        except (ValueError, OSError, smtplib.SMTPException) as exc:
            return False, str(exc)
        return True, None
