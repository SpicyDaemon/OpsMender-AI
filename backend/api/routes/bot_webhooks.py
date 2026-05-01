"""Inbound chat bot webhook endpoints."""

from __future__ import annotations

import secrets
import uuid
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.deps import get_db
from backend.db.models import BotConnector, Incident
from backend.db.repos import BotConnectorRepo, IncidentRepo

router = APIRouter(prefix="/bot-connectors", tags=["bot-webhooks"])


def _telegram_chat_id(payload: dict[str, Any]) -> str | None:
    message = payload.get("message") or payload.get("edited_message") or {}
    chat = message.get("chat") or {}
    chat_id = chat.get("id")
    return None if chat_id is None else str(chat_id)


def _telegram_message_text(payload: dict[str, Any]) -> str:
    message = payload.get("message") or payload.get("edited_message") or {}
    text = message.get("text")
    return text.strip() if isinstance(text, str) else ""


def _telegram_reply(chat_id: str, text: str) -> dict[str, Any]:
    return {
        "method": "sendMessage",
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "Markdown",
        "disable_web_page_preview": True,
    }


def _help_text() -> str:
    return (
        "AIM Telegram connector commands:\n"
        "/incidents - list recent incidents\n"
        "/incident <id> - show one incident"
    )


def _format_incident(incident: Incident) -> str:
    severity = incident.severity or "unknown"
    return (
        f"*{incident.title}*\n"
        f"ID: `{incident.id}`\n"
        f"Severity: `{severity}`\n"
        f"Status: `{incident.status}`\n"
        f"{incident.description}"
    )


def _format_incident_list(incidents: list[Incident]) -> str:
    if not incidents:
        return "No incidents found."
    lines = ["Recent incidents:"]
    for incident in incidents:
        severity = incident.severity or "unknown"
        lines.append(
            f"- `{incident.id}` {incident.title} ({severity}, {incident.status})"
        )
    return "\n".join(lines)


def _validate_telegram_connector(
    connector: BotConnector,
    secret_token: str | None,
) -> None:
    if connector.platform != "telegram":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Connector is not a Telegram connector",
        )
    if not connector.is_enabled:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Connector is disabled",
        )

    credentials = connector.credentials or {}
    expected_secret = credentials.get("webhook_secret")
    if not expected_secret:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Telegram webhook secret is not configured",
        )
    if not secret_token or not secrets.compare_digest(
        str(expected_secret),
        secret_token,
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid Telegram webhook secret",
        )


def _chat_allowed(connector: BotConnector, chat_id: str) -> bool:
    config = connector.config or {}
    allowed_chat_ids = config.get("allowed_chat_ids")
    if not allowed_chat_ids:
        return True
    if not isinstance(allowed_chat_ids, list):
        return False
    return chat_id in {str(item) for item in allowed_chat_ids}


@router.post(
    "/{connector_id}/telegram/webhook",
    summary="Handle inbound Telegram bot webhook updates",
)
async def telegram_webhook(
    connector_id: uuid.UUID,
    payload: dict[str, Any],
    db: AsyncSession = Depends(get_db),
    telegram_secret_token: str | None = Header(
        default=None,
        alias="X-Telegram-Bot-Api-Secret-Token",
    ),
):
    connector = await BotConnectorRepo.get_by_id(db, connector_id)
    if connector is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Bot connector not found",
        )

    _validate_telegram_connector(connector, telegram_secret_token)

    chat_id = _telegram_chat_id(payload)
    if not chat_id:
        return {"ok": True}
    if not _chat_allowed(connector, chat_id):
        return _telegram_reply(chat_id, "This chat is not allowed to use AIM.")

    text = _telegram_message_text(payload)
    command, _, raw_arg = text.partition(" ")
    command = command.split("@", 1)[0].lower()
    arg = raw_arg.strip()

    if command in {"/start", "/help", "help"}:
        return _telegram_reply(chat_id, _help_text())

    if "incident_lookup" not in set(connector.allowed_capabilities or []):
        return _telegram_reply(
            chat_id,
            "Incident lookup is not enabled for this connector.",
        )

    if command == "/incidents":
        incidents = list(await IncidentRepo.list_all(db, limit=5, offset=0))
        return _telegram_reply(chat_id, _format_incident_list(incidents))

    if command == "/incident":
        if not arg:
            return _telegram_reply(chat_id, "Usage: /incident <incident-id>")
        try:
            incident_id = uuid.UUID(arg)
        except ValueError:
            return _telegram_reply(chat_id, "Incident ID must be a valid UUID.")
        incident = await IncidentRepo.get_by_id(db, incident_id)
        if incident is None:
            return _telegram_reply(chat_id, "Incident not found.")
        return _telegram_reply(chat_id, _format_incident(incident))

    return _telegram_reply(chat_id, _help_text())
