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
from .registry import get_adapter, register_adapter
from .telegram import TelegramAdapter

__all__ = [
    "BotConnectorAdapter",
    "InboundMessage",
    "TelegramAdapter",
    "get_adapter",
    "register_adapter",
]
