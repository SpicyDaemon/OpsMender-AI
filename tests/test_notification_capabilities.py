"""Tests for the shared Notification Channel capability model.

These lock in the honesty guarantees: every supported platform is modelled,
delivery-only platforms don't advertise rich/interactive support, and - most
importantly - only platforms with verified callback implementations advertise
interactive action support.
"""

from __future__ import annotations

from backend.bots.capabilities import (
    PLATFORM_CAPABILITIES,
    TEAMS_REBUILDING,
    display_name,
    get_platform_capabilities,
    is_delivery_only,
    platform_delivers,
    supports_incident_card,
    supports_interactive_actions,
    supports_message_update,
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
    assert display_name("email") == "Mailgun Email"


def test_rich_chat_platforms_support_incident_cards():
    for platform in ("slack", "discord", "telegram"):
        assert supports_incident_card(platform) is True
        caps = get_platform_capabilities(platform)
        assert caps is not None
        assert caps.delivery_only is False


def test_only_verified_callback_platforms_advertise_interactive_actions():
    for platform, caps in PLATFORM_CAPABILITIES.items():
        expected = platform in {"slack", "discord"}
        assert caps.interactive_actions is expected, platform
        assert supports_interactive_actions(platform) is expected, platform
        assert bool(caps.interaction_route) is expected, platform


def test_supported_platforms_advertise_message_updates():
    # Slack, Discord, and Google Chat all retain provider message IDs and expose
    # verified edit paths. Teams remains follow-up-only.
    for platform, caps in PLATFORM_CAPABILITIES.items():
        expected = platform in {"slack", "discord", "google_chat"}
        assert caps.message_update is expected, platform
        assert supports_message_update(platform) is expected, platform


def test_delivery_is_the_floor_for_every_platform_but_one_being_rebuilt():
    for platform, caps in PLATFORM_CAPABILITIES.items():
        if caps.unavailable_reason:
            continue
        assert caps.delivery is True, platform
        assert caps.incident_updates is True, platform


def test_teams_claims_nothing_while_it_is_rebuilt():
    # M1-96: Graph app-only chat posts work only during a migration and Graph
    # cards allow link buttons only, so Teams claims no capability at all.
    caps = get_platform_capabilities("teams")
    assert caps.unavailable_reason == TEAMS_REBUILDING
    assert "being rebuilt" in TEAMS_REBUILDING
    assert not any(
        (
            caps.delivery,
            caps.incident_card,
            caps.incident_updates,
            caps.interactive_actions,
            caps.direct_message,
            caps.shared_channel,
            caps.message_update,
            caps.ai_session_link,
        )
    )
    assert caps.interaction_route is None
    assert caps.as_dict()["unavailable_reason"] == TEAMS_REBUILDING
    assert platform_delivers("teams") is False
    assert platform_delivers("slack") is True
    assert platform_delivers("nope") is True
    others = [c for c in PLATFORM_CAPABILITIES.values() if c.platform != "teams"]
    assert all(c.unavailable_reason is None for c in others)


def test_unknown_platform_is_treated_as_delivery_only():
    assert get_platform_capabilities("nope") is None
    assert is_delivery_only("nope") is True
    assert supports_incident_card("nope") is False
    assert supports_interactive_actions("nope") is False
    assert supports_message_update("nope") is False


def test_as_dict_round_trip_exposes_delivery_only():
    data = get_platform_capabilities("slack").as_dict()
    assert data["platform"] == "slack"
    assert data["display_name"] == "Slack"
    assert data["incident_card"] is True
    assert data["incident_updates"] is True
    assert data["interactive_actions"] is True
    assert data["message_update"] is True
    assert data["delivery_only"] is False
