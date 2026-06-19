"""Per-platform connector adapters.

Each chat platform (Telegram, Signal, WhatsApp, ...) is plugged into OpsMender
through a ``BotConnectorAdapter`` implementation registered against its
``platform`` key. The shared dispatcher in
``backend.bots.dispatcher`` handles capability gating, identity / role
enforcement, rate limiting, audit logging, and command routing — the
adapter is responsible only for the platform-specific bits: webhook
verification, payload parsing, inline-reply shaping (if supported), and
outbound ``send_message`` delivery.
"""

from .base import BotConnectorAdapter, FieldSpec, InboundMessage
from .registry import get_adapter, list_platforms, register_adapter
from .bluebubbles import BlueBubblesAdapter
from .dingtalk import DingTalkAdapter
from .discord import DiscordAdapter
from .email import EmailAdapter
from .eventbridge import EventBridgeAdapter
from .feishu import FeishuAdapter
from .google_chat import GoogleChatAdapter
from .homeassistant import HomeAssistantAdapter
from .matrix import MatrixAdapter
from .mattermost import MattermostAdapter
from .signal import SignalAdapter
from .slack import SlackAdapter
from .smtp import SMTPEmailAdapter
from .teams import TeamsAdapter
from .telegram import TelegramAdapter
from .twilio import TwilioAdapter
from .wecom import WeComAdapter
from .weixin import WeixinAdapter
from .whatsapp import WhatsAppAdapter

__all__ = [
    "BotConnectorAdapter",
    "FieldSpec",
    "InboundMessage",
    "BlueBubblesAdapter",
    "DingTalkAdapter",
    "DiscordAdapter",
    "EmailAdapter",
    "EventBridgeAdapter",
    "FeishuAdapter",
    "GoogleChatAdapter",
    "HomeAssistantAdapter",
    "MatrixAdapter",
    "MattermostAdapter",
    "SignalAdapter",
    "SlackAdapter",
    "SMTPEmailAdapter",
    "TeamsAdapter",
    "TelegramAdapter",
    "TwilioAdapter",
    "WeComAdapter",
    "WeixinAdapter",
    "WhatsAppAdapter",
    "get_adapter",
    "list_platforms",
    "register_adapter",
]
