"""Twilio (SMS) connector adapter."""

from __future__ import annotations

import base64
import hmac
import hashlib
from typing import Any, Mapping

from fastapi import HTTPException, status
import httpx

from backend.db.models import BotConnector
from .base import BotConnectorAdapter, InboundMessage


class TwilioAdapter:
    """Adapter for Twilio SMS."""

    platform = "twilio"

    def verify_webhook(
        self,
        connector: BotConnector,
        *,
        headers: Mapping[str, str],
        raw_body: bytes,
    ) -> None:
        if connector.platform != self.platform:
            raise HTTPException(status_code=400, detail="Not a Twilio connector")
            
        credentials = connector.credentials or {}
        auth_token = credentials.get("auth_token")
        if not auth_token:
            raise HTTPException(status_code=403, detail="Twilio auth token not configured")

        # Twilio signature verification: https://www.twilio.com/docs/usage/security#validating-requests
        signature = headers.get("x-twilio-signature") or headers.get("X-Twilio-Signature")
        if not signature:
             raise HTTPException(status_code=403, detail="Missing Twilio signature")

        # We need the full URL of the request
        # For simplicity, we assume the host is known or passed in config
        config = connector.config or {}
        webhook_url = config.get("webhook_url")
        if not webhook_url:
            # Fallback to reconstructing if possible, but Twilio needs the EXACT URL
            raise HTTPException(status_code=403, detail="Twilio webhook_url must be configured for signature validation")

        from urllib.parse import parse_qs
        params = parse_qs(raw_body.decode("utf-8"))
        
        # Twilio signature basestring: URL + sorted params
        basestring = webhook_url
        for key in sorted(params.keys()):
            basestring += key + params[key][0]

        mac = hmac.new(auth_token.encode("utf-8"), basestring.encode("utf-8"), hashlib.sha1)
        expected_sig = base64.b64encode(mac.digest()).decode("utf-8")

        if not hmac.compare_digest(expected_sig, signature):
            raise HTTPException(status_code=403, detail="Invalid Twilio signature")

    def parse_inbound(
        self,
        payload: dict[str, Any],
    ) -> InboundMessage | None:
        # Twilio sends form-encoded data
        chat_id = payload.get("From")
        text = payload.get("Body")
        
        if not chat_id or not text:
            return None
            
        return InboundMessage(
            chat_id=str(chat_id),
            platform_user_id=str(chat_id),
            text=text.strip(),
        )

    def inline_reply(
        self,
        chat_id: str,
        text: str,
    ) -> dict[str, Any] | None:
        # Twilio supports TwiML response
        return None

    async def send_message(
        self,
        connector: BotConnector,
        *,
        chat_id: str,
        text: str,
    ) -> tuple[bool, str | None]:
        credentials = connector.credentials or {}
        sid = credentials.get("account_sid")
        token = credentials.get("auth_token")
        from_number = credentials.get("phone_number")
        
        if not all([sid, token, from_number]):
            return False, "Twilio credentials (account_sid, auth_token, phone_number) not configured"
            
        auth = base64.b64encode(f"{sid}:{token}".encode("utf-8")).decode("utf-8")
        
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"https://api.twilio.com/2010-04-01/Accounts/{sid}/Messages.json",
                headers={"Authorization": f"Basic {auth}"},
                data={
                    "To": chat_id,
                    "From": from_number,
                    "Body": text,
                },
                timeout=10.0,
            )
            if resp.status_code not in (200, 201):
                return False, f"Twilio API error: HTTP {resp.status_code} - {resp.text}"
                
            return True, None
