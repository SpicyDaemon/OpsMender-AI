"""Weixin (WeChat Official Account) connector adapter."""

from __future__ import annotations

import base64
import hashlib
import json
import socket
import struct
import time
from typing import Any, Mapping
import defusedxml.ElementTree as ET

from fastapi import HTTPException, status
import httpx
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives import padding

from backend.db.models import BotConnector
from .base import BotConnectorAdapter, FieldSpec, InboundMessage


class WeixinAdapter:
    """Adapter for Weixin (WeChat Official Account) webhooks."""

    platform = "weixin"

    @classmethod
    def form_schema(cls) -> list[FieldSpec]:
        return [
            FieldSpec(
                name="token",
                label="Callback token",
                kind="secret",
                group="credentials",
                required=True,
                helper="Set in the WeChat Official Account admin → Basic Configuration → Server Config.",
                doc_url="https://developers.weixin.qq.com/doc/offiaccount/Basic_Information/Access_Overview.html",
            ),
            FieldSpec(
                name="appid",
                label="AppID",
                kind="text",
                group="credentials",
                required=True,
                helper="Public account AppID.",
            ),
            FieldSpec(
                name="appsecret",
                label="AppSecret",
                kind="secret",
                group="credentials",
                required=True,
                helper="Public account AppSecret. Used to obtain access tokens for outbound replies.",
            ),
            FieldSpec(
                name="default_chat_id",
                label="Default recipient OpenID",
                kind="text",
                group="config",
                required=False,
                helper="Optional. OpenID of the recipient for outbound customer-service messages.",
            ),
        ]

    def _get_signature(self, token: str, timestamp: str, nonce: str) -> str:
        v = sorted([token, timestamp, nonce])
        # SHA-1 is mandated by the WeChat (Weixin) callback signature spec; it is
        # not a security-sensitive digest of our choosing.
        return hashlib.sha1(  # noqa: S324
            "".join(v).encode("utf-8"), usedforsecurity=False
        ).hexdigest()

    def verify_webhook(
        self,
        connector: BotConnector,
        *,
        headers: Mapping[str, str],
        raw_body: bytes,
    ) -> None:
        # For Weixin, signature verification is done on the query parameters
        # during the handshake (GET) or on every update (POST).
        pass

    def handle_handshake(self, connector: BotConnector, params: Mapping[str, str]) -> str:
        credentials = connector.credentials or {}
        token = credentials.get("token")
        
        signature = params.get("signature")
        timestamp = params.get("timestamp")
        nonce = params.get("nonce")
        echostr = params.get("echostr")
        
        if not all([signature, timestamp, nonce, echostr]):
            raise HTTPException(status_code=400, detail="Missing handshake params")
            
        expected_sig = self._get_signature(token, timestamp, nonce)
        if signature != expected_sig:
            raise HTTPException(status_code=403, detail="Invalid Weixin signature")
            
        return echostr

    def parse_inbound(
        self,
        payload: dict[str, Any],
    ) -> InboundMessage | None:
        # Weixin sends XML.
        xml_content = payload.get("_xml_content")
        if not xml_content:
            return None
            
        root = ET.fromstring(xml_content)
        msg_type = root.findtext("MsgType")
        if msg_type != "text":
            return None
            
        chat_id = root.findtext("FromUserName")
        text = root.findtext("Content")
        
        if not chat_id or not text:
            return None
            
        return InboundMessage(
            chat_id=str(chat_id),
            platform_user_id=str(chat_id),
            text=text.strip(),
        )

    async def _get_access_token(self, appid: str, appsecret: str) -> str | None:
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                "https://api.weixin.qq.com/cgi-bin/token",
                params={"grant_type": "client_credential", "appid": appid, "secret": appsecret},
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
        credentials = connector.credentials or {}
        appid = credentials.get("appid")
        appsecret = credentials.get("appsecret")
        
        if not all([appid, appsecret]):
            return False, "Weixin credentials (appid, appsecret) not configured"
            
        token = await self._get_access_token(str(appid), str(appsecret))
        if not token:
            return False, "Failed to obtain Weixin access token"
            
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                "https://api.weixin.qq.com/cgi-bin/message/custom/send",
                params={"access_token": token},
                json={
                    "touser": chat_id,
                    "msgtype": "text",
                    "text": {"content": text},
                },
                timeout=10.0,
            )
            if resp.status_code != 200:
                return False, f"Weixin API error: HTTP {resp.status_code}"
            
            data = resp.json()
            if data.get("errcode") != 0:
                return False, f"Weixin API error: {data.get('errmsg')}"
                
            return True, None
