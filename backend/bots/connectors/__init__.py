"""Per-platform connector adapters.

Each chat platform (Telegram, Signal, WhatsApp, ...) is plugged into AIM
through a ``BotConnectorAdapter`` implementation registered against its
``platform`` key. The shared dispatcher in
``backend.bots.dispatcher`` handles capability gating, identity / role
enforcement, rate limiting, audit logging, and command routing — the
adapter is responsible only for the platform-specific bits: webhook
verification, payload parsing, inline-reply shaping (if supported), and
outbound ``send_message`` delivery.
"""

from .base import BotConnectorAdapter, InboundMessage
from .registry import get_adapter, list_platforms, register_adapter
from .dingtalk import DingTalkAdapter
from .discord import DiscordAdapter
from .feishu import FeishuAdapter
from .matrix import MatrixAdapter
from .mattermost import MattermostAdapter
from .signal import SignalAdapter
from .slack import SlackAdapter
from .telegram import TelegramAdapter
from .whatsapp import WhatsAppAdapter

__all__ = [
    "BotConnectorAdapter",
    "InboundMessage",
    "DingTalkAdapter",
    "DiscordAdapter",
    "FeishuAdapter",
    "MatrixAdapter",
    "MattermostAdapter",
    "SignalAdapter",
    "SlackAdapter",
    "TelegramAdapter",
    "WhatsAppAdapter",
    "get_adapter",
    "list_platforms",
    "register_adapter",
]
