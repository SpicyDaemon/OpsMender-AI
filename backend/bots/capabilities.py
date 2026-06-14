"""Shared Notification Channel capability model.

This module is the single source of truth for *what each chat/notification
platform can actually do*. It exists so OpsMender can be honest — in the UI,
in the API, and in delivery routing — about which channels can render a rich
incident card, which can host secure interactive responder actions, and which
can only receive a plain delivery-only message.

Capability flags
----------------
``delivery``
    The platform can receive an outbound incident message at all. True for
    every supported platform — this is the floor.
``incident_card``
    The platform/adapter can render a structured, multi-field incident card
    (rich formatting / blocks) rather than a single plain-text line.
``incident_updates``
    The platform participates in incident lifecycle notifications. This is the
    product-level feature flag shown in the UI; it does not imply interactive
    buttons or in-place message edits.
``interactive_actions``
    The platform/adapter can render *secure* interactive action controls
    (Acknowledge / Resolve / Escalate / Start AI Session) whose callbacks are
    authenticated (signed token or platform signature verification).

    IMPORTANT — honesty guardrail: this is enabled only for Slack in v1.1
    Phase B. A Slack channel must also opt in and have its signing secret
    configured before buttons render; every click is signature-verified before
    execution. Every other platform remains false.
``direct_message``
    The platform can deliver a 1:1 direct message (relevant to Personal
    Routing, not Notification Channels, but modelled here for completeness).
``shared_channel``
    The platform can post into a shared team channel/group/room.
``ai_session_link``
    The incident message can carry a link to an AI session for this incident.
    Always available because it is just another authenticated deep link.
``message_update``
    The platform/adapter can update a previously-posted incident message in
    place using a stored provider message id. This is False everywhere in v1
    until an adapter ships a verified update path.

``delivery_only`` is *derived*: a channel is delivery-only when it can neither
render an incident card nor host interactive actions. Such channels still get
a useful incident message (title/severity/status + authenticated link).
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PlatformCapabilities:
    """Honest capability descriptor for one notification platform."""

    platform: str
    display_name: str
    delivery: bool = True
    incident_card: bool = False
    incident_updates: bool = True
    interactive_actions: bool = False
    direct_message: bool = False
    shared_channel: bool = False
    ai_session_link: bool = True
    message_update: bool = False

    @property
    def delivery_only(self) -> bool:
        """A channel is delivery-only when it can neither render an incident
        card nor host secure interactive actions."""
        return not self.incident_card and not self.interactive_actions

    def as_dict(self) -> dict[str, object]:
        return {
            "platform": self.platform,
            "display_name": self.display_name,
            "delivery": self.delivery,
            "incident_card": self.incident_card,
            "incident_updates": self.incident_updates,
            "interactive_actions": self.interactive_actions,
            "direct_message": self.direct_message,
            "shared_channel": self.shared_channel,
            "ai_session_link": self.ai_session_link,
            "message_update": self.message_update,
            "delivery_only": self.delivery_only,
        }


def _cap(
    platform: str,
    display_name: str,
    *,
    incident_card: bool = False,
    interactive_actions: bool = False,
    direct_message: bool = False,
    shared_channel: bool = False,
) -> PlatformCapabilities:
    return PlatformCapabilities(
        platform=platform,
        display_name=display_name,
        incident_card=incident_card,
        interactive_actions=interactive_actions,
        direct_message=direct_message,
        shared_channel=shared_channel,
    )


# Single source of truth. Display names are user-friendly and must match the
# frontend platform labels. Twilio is shown as "Twilio (SMS)" per product spec.
PLATFORM_CAPABILITIES: dict[str, PlatformCapabilities] = {
    # Rich chat platforms — can render an incident card and post to channels.
    "slack": _cap(
        "slack",
        "Slack",
        incident_card=True,
        interactive_actions=True,
        direct_message=True,
        shared_channel=True,
    ),
    "teams": _cap("teams", "Microsoft Teams", incident_card=True, direct_message=True, shared_channel=True),
    "discord": _cap("discord", "Discord", incident_card=True, shared_channel=True),
    "telegram": _cap("telegram", "Telegram", incident_card=True, direct_message=True, shared_channel=True),
    "mattermost": _cap("mattermost", "Mattermost", incident_card=True, direct_message=True, shared_channel=True),
    "matrix": _cap("matrix", "Matrix", incident_card=True, direct_message=True, shared_channel=True),
    "feishu": _cap("feishu", "Lark / Feishu", incident_card=True, direct_message=True, shared_channel=True),
    "dingtalk": _cap("dingtalk", "DingTalk", incident_card=True, shared_channel=True),
    "wecom": _cap("wecom", "WeCom", incident_card=True, direct_message=True, shared_channel=True),
    # Delivery-only platforms — plain message + authenticated incident link.
    "whatsapp": _cap("whatsapp", "WhatsApp", direct_message=True),
    "signal": _cap("signal", "Signal", direct_message=True, shared_channel=True),
    "twilio": _cap("twilio", "Twilio (SMS)", direct_message=True),
    "email": _cap("email", "Mailgun Email", direct_message=True, shared_channel=True),
    "smtp": _cap("smtp", "SMTP Email", direct_message=True, shared_channel=True),
    "weixin": _cap("weixin", "WeChat (Official Account)"),
    "homeassistant": _cap("homeassistant", "Home Assistant", shared_channel=True),
    "bluebubbles": _cap("bluebubbles", "BlueBubbles (iMessage)", direct_message=True, shared_channel=True),
    "custom": _cap("custom", "Custom Webhook", shared_channel=True),
}


def get_platform_capabilities(platform: str) -> PlatformCapabilities | None:
    """Return the capability descriptor for ``platform``, or ``None`` when the
    platform key is unknown."""
    return PLATFORM_CAPABILITIES.get(platform)


def display_name(platform: str) -> str:
    """User-friendly platform name, falling back to a title-cased key."""
    caps = PLATFORM_CAPABILITIES.get(platform)
    if caps is not None:
        return caps.display_name
    return platform.replace("_", " ").title()


def supports_incident_card(platform: str) -> bool:
    caps = PLATFORM_CAPABILITIES.get(platform)
    return bool(caps and caps.incident_card)


def supports_interactive_actions(platform: str) -> bool:
    caps = PLATFORM_CAPABILITIES.get(platform)
    return bool(caps and caps.interactive_actions)


def supports_message_update(platform: str) -> bool:
    caps = PLATFORM_CAPABILITIES.get(platform)
    return bool(caps and caps.message_update)


def is_delivery_only(platform: str) -> bool:
    """True when the platform can only receive a plain delivery message.

    Unknown platforms are treated as delivery-only — the safe default that
    never over-promises rich/interactive support.
    """
    caps = PLATFORM_CAPABILITIES.get(platform)
    if caps is None:
        return True
    return caps.delivery_only
