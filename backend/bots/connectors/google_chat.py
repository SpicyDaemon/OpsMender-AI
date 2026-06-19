"""Google Chat Respond/Track notification adapter with durable message edits."""

from __future__ import annotations

from typing import Any, Callable, Mapping

from fastapi import HTTPException, status
import httpx

from backend.auth.secrets import decrypt_secret
from backend.bots.delivery import DeliveryReceipt, UpdateResult
from backend.db.models import BotConnector
from backend.integrations.google_auth import service_account_access_token
from .base import FieldSpec, InboundMessage

_ENCRYPTED_PREFIX = "enc:"
_CHAT_SCOPE = ("https://www.googleapis.com/auth/chat.bot",)


def _plain(value: object) -> str:
    text = str(value or "")
    if text.startswith(_ENCRYPTED_PREFIX):
        return decrypt_secret(text[len(_ENCRYPTED_PREFIX) :])
    return text


class GoogleChatAdapter:
    platform = "google_chat"

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
                name="default_chat_id",
                label="Google Chat space name",
                group="config",
                required=True,
                placeholder="spaces/AAAA...",
                helper=(
                    "Resource name of a space that contains the configured Chat app."
                ),
                doc_url=(
                    "https://developers.google.com/workspace/chat/"
                    "authenticate-authorize-chat-app"
                ),
            ),
            FieldSpec(
                name="client_email",
                label="Service account email",
                group="credentials",
                required=True,
                placeholder="chat-app@project.iam.gserviceaccount.com",
            ),
            FieldSpec(
                name="private_key",
                label="Service account private key",
                kind="textarea",
                group="credentials",
                required=True,
                placeholder="-----BEGIN PRIVATE KEY-----",
                helper="Paste the private_key value from the service-account JSON key.",
            ),
            FieldSpec(
                name="token_uri",
                label="OAuth token URL",
                kind="url",
                group="config",
                required=False,
                default="https://oauth2.googleapis.com/token",
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
            status_code=status.HTTP_405_METHOD_NOT_ALLOWED,
            detail="Google Chat notification channels are outbound-only",
        )

    def parse_inbound(self, payload: dict[str, Any]) -> InboundMessage | None:
        return None

    def inline_reply(self, chat_id: str, text: str) -> dict[str, Any] | None:
        return None

    @staticmethod
    def _auth(connector: BotConnector) -> dict[str, str]:
        credentials = connector.credentials or {}
        config = connector.config or {}
        return {
            "client_email": _plain(credentials.get("client_email")),
            "private_key": _plain(credentials.get("private_key")),
            "token_uri": str(
                config.get("token_uri") or "https://oauth2.googleapis.com/token"
            ),
        }

    async def _token(self, client: httpx.AsyncClient, connector: BotConnector) -> str:
        return await service_account_access_token(
            client, self._auth(connector), scopes=_CHAT_SCOPE
        )

    async def _create(
        self, connector: BotConnector, *, chat_id: str, text: str
    ) -> DeliveryReceipt:
        try:
            async with self._factory() as client:
                token = await self._token(client, connector)
                response = await client.post(
                    f"https://chat.googleapis.com/v1/{chat_id.strip('/')}/messages",
                    headers={
                        "Authorization": f"Bearer {token}",
                        "Content-Type": "application/json",
                    },
                    json={"text": text[:32000]},
                )
        except (httpx.HTTPError, RuntimeError, ValueError) as exc:
            return DeliveryReceipt(ok=False, error=f"Google Chat error: {exc}")
        if response.status_code not in {200, 201}:
            return DeliveryReceipt(
                ok=False,
                error=f"Google Chat API error: HTTP {response.status_code}",
            )
        data = response.json()
        message_name = data.get("name")
        thread_name = (data.get("thread") or {}).get("name")
        return DeliveryReceipt(
            ok=True,
            external_channel_id=chat_id,
            external_message_id=str(message_name) if message_name else None,
            external_thread_id=str(thread_name) if thread_name else None,
            can_update=bool(message_name),
        )

    async def test_connection(self, connector: BotConnector) -> tuple[bool, str | None]:
        chat_id = str((connector.config or {}).get("default_chat_id") or "").strip("/")
        if not chat_id:
            return False, "Google Chat space name is not configured"
        try:
            async with self._factory() as client:
                token = await self._token(client, connector)
                response = await client.get(
                    f"https://chat.googleapis.com/v1/{chat_id}",
                    headers={"Authorization": f"Bearer {token}"},
                )
        except (httpx.HTTPError, RuntimeError, ValueError) as exc:
            return False, f"Google Chat error: {exc}"
        if response.status_code != 200:
            return False, f"Google Chat API error: HTTP {response.status_code}"
        return True, None

    async def send_message(
        self,
        connector: BotConnector,
        *,
        chat_id: str,
        text: str,
    ) -> tuple[bool, str | None]:
        receipt = await self._create(connector, chat_id=chat_id, text=text)
        return receipt.ok, receipt.error

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
        return await self._create(connector, chat_id=chat_id, text=text)

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
        try:
            async with self._factory() as client:
                token = await self._token(client, connector)
                response = await client.patch(
                    f"https://chat.googleapis.com/v1/{external_message_id.strip('/')}",
                    params={"updateMask": "text"},
                    headers={
                        "Authorization": f"Bearer {token}",
                        "Content-Type": "application/json",
                    },
                    json={"text": text[:32000]},
                )
        except (httpx.HTTPError, RuntimeError, ValueError) as exc:
            return UpdateResult(
                ok=False,
                error=f"Google Chat error: {exc}",
                fallback_to_followup=True,
            )
        if response.status_code != 200:
            return UpdateResult(
                ok=False,
                error=f"Google Chat API error: HTTP {response.status_code}",
                fallback_to_followup=response.status_code in {403, 404},
            )
        data = response.json()
        return UpdateResult(
            ok=True,
            receipt=DeliveryReceipt(
                ok=True,
                external_channel_id=chat_id,
                external_message_id=str(data.get("name") or external_message_id),
                external_thread_id=external_thread_id,
                can_update=True,
            ),
        )
