"""Inbound chat bot webhook endpoints."""

from __future__ import annotations

import asyncio
import secrets
import uuid
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.deps import get_current_session_factory, get_db
from backend.api.routes.ws import publish
from backend.api.schemas import WSMessage
from backend.bots.rate_limit import rate_limiter, resolve_per_minute
from backend.chat import respond_to_user_message
from backend.db.models import BotConnector, Incident
from backend.db.repos import (
    ApprovalRequestRepo,
    BotActionAuditRepo,
    BotConnectorRepo,
    IncidentRepo,
    SessionMessageRepo,
    SessionRepo,
)

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
        "/incident <id> - show one incident\n"
        "/sessions - list recent sessions\n"
        "/session <id> - show one session\n"
        "/approvals - list pending approvals\n"
        "/approve <id> - approve a pending request\n"
        "/reject <id> - reject a pending request\n"
        "/chat <session-id> <message> - relay a message to a session co-pilot"
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


def _has_capability(connector: BotConnector, capability: str) -> bool:
    return capability in set(connector.allowed_capabilities or [])


def _capability_denied(capability: str) -> str:
    return f"{capability.replace('_', ' ').title()} is not enabled for this connector."


def _format_session(session) -> str:
    lines = [
        f"*Session `{session.id}`*",
        f"Status: `{session.status}`",
        f"Tier: `{session.tier}`",
    ]
    if session.incident_id:
        lines.append(f"Incident: `{session.incident_id}`")
    if session.model_provider or session.model_id:
        lines.append(
            f"Model: `{session.model_provider or 'unknown'} / {session.model_id or 'unknown'}`"
        )
    if session.summary:
        lines.append(f"Summary: {session.summary}")
    return "\n".join(lines)


def _format_session_list(sessions: list) -> str:
    if not sessions:
        return "No sessions found."
    lines = ["Recent sessions:"]
    for session in sessions:
        incident = f" incident `{session.incident_id}`" if session.incident_id else ""
        lines.append(
            f"- `{session.id}` ({session.status}, tier {session.tier}){incident}"
        )
    return "\n".join(lines)


def _format_approval_list(requests: list) -> str:
    if not requests:
        return "No pending approvals."
    lines = ["Pending approvals:"]
    for request in requests:
        tool_name = request.action.get("tool_name") or request.action.get("name") or "action"
        lines.append(f"- `{request.id}` session `{request.session_id}` action `{tool_name}`")
    return "\n".join(lines)


async def _audit(
    db: AsyncSession,
    connector: BotConnector,
    *,
    chat_id: str | None,
    command: str | None,
    status: str,
    detail: str | None = None,
    session_id: uuid.UUID | None = None,
) -> None:
    await BotActionAuditRepo.create(
        db,
        connector_id=connector.id,
        platform=connector.platform,
        chat_id=chat_id,
        command=command,
        status=status,
        detail=detail,
        session_id=session_id,
    )
    await db.commit()


async def _resolve_approval_from_bot(
    db: AsyncSession,
    request_id: uuid.UUID,
    *,
    decision: str,
) -> str:
    request = await ApprovalRequestRepo.get_by_id(db, request_id)
    if request is None:
        return "Approval request not found."
    if request.status != "pending":
        return f"Approval request is already {request.status}."

    updated = await ApprovalRequestRepo.resolve(db, request.id, status=decision)
    if not updated:
        return "Approval request could not be resolved."

    await SessionRepo.set_status(db, request.session_id, status="active")
    await db.commit()
    return f"Approval request `{request.id}` {decision}."


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
        await _audit(
            db,
            connector,
            chat_id=chat_id,
            command=None,
            status="chat_not_allowed",
        )
        return _telegram_reply(chat_id, "This chat is not allowed to use AIM.")

    text = _telegram_message_text(payload)
    command, _, raw_arg = text.partition(" ")
    command = command.split("@", 1)[0].lower()
    arg = raw_arg.strip()

    # Rate limit before any meaningful work — skip for /start /help so users
    # can still discover the bot when their bucket is full.
    if command not in {"/start", "/help", "help"}:
        per_minute = resolve_per_minute(connector.config)
        allowed, _remaining = rate_limiter.check(
            connector.id, chat_id, per_minute=per_minute
        )
        if not allowed:
            await _audit(
                db,
                connector,
                chat_id=chat_id,
                command=command or None,
                status="rate_limited",
                detail=f"limit={per_minute}/min",
            )
            return _telegram_reply(
                chat_id,
                f"Rate limit hit ({per_minute}/min). Please wait a moment.",
            )

    if command in {"/start", "/help", "help"}:
        await _audit(db, connector, chat_id=chat_id, command="/help", status="ok")
        return _telegram_reply(chat_id, _help_text())

    if command == "/incidents":
        if not _has_capability(connector, "incident_lookup"):
            await _audit(
                db, connector, chat_id=chat_id, command=command,
                status="capability_denied", detail="incident_lookup",
            )
            return _telegram_reply(chat_id, _capability_denied("incident_lookup"))
        incidents = list(await IncidentRepo.list_all(db, limit=5, offset=0))
        await _audit(db, connector, chat_id=chat_id, command=command, status="ok")
        return _telegram_reply(chat_id, _format_incident_list(incidents))

    if command == "/incident":
        if not _has_capability(connector, "incident_lookup"):
            await _audit(
                db, connector, chat_id=chat_id, command=command,
                status="capability_denied", detail="incident_lookup",
            )
            return _telegram_reply(chat_id, _capability_denied("incident_lookup"))
        if not arg:
            await _audit(db, connector, chat_id=chat_id, command=command, status="bad_args")
            return _telegram_reply(chat_id, "Usage: /incident <incident-id>")
        try:
            incident_id = uuid.UUID(arg)
        except ValueError:
            await _audit(db, connector, chat_id=chat_id, command=command, status="bad_args")
            return _telegram_reply(chat_id, "Incident ID must be a valid UUID.")
        incident = await IncidentRepo.get_by_id(db, incident_id)
        if incident is None:
            await _audit(db, connector, chat_id=chat_id, command=command, status="not_found")
            return _telegram_reply(chat_id, "Incident not found.")
        await _audit(db, connector, chat_id=chat_id, command=command, status="ok")
        return _telegram_reply(chat_id, _format_incident(incident))

    if command == "/sessions":
        if not _has_capability(connector, "session_status"):
            await _audit(
                db, connector, chat_id=chat_id, command=command,
                status="capability_denied", detail="session_status",
            )
            return _telegram_reply(chat_id, _capability_denied("session_status"))
        sessions = list(await SessionRepo.list_all(db, limit=5, offset=0))
        await _audit(db, connector, chat_id=chat_id, command=command, status="ok")
        return _telegram_reply(chat_id, _format_session_list(sessions))

    if command == "/session":
        if not _has_capability(connector, "session_status"):
            await _audit(
                db, connector, chat_id=chat_id, command=command,
                status="capability_denied", detail="session_status",
            )
            return _telegram_reply(chat_id, _capability_denied("session_status"))
        if not arg:
            await _audit(db, connector, chat_id=chat_id, command=command, status="bad_args")
            return _telegram_reply(chat_id, "Usage: /session <session-id>")
        try:
            session_id = uuid.UUID(arg)
        except ValueError:
            await _audit(db, connector, chat_id=chat_id, command=command, status="bad_args")
            return _telegram_reply(chat_id, "Session ID must be a valid UUID.")
        session = await SessionRepo.get_by_id(db, session_id)
        if session is None:
            await _audit(
                db, connector, chat_id=chat_id, command=command,
                status="not_found", session_id=session_id,
            )
            return _telegram_reply(chat_id, "Session not found.")
        await _audit(
            db, connector, chat_id=chat_id, command=command,
            status="ok", session_id=session_id,
        )
        return _telegram_reply(chat_id, _format_session(session))

    if command == "/approvals":
        if not _has_capability(connector, "approvals"):
            await _audit(
                db, connector, chat_id=chat_id, command=command,
                status="capability_denied", detail="approvals",
            )
            return _telegram_reply(chat_id, _capability_denied("approvals"))
        requests = list(await ApprovalRequestRepo.list(db, status="pending", limit=5))
        await _audit(db, connector, chat_id=chat_id, command=command, status="ok")
        return _telegram_reply(chat_id, _format_approval_list(requests))

    if command in {"/approve", "/reject"}:
        if not _has_capability(connector, "approvals"):
            await _audit(
                db, connector, chat_id=chat_id, command=command,
                status="capability_denied", detail="approvals",
            )
            return _telegram_reply(chat_id, _capability_denied("approvals"))
        if not arg:
            await _audit(db, connector, chat_id=chat_id, command=command, status="bad_args")
            return _telegram_reply(chat_id, f"Usage: {command} <approval-id>")
        try:
            request_id = uuid.UUID(arg)
        except ValueError:
            await _audit(db, connector, chat_id=chat_id, command=command, status="bad_args")
            return _telegram_reply(chat_id, "Approval ID must be a valid UUID.")
        decision = "approved" if command == "/approve" else "rejected"
        text = await _resolve_approval_from_bot(db, request_id, decision=decision)
        await _audit(
            db, connector, chat_id=chat_id, command=command,
            status="ok" if decision in text else "noop", detail=decision,
        )
        return _telegram_reply(chat_id, text)

    if command == "/chat":
        if not _has_capability(connector, "copilot_chat"):
            await _audit(
                db, connector, chat_id=chat_id, command=command,
                status="capability_denied", detail="copilot_chat",
            )
            return _telegram_reply(chat_id, _capability_denied("copilot_chat"))

        session_token, _, message_body = arg.partition(" ")
        session_token = session_token.strip()
        message_body = message_body.strip()
        if not session_token or not message_body:
            await _audit(db, connector, chat_id=chat_id, command=command, status="bad_args")
            return _telegram_reply(
                chat_id, "Usage: /chat <session-id> <message>"
            )
        try:
            target_session_id = uuid.UUID(session_token)
        except ValueError:
            await _audit(db, connector, chat_id=chat_id, command=command, status="bad_args")
            return _telegram_reply(chat_id, "Session ID must be a valid UUID.")

        target_session = await SessionRepo.get_by_id(db, target_session_id)
        if target_session is None:
            await _audit(
                db, connector, chat_id=chat_id, command=command,
                status="not_found", session_id=target_session_id,
            )
            return _telegram_reply(chat_id, "Session not found.")

        message = await SessionMessageRepo.create(
            db,
            session_id=target_session_id,
            role="user",
            content=f"[telegram chat {chat_id}] {message_body}",
        )
        await db.commit()

        await publish(
            target_session_id,
            WSMessage(
                type="chat_message_user",
                data={
                    "id": str(message.id),
                    "session_id": str(target_session_id),
                    "role": "user",
                    "content": message.content,
                    "created_at": message.created_at.isoformat(),
                    "node_context": message.node_context,
                },
            ),
        )

        try:
            factory = get_current_session_factory()
            asyncio.create_task(
                respond_to_user_message(
                    factory,
                    session_id=target_session_id,
                    user_message_id=message.id,
                )
            )
        except RuntimeError:
            # Session factory not registered (e.g. unit tests bypassing app
            # lifespan). The user message is still persisted; skip the
            # background reply.
            pass

        await _audit(
            db, connector, chat_id=chat_id, command=command,
            status="ok", session_id=target_session_id,
        )
        return _telegram_reply(
            chat_id,
            f"Message relayed to session `{target_session_id}`. "
            "The co-pilot reply will appear in the AIM dashboard.",
        )

    await _audit(
        db, connector, chat_id=chat_id, command=command or None,
        status="unknown_command",
    )
    return _telegram_reply(chat_id, _help_text())
