"""Discord connector adapter."""

from __future__ import annotations

import json
from typing import Any, Mapping

from fastapi import HTTPException, status
import httpx
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from cryptography.exceptions import InvalidSignature

from backend.db.models import BotConnector
from .base import BotConnectorAdapter, FieldSpec, InboundMessage


class DiscordAdapter:
    """Adapter for Discord Interactions (webhooks)."""

    platform = "discord"

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

        signature = headers.get("x-signature-ed25519") or headers.get("X-Signature-Ed25519")
        timestamp = headers.get("x-signature-timestamp") or headers.get("X-Signature-Timestamp")

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
            }
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

        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"https://discord.com/api/v10/channels/{chat_id}/messages",
                headers={
                    "Authorization": f"Bot {bot_token}",
                    "Content-Type": "application/json",
                },
                json={
                    "content": text,
                },
                timeout=10.0,
            )
            if resp.status_code not in (200, 201):
                return False, f"Discord API error: HTTP {resp.status_code} - {resp.text}"
            
            return True, None
