"""Tests for chat bot platform adapters."""

from __future__ import annotations

import base64
import hmac
import hashlib
import json
import time
import uuid
from typing import Any

import pytest
from fastapi import HTTPException
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from backend.bots.connectors.slack import SlackAdapter
from backend.bots.connectors.discord import DiscordAdapter
from backend.bots.connectors.mattermost import MattermostAdapter
from backend.bots.connectors.matrix import MatrixAdapter
from backend.bots.connectors.feishu import FeishuAdapter
from backend.bots.connectors.dingtalk import DingTalkAdapter
from backend.db.models import BotConnector


def test_slack_verify_webhook():
    adapter = SlackAdapter()
    secret = "secret123"
    connector = BotConnector(
        platform="slack",
        is_enabled=True,
        credentials={"signing_secret": secret}
    )
    
    body = b'{"type": "url_verification", "challenge": "abc"}'
    timestamp = str(int(time.time()))
    
    sig_basestring = f"v0:{timestamp}:{body.decode('utf-8')}"
    signature = "v0=" + hmac.new(
        secret.encode("utf-8"),
        sig_basestring.encode("utf-8"),
        hashlib.sha256
    ).hexdigest()
    
    headers = {
        "x-slack-request-timestamp": timestamp,
        "x-slack-signature": signature
    }
    
    # Should not raise
    adapter.verify_webhook(connector, headers=headers, raw_body=body)
    
    # Test failure
    with pytest.raises(HTTPException) as exc:
        adapter.verify_webhook(connector, headers={"x-slack-signature": "wrong"}, raw_body=body)
    assert exc.value.status_code == 403


def test_slack_parse_inbound():
    adapter = SlackAdapter()
    payload = {
        "type": "event_callback",
        "event": {
            "type": "message",
            "user": "U123",
            "text": " /incidents ",
            "channel": "C456"
        }
    }
    msg = adapter.parse_inbound(payload)
    assert msg.chat_id == "C456"
    assert msg.platform_user_id == "U123"
    assert msg.text == "/incidents"
    
    # Test bot message skip
    payload["event"]["bot_id"] = "B123"
    assert adapter.parse_inbound(payload) is None


def test_discord_verify_webhook():
    adapter = DiscordAdapter()
    
    # Generate a real Ed25519 key pair for testing
    private_key = Ed25519PrivateKey.generate()
    public_key = private_key.public_key()
    public_key_hex = public_key.public_bytes_raw().hex()
    
    connector = BotConnector(
        platform="discord",
        is_enabled=True,
        credentials={"public_key": public_key_hex}
    )
    
    body = b'{"type": 1}'
    timestamp = str(int(time.time()))
    
    msg = timestamp.encode("utf-8") + body
    signature = private_key.sign(msg).hex()
    
    headers = {
        "x-signature-ed25519": signature,
        "x-signature-timestamp": timestamp
    }
    
    # Should not raise
    adapter.verify_webhook(connector, headers=headers, raw_body=body)
    
    # Test failure
    with pytest.raises(HTTPException) as exc:
        adapter.verify_webhook(connector, headers={"x-signature-ed25519": "wrong"}, raw_body=body)
    assert exc.value.status_code == 401


def test_discord_parse_inbound():
    adapter = DiscordAdapter()
    payload = {
        "type": 2, # APPLICATION_COMMAND
        "channel_id": "C789",
        "member": {
            "user": {"id": "D123"}
        },
        "data": {
            "name": "incident",
            "options": [
                {"name": "id", "value": "uuid-abc"}
            ]
        }
    }
    msg = adapter.parse_inbound(payload)
    assert msg.chat_id == "C789"
    assert msg.platform_user_id == "D123"
    assert msg.text == "/incident uuid-abc"
    
    # Test PING skip
    assert adapter.parse_inbound({"type": 1}) is None


def test_mattermost_verify_webhook():
    adapter = MattermostAdapter()
    token = "mattertoken123"
    connector = BotConnector(
        platform="mattermost",
        is_enabled=True,
        credentials={"webhook_token": token}
    )
    
    body = f"token={token}&channel_id=C1&text=hi".encode("utf-8")
    
    # Should not raise
    adapter.verify_webhook(connector, headers={}, raw_body=body)
    
    # Test failure
    with pytest.raises(HTTPException) as exc:
        adapter.verify_webhook(connector, headers={}, raw_body=b"token=wrong")
    assert exc.value.status_code == 403


def test_mattermost_parse_inbound():
    adapter = MattermostAdapter()
    payload = {
        "channel_id": "C123",
        "user_id": "U456",
        "text": "/incidents",
        "user_name": "alice"
    }
    msg = adapter.parse_inbound(payload)
    assert msg.chat_id == "C123"
    assert msg.platform_user_id == "U456"
    assert msg.text == "/incidents"


def test_matrix_verify_webhook():
    adapter = MatrixAdapter()
    secret = "matrixsecret"
    connector = BotConnector(
        platform="matrix",
        is_enabled=True,
        credentials={"webhook_secret": secret}
    )
    
    headers = {"Authorization": f"Bearer {secret}"}
    
    # Should not raise
    adapter.verify_webhook(connector, headers=headers, raw_body=b"{}")
    
    # Test failure
    with pytest.raises(HTTPException) as exc:
        adapter.verify_webhook(connector, headers={"Authorization": "Bearer wrong"}, raw_body=b"{}")
    assert exc.value.status_code == 403


def test_matrix_parse_inbound():
    adapter = MatrixAdapter()
    payload = {
        "events": [
            {
                "type": "m.room.message",
                "room_id": "!room:matrix.org",
                "sender": "@user:matrix.org",
                "content": {
                    "msgtype": "m.text",
                    "body": "/approvals"
                }
            }
        ]
    }
    msg = adapter.parse_inbound(payload)
    assert msg.chat_id == "!room:matrix.org"
    assert msg.platform_user_id == "@user:matrix.org"
    assert msg.text == "/approvals"


def test_feishu_verify_webhook():
    adapter = FeishuAdapter()
    token = "feishutoken"
    connector = BotConnector(
        platform="feishu",
        is_enabled=True,
        credentials={"verification_token": token}
    )
    
    body = json.dumps({"token": token, "type": "url_verification"}).encode("utf-8")
    
    # Should not raise
    adapter.verify_webhook(connector, headers={}, raw_body=body)
    
    # Test failure
    with pytest.raises(HTTPException) as exc:
        adapter.verify_webhook(connector, headers={}, raw_body=json.dumps({"token": "wrong"}).encode("utf-8"))
    assert exc.value.status_code == 403


def test_feishu_parse_inbound():
    adapter = FeishuAdapter()
    payload = {
        "header": {"event_type": "im.message.receive_v1"},
        "event": {
            "message": {
                "chat_id": "oc_123",
                "message_type": "text",
                "content": json.dumps({"text": "/session abc"})
            },
            "sender": {
                "sender_id": {"open_id": "ou_456"}
            }
        }
    }
    msg = adapter.parse_inbound(payload)
    assert msg.chat_id == "oc_123"
    assert msg.platform_user_id == "ou_456"
    assert msg.text == "/session abc"


def test_dingtalk_verify_webhook():
    adapter = DingTalkAdapter()
    secret = "dingsecret"
    connector = BotConnector(
        platform="dingtalk",
        is_enabled=True,
        credentials={"app_secret": secret}
    )
    
    timestamp = str(int(time.time() * 1000))
    string_to_sign = f"{timestamp}\n{secret}"
    hmac_code = hmac.new(
        secret.encode("utf-8"),
        string_to_sign.encode("utf-8"),
        digestmod=hashlib.sha256
    ).digest()
    sign = base64.b64encode(hmac_code).decode("utf-8")
    
    headers = {"timestamp": timestamp, "sign": sign}
    
    # Should not raise
    adapter.verify_webhook(connector, headers=headers, raw_body=b"{}")
    
    # Test failure
    with pytest.raises(HTTPException) as exc:
        adapter.verify_webhook(connector, headers={"timestamp": timestamp, "sign": "wrong"}, raw_body=b"{}")
    assert exc.value.status_code == 403


def test_dingtalk_parse_inbound():
    adapter = DingTalkAdapter()
    payload = {
        "msgtype": "text",
        "conversationId": "conv123",
        "senderId": "user789",
        "text": {"content": " /incident def "}
    }
    msg = adapter.parse_inbound(payload)
    assert msg.chat_id == "conv123"
    assert msg.platform_user_id == "user789"
    assert msg.text == "/incident def"
