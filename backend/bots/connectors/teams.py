"""Microsoft Teams connector adapter.

For v1 we use **app-only** auth — the operator registers an Azure AD
app, grants application permissions in the Azure portal, and pastes
``tenant_id`` / ``client_id`` / ``client_secret`` here. No browser
redirect. The :mod:`backend.auth.graph_oauth` helper acquires + caches
Graph tokens from those credentials.

Notification delivery uses Microsoft Graph app-only authentication. Verified
native actions arrive through the Bot Framework activity endpoint.
"""

from __future__ import annotations

import os
from typing import Any, Mapping

from fastapi import HTTPException, status
import httpx

from backend.auth.graph_oauth import GraphOAuthError, acquire_app_only_token
from backend.bots.delivery import DeliveryReceipt
from backend.db.models import BotConnector
from backend.paging.teams_cards import build_graph_chat_message
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
                helper="App ID of the Teams bot registration. Required for verified native actions.",
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
        receipt = await self._post_graph_message(
            connector,
            chat_id=chat_id,
            payload={
                "body": {
                    "contentType": "text",
                    "content": text,
                }
            },
        )
        return receipt.ok, receipt.error

    async def send_incident_update(
        self,
        connector: BotConnector,
        *,
        chat_id: str,
        text: str,
        incident=None,
        native_actions_ready: bool = False,
    ) -> DeliveryReceipt:
        payload = (
            build_graph_chat_message(
                incident,
                base_url=os.environ.get("OPSMENDER_PUBLIC_URL"),
                include_native_actions=native_actions_ready,
            )
            if incident is not None
            else {
                "body": {
                    "contentType": "text",
                    "content": text,
                }
            }
        )
        return await self._post_graph_message(
            connector,
            chat_id=chat_id,
            payload=payload,
        )

    async def _post_graph_message(
        self,
        connector: BotConnector,
        *,
        chat_id: str,
        payload: dict[str, Any],
    ) -> DeliveryReceipt:
        credentials = connector.credentials or {}
        try:
            token = await acquire_app_only_token(
                tenant_id=str(credentials.get("tenant_id") or ""),
                client_id=str(credentials.get("client_id") or ""),
                client_secret=str(credentials.get("client_secret") or ""),
            )
        except GraphOAuthError as exc:
            return DeliveryReceipt(ok=False, error=f"graph_oauth: {exc}")

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.post(
                    f"https://graph.microsoft.com/v1.0/chats/{chat_id}/messages",
                    headers={
                        "Authorization": (
                            f"{token.token_type} {token.access_token}"
                        ),
                        "Content-Type": "application/json; charset=utf-8",
                    },
                    json=payload,
                )
        except httpx.HTTPError as exc:
            return DeliveryReceipt(ok=False, error=f"network: {exc}")

        if response.status_code not in {200, 201}:
            try:
                error = (response.json().get("error") or {}).get("code")
            except ValueError:
                error = None
            return DeliveryReceipt(
                ok=False,
                error=f"graph: {error or f'http {response.status_code}'}",
            )
        try:
            data = response.json()
        except ValueError:
            data = {}
        return DeliveryReceipt(
            ok=True,
            external_channel_id=chat_id,
            external_message_id=str(data.get("id")) if data.get("id") else None,
            can_update=False,
        )

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
