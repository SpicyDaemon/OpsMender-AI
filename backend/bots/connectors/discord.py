"""Discord connector adapter."""

from __future__ import annotations

from typing import Any, Callable, Mapping

from fastapi import HTTPException, status
import httpx
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from cryptography.exceptions import InvalidSignature

from backend.bots.delivery import DeliveryReceipt, UpdateResult
from backend.db.models import BotConnector
from .base import FieldSpec, InboundMessage


class DiscordAdapter:
    """Adapter for Discord Interactions (webhooks)."""

    platform = "discord"

    def __init__(
        self,
        *,
        http_client_factory: Callable[[], httpx.AsyncClient] | None = None,
    ) -> None:
        self._factory = http_client_factory or (lambda: httpx.AsyncClient(timeout=10.0))

    @classmethod
    def form_schema(cls) -> list[FieldSpec]:
        return [
            FieldSpec(
                name="public_key",
                label="Application public key",
                kind="secret",
                group="credentials",
                required=True,
                helper="Hex string from your Discord application → General Information → Public Key.",
                doc_url="https://discord.com/developers/docs/interactions/receiving-and-responding#security-and-authorization",
            ),
            FieldSpec(
                name="bot_token",
                label="Bot token",
                kind="secret",
                group="credentials",
                required=True,
                helper="From your application's Bot tab. Required for outbound message delivery.",
                doc_url="https://discord.com/developers/docs/topics/oauth2#bots",
            ),
            FieldSpec(
                name="default_chat_id",
                label="Discord Channel ID",
                kind="text",
                group="config",
                required=False,
                helper=(
                    "Optional. The Discord channel ID where OpsMender should post "
                    "outbound notifications. In Discord, enable Developer Mode, "
                    "right-click the channel, and copy its ID."
                ),
                placeholder="123456789012345678",
            ),
        ]

    def verify_webhook(
        self,
        connector: BotConnector,
        *,
        headers: Mapping[str, str],
        raw_body: bytes,
    ) -> None:
        if connector.platform != self.platform:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Connector is not a Discord connector",
            )
        if not connector.is_enabled:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Connector is disabled",
            )

        credentials = connector.credentials or {}
        public_key_hex = credentials.get("public_key")
        if not public_key_hex:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Discord public key is not configured",
            )

        signature = headers.get("x-signature-ed25519") or headers.get(
            "X-Signature-Ed25519"
        )
        timestamp = headers.get("x-signature-timestamp") or headers.get(
            "X-Signature-Timestamp"
        )

        if not signature or not timestamp:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Missing Discord signature headers",
            )

        try:
            vk = Ed25519PublicKey.from_public_bytes(bytes.fromhex(public_key_hex))
            msg = timestamp.encode("utf-8") + raw_body
            vk.verify(bytes.fromhex(signature), msg)
        except (InvalidSignature, ValueError) as exc:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=f"Invalid Discord signature: {exc}",
            )

    def parse_inbound(
        self,
        payload: dict[str, Any],
    ) -> InboundMessage | None:
        # Discord Interaction Types:
        # 1: PING
        # 2: APPLICATION_COMMAND
        # 3: MESSAGE_COMPONENT
        # 4: APPLICATION_COMMAND_AUTOCOMPLETE
        # 5: MODAL_SUBMIT

        interaction_type = payload.get("type")
        if interaction_type == 1:  # PING
            # This is handled in the route
            return None

        if interaction_type != 2:  # We only care about commands for now
            return None

        data = payload.get("data") or {}
        # For now, we assume simple text-based commands or a fallback to text
        # If it's a slash command, we might want to reconstruct the command string
        command_name = data.get("name")
        options = data.get("options") or []

        # Reconstruct something like "/command arg1 arg2"
        args = [str(opt.get("value")) for opt in options]
        text = f"/{command_name} {' '.join(args)}".strip()

        channel_id = payload.get("channel_id")
        member = payload.get("member") or {}
        user = member.get("user") or payload.get("user") or {}
        user_id = user.get("id")

        return InboundMessage(
            chat_id=str(channel_id),
            platform_user_id=str(user_id) if user_id else None,
            text=text,
        )

    def inline_reply(
        self,
        chat_id: str,
        text: str,
    ) -> dict[str, Any] | None:
        # For Discord Interactions, the response type 4 is "CHANNEL_MESSAGE_WITH_SOURCE"
        return {
            "type": 4,
            "data": {
                "content": text,
            },
        }

    async def send_message(
        self,
        connector: BotConnector,
        *,
        chat_id: str,
        text: str,
    ) -> tuple[bool, str | None]:
        credentials = connector.credentials or {}
        bot_token = credentials.get("bot_token")
        if not bot_token:
            return False, "Discord bot token is not configured"

        try:
            async with self._factory() as client:
                resp = await client.post(
                    f"https://discord.com/api/v10/channels/{chat_id}/messages",
                    headers={
                        "Authorization": f"Bot {bot_token}",
                        "Content-Type": "application/json",
                    },
                    json={"content": text[:2000]},
                )
        except httpx.HTTPError as exc:
            return False, f"Discord network error: {exc}"
        if resp.status_code not in (200, 201):
            return False, f"Discord API error: HTTP {resp.status_code} - {resp.text}"
        return True, None

    async def send_incident_update(
        self,
        connector: BotConnector,
        *,
        chat_id: str,
        text: str,
        incident=None,
        native_actions_ready: bool = False,
        status_update: bool = False,
    ) -> DeliveryReceipt:
        credentials = connector.credentials or {}
        bot_token = credentials.get("bot_token")
        if not bot_token:
            return DeliveryReceipt(
                ok=False, error="Discord bot token is not configured"
            )
        try:
            async with self._factory() as client:
                response = await client.post(
                    f"https://discord.com/api/v10/channels/{chat_id}/messages",
                    headers={
                        "Authorization": f"Bot {bot_token}",
                        "Content-Type": "application/json",
                    },
                    json={"content": text[:2000]},
                )
        except httpx.HTTPError as exc:
            return DeliveryReceipt(ok=False, error=f"Discord network error: {exc}")
        if response.status_code not in (200, 201):
            return DeliveryReceipt(
                ok=False,
                error=(
                    f"Discord API error: HTTP {response.status_code} - {response.text}"
                ),
            )
        data = response.json()
        message_id = data.get("id")
        return DeliveryReceipt(
            ok=True,
            external_channel_id=str(data.get("channel_id") or chat_id),
            external_message_id=str(message_id) if message_id else None,
            can_update=bool(message_id),
        )

    async def update_incident_update(
        self,
        connector: BotConnector,
        *,
        chat_id: str,
        text: str,
        external_message_id: str,
        external_thread_id: str | None = None,
        incident=None,
        native_actions_ready: bool = False,
        status_update: bool = False,
    ) -> UpdateResult:
        credentials = connector.credentials or {}
        bot_token = credentials.get("bot_token")
        if not bot_token:
            return UpdateResult(ok=False, error="Discord bot token is not configured")
        try:
            async with self._factory() as client:
                response = await client.patch(
                    f"https://discord.com/api/v10/channels/{chat_id}/messages/"
                    f"{external_message_id}",
                    headers={
                        "Authorization": f"Bot {bot_token}",
                        "Content-Type": "application/json",
                    },
                    json={"content": text[:2000]},
                )
        except httpx.HTTPError as exc:
            return UpdateResult(
                ok=False,
                error=f"Discord network error: {exc}",
                fallback_to_followup=True,
            )
        if response.status_code != 200:
            return UpdateResult(
                ok=False,
                error=(
                    f"Discord API error: HTTP {response.status_code} - {response.text}"
                ),
                fallback_to_followup=response.status_code in {403, 404},
            )
        data = response.json()
        return UpdateResult(
            ok=True,
            receipt=DeliveryReceipt(
                ok=True,
                external_channel_id=str(data.get("channel_id") or chat_id),
                external_message_id=str(data.get("id") or external_message_id),
                external_thread_id=external_thread_id,
                can_update=True,
            ),
        )
