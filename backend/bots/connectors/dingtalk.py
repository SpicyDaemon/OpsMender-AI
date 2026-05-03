"""DingTalk connector adapter."""

from __future__ import annotations

import base64
import hmac
import hashlib
import json
import time
from typing import Any, Mapping

from fastapi import HTTPException, status
import httpx

from backend.db.models import BotConnector
from .base import BotConnectorAdapter, InboundMessage


class DingTalkAdapter:
    """Adapter for DingTalk Outgoing Bots."""

    platform = "dingtalk"

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
                detail="Connector is not a DingTalk connector",
            )
        if not connector.is_enabled:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Connector is disabled",
            )

        credentials = connector.credentials or {}
        app_secret = credentials.get("app_secret")
        if not app_secret:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="DingTalk app secret is not configured",
            )

        # DingTalk signature verification: https://open.dingtalk.com/document/robots/customize-robot-security-settings
        timestamp = headers.get("timestamp") or headers.get("Timestamp")
        sign = headers.get("sign") or headers.get("Sign")

        if not timestamp or not sign:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Missing DingTalk signature headers",
            )

        # Check for replay attacks (5 minute window)
        if abs(time.time() * 1000 - int(timestamp)) > 60 * 5 * 1000:
             raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="DingTalk request timestamp is too old",
            )

        string_to_sign = f"{timestamp}\n{app_secret}"
        hmac_code = hmac.new(
            app_secret.encode("utf-8"),
            string_to_sign.encode("utf-8"),
            digestmod=hashlib.sha256
        ).digest()
        my_sign = base64.b64encode(hmac_code).decode("utf-8")

        if not hmac.compare_digest(my_sign, sign):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Invalid DingTalk signature",
            )

    def parse_inbound(
        self,
        payload: dict[str, Any],
    ) -> InboundMessage | None:
        msg_type = payload.get("msgtype")
        if msg_type != "text":
            return None

        chat_id = payload.get("conversationId")
        user_id = payload.get("senderId")
        text = (payload.get("text") or {}).get("content")

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
        # DingTalk supports returning a JSON response to the outgoing webhook
        return {
            "msgtype": "text",
            "text": {
                "content": text
            }
        }

    async def _get_access_token(self, app_key: str, app_secret: str) -> str | None:
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                "https://oapi.dingtalk.com/gettoken",
                params={
                    "appkey": app_key,
                    "appsecret": app_secret,
                },
                timeout=10.0,
            )
            if resp.status_code == 200:
                return resp.json().get("access_token")
        return None

    async def send_message(
        self,
        connector: BotConnector,
        *,
        chat_id: str,
        text: str,
    ) -> tuple[bool, str | None]:
        # DingTalk can use Webhooks for outbound or the API.
        # For simplicity and to support "Push", we use the Robot Webhook if provided in config,
        # or fall back to the Robot message API.
        
        config = connector.config or {}
        webhook_url = config.get("webhook_url")
        if webhook_url:
            async with httpx.AsyncClient() as client:
                resp = await client.post(
                    webhook_url,
                    json={
                        "msgtype": "text",
                        "text": {"content": text}
                    },
                    timeout=10.0,
                )
                if resp.status_code == 200:
                    return True, None
                return False, f"DingTalk Webhook error: HTTP {resp.status_code}"

        # Fallback to API delivery
        credentials = connector.credentials or {}
        app_key = credentials.get("app_key")
        app_secret = credentials.get("app_secret")
        if not app_key or not app_secret:
            return False, "DingTalk credentials (app_key, app_secret) or webhook_url not configured"

        token = await self._get_access_token(str(app_key), str(app_secret))
        if not token:
            return False, "Failed to obtain DingTalk access token"

        async with httpx.AsyncClient() as client:
            # Note: This is a simplified example of the Robot message API
            # In a real app, you might need to use the specific conversation API
            resp = await client.post(
                "https://oapi.dingtalk.com/robot/send",
                params={"access_token": token},
                json={
                    "msgtype": "text",
                    "text": {"content": text}
                },
                timeout=10.0,
            )
            if resp.status_code == 200:
                return True, None
            return False, f"DingTalk API error: HTTP {resp.status_code}"
