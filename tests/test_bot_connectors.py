"""Connector adapter / registry unit tests."""

from __future__ import annotations

import pytest
from fastapi import HTTPException

from backend.bots.connectors import SignalAdapter, TelegramAdapter, get_adapter
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


def _make_signal_connector(
    *,
    is_enabled: bool = True,
    webhook_secret: str | None = "sig-secret",
    service_url: str | None = "http://signal-bridge:8080",
    bot_number: str | None = "+15555550100",
) -> BotConnector:
    creds: dict[str, str] = {}
    if webhook_secret is not None:
        creds["webhook_secret"] = webhook_secret
    if service_url is not None:
        creds["service_url"] = service_url
    if bot_number is not None:
        creds["bot_number"] = bot_number
    return BotConnector(
        name="signal-test",
        platform="signal",
        config={},
        credentials=creds,
        allowed_capabilities=["incident_lookup"],
        status="configured",
        is_enabled=is_enabled,
    )


class TestSignalAdapter:

    def test_registered(self):
        import backend.bots  # noqa: F401
        assert get_adapter("signal") is not None

    def test_verify_rejects_wrong_platform(self):
        adapter = SignalAdapter()
        connector = _make_signal_connector()
        connector.platform = "telegram"
        with pytest.raises(HTTPException) as exc:
            adapter.verify_webhook(connector, headers={}, raw_body=b"")
        assert exc.value.status_code == 400

    def test_verify_rejects_disabled(self):
        adapter = SignalAdapter()
        connector = _make_signal_connector(is_enabled=False)
        with pytest.raises(HTTPException) as exc:
            adapter.verify_webhook(connector, headers={}, raw_body=b"")
        assert exc.value.status_code == 403

    def test_verify_rejects_missing_secret_header(self):
        adapter = SignalAdapter()
        connector = _make_signal_connector()
        with pytest.raises(HTTPException) as exc:
            adapter.verify_webhook(connector, headers={}, raw_body=b"")
        assert exc.value.status_code == 403

    def test_verify_accepts_correct_secret(self):
        adapter = SignalAdapter()
        connector = _make_signal_connector()
        adapter.verify_webhook(
            connector,
            headers={"X-AIM-Webhook-Secret": "sig-secret"},
            raw_body=b"",
        )

    def test_parse_inbound_group_message(self):
        adapter = SignalAdapter()
        msg = adapter.parse_inbound(
            {
                "envelope": {
                    "source": "+15555550111",
                    "dataMessage": {
                        "message": "  /incidents  ",
                        "groupInfo": {"groupId": "GROUP-XYZ"},
                    },
                }
            }
        )
        assert msg is not None
        assert msg.chat_id == "GROUP-XYZ"
        assert msg.platform_user_id == "+15555550111"
        assert msg.text == "/incidents"

    def test_parse_inbound_one_to_one_uses_source_as_chat(self):
        adapter = SignalAdapter()
        msg = adapter.parse_inbound(
            {
                "envelope": {
                    "source": "+15555550111",
                    "dataMessage": {"message": "/help"},
                }
            }
        )
        assert msg is not None
        assert msg.chat_id == "+15555550111"
        assert msg.platform_user_id == "+15555550111"

    def test_parse_inbound_returns_none_without_data_message(self):
        adapter = SignalAdapter()
        assert adapter.parse_inbound({"envelope": {"source": "+1"}}) is None

    def test_inline_reply_is_none(self):
        adapter = SignalAdapter()
        assert adapter.inline_reply("group", "hi") is None

    async def test_send_message_calls_signal_bridge(self, monkeypatch):
        from backend.bots.connectors import signal as signal_mod

        captured: dict = {}

        async def fake_send(*, service_url, bot_number, chat_id, text, timeout_seconds=10.0):
            captured.update(
                service_url=service_url,
                bot_number=bot_number,
                chat_id=chat_id,
                text=text,
            )
            return True, None

        monkeypatch.setattr(signal_mod, "signal_send", fake_send)

        adapter = SignalAdapter()
        connector = _make_signal_connector()
        ok, err = await adapter.send_message(
            connector, chat_id="GROUP-XYZ", text="hello"
        )
        assert ok is True
        assert err is None
        assert captured["service_url"] == "http://signal-bridge:8080"
        assert captured["bot_number"] == "+15555550100"
        assert captured["chat_id"] == "GROUP-XYZ"
        assert captured["text"] == "hello"
