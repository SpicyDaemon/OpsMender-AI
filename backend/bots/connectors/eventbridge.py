"""AWS EventBridge Track-lane delivery adapter."""

from __future__ import annotations

import asyncio
import json
from typing import Any, Mapping

from fastapi import HTTPException, status

from backend.auth.secrets import decrypt_secret
from backend.bots.delivery import DeliveryReceipt
from backend.db.models import BotConnector
from .base import FieldSpec, InboundMessage

_ENCRYPTED_PREFIX = "enc:"


def _plain(value: object) -> str:
    text = str(value or "")
    if text.startswith(_ENCRYPTED_PREFIX):
        return decrypt_secret(text[len(_ENCRYPTED_PREFIX):])
    return text


class EventBridgeAdapter:
    platform = "eventbridge"

    @classmethod
    def form_schema(cls) -> list[FieldSpec]:
        return [
            FieldSpec(
                name="region",
                label="AWS region",
                group="credentials",
                required=True,
                placeholder="us-east-1",
            ),
            FieldSpec(
                name="event_bus_name",
                label="Event bus name",
                group="credentials",
                required=True,
                placeholder="default",
            ),
            FieldSpec(
                name="access_key_id",
                label="Access key ID",
                kind="secret",
                group="credentials",
                required=False,
                helper="Optional. Leave blank to use the runtime AWS credential chain.",
            ),
            FieldSpec(
                name="secret_access_key",
                label="Secret access key",
                kind="secret",
                group="credentials",
                required=False,
            ),
            FieldSpec(
                name="session_token",
                label="Session token",
                kind="secret",
                group="credentials",
                required=False,
            ),
            FieldSpec(
                name="role_arn",
                label="Assume-role ARN",
                kind="secret",
                group="credentials",
                required=False,
                helper="Optional role to assume before publishing events.",
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
            detail="EventBridge is outbound-only",
        )

    def parse_inbound(self, payload: dict[str, Any]) -> InboundMessage | None:
        return None

    def inline_reply(self, chat_id: str, text: str) -> dict[str, Any] | None:
        return None

    def _client(self, connector: BotConnector):
        import boto3

        creds = connector.credentials or {}
        region = _plain(creds.get("region"))
        session_kwargs: dict[str, str] = {"region_name": region}
        access_key = _plain(creds.get("access_key_id"))
        secret_key = _plain(creds.get("secret_access_key"))
        session_token = _plain(creds.get("session_token"))
        if access_key and secret_key:
            session_kwargs.update(
                aws_access_key_id=access_key,
                aws_secret_access_key=secret_key,
            )
            if session_token:
                session_kwargs["aws_session_token"] = session_token
        session = boto3.Session(**session_kwargs)
        role_arn = _plain(creds.get("role_arn"))
        if not role_arn:
            return session.client("events", region_name=region)
        assumed = session.client("sts", region_name=region).assume_role(
            RoleArn=role_arn,
            RoleSessionName="opsmender-eventbridge",
        )["Credentials"]
        return boto3.client(
            "events",
            region_name=region,
            aws_access_key_id=assumed["AccessKeyId"],
            aws_secret_access_key=assumed["SecretAccessKey"],
            aws_session_token=assumed["SessionToken"],
        )

    def _put(self, connector: BotConnector, detail: dict[str, Any]) -> DeliveryReceipt:
        creds = connector.credentials or {}
        bus = _plain(creds.get("event_bus_name"))
        try:
            response = self._client(connector).put_events(
                Entries=[
                    {
                        "Source": "opsmender",
                        "DetailType": "opsmender.incident.status",
                        "Detail": json.dumps(detail),
                        "EventBusName": bus,
                    }
                ]
            )
        except Exception as exc:  # noqa: BLE001 - outbound failures are non-fatal
            return DeliveryReceipt(ok=False, error=str(exc))
        entry = (response.get("Entries") or [{}])[0]
        if response.get("FailedEntryCount") or entry.get("ErrorCode"):
            return DeliveryReceipt(
                ok=False,
                error=str(entry.get("ErrorMessage") or entry.get("ErrorCode") or "PutEvents failed"),
            )
        return DeliveryReceipt(
            ok=True,
            external_channel_id=bus,
            external_message_id=str(entry.get("EventId")) if entry.get("EventId") else None,
            can_update=False,
        )

    async def send_message(
        self,
        connector: BotConnector,
        *,
        chat_id: str,
        text: str,
    ) -> tuple[bool, str | None]:
        receipt = await asyncio.to_thread(
            self._put,
            connector,
            {"schema_version": "1.0", "message": text},
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
        service_name: str | None = None,
        team_name: str | None = None,
    ) -> DeliveryReceipt:
        if incident is None:
            return DeliveryReceipt(ok=False, error="Incident is required")
        priority = incident.priority or (
            "P0" if str(incident.severity or "").lower() == "critical" else None
        )
        detail = {
            "schema_version": "1.0",
            "incident": {
                "id": str(incident.id),
                "title": incident.title,
                "severity": incident.severity,
                "priority": priority,
                "status": incident.status,
                "service": service_name or incident.external_source,
                "team": team_name,
                "timestamps": {
                    "created_at": incident.created_at.isoformat() if incident.created_at else None,
                    "acknowledged_at": incident.acknowledged_at.isoformat()
                    if getattr(incident, "acknowledged_at", None)
                    else None,
                    "updated_at": incident.updated_at.isoformat() if incident.updated_at else None,
                },
            },
        }
        return await asyncio.to_thread(self._put, connector, detail)

    async def test_connection(
        self,
        connector: BotConnector,
    ) -> tuple[bool, str | None]:
        try:
            await asyncio.to_thread(self._client(connector).list_event_buses, Limit=1)
        except Exception as exc:  # noqa: BLE001
            return False, str(exc)
        return True, None
