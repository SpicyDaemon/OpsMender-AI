"""Tests for chat bot platform adapters."""

from __future__ import annotations

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
