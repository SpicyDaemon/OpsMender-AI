"""v2 Phase 6 — provider-agnostic Voice Call paging medium."""

from __future__ import annotations

from urllib.parse import parse_qs

import httpx

from backend.bots.capabilities import supports_voice_call
from backend.paging.channel_factory import build_channel_factory
from backend.paging.channels import VoiceChannel
from backend.paging.dispatch import CHANNEL_KEYS


def _mock_factory(handler):
    transport = httpx.MockTransport(handler)

    def factory():
        return httpx.AsyncClient(transport=transport, timeout=5.0)

    return factory


def _form(request: httpx.Request) -> dict[str, str]:
    return {k: v[0] for k, v in parse_qs(request.content.decode()).items()}


class TestVoiceChannel:
    async def test_places_call_with_spoken_twiml(self):
        captured: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["url"] = str(request.url)
            captured["form"] = _form(request)
            return httpx.Response(201, json={"sid": "CA123", "status": "queued"})

        channel = VoiceChannel(
            account_sid="AC1",
            auth_token="tok",
            from_number="+15550000000",
            status_callback_url="https://op.example.com/cb",
            http_client_factory=_mock_factory(handler),
        )
        attempt = await channel.send(
            recipient="+15559999999", subject="P0 AWS Critical", body="DB is down"
        )
        assert attempt.status == "sent"
        assert captured["url"].endswith("/Accounts/AC1/Calls.json")
        form = captured["form"]
        assert form["To"] == "+15559999999"
        assert form["From"] == "+15550000000"
        assert "<Say>" in form["Twiml"] and "AWS Critical" in form["Twiml"]
        assert form["StatusCallback"] == "https://op.example.com/cb"

    async def test_failed_on_http_error(self):
        def handler(request):
            return httpx.Response(400, json={"message": "not a voice number"})

        channel = VoiceChannel(
            account_sid="AC1",
            auth_token="tok",
            from_number="+15550000000",
            http_client_factory=_mock_factory(handler),
        )
        attempt = await channel.send(recipient="+1555", subject="x", body="y")
        assert attempt.status == "failed"
        assert "not a voice number" in (attempt.error or "")


class TestVoiceCapabilityGate:
    def test_voice_is_a_channel_key(self):
        assert "voice" in CHANNEL_KEYS

    def test_factory_builds_voice_only_when_configured(self):
        configured = build_channel_factory(
            {
                "OPSMENDER_TWILIO_ACCOUNT_SID": "AC1",
                "OPSMENDER_TWILIO_AUTH_TOKEN": "tok",
                "OPSMENDER_TWILIO_FROM_NUMBER": "+15550000000",
            }
        )
        assert isinstance(configured("voice"), VoiceChannel)
        # No Twilio config → voice is not offered.
        assert build_channel_factory({})("voice") is None

    def test_voice_from_number_override(self):
        factory = build_channel_factory(
            {
                "OPSMENDER_TWILIO_ACCOUNT_SID": "AC1",
                "OPSMENDER_TWILIO_AUTH_TOKEN": "tok",
                "OPSMENDER_TWILIO_FROM_NUMBER": "+15550000000",
                "OPSMENDER_TWILIO_VOICE_FROM_NUMBER": "+15551111111",
            }
        )
        ch = factory("voice")
        assert isinstance(ch, VoiceChannel)
        assert ch._from == "+15551111111"

    def test_capability_gate(self):
        assert supports_voice_call("twilio") is True
        assert supports_voice_call("slack") is False
        assert supports_voice_call("unknown") is False
