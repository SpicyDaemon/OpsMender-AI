"""Tests for the shared Notification Channel capability model.

These lock in the honesty guarantees: every supported platform is modelled,
delivery-only platforms don't advertise rich/interactive support, and — most
importantly — no platform claims interactive action buttons in v1.
"""

from __future__ import annotations

from backend.bots.capabilities import (
    PLATFORM_CAPABILITIES,
    display_name,
    get_platform_capabilities,
    is_delivery_only,
    supports_incident_card,
    supports_interactive_actions,
)
from backend.bots.connectors import list_platforms


def test_every_registered_adapter_has_capabilities():
    for platform in list_platforms():
        assert get_platform_capabilities(platform) is not None, platform


def test_twilio_is_named_sms_and_is_delivery_only():
    caps = get_platform_capabilities("twilio")
    assert caps is not None
    assert caps.display_name == "Twilio (SMS)"
    assert display_name("twilio") == "Twilio (SMS)"
    assert caps.delivery_only is True
    assert is_delivery_only("twilio") is True


def test_email_and_custom_are_delivery_only():
    assert is_delivery_only("email") is True
    assert is_delivery_only("custom") is True


def test_rich_chat_platforms_support_incident_cards():
    for platform in ("slack", "teams", "discord", "telegram"):
        assert supports_incident_card(platform) is True
        caps = get_platform_capabilities(platform)
        assert caps is not None
        assert caps.delivery_only is False


def test_no_platform_advertises_interactive_actions_in_v1():
    # Honesty guardrail: no adapter ships a verified interactive-callback path
    # yet, so we must not advertise interactive buttons anywhere.
    for platform, caps in PLATFORM_CAPABILITIES.items():
        assert caps.interactive_actions is False, platform
        assert supports_interactive_actions(platform) is False, platform


def test_delivery_is_the_floor_for_every_platform():
    for caps in PLATFORM_CAPABILITIES.values():
        assert caps.delivery is True


def test_unknown_platform_is_treated_as_delivery_only():
    assert get_platform_capabilities("nope") is None
    assert is_delivery_only("nope") is True
    assert supports_incident_card("nope") is False
    assert supports_interactive_actions("nope") is False


def test_as_dict_round_trip_exposes_delivery_only():
    data = get_platform_capabilities("slack").as_dict()
    assert data["platform"] == "slack"
    assert data["display_name"] == "Slack"
    assert data["incident_card"] is True
    assert data["interactive_actions"] is False
    assert data["delivery_only"] is False
