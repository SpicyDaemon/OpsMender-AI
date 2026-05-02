"""Connector adapter interface."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Protocol

from backend.db.models import BotConnector


@dataclass(frozen=True)
class InboundMessage:
    """Normalized inbound chat message.

    ``chat_id`` and ``platform_user_id`` are opaque strings whose meaning
    is platform-specific; AIM uses them only as map keys for allowlists,
    rate-limit buckets, and ``bot_user_links`` lookup.
    """

    chat_id: str
    platform_user_id: str | None
    text: str


class BotConnectorAdapter(Protocol):
    """Per-platform contract.

    ``verify_webhook`` should raise ``fastapi.HTTPException`` (or return
    ``False``) when the inbound request is not authentic. Implementations
    typically check a shared secret header (Telegram), an HMAC signature
    (Signal / WhatsApp / Slack), or both.
    """

    platform: str

    def verify_webhook(
        self,
        connector: BotConnector,
        *,
        headers: Mapping[str, str],
        raw_body: bytes,
    ) -> None: ...

    def parse_inbound(
        self,
        payload: dict[str, Any],
    ) -> InboundMessage | None: ...

    def inline_reply(
        self,
        chat_id: str,
        text: str,
    ) -> dict[str, Any] | None:
        """Return a webhook-response payload, or ``None`` if the platform
        delivers replies via outbound API instead of inline."""

    async def send_message(
        self,
        connector: BotConnector,
        *,
        chat_id: str,
        text: str,
    ) -> tuple[bool, str | None]: ...
