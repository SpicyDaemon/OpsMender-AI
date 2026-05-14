"""Platform-agnostic command dispatcher for inbound bot messages.

Owns capability gating, identity / role enforcement, rate limiting,
audit logging, and command handlers.
"""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.deps import get_current_session_factory
from backend.api.routes.ws import publish
from backend.api.schemas import WSMessage
from backend.bots.connectors.base import InboundMessage
from backend.bots.rate_limit import rate_limiter, resolve_per_minute
from backend.chat import respond_to_user_message
from backend.db.models import BotConnector, Incident, User
from backend.db.repos import (
    ApprovalRequestRepo,
    BotActionAuditRepo,
    BotUserLinkRepo,
    IncidentRepo,
    SessionMessageRepo,
    SessionRepo,
    UserRepo,
)


@dataclass(frozen=True)
class DispatchResult:
    reply_text: str | None
    """Text to deliver to the chat. ``None`` skips delivery (e.g. unknown chat)."""


_HELP_TEXT = (
    "OpsMender connector commands:\n"
    "/incidents - list recent incidents\n"
    "/incident <id> - show one incident\n"
    "/sessions - list recent sessions\n"
    "/session <id> - show one session\n"
    "/approvals - list pending approvals\n"
    "/approve <id> - approve a pending request\n"
    "/reject <id> - reject a pending request\n"
    "/chat <session-id> <message> - relay a message to a session co-pilot"
)

_MUTATING_COMMANDS = {"/approve", "/reject", "/chat"}
_REQUIRED_ROLES_BY_COMMAND = {
    "/approve": {"admin", "operator"},
    "/reject": {"admin", "operator"},
    "/chat": {"admin", "operator"},
}
_REQUIRED_CAPABILITY_BY_COMMAND = {
    "/approve": "approvals",
    "/reject": "approvals",
    "/chat": "copilot_chat",
}


def _has_capability(connector: BotConnector, capability: str) -> bool:
    return capability in set(connector.allowed_capabilities or [])


def _capability_denied_text(capability: str) -> str:
    return f"{capability.replace('_', ' ').title()} is not enabled for this connector."


def _chat_allowed(connector: BotConnector, chat_id: str) -> bool:
    config = connector.config or {}
    allowed = config.get("allowed_chat_ids")
    if not allowed:
        return True
    if not isinstance(allowed, list):
        return False
    return chat_id in {str(item) for item in allowed}


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


def _format_session_list(sessions) -> str:
    if not sessions:
        return "No sessions found."
    lines = ["Recent sessions:"]
    for session in sessions:
        incident = f" incident `{session.incident_id}`" if session.incident_id else ""
        lines.append(
            f"- `{session.id}` ({session.status}, tier {session.tier}){incident}"
        )
    return "\n".join(lines)


def _format_approval_list(requests) -> str:
    if not requests:
        return "No pending approvals."
    lines = ["Pending approvals:"]
    for request in requests:
        tool_name = (
            request.action.get("tool_name")
            or request.action.get("name")
            or "action"
        )
        lines.append(
            f"- `{request.id}` session `{request.session_id}` action `{tool_name}`"
        )
    return "\n".join(lines)


async def _audit(
    db: AsyncSession,
    org_id: uuid.UUID,
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
        org_id,
        connector_id=connector.id,
        platform=connector.platform,
        chat_id=chat_id,
        command=command,
        status=status,
        detail=detail,
        session_id=session_id,
    )
    await db.commit()


async def _resolve_opsmender_user(
    db: AsyncSession,
    org_id: uuid.UUID,
    connector: BotConnector,
    platform_user_id: str | None,
) -> User | None:
    if platform_user_id is None:
        return None
    link = await BotUserLinkRepo.get_by_platform_user(
        db,
        org_id,
        connector_id=connector.id,
        platform_user_id=platform_user_id,
    )
    if link is None:
        return None
    opsmender_user = await UserRepo.get_by_id(db, link.opsmender_user_id)
    if opsmender_user is None or not opsmender_user.is_active:
        return None
    return opsmender_user


async def _resolve_approval_from_bot(
    db: AsyncSession,
    org_id: uuid.UUID,
    request_id: uuid.UUID,
    *,
    decision: str,
) -> str:
    request = await ApprovalRequestRepo.get_by_id(db, org_id, request_id)
    if request is None:
        return "Approval request not found."
    if request.status != "pending":
        return f"Approval request is already {request.status}."

    updated = await ApprovalRequestRepo.resolve(db, org_id, request.id, status=decision)
    if not updated:
        return "Approval request could not be resolved."

    await SessionRepo.set_status(db, org_id, request.session_id, status="active")
    await db.commit()
    return f"Approval request `{request.id}` {decision}."


async def dispatch_inbound(
    db: AsyncSession,
    *,
    connector: BotConnector,
    message: InboundMessage,
) -> DispatchResult:
    org_id = connector.org_id
    chat_id = message.chat_id
    platform_user_id = message.platform_user_id

    if not _chat_allowed(connector, chat_id):
        await _audit(
            db,
            org_id,
            connector,
            chat_id=chat_id,
            command=None,
            status="chat_not_allowed",
            detail=f"from={platform_user_id}" if platform_user_id else None,
        )
        return DispatchResult(reply_text="This chat is not allowed to use OpsMender.")

    text = message.text
    command, _, raw_arg = text.partition(" ")
    command = command.split("@", 1)[0].lower()
    arg = raw_arg.strip()

    if command not in {"/start", "/help", "help"}:
        per_minute = resolve_per_minute(connector.config)
        allowed, _remaining = rate_limiter.check(
            connector.id, chat_id, per_minute=per_minute
        )
        if not allowed:
            await _audit(
                db,
                org_id,
                connector,
                chat_id=chat_id,
                command=command or None,
                status="rate_limited",
                detail=f"limit={per_minute}/min",
            )
            return DispatchResult(
                reply_text=f"Rate limit hit ({per_minute}/min). Please wait a moment.",
            )

    opsmender_user: User | None = None
    capability_for_command = _REQUIRED_CAPABILITY_BY_COMMAND.get(command)
    if (
        command in _MUTATING_COMMANDS
        and capability_for_command is not None
        and _has_capability(connector, capability_for_command)
    ):
        opsmender_user = await _resolve_opsmender_user(db, org_id, connector, platform_user_id)
        if opsmender_user is None:
            await _audit(
                db,
                org_id,
                connector,
                chat_id=chat_id,
                command=command,
                status="unauthorized",
                detail=f"from={platform_user_id}",
            )
            return DispatchResult(
                reply_text=(
                    "This user is not linked to an OpsMender account. "
                    f"Ask an admin to link platform user `{platform_user_id}` "
                    "via `POST /bot-connectors/<id>/user-links`."
                ),
            )
        required_roles = _REQUIRED_ROLES_BY_COMMAND.get(command, set())
        if required_roles and opsmender_user.role not in required_roles:
            await _audit(
                db,
                org_id,
                connector,
                chat_id=chat_id,
                command=command,
                status="role_denied",
                detail=f"from={platform_user_id} role={opsmender_user.role}",
            )
            return DispatchResult(
                reply_text=(
                    f"Your OpsMender role `{opsmender_user.role}` cannot run `{command}`. "
                    f"Required: {', '.join(sorted(required_roles))}."
                ),
            )

    if command in {"/start", "/help", "help"}:
        await _audit(db, org_id, connector, chat_id=chat_id, command="/help", status="ok")
        return DispatchResult(reply_text=_HELP_TEXT)

    if command == "/incidents":
        if not _has_capability(connector, "incident_lookup"):
            await _audit(
                db, org_id, connector, chat_id=chat_id, command=command,
                status="capability_denied", detail="incident_lookup",
            )
            return DispatchResult(reply_text=_capability_denied_text("incident_lookup"))
        incidents = list(await IncidentRepo.list_all(db, org_id, limit=5, offset=0))
        await _audit(db, org_id, connector, chat_id=chat_id, command=command, status="ok")
        return DispatchResult(reply_text=_format_incident_list(incidents))

    if command == "/incident":
        if not _has_capability(connector, "incident_lookup"):
            await _audit(
                db, org_id, connector, chat_id=chat_id, command=command,
                status="capability_denied", detail="incident_lookup",
            )
            return DispatchResult(reply_text=_capability_denied_text("incident_lookup"))
        if not arg:
            await _audit(db, org_id, connector, chat_id=chat_id, command=command, status="bad_args")
            return DispatchResult(reply_text="Usage: /incident <incident-id>")
        try:
            incident_id = uuid.UUID(arg)
        except ValueError:
            await _audit(db, org_id, connector, chat_id=chat_id, command=command, status="bad_args")
            return DispatchResult(reply_text="Incident ID must be a valid UUID.")
        incident = await IncidentRepo.get_by_id(db, org_id, incident_id)
        if incident is None:
            await _audit(db, org_id, connector, chat_id=chat_id, command=command, status="not_found")
            return DispatchResult(reply_text="Incident not found.")
        await _audit(db, org_id, connector, chat_id=chat_id, command=command, status="ok")
        return DispatchResult(reply_text=_format_incident(incident))

    if command == "/sessions":
        if not _has_capability(connector, "session_status"):
            await _audit(
                db, org_id, connector, chat_id=chat_id, command=command,
                status="capability_denied", detail="session_status",
            )
            return DispatchResult(reply_text=_capability_denied_text("session_status"))
        sessions = list(await SessionRepo.list_all(db, org_id, limit=5, offset=0))
        await _audit(db, org_id, connector, chat_id=chat_id, command=command, status="ok")
        return DispatchResult(reply_text=_format_session_list(sessions))

    if command == "/session":
        if not _has_capability(connector, "session_status"):
            await _audit(
                db, org_id, connector, chat_id=chat_id, command=command,
                status="capability_denied", detail="session_status",
            )
            return DispatchResult(reply_text=_capability_denied_text("session_status"))
        if not arg:
            await _audit(db, org_id, connector, chat_id=chat_id, command=command, status="bad_args")
            return DispatchResult(reply_text="Usage: /session <session-id>")
        try:
            session_id = uuid.UUID(arg)
        except ValueError:
            await _audit(db, org_id, connector, chat_id=chat_id, command=command, status="bad_args")
            return DispatchResult(reply_text="Session ID must be a valid UUID.")
        session = await SessionRepo.get_by_id(db, org_id, session_id)
        if session is None:
            await _audit(
                db, org_id, connector, chat_id=chat_id, command=command,
                status="not_found", session_id=session_id,
            )
            return DispatchResult(reply_text="Session not found.")
        await _audit(
            db, org_id, connector, chat_id=chat_id, command=command,
            status="ok", session_id=session_id,
        )
        return DispatchResult(reply_text=_format_session(session))

    if command == "/approvals":
        if not _has_capability(connector, "approvals"):
            await _audit(
                db, org_id, connector, chat_id=chat_id, command=command,
                status="capability_denied", detail="approvals",
            )
            return DispatchResult(reply_text=_capability_denied_text("approvals"))
        requests = list(await ApprovalRequestRepo.list(db, org_id, status="pending", limit=5))
        await _audit(db, org_id, connector, chat_id=chat_id, command=command, status="ok")
        return DispatchResult(reply_text=_format_approval_list(requests))

    if command in {"/approve", "/reject"}:
        if not _has_capability(connector, "approvals"):
            await _audit(
                db, org_id, connector, chat_id=chat_id, command=command,
                status="capability_denied", detail="approvals",
            )
            return DispatchResult(reply_text=_capability_denied_text("approvals"))
        if not arg:
            await _audit(db, org_id, connector, chat_id=chat_id, command=command, status="bad_args")
            return DispatchResult(reply_text=f"Usage: {command} <approval-id>")
        try:
            request_id = uuid.UUID(arg)
        except ValueError:
            await _audit(db, org_id, connector, chat_id=chat_id, command=command, status="bad_args")
            return DispatchResult(reply_text="Approval ID must be a valid UUID.")
        decision = "approved" if command == "/approve" else "rejected"
        result_text = await _resolve_approval_from_bot(
            db, org_id, request_id, decision=decision
        )
        await _audit(
            db, org_id, connector, chat_id=chat_id, command=command,
            status="ok" if decision in result_text else "noop",
            detail=decision,
        )
        return DispatchResult(reply_text=result_text)

    if command == "/chat":
        if not _has_capability(connector, "copilot_chat"):
            await _audit(
                db, org_id, connector, chat_id=chat_id, command=command,
                status="capability_denied", detail="copilot_chat",
            )
            return DispatchResult(reply_text=_capability_denied_text("copilot_chat"))

        session_token, _, message_body = arg.partition(" ")
        session_token = session_token.strip()
        message_body = message_body.strip()
        if not session_token or not message_body:
            await _audit(db, org_id, connector, chat_id=chat_id, command=command, status="bad_args")
            return DispatchResult(reply_text="Usage: /chat <session-id> <message>")
        try:
            target_session_id = uuid.UUID(session_token)
        except ValueError:
            await _audit(db, org_id, connector, chat_id=chat_id, command=command, status="bad_args")
            return DispatchResult(reply_text="Session ID must be a valid UUID.")

        target_session = await SessionRepo.get_by_id(db, org_id, target_session_id)
        if target_session is None:
            await _audit(
                db, org_id, connector, chat_id=chat_id, command=command,
                status="not_found", session_id=target_session_id,
            )
            return DispatchResult(reply_text="Session not found.")

        new_msg = await SessionMessageRepo.create(
            db,
            org_id,
            session_id=target_session_id,
            role="user",
            content=f"[{connector.platform} chat {chat_id}] {message_body}",
        )
        await db.commit()

        await publish(
            target_session_id,
            WSMessage(
                type="chat_message_user",
                data={
                    "id": str(new_msg.id),
                    "session_id": str(target_session_id),
                    "role": "user",
                    "content": new_msg.content,
                    "created_at": new_msg.created_at.isoformat(),
                    "node_context": new_msg.node_context,
                },
            ),
        )

        try:
            factory = get_current_session_factory()
            asyncio.create_task(
                respond_to_user_message(
                    factory,
                    org_id=org_id,
                    session_id=target_session_id,
                    user_message_id=new_msg.id,
                )
            )
        except RuntimeError:
            pass

        await _audit(
            db, org_id, connector, chat_id=chat_id, command=command,
            status="ok", session_id=target_session_id,
        )
        return DispatchResult(
            reply_text=(
                f"Message relayed to session `{target_session_id}`. "
                "The co-pilot reply will appear in the OpsMender dashboard."
            ),
        )

    await _audit(
        db, org_id, connector, chat_id=chat_id, command=command or None,
        status="unknown_command",
    )
    return DispatchResult(reply_text=_HELP_TEXT)
