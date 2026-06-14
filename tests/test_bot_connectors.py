"""Connector adapter / registry unit tests."""

from __future__ import annotations

import hashlib
import hmac
import json
from types import SimpleNamespace

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
            headers={"X-OpsMender-Webhook-Secret": "sig-secret"},
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

        async def fake_send(
            *, service_url, bot_number, chat_id, text, timeout_seconds=10.0
        ):
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


# ---------------------------------------------------------------------------
# WhatsApp adapter
# ---------------------------------------------------------------------------


def _make_whatsapp_connector(
    *,
    is_enabled: bool = True,
    app_secret: str | None = "wa-app-secret",
    access_token: str | None = "WA-ACCESS-TOKEN",
    phone_number_id: str | None = "123456789",
    verify_token: str | None = "my-verify-token",
) -> BotConnector:
    creds: dict[str, str] = {}
    if app_secret is not None:
        creds["app_secret"] = app_secret
    if access_token is not None:
        creds["access_token"] = access_token
    if phone_number_id is not None:
        creds["phone_number_id"] = phone_number_id
    if verify_token is not None:
        creds["verify_token"] = verify_token
    return BotConnector(
        name="whatsapp-test",
        platform="whatsapp",
        config={},
        credentials=creds,
        allowed_capabilities=["incident_lookup"],
        status="configured",
        is_enabled=is_enabled,
    )


def _whatsapp_signature(secret: str, body: bytes) -> str:
    """Compute the X-Hub-Signature-256 value Meta would send."""
    import hashlib
    import hmac as _hmac

    digest = _hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


class TestWhatsAppAdapter:
    def test_registered(self):
        from backend.bots.connectors import WhatsAppAdapter
        import backend.bots  # noqa: F401

        adapter = get_adapter("whatsapp")
        assert adapter is not None
        assert adapter.platform == "whatsapp"

    def test_verify_rejects_wrong_platform(self):
        from backend.bots.connectors import WhatsAppAdapter

        adapter = WhatsAppAdapter()
        connector = _make_whatsapp_connector()
        connector.platform = "telegram"
        with pytest.raises(HTTPException) as exc:
            adapter.verify_webhook(connector, headers={}, raw_body=b"")
        assert exc.value.status_code == 400

    def test_verify_rejects_disabled(self):
        from backend.bots.connectors import WhatsAppAdapter

        adapter = WhatsAppAdapter()
        connector = _make_whatsapp_connector(is_enabled=False)
        with pytest.raises(HTTPException) as exc:
            adapter.verify_webhook(connector, headers={}, raw_body=b"")
        assert exc.value.status_code == 403

    def test_verify_rejects_missing_app_secret(self):
        from backend.bots.connectors import WhatsAppAdapter

        adapter = WhatsAppAdapter()
        connector = _make_whatsapp_connector(app_secret=None)
        with pytest.raises(HTTPException) as exc:
            adapter.verify_webhook(
                connector,
                headers={"X-Hub-Signature-256": "sha256=abc"},
                raw_body=b"{}",
            )
        assert exc.value.status_code == 403

    def test_verify_rejects_missing_signature_header(self):
        from backend.bots.connectors import WhatsAppAdapter

        adapter = WhatsAppAdapter()
        connector = _make_whatsapp_connector()
        with pytest.raises(HTTPException) as exc:
            adapter.verify_webhook(connector, headers={}, raw_body=b"{}")
        assert exc.value.status_code == 403

    def test_verify_rejects_wrong_signature(self):
        from backend.bots.connectors import WhatsAppAdapter

        adapter = WhatsAppAdapter()
        connector = _make_whatsapp_connector()
        with pytest.raises(HTTPException) as exc:
            adapter.verify_webhook(
                connector,
                headers={"X-Hub-Signature-256": "sha256=deadbeef"},
                raw_body=b'{"test": true}',
            )
        assert exc.value.status_code == 403

    def test_verify_accepts_correct_signature(self):
        from backend.bots.connectors import WhatsAppAdapter

        adapter = WhatsAppAdapter()
        connector = _make_whatsapp_connector()
        body = b'{"test": true}'
        sig = _whatsapp_signature("wa-app-secret", body)
        # Should not raise
        adapter.verify_webhook(
            connector,
            headers={"X-Hub-Signature-256": sig},
            raw_body=body,
        )

    def test_parse_inbound_extracts_text_message(self):
        from backend.bots.connectors import WhatsAppAdapter

        adapter = WhatsAppAdapter()
        msg = adapter.parse_inbound(
            {
                "object": "whatsapp_business_account",
                "entry": [
                    {
                        "id": "WABA-ID",
                        "changes": [
                            {
                                "value": {
                                    "messaging_product": "whatsapp",
                                    "metadata": {
                                        "phone_number_id": "123456789",
                                        "display_phone_number": "+15550001111",
                                    },
                                    "messages": [
                                        {
                                            "from": "15559998888",
                                            "id": "wamid.xxx",
                                            "timestamp": "1700000000",
                                            "type": "text",
                                            "text": {"body": "  /incidents  "},
                                        }
                                    ],
                                },
                                "field": "messages",
                            }
                        ],
                    }
                ],
            }
        )
        assert msg is not None
        assert msg.chat_id == "15559998888"
        assert msg.platform_user_id == "15559998888"
        assert msg.text == "/incidents"

    def test_parse_inbound_returns_none_without_messages(self):
        from backend.bots.connectors import WhatsAppAdapter

        adapter = WhatsAppAdapter()
        assert adapter.parse_inbound({}) is None
        assert adapter.parse_inbound({"entry": []}) is None

    def test_parse_inbound_skips_non_text_messages(self):
        from backend.bots.connectors import WhatsAppAdapter

        adapter = WhatsAppAdapter()
        msg = adapter.parse_inbound(
            {
                "object": "whatsapp_business_account",
                "entry": [
                    {
                        "changes": [
                            {
                                "value": {
                                    "messages": [
                                        {"from": "1555", "type": "image", "image": {}},
                                    ]
                                }
                            }
                        ]
                    }
                ],
            }
        )
        assert msg is None

    def test_inline_reply_is_none(self):
        from backend.bots.connectors import WhatsAppAdapter

        adapter = WhatsAppAdapter()
        assert adapter.inline_reply("15559998888", "hi") is None

    async def test_send_message_calls_whatsapp_client(self, monkeypatch):
        from backend.bots.connectors import whatsapp as whatsapp_mod

        captured: dict = {}

        async def fake_send(
            *, access_token, phone_number_id, recipient, text, timeout_seconds=10.0
        ):
            captured.update(
                access_token=access_token,
                phone_number_id=phone_number_id,
                recipient=recipient,
                text=text,
            )
            return True, None

        monkeypatch.setattr(whatsapp_mod, "whatsapp_send", fake_send)

        from backend.bots.connectors import WhatsAppAdapter

        adapter = WhatsAppAdapter()
        connector = _make_whatsapp_connector()
        ok, err = await adapter.send_message(
            connector, chat_id="15559998888", text="hello"
        )
        assert ok is True
        assert err is None
        assert captured["access_token"] == "WA-ACCESS-TOKEN"
        assert captured["phone_number_id"] == "123456789"
        assert captured["recipient"] == "15559998888"
        assert captured["text"] == "hello"


class TestFormSchema:
    """Every registered adapter exposes a non-trivial form_schema()."""

    PLATFORMS = [
        "telegram",
        "signal",
        "whatsapp",
        "slack",
        "discord",
        "mattermost",
        "matrix",
        "feishu",
        "dingtalk",
        "wecom",
        "weixin",
        "twilio",
        "email",
        "smtp",
        "homeassistant",
        "bluebubbles",
    ]

    def test_all_adapters_registered(self):
        import backend.bots  # noqa: F401

        from backend.bots.connectors import list_platforms

        registered = set(list_platforms())
        for platform in self.PLATFORMS:
            assert platform in registered, f"{platform} not in registry"

    def test_every_adapter_has_non_empty_schema(self):
        import backend.bots  # noqa: F401

        from backend.bots.connectors import FieldSpec, get_adapter

        for platform in self.PLATFORMS:
            adapter = get_adapter(platform)
            assert adapter is not None, platform
            schema = adapter.form_schema()
            assert isinstance(schema, list) and len(schema) > 0, platform
            for field in schema:
                assert isinstance(field, FieldSpec)
                assert field.name
                assert field.label
                assert field.kind in {"text", "secret", "select", "textarea", "url"}
                assert field.group in {"config", "credentials"}

    def test_telegram_schema_includes_required_credentials(self):
        import backend.bots  # noqa: F401

        from backend.bots.connectors import get_adapter

        adapter = get_adapter("telegram")
        names = {f.name: f for f in adapter.form_schema()}
        assert names["bot_token"].group == "credentials"
        assert names["bot_token"].kind == "secret"
        assert names["bot_token"].required is True
        assert names["webhook_secret"].required is True
        assert names["default_chat_id"].group == "config"
        assert names["default_chat_id"].required is False

    def test_whatsapp_schema_has_phone_number_id_and_verify_token(self):
        import backend.bots  # noqa: F401

        from backend.bots.connectors import get_adapter

        adapter = get_adapter("whatsapp")
        names = {f.name for f in adapter.form_schema()}
        assert {"app_secret", "verify_token", "access_token", "phone_number_id"} <= names

    def test_discord_schema_uses_customer_facing_channel_copy(self):
        adapter = get_adapter("discord")
        fields = {field.name: field for field in adapter.form_schema()}
        channel = fields["default_chat_id"]
        assert channel.label == "Discord Channel ID"
        assert "where OpsMender should post outbound notifications" in channel.helper
        assert "Snowflake" not in channel.helper

    def test_email_schema_is_mailgun_specific(self):
        adapter = get_adapter("email")
        fields = {field.name: field for field in adapter.form_schema()}
        assert set(fields) == {
            "mailgun_api_key",
            "mailgun_domain",
            "from_email",
            "default_chat_id",
        }
        assert fields["mailgun_api_key"].required is True
        assert fields["mailgun_domain"].required is True

    def test_smtp_schema_supports_hosted_and_internal_relays(self):
        adapter = get_adapter("smtp")
        fields = {field.name: field for field in adapter.form_schema()}
        assert set(fields) == {
            "smtp_host",
            "smtp_port",
            "security",
            "smtp_username",
            "smtp_password",
            "from_email",
            "default_chat_id",
        }
        assert fields["smtp_host"].required is True
        assert fields["smtp_port"].default == "587"
        assert fields["security"].default == "starttls"
        assert fields["smtp_password"].kind == "secret"


class TestSMTPEmailAdapter:
    async def test_send_uses_starttls_and_login(self, monkeypatch):
        events = []

        class FakeSMTP:
            def __init__(self, host, port, timeout):
                events.append(("connect", host, port, timeout))

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return None

            def starttls(self):
                events.append(("starttls",))

            def login(self, username, password):
                events.append(("login", username, password))

            def send_message(self, message):
                events.append(
                    ("send", message["From"], message["To"], message["Subject"])
                )

        monkeypatch.setattr("backend.bots.connectors.smtp.smtplib.SMTP", FakeSMTP)
        connector = BotConnector(
            name="smtp-test",
            platform="smtp",
            config={},
            credentials={
                "smtp_host": "smtp.example.com",
                "smtp_port": "587",
                "security": "starttls",
                "smtp_username": "ops",
                "smtp_password": "secret",
                "from_email": "opsmender@example.com",
            },
            allowed_capabilities=["notifications"],
            status="configured",
            is_enabled=True,
        )
        ok, error = await get_adapter("smtp").send_message(
            connector,
            chat_id="oncall@example.com",
            text="test notification",
        )
        assert ok is True
        assert error is None
        assert events == [
            ("connect", "smtp.example.com", 587, 10),
            ("starttls",),
            ("login", "ops", "secret"),
            (
                "send",
                "opsmender@example.com",
                "oncall@example.com",
                "OpsMender Incident Update",
            ),
        ]


class TestTeamsAdapter:
    async def test_incident_delivery_posts_verified_adaptive_card(
        self, monkeypatch
    ):
        captured = {}

        async def fake_token(**kwargs):
            return SimpleNamespace(token_type="Bearer", access_token="token")

        class FakeResponse:
            status_code = 201

            @staticmethod
            def json():
                return {"id": "teams-message-1"}

        class FakeClient:
            def __init__(self, *args, **kwargs):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                return None

            async def post(self, url, **kwargs):
                captured["url"] = url
                captured["json"] = kwargs["json"]
                return FakeResponse()

        monkeypatch.setattr(
            "backend.bots.connectors.teams.acquire_app_only_token",
            fake_token,
        )
        monkeypatch.setattr(
            "backend.bots.connectors.teams.httpx.AsyncClient",
            FakeClient,
        )
        connector = BotConnector(
            name="teams-test",
            platform="teams",
            config={"bot_app_id": "bot-app"},
            credentials={
                "tenant_id": "tenant",
                "client_id": "client",
                "client_secret": "secret",
            },
            allowed_capabilities=["notifications"],
            status="configured",
            is_enabled=True,
            native_actions_enabled=True,
        )
        incident = SimpleNamespace(
            id="00000000-0000-0000-0000-000000000123",
            title="API outage",
            description="500s",
            priority="P1",
            status="open",
            severity="high",
        )
        receipt = await get_adapter("teams").send_incident_update(
            connector,
            chat_id="19:chat@thread.v2",
            text="fallback",
            incident=incident,
            native_actions_ready=True,
        )
        assert receipt.ok is True
        assert receipt.external_message_id == "teams-message-1"
        assert captured["url"].endswith(
            "/chats/19:chat@thread.v2/messages"
        )
        actions = captured["json"]["attachments"][0]["content"]["actions"]
        assert {action["title"] for action in actions} == {
            "Acknowledge",
            "Resolve",
            "Escalate",
            "Start AI Session",
        }


class TestMailgunEmailAdapter:
    def _connector(
        self,
        *,
        api_key: str | None = "key-test",
        enabled: bool = True,
    ) -> BotConnector:
        credentials = {"mailgun_domain": "mg.example.com"}
        if api_key is not None:
            credentials["mailgun_api_key"] = api_key
        return BotConnector(
            name="mailgun-test",
            platform="email",
            config={},
            credentials=credentials,
            allowed_capabilities=["notifications"],
            status="configured",
            is_enabled=enabled,
        )

    def test_webhook_rejects_disabled_connector(self):
        adapter = get_adapter("email")
        with pytest.raises(HTTPException) as exc:
            adapter.verify_webhook(
                self._connector(enabled=False),
                headers={},
                raw_body=b"{}",
            )
        assert exc.value.status_code == 403

    def test_webhook_rejects_missing_api_key(self):
        adapter = get_adapter("email")
        with pytest.raises(HTTPException) as exc:
            adapter.verify_webhook(
                self._connector(api_key=None),
                headers={},
                raw_body=b"{}",
            )
        assert exc.value.status_code == 403

    def test_webhook_rejects_missing_signature(self):
        adapter = get_adapter("email")
        with pytest.raises(HTTPException) as exc:
            adapter.verify_webhook(
                self._connector(),
                headers={},
                raw_body=b"{}",
            )
        assert exc.value.status_code == 401

    def test_webhook_accepts_valid_signature(self):
        adapter = get_adapter("email")
        timestamp = "1710000000"
        token = "token-123"
        signature = hmac.new(
            b"key-test",
            f"{timestamp}{token}".encode(),
            hashlib.sha256,
        ).hexdigest()
        raw_body = json.dumps(
            {
                "signature": {
                    "timestamp": timestamp,
                    "token": token,
                    "signature": signature,
                }
            }
        ).encode()
        adapter.verify_webhook(
            self._connector(),
            headers={},
            raw_body=raw_body,
        )
