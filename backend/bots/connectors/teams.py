"""Microsoft Teams connector adapter (Sprint 37 step 1).

For v1 we use **app-only** auth — the operator registers an Azure AD
app, grants application permissions in the Azure portal, and pastes
``tenant_id`` / ``client_id`` / ``client_secret`` here. No browser
redirect. The :mod:`backend.auth.graph_oauth` helper acquires + caches
Graph tokens from those credentials.

This step lands the connector shape (form schema + typed credentials)
and a "test connection" path. Outbound DM delivery via
``chats/{id}/messages`` and the inbound bot-activity endpoint land in
subsequent Sprint 37 steps.
"""

from __future__ import annotations

from typing import Any, Mapping

from fastapi import HTTPException, status

from backend.auth.graph_oauth import GraphOAuthError, acquire_app_only_token
from backend.db.models import BotConnector
from .base import BotConnectorAdapter, FieldSpec, InboundMessage


class TeamsAdapter:
    """Adapter for Microsoft Teams via Microsoft Graph (app-only auth)."""

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
                helper="Directory (tenant) ID for your Azure AD app — found on the app's Overview page.",
                doc_url="https://learn.microsoft.com/azure/active-directory/develop/howto-create-service-principal-portal",
                placeholder="00000000-0000-0000-0000-000000000000",
            ),
            FieldSpec(
                name="client_id",
                label="Application (client) ID",
                kind="text",
                group="credentials",
                required=True,
                helper="Application (client) ID for the Azure AD app registration.",
                placeholder="00000000-0000-0000-0000-000000000000",
            ),
            FieldSpec(
                name="client_secret",
                label="Client secret",
                kind="secret",
                group="credentials",
                required=True,
                helper="Client secret value (not the secret ID). Create one under Certificates & secrets.",
                doc_url="https://learn.microsoft.com/azure/active-directory/develop/howto-create-service-principal-portal#option-3-create-a-new-client-secret",
            ),
            FieldSpec(
                name="bot_app_id",
                label="Bot framework app ID",
                kind="text",
                group="config",
                required=False,
                helper="Optional. App ID of the Teams bot registration if you've set one up — needed later for inbound activity callbacks.",
            ),
            FieldSpec(
                name="default_chat_id",
                label="Default chat ID",
                kind="text",
                group="config",
                required=False,
                helper="Optional. Teams chat ID used for outbound notifications when no per-user chat is resolved.",
            ),
        ]

    def verify_webhook(
        self,
        connector: BotConnector,
        *,
        headers: Mapping[str, str],
        raw_body: bytes,
    ) -> None:
        # Teams bot-activity verification lands in a later Sprint 37
        # step. For now we refuse inbound webhooks — the connector is
        # outbound-only.
        raise HTTPException(
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
            detail="Teams inbound activity endpoint is not yet wired (Sprint 37 step 4)",
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
        # Outbound delivery lands in Sprint 37 step 2 (Graph
        # `chats/{id}/messages`). Until then, the connector advertises
        # itself but explicitly refuses to send.
        return False, "Teams outbound delivery lands in Sprint 37 step 2"

    async def test_connection(
        self,
        connector: BotConnector,
    ) -> tuple[bool, str | None]:
        """Verify the configured Graph credentials by acquiring a token.

        Called by the existing ``POST /bot-connectors/{id}/test`` route.
        Returns ``(True, None)`` on success, ``(False, error)`` on
        failure. Never raises.
        """

        creds = connector.credentials or {}
        try:
            await acquire_app_only_token(
                tenant_id=creds.get("tenant_id") or "",
                client_id=creds.get("client_id") or "",
                client_secret=creds.get("client_secret") or "",
                force_refresh=True,
            )
        except GraphOAuthError as exc:
            return False, str(exc)
        return True, None
