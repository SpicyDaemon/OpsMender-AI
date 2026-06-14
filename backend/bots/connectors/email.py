"""Mailgun Email connector adapter."""

from __future__ import annotations

import hmac
import hashlib
from typing import Any, Mapping

from fastapi import HTTPException, status
import httpx

from backend.db.models import BotConnector
from .base import BotConnectorAdapter, FieldSpec, InboundMessage


class EmailAdapter:
    """Adapter for email delivery and inbound routes through Mailgun."""

    platform = "email"

    @classmethod
    def form_schema(cls) -> list[FieldSpec]:
        return [
            FieldSpec(
                name="mailgun_api_key",
                label="Mailgun API key",
                kind="secret",
                group="credentials",
                required=True,
                helper="Used both to verify inbound webhook signatures and to send outbound email.",
                doc_url="https://documentation.mailgun.com/en/latest/api-intro.html#authentication",
            ),
            FieldSpec(
                name="mailgun_domain",
                label="Mailgun sending domain",
                kind="text",
                group="credentials",
                required=True,
                helper="Domain configured in Mailgun (e.g. mg.example.com).",
                placeholder="mg.example.com",
            ),
            FieldSpec(
                name="from_email",
                label="From address",
                kind="text",
                group="credentials",
                required=False,
                helper="Optional. Defaults to bot@<mailgun_domain>.",
            ),
            FieldSpec(
                name="default_chat_id",
                label="Default recipient",
                kind="text",
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
        if connector.platform != self.platform:
            raise HTTPException(status_code=400, detail="Not a Mailgun Email connector")
        if not connector.is_enabled:
            raise HTTPException(status_code=403, detail="Connector is disabled")

        credentials = connector.credentials or {}
        api_key = credentials.get("mailgun_api_key")
        if not api_key:
            raise HTTPException(
                status_code=403,
                detail="Mailgun API key is not configured",
            )

        # Mailgun signature verification: https://documentation.mailgun.com/en/latest/user_manual.html#webhooks
        # Mailgun sends: timestamp, token, signature in the JSON body or form
        try:
            payload = httpx.Response(status_code=200, content=raw_body).json()
            signature_data = payload.get("signature") or {}
            timestamp = signature_data.get("timestamp")
            token = signature_data.get("token")
            signature = signature_data.get("signature")
        except Exception:
            # Might be form-encoded
            from urllib.parse import parse_qs
            params = parse_qs(raw_body.decode("utf-8"))
            timestamp = params.get("timestamp", [None])[0]
            token = params.get("token", [None])[0]
            signature = params.get("signature", [None])[0]

        if not all([timestamp, token, signature]):
            raise HTTPException(
                status_code=401,
                detail="Missing Mailgun webhook signature",
            )

        hmac_digest = hmac.new(
            api_key.encode("utf-8"),
            (str(timestamp) + str(token)).encode("utf-8"),
            hashlib.sha256
        ).hexdigest()

        if not hmac.compare_digest(str(signature), hmac_digest):
            raise HTTPException(status_code=403, detail="Invalid Mailgun signature")

    def parse_inbound(
        self,
        payload: dict[str, Any],
    ) -> InboundMessage | None:
        # Mailgun 'Routes' send a POST with: sender, subject, stripped-text, etc.
        sender = payload.get("sender") or payload.get("from")
        text = payload.get("stripped-text") or payload.get("body-plain")
        subject = payload.get("subject", "")
        
        if not sender or not text:
            return None
            
        # Reconstruct message: Subject + Body
        full_text = f"Subject: {subject}\n\n{text}" if subject else text
            
        return InboundMessage(
            chat_id=str(sender),
            platform_user_id=str(sender),
            text=full_text.strip(),
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
        api_key = credentials.get("mailgun_api_key")
        domain = credentials.get("mailgun_domain")
        from_email = credentials.get("from_email") or f"bot@{domain}"
        
        if not api_key or not domain:
            return False, "Mailgun credentials (api_key, domain) not configured"
            
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"https://api.mailgun.net/v3/{domain}/messages",
                auth=("api", api_key),
                data={
                    "from": from_email,
                    "to": chat_id,
                    "subject": "OpsMender Incident Update",
                    "text": text,
                },
                timeout=10.0,
            )
            if resp.status_code != 200:
                return False, f"Mailgun API error: HTTP {resp.status_code} - {resp.text}"
                
            return True, None
