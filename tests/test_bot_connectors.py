"""Connector adapter / registry unit tests."""

from __future__ import annotations

import pytest
from fastapi import HTTPException

from backend.bots.connectors import TelegramAdapter, get_adapter
from backend.db.models import BotConnector


def _make_connector(
    *,
    platform: str = "telegram",
    is_enabled: bool = True,
    webhook_secret: str | None = "secret-xyz",
    bot_token: str | None = "BOT-TOKEN",
) -> BotConnector:
    creds: dict[str, str] = {}
    if webhook_secret is not None:
        creds["webhook_secret"] = webhook_secret
    if bot_token is not None:
        creds["bot_token"] = bot_token
    return BotConnector(
        name="test",
        platform=platform,
        config={},
        credentials=creds,
        allowed_capabilities=["incident_lookup"],
        status="configured",
        is_enabled=is_enabled,
    )


class TestRegistry:
    def test_telegram_adapter_registered(self):
        # Importing backend.bots registers built-in adapters.
        import backend.bots  # noqa: F401
        adapter = get_adapter("telegram")
        assert adapter is not None
        assert adapter.platform == "telegram"

    def test_unknown_platform_returns_none(self):
        import backend.bots  # noqa: F401
        assert get_adapter("does-not-exist") is None


class TestTelegramAdapter:
    def test_verify_rejects_wrong_platform(self):
        adapter = TelegramAdapter()
        connector = _make_connector(platform="signal")
        with pytest.raises(HTTPException) as exc:
            adapter.verify_webhook(connector, headers={}, raw_body=b"")
        assert exc.value.status_code == 400

    def test_verify_rejects_disabled_connector(self):
        adapter = TelegramAdapter()
        connector = _make_connector(is_enabled=False)
        with pytest.raises(HTTPException) as exc:
            adapter.verify_webhook(connector, headers={}, raw_body=b"")
        assert exc.value.status_code == 403

    def test_verify_rejects_missing_secret(self):
        adapter = TelegramAdapter()
        connector = _make_connector(webhook_secret=None)
        with pytest.raises(HTTPException):
            adapter.verify_webhook(connector, headers={}, raw_body=b"")

    def test_verify_rejects_wrong_secret(self):
        adapter = TelegramAdapter()
        connector = _make_connector()
        with pytest.raises(HTTPException) as exc:
            adapter.verify_webhook(
                connector,
                headers={"X-Telegram-Bot-Api-Secret-Token": "nope"},
                raw_body=b"",
            )
        assert exc.value.status_code == 403

    def test_verify_accepts_correct_secret(self):
        adapter = TelegramAdapter()
        connector = _make_connector()
        # Returns None on success
        adapter.verify_webhook(
            connector,
            headers={"X-Telegram-Bot-Api-Secret-Token": "secret-xyz"},
            raw_body=b"",
        )

    def test_parse_inbound_extracts_chat_user_text(self):
        adapter = TelegramAdapter()
        msg = adapter.parse_inbound(
            {
                "message": {
                    "from": {"id": 12345},
                    "chat": {"id": -100888},
                    "text": "  /incidents  ",
                }
            }
        )
        assert msg is not None
        assert msg.chat_id == "-100888"
        assert msg.platform_user_id == "12345"
        assert msg.text == "/incidents"

    def test_parse_inbound_returns_none_without_chat(self):
        adapter = TelegramAdapter()
        assert adapter.parse_inbound({}) is None

    def test_parse_inbound_handles_edited_message(self):
        adapter = TelegramAdapter()
        msg = adapter.parse_inbound(
            {
                "edited_message": {
                    "chat": {"id": -100777},
                    "text": "/help",
                }
            }
        )
        assert msg is not None
        assert msg.chat_id == "-100777"
        assert msg.platform_user_id is None
        assert msg.text == "/help"

    def test_inline_reply_returns_send_message_envelope(self):
        adapter = TelegramAdapter()
        reply = adapter.inline_reply("-100777", "hello")
        assert reply == {
            "method": "sendMessage",
            "chat_id": "-100777",
            "text": "hello",
            "parse_mode": "Markdown",
            "disable_web_page_preview": True,
        }
