"""Weixin (WeChat Official Account) connector adapter."""

from __future__ import annotations

import base64
import hashlib
import json
import socket
import struct
import time
from typing import Any, Mapping
import xml.etree.ElementTree as ET

from fastapi import HTTPException, status
import httpx
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives import padding

from backend.db.models import BotConnector
from .base import BotConnectorAdapter, InboundMessage


class WeixinAdapter:
    """Adapter for Weixin (WeChat Official Account) webhooks."""

    platform = "weixin"

    def _get_signature(self, token: str, timestamp: str, nonce: str) -> str:
        v = sorted([token, timestamp, nonce])
        return hashlib.sha1("".join(v).encode("utf-8")).hexdigest()

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
