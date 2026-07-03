"""WeCom (WeChat Work) connector adapter."""

from __future__ import annotations

import base64
import hashlib
import struct
from typing import Any, Mapping
import defusedxml.ElementTree as ET

from fastapi import HTTPException
import httpx
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives import padding

from backend.db.models import BotConnector
from .base import FieldSpec, InboundMessage


class WeComAdapter:
    """Adapter for WeCom (WeChat Work) webhooks."""

    platform = "wecom"

    @classmethod
    def form_schema(cls) -> list[FieldSpec]:
        return [
            FieldSpec(
                name="token",
                label="Callback token",
                kind="secret",
                group="credentials",
                required=True,
                helper="Token set in your WeCom self-built app → Receive Messages config.",
                doc_url="https://developer.work.weixin.qq.com/document/path/90930",
            ),
            FieldSpec(
                name="encoding_aes_key",
                label="EncodingAESKey",
                kind="secret",
                group="credentials",
                required=True,
                helper="43-character AES key from the same config page. Used to decrypt callback bodies.",
            ),
            FieldSpec(
                name="corpid",
                label="Corp ID",
                kind="text",
                group="credentials",
                required=True,
                helper="Enterprise ID (CorpID) from WeCom admin console.",
            ),
            FieldSpec(
                name="corpsecret",
                label="Corp secret",
                kind="secret",
                group="credentials",
                required=True,
                helper="Secret for the self-built app. Used to obtain access tokens for outbound messages.",
            ),
            FieldSpec(
                name="agentid",
                label="Agent ID",
                kind="text",
                group="credentials",
                required=True,
                helper="Numeric AgentID of the self-built app.",
            ),
            FieldSpec(
                name="default_chat_id",
                label="Default recipient (touser)",
                kind="text",
                group="config",
                required=False,
                helper="Optional. UserID or @all for outbound notifications.",
            ),
        ]

    def _get_signature(self, token: str, timestamp: str, nonce: str, msg_encrypt: str) -> str:
        v = sorted([token, timestamp, nonce, msg_encrypt])
        # SHA-1 is mandated by the WeCom callback signature spec; it is not a
        # security-sensitive digest of our choosing.
        return hashlib.sha1(  # noqa: S324
            "".join(v).encode("utf-8"), usedforsecurity=False
        ).hexdigest()

    def _decrypt(self, aes_key: str, msg_encrypt: str) -> str:
        key = base64.b64decode(aes_key + "=")
        iv = key[:16]
        
        cipher = Cipher(algorithms.AES(key), modes.CBC(iv))
        decryptor = cipher.decryptor()
        
        raw_data = base64.b64decode(msg_encrypt)
        decrypted = decryptor.update(raw_data) + decryptor.finalize()
        
        # Remove PKCS7 padding
        unpadder = padding.PKCS7(128).unpadder()
        decrypted = unpadder.update(decrypted) + unpadder.finalize()
        
        # The structure of decrypted data is:
        # random(16B) + msg_len(4B) + msg + corpid
        msg_len = struct.unpack(">I", decrypted[16:20])[0]
        msg = decrypted[20 : 20 + msg_len].decode("utf-8")
        return msg

    def verify_webhook(
        self,
        connector: BotConnector,
        *,
        headers: Mapping[str, str],
        raw_body: bytes,
    ) -> None:
        if connector.platform != self.platform:
            raise HTTPException(status_code=400, detail="Not a WeCom connector")
        
        credentials = connector.credentials or {}
        token = credentials.get("token")
        if not token:
            raise HTTPException(status_code=403, detail="WeCom token not configured")

        # Note: WeCom handshake is a GET request, while updates are POST
        # This verify_webhook is called for both.
        # Handshake params: msg_signature, timestamp, nonce, echostr
        # Update params: msg_signature, timestamp, nonce
    
    def handle_handshake(self, connector: BotConnector, params: Mapping[str, str]) -> str:
        credentials = connector.credentials or {}
        token = credentials.get("token")
        aes_key = credentials.get("encoding_aes_key")
        
        signature = params.get("msg_signature")
        timestamp = params.get("timestamp")
        nonce = params.get("nonce")
        echostr = params.get("echostr")
        
        if not all([signature, timestamp, nonce, echostr]):
            raise HTTPException(status_code=400, detail="Missing handshake params")
            
        expected_sig = self._get_signature(token, timestamp, nonce, echostr)
        if signature != expected_sig:
            raise HTTPException(status_code=403, detail="Invalid WeCom signature")
            
        return self._decrypt(aes_key, echostr)

    def parse_inbound(
        self,
        payload: dict[str, Any],
    ) -> InboundMessage | None:
        # WeCom sends XML in the body. The 'payload' passed here might be 
        # the parsed XML if the route handles it, or the raw dict if JSON.
        # But WeCom is always XML.
        
        # We assume the route has passed the decrypted XML content as a string
        # or we need to decrypt it here.
        xml_content = payload.get("_xml_content")
        if not xml_content:
            return None
            
        root = ET.fromstring(xml_content)
        msg_type = root.findtext("MsgType")
        if msg_type != "text":
            return None
            
        chat_id = root.findtext("FromUserName") # In WeCom, FromUserName is the UserID
        text = root.findtext("Content")
        
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
        # WeCom supports returning an XML response to the webhook
        # but it's complex to format here. We'll use outbound API.
        return None

    async def _get_access_token(self, corpid: str, corpsecret: str) -> str | None:
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                "https://qyapi.weixin.qq.com/cgi-bin/gettoken",
                params={"corpid": corpid, "corpsecret": corpsecret},
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
        corpid = credentials.get("corpid")
        corpsecret = credentials.get("corpsecret")
        agentid = credentials.get("agentid")
        
        if not all([corpid, corpsecret, agentid]):
            return False, "WeCom credentials (corpid, corpsecret, agentid) not configured"
            
        token = await self._get_access_token(str(corpid), str(corpsecret))
        if not token:
            return False, "Failed to obtain WeCom access token"
            
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                "https://qyapi.weixin.qq.com/cgi-bin/message/send",
                params={"access_token": token},
                json={
                    "touser": chat_id,
                    "msgtype": "text",
                    "agentid": agentid,
                    "text": {"content": text},
                    "safe": 0,
                },
                timeout=10.0,
            )
            if resp.status_code != 200:
                return False, f"WeCom API error: HTTP {resp.status_code}"
            
            data = resp.json()
            if data.get("errcode") != 0:
                return False, f"WeCom API error: {data.get('errmsg')}"
                
            return True, None
