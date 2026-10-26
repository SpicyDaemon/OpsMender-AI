"""Microsoft Teams connector adapter.

Teams delivery is being rebuilt (M1-96). Microsoft Graph lets an app post
chat messages with app-only auth only during a migration, and Graph cards allow
link buttons only, so this adapter sends nothing and its connection test fails
with the reason. Stored connectors keep their credentials for the Teams
Workflows connector and bot that replace this path.
"""

from __future__ import annotations

from typing import Any, Mapping

from fastapi import HTTPException, status

from backend.bots.capabilities import TEAMS_REBUILDING
from backend.bots.delivery import DeliveryReceipt
from backend.db.models import BotConnector
from .base import FieldSpec, InboundMessage


class TeamsAdapter:
    """Microsoft Teams adapter while Teams delivery is being rebuilt (M1-96).

    It sends nothing. Its form keeps the values a saved Teams channel holds
    for the Teams Workflows connector and bot that replace this channel.
    """

    platform = "teams"

    @classmethod
    def form_schema(cls) -> list[FieldSpec]:
        return [
            FieldSpec(
                name="tenant_id",
                label="Azure AD tenant ID",
                kind="text",
                group="credentials",
                required=True,
                helper=(
                    "Directory (tenant) ID of your Azure AD app. Kept for the "
                    "Teams Workflows connector and bot that replace this channel. "
                    "Nothing is sent to Teams today."
                ),
                placeholder="00000000-0000-0000-0000-000000000000",
            ),
            FieldSpec(
                name="client_id",
                label="Application (client) ID",
                kind="text",
                group="credentials",
                required=True,
                helper=(
                    "Application (client) ID of your Azure AD app. Kept for the "
                    "Teams Workflows connector and bot."
                ),
                placeholder="00000000-0000-0000-0000-000000000000",
            ),
            FieldSpec(
                name="client_secret",
                label="Client secret",
                kind="secret",
                group="credentials",
                required=True,
                helper=(
                    "Client secret value (not the secret ID). Kept for the Teams "
                    "Workflows connector and bot."
                ),
            ),
            FieldSpec(
                name="bot_app_id",
                label="Bot framework app ID",
                kind="text",
                group="config",
                required=False,
                helper=(
                    "App ID of the Teams bot registration. Kept for the Teams bot "
                    "that will add card actions. Teams has no actions today."
                ),
            ),
            FieldSpec(
                name="default_chat_id",
                label="Default chat ID",
                kind="text",
                group="config",
                required=False,
                helper=(
                    "Optional. Teams chat ID. Kept for the Teams Workflows "
                    "connector and bot. Nothing is sent to it today."
                ),
            ),
        ]

    def verify_webhook(
        self,
        connector: BotConnector,
        *,
        headers: Mapping[str, str],
        raw_body: bytes,
    ) -> None:
        raise HTTPException(
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
            detail="Teams activities are verified by the Bot Framework activity endpoint",
        )

    def parse_inbound(
        self,
        payload: dict[str, Any],
    ) -> InboundMessage | None:
        return None

    def inline_reply(
        self,
        chat_id: str,
        text: str,
    ) -> dict[str, Any] | None:
        return None

    async def send_message(
        self,
        connector: BotConnector,
        *,
        chat_id: str,
        text: str,
    ) -> tuple[bool, str | None]:
        """Send nothing (M1-96): Teams delivery is being rebuilt."""
        return False, TEAMS_REBUILDING

    async def send_incident_update(
        self,
        connector: BotConnector,
        *,
        chat_id: str,
        text: str,
        incident=None,
        native_actions_ready: bool = False,
        status_update: bool = False,
        facts: list[tuple[str, str]] | None = None,
        acknowledged_by: str | None = None,
    ) -> DeliveryReceipt:
        """Send nothing (M1-96): Teams delivery is being rebuilt."""
        return DeliveryReceipt(ok=False, error=TEAMS_REBUILDING)

    async def test_connection(
        self,
        connector: BotConnector,
    ) -> tuple[bool, str | None]:
        """Fail with the reason, without a network call (M1-96). Fetching a
        Graph token used to pass here although no message could be sent."""
        return False, TEAMS_REBUILDING
