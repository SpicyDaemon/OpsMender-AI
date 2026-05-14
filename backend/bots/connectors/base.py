"""Connector adapter interface."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Mapping, Protocol

from backend.db.models import BotConnector


FieldKind = Literal["text", "secret", "select", "textarea", "url"]
FieldGroup = Literal["config", "credentials"]


@dataclass(frozen=True)
class FieldSpec:
    """Schema for a single typed field shown to operators in the
    connector configuration form.

    ``group`` decides which JSON blob on ``BotConnector`` the value is
    persisted under: ``"credentials"`` is the encrypted secret bag,
    ``"config"`` is the non-secret JSON. ``kind`` is a UI hint —
    ``secret`` renders as a password input with show/hide, ``select``
    requires ``options``, ``textarea`` is a multi-line text input.
    """

    name: str
    label: str
    kind: FieldKind = "text"
    group: FieldGroup = "config"
    required: bool = False
    default: Any = None
    helper: str | None = None
    doc_url: str | None = None
    placeholder: str | None = None
    options: tuple[tuple[str, str], ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class InboundMessage:
    """Normalized inbound chat message.

    ``chat_id`` and ``platform_user_id`` are opaque strings whose meaning
    is platform-specific; OpsMender uses them only as map keys for allowlists,
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

    @classmethod
    def form_schema(cls) -> list[FieldSpec]:
        """Return the typed field schema for this adapter's config form.

        Default returns an empty list; concrete adapters override.
        """
        return []

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
