"""Feishu / Lark connector adapter."""

from __future__ import annotations

import secrets
from typing import Any, Mapping

from fastapi import HTTPException, status
import httpx

from backend.db.models import BotConnector
from .base import BotConnectorAdapter, InboundMessage


class FeishuAdapter:
    """Adapter for Feishu / Lark Events."""

    platform = "feishu"

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
                detail="Connector is not a Feishu connector",
            )
        if not connector.is_enabled:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Connector is disabled",
            )

        # Feishu sends a token in the payload for verification
        # Note: We also support HMAC verification for higher security in production
        # but the token is the standard baseline.
        
        try:
            payload = httpx.Response(status_code=200, content=raw_body).json()
        except Exception:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid JSON payload",
            )

        credentials = connector.credentials or {}
        expected_token = credentials.get("verification_token")
        
        # Handle Feishu's nested header/token structure
        provided = payload.get("token") or (payload.get("header") or {}).get("token")

        if not expected_token or not provided or not secrets.compare_digest(str(expected_token), provided):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Invalid Feishu verification token",
            )

    def parse_inbound(
        self,
        payload: dict[str, Any],
    ) -> InboundMessage | None:
        # Handle URL verification challenge
        if payload.get("type") == "url_verification":
            return None

        header = payload.get("header") or {}
        event = payload.get("event") or {}
        
        event_type = header.get("event_type") or payload.get("type")
        
        # We handle im.message.receive_v1
        if event_type != "im.message.receive_v1":
            return None

        message = event.get("message") or {}
        if message.get("message_type") != "text":
            return None

        chat_id = message.get("chat_id")
        sender = event.get("sender") or {}
        user_id = sender.get("sender_id", {}).get("open_id")
        
        content_raw = message.get("content")
        try:
            import json
            content = json.loads(content_raw)
            text = content.get("text", "")
        except Exception:
            text = content_raw or ""

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
        # Feishu doesn't typically use inline replies in the webhook response for v2 events
        return None

    async def _get_tenant_access_token(self, app_id: str, app_secret: str) -> str | None:
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
                json={
                    "app_id": app_id,
                    "app_secret": app_secret,
                },
                timeout=10.0,
            )
            if resp.status_code == 200:
                return resp.json().get("tenant_access_token")
        return None

    async def send_message(
        self,
        connector: BotConnector,
        *,
        chat_id: str,
        text: str,
    ) -> tuple[bool, str | None]:
        credentials = connector.credentials or {}
        app_id = credentials.get("app_id")
        app_secret = credentials.get("app_secret")
        if not app_id or not app_secret:
            return False, "Feishu credentials (app_id, app_secret) not configured"

        token = await self._get_tenant_access_token(str(app_id), str(app_secret))
        if not token:
            return False, "Failed to obtain Feishu tenant access token"

        async with httpx.AsyncClient() as client:
            resp = await client.post(
                "https://open.feishu.cn/open-apis/im/v1/messages?receive_id_type=chat_id",
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json; charset=utf-8",
                },
                json={
                    "receive_id": chat_id,
                    "msg_type": "text",
                    "content": json.dumps({"text": text}),
                },
                timeout=10.0,
            )
            if resp.status_code != 200:
                return False, f"Feishu API error: HTTP {resp.status_code} - {resp.text}"
            
            data = resp.json()
            if data.get("code") != 0:
                return False, f"Feishu API error: {data.get('msg')}"
            
            return True, None
