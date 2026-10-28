"""Shared Notification Channel capability model.

This module is the single source of truth for *what each chat/notification
platform can actually do*. It exists so OpsMender can be honest - in the UI,
in the API, and in delivery routing - about which channels can render a rich
incident card, which can host secure interactive responder actions, and which
can only receive a plain delivery-only message.

Capability flags
----------------
``delivery``
    The platform can receive an outbound incident message at all. True for
    every platform OpsMender delivers to today. False only for a platform that
    is being rebuilt (see ``unavailable_reason``), which receives nothing.
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

    IMPORTANT - honesty guardrail: this is enabled only for platforms with a
    registered verifier and mounted interaction route. Each channel must opt in
    before buttons render; every click is cryptographically verified before
    execution.
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
    place using a stored provider message id. Enabled only when the adapter has
    a verified provider edit path.
``link_actions``
    The incident card carries signed links (M1-99) that open OpsMender's
    confirm page, where the action runs after sign-in and Confirm. Nothing
    calls back from the chat, so no verifier or interaction route is needed.
    Not ``interactive_actions``.

``delivery_only`` is *derived*: a channel is delivery-only when it receives
messages but can neither render an incident card nor host interactive actions.
Such channels still get a useful incident message (title/severity/status +
authenticated link). A platform that receives nothing is not delivery-only.

``unavailable_reason``
    Set when OpsMender cannot deliver to the platform at all. The platform
    claims no capability, nothing is sent to it, and the settings page shows
    the reason instead of capability chips (M1-96: Microsoft Teams).
"""

from __future__ import annotations

from collections.abc import Collection
from dataclasses import dataclass

# M1-96: Microsoft Graph lets an app post chat messages with app-only auth only
# during a migration, Graph cards allow link buttons only, and the Office 365
# connector webhooks are retired. The old Teams channel type sends nothing
# until the Teams bot is built (M1-101); incident cards go to Teams through a
# Microsoft Teams (Workflows) channel (M1-100).
TEAMS_REBUILDING = (
    "The Microsoft Teams bot is being rebuilt. Microsoft lets an app post Teams "
    "chat messages through Microsoft Graph only during a migration, and card "
    "buttons that act inside Teams need a bot, so this channel type sends nothing "
    "until the Teams bot is ready. To post incident cards to Teams now, add a "
    "Microsoft Teams (Workflows) channel."
)


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
    link_actions: bool = False
    interaction_route: str | None = None
    # Can this provider place an outbound **phone call** (PSTN voice)? True only
    # for telephony providers (e.g. Twilio); chat bots cannot place calls. Gates
    # whether Voice Call is offered as a personal-routing delivery medium.
    voice_call: bool = False
    unavailable_reason: str | None = None

    @property
    def delivery_only(self) -> bool:
        """A channel is delivery-only when it receives messages but can neither
        render an incident card nor host secure interactive actions. A platform
        that receives nothing (M1-96: Teams) is not delivery-only."""
        return self.delivery and not self.incident_card and not self.interactive_actions

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
            "link_actions": self.link_actions,
            "interaction_route": self.interaction_route,
            "voice_call": self.voice_call,
            "delivery_only": self.delivery_only,
            "unavailable_reason": self.unavailable_reason,
        }


def _cap(
    platform: str,
    display_name: str,
    *,
    incident_card: bool = False,
    interactive_actions: bool = False,
    direct_message: bool = False,
    shared_channel: bool = False,
    message_update: bool = False,
    link_actions: bool = False,
    interaction_route: str | None = None,
    voice_call: bool = False,
) -> PlatformCapabilities:
    return PlatformCapabilities(
        platform=platform,
        display_name=display_name,
        incident_card=incident_card,
        interactive_actions=interactive_actions,
        direct_message=direct_message,
        shared_channel=shared_channel,
        message_update=message_update,
        link_actions=link_actions,
        interaction_route=interaction_route,
        voice_call=voice_call,
    )


# Single source of truth. Display names are user-friendly and must match the
# frontend platform labels. Twilio is shown as "Twilio (SMS)" per product spec.
PLATFORM_CAPABILITIES: dict[str, PlatformCapabilities] = {
    # Rich chat platforms - can render an incident card and post to channels.
    "slack": _cap(
        "slack",
        "Slack",
        incident_card=True,
        interactive_actions=True,
        interaction_route="/bot/slack/interactions",
        direct_message=True,
        shared_channel=True,
        # Slack chat.update edits a previously-posted message in place using the
        # stored message ``ts``. Lifecycle status changes edit the card; the
        # follow-up path covers edit-window/permission errors.
        message_update=True,
    ),
    # Being rebuilt (M1-96): nothing is claimed and nothing is sent. Stored Teams
    # connectors and contact methods are kept for the Teams bot (M1-101).
    "teams": PlatformCapabilities(
        platform="teams",
        display_name="Microsoft Teams",
        delivery=False,
        incident_updates=False,
        ai_session_link=False,
        unavailable_reason=TEAMS_REBUILDING,
    ),
    # M1-100: a Teams workflow posts a new card per incident change. The card's
    # buttons are signed links to OpsMender's confirm page; nothing acts inside
    # Teams and a posted card is never edited.
    "teams_workflows": _cap(
        "teams_workflows",
        "Microsoft Teams (Workflows)",
        incident_card=True,
        shared_channel=True,
        link_actions=True,
    ),
    "discord": _cap(
        "discord",
        "Discord",
        incident_card=True,
        interactive_actions=True,
        interaction_route="/bot-connectors/{connector_id}/discord/webhook",
        shared_channel=True,
        # Discord returns a durable message id and supports editing bot-authored
        # channel messages through the message resource.
        message_update=True,
    ),
    "google_chat": _cap(
        "google_chat",
        "Google Chat",
        shared_channel=True,
        # Chat app-authored messages return a durable resource name and can be
        # patched later with the chat.bot scope.
        message_update=True,
    ),
    "telegram": _cap(
        "telegram",
        "Telegram",
        incident_card=True,
        direct_message=True,
        shared_channel=True,
    ),
    "mattermost": _cap(
        "mattermost",
        "Mattermost",
        incident_card=True,
        direct_message=True,
        shared_channel=True,
    ),
    "matrix": _cap(
        "matrix", "Matrix", incident_card=True, direct_message=True, shared_channel=True
    ),
    "feishu": _cap(
        "feishu",
        "Lark / Feishu",
        incident_card=True,
        direct_message=True,
        shared_channel=True,
    ),
    "dingtalk": _cap("dingtalk", "DingTalk", incident_card=True, shared_channel=True),
    "wecom": _cap(
        "wecom", "WeCom", incident_card=True, direct_message=True, shared_channel=True
    ),
    # Delivery-only platforms - plain message + authenticated incident link.
    "whatsapp": _cap("whatsapp", "WhatsApp", direct_message=True),
    "signal": _cap("signal", "Signal", direct_message=True, shared_channel=True),
    "twilio": _cap("twilio", "Twilio (SMS)", direct_message=True, voice_call=True),
    "email": _cap("email", "Mailgun Email", direct_message=True, shared_channel=True),
    "smtp": _cap("smtp", "SMTP Email", direct_message=True, shared_channel=True),
    "weixin": _cap("weixin", "WeChat (Official Account)"),
    "homeassistant": _cap("homeassistant", "Home Assistant", shared_channel=True),
    "bluebubbles": _cap(
        "bluebubbles",
        "BlueBubbles (iMessage)",
        direct_message=True,
        shared_channel=True,
    ),
    "custom": _cap("custom", "Custom Webhook", shared_channel=True),
    "eventbridge": _cap("eventbridge", "AWS EventBridge", shared_channel=True),
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


def platform_delivers(platform: str) -> bool:
    """Whether OpsMender sends anything to ``platform`` (M1-96: not Teams)."""
    caps = PLATFORM_CAPABILITIES.get(platform)
    return caps is None or caps.delivery


def supports_incident_card(platform: str) -> bool:
    caps = PLATFORM_CAPABILITIES.get(platform)
    return bool(caps and caps.incident_card)


def supports_interactive_actions(platform: str) -> bool:
    caps = PLATFORM_CAPABILITIES.get(platform)
    return bool(caps and caps.interactive_actions)


def supports_link_actions(platform: str) -> bool:
    caps = PLATFORM_CAPABILITIES.get(platform)
    return bool(caps and caps.link_actions)


def assert_interactive_action_contract(route_paths: Collection[str]) -> None:
    """Fail startup when an advertised native-action path is incomplete.

    A platform may opt into ``interactive_actions`` only when its adapter
    provides a webhook verifier and its declared interaction route is mounted
    in the current dispatcher-capable application.
    """

    from backend.bots.connectors import get_adapter

    errors: list[str] = []
    for platform, caps in PLATFORM_CAPABILITIES.items():
        if not caps.interactive_actions:
            continue
        adapter = get_adapter(platform)
        verifier = (
            getattr(type(adapter), "verify_webhook", None)
            if adapter is not None
            else None
        )
        if adapter is None or not callable(verifier):
            errors.append(f"{platform}: registered adapter verifier missing")
        if not caps.interaction_route or caps.interaction_route not in route_paths:
            errors.append(f"{platform}: mounted interaction route missing")
    if errors:
        raise RuntimeError(
            "Invalid interactive-action capability contract: " + "; ".join(errors)
        )


def supports_message_update(platform: str) -> bool:
    caps = PLATFORM_CAPABILITIES.get(platform)
    return bool(caps and caps.message_update)


def supports_voice_call(platform: str) -> bool:
    """True only for telephony providers that can place outbound phone calls."""
    caps = PLATFORM_CAPABILITIES.get(platform)
    return bool(caps and caps.voice_call)


def is_delivery_only(platform: str) -> bool:
    """True when the platform can only receive a plain delivery message.

    Unknown platforms are treated as delivery-only - the safe default that
    never over-promises rich/interactive support. A platform that receives
    nothing (M1-96: Teams) is not delivery-only.
    """
    caps = PLATFORM_CAPABILITIES.get(platform)
    if caps is None:
        return True
    return caps.delivery_only
