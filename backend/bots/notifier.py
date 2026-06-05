"""Outbound delivery of OpsMender events into chat connectors."""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Iterable

import sqlalchemy
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

import backend.bots  # noqa: F401  -- registers built-in adapters
from backend.bots.capabilities import supports_interactive_actions
from backend.bots.connectors import get_adapter
from backend.bots.incident_card import build_incident_message
from backend.db.models import BotActionAudit, BotConnector
from backend.db.repos import (
    BotActionAuditRepo,
    BotConnectorRepo,
    IncidentAssignmentRepo,
    IncidentPageRepo,
    IncidentRepo,
    ServiceRepo,
    SessionRepo,
    TeamRepo,
    UserRepo,
)

log = logging.getLogger(__name__)


SESSION_CHAT_EVENTS = {
    "session.created",
    "session.awaiting_approval",
    "session.active",
    "session.completed",
    "session.failed",
    "session.timed_out",
}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _allowed_chat_ids(connector: BotConnector) -> list[str]:
    config = connector.config or {}
    raw = config.get("allowed_chat_ids") or []
    if not isinstance(raw, list):
        return []
    return [str(item) for item in raw if item is not None]


def _has_capability(connector: BotConnector, capability: str) -> bool:
    return capability in set(connector.allowed_capabilities or [])


def _format_session_event(
    *,
    event_type: str,
    session_id: uuid.UUID,
    session,
    incident,
) -> str:
    label = event_type.replace("session.", "").replace("_", " ").title()
    lines = [f"*OpsMender Session {label}*", f"Session: `{session_id}`"]
    if session is not None:
        lines.append(f"Tier: `{session.tier}`")
        lines.append(f"Status: `{session.status}`")
    if incident is not None:
        lines.append(f"Incident: `{incident.title}` ({incident.severity or 'unknown'})")
    return "\n".join(lines)


def _format_copilot_reply(text: str) -> str:
    return f"*Co-pilot reply:*\n{text}"


async def _deliver_to_telegram(
    factory: async_sessionmaker[AsyncSession],
    *,
    org_id: uuid.UUID,
    connector: BotConnector,
    chat_id: str,
    text: str,
    command_label: str,
    session_id: uuid.UUID | None,
) -> None:
    bot_token = (connector.credentials or {}).get("bot_token")
    from backend.bots.telegram import send_message as telegram_send

    ok, error = await telegram_send(
        bot_token=str(bot_token) if bot_token else "",
        chat_id=chat_id,
        text=text,
    )

    async with factory() as db:
        await BotActionAuditRepo.create(
            db,
            org_id,
            connector_id=connector.id,
            platform=connector.platform,
            chat_id=chat_id,
            command=command_label,
            status="ok" if ok else "delivery_failed",
            detail=None if ok else (error or "")[:1000],
            session_id=session_id,
        )
        if not ok:
            await BotConnectorRepo.mark_status(
                db,
                org_id,
                connector.id,
                status="error",
                error=(error or "")[:1000],
            )
        await db.commit()


async def _deliver_via_adapter(
    factory: async_sessionmaker[AsyncSession],
    *,
    org_id: uuid.UUID,
    connector: BotConnector,
    chat_id: str,
    text: str,
    command_label: str,
    session_id: uuid.UUID | None,
) -> None:
    adapter = get_adapter(connector.platform)
    if adapter is None:
        return
    ok, error = await adapter.send_message(connector, chat_id=chat_id, text=text)

    async with factory() as db:
        await BotActionAuditRepo.create(
            db,
            org_id,
            connector_id=connector.id,
            platform=connector.platform,
            chat_id=chat_id,
            command=command_label,
            status="ok" if ok else "delivery_failed",
            detail=None if ok else (error or "")[:1000],
            session_id=session_id,
        )
        if not ok:
            await BotConnectorRepo.mark_status(
                db,
                org_id,
                connector.id,
                status="error",
                error=(error or "")[:1000],
            )
        await db.commit()


async def _deliver(
    factory: async_sessionmaker[AsyncSession],
    *,
    org_id: uuid.UUID,
    connector: BotConnector,
    chat_id: str,
    text: str,
    command_label: str,
    session_id: uuid.UUID | None,
) -> None:
    if connector.platform == "telegram":
        await _deliver_to_telegram(
            factory,
            org_id=org_id,
            connector=connector,
            chat_id=chat_id,
            text=text,
            command_label=command_label,
            session_id=session_id,
        )
    else:
        await _deliver_via_adapter(
            factory,
            org_id=org_id,
            connector=connector,
            chat_id=chat_id,
            text=text,
            command_label=command_label,
            session_id=session_id,
        )


async def deliver_session_chat_event(
    factory: async_sessionmaker[AsyncSession],
    *,
    org_id: uuid.UUID,
    event_type: str,
    session_id: uuid.UUID,
) -> None:
    if event_type not in SESSION_CHAT_EVENTS:
        return

    async with factory() as db:
        session = await SessionRepo.get_by_id(db, org_id, session_id)
        incident = None
        if session and session.incident_id:
            incident = await IncidentRepo.get_by_id(db, org_id, session.incident_id)
        connectors = list(
            await BotConnectorRepo.list_all(db, org_id, enabled_only=True)
        )

    if session is None:
        return

    text = _format_session_event(
        event_type=event_type,
        session_id=session_id,
        session=session,
        incident=incident,
    )

    for connector in connectors:
        if not _has_capability(connector, "notifications"):
            continue
        if get_adapter(connector.platform) is None:
            continue
        for chat_id in _allowed_chat_ids(connector):
            await _deliver(
                factory,
                org_id=org_id,
                connector=connector,
                chat_id=chat_id,
                text=text,
                command_label=f"notify:{event_type}",
                session_id=session_id,
            )


def schedule_session_chat_event(
    factory: async_sessionmaker[AsyncSession],
    *,
    org_id: uuid.UUID,
    task_registry: set[asyncio.Task] | None,
    event_type: str,
    session_id: uuid.UUID,
) -> asyncio.Task:
    return asyncio.create_task(
        deliver_session_chat_event(
            factory,
            org_id=org_id,
            event_type=event_type,
            session_id=session_id,
        )
    )


INCIDENT_CHAT_EVENTS = {
    "incident.created",
    "incident.acknowledged",
    "incident.resolved",
    "incident.escalated",
    "incident.updated",
}


def _display_name(user) -> str | None:
    if user is None or getattr(user, "deleted_at", None) is not None:
        return None
    full = f"{user.first_name or ''} {user.last_name or ''}".strip()
    return full or user.username


async def _resolve_incident_responder(
    db: AsyncSession,
    org_id: uuid.UUID,
    incident_id: uuid.UUID,
) -> dict:
    """Lightweight responder snapshot for the card.

    Mirrors the incidents route precedence (acknowledged assignment wins,
    otherwise the latest escalation page) without importing the route module.
    Also surfaces escalation context — the level (latest page step index) and
    the previous responder paged before the current one — for escalation cards.
    """
    assignment = await IncidentAssignmentRepo.get_active(db, org_id, incident_id)
    pages = list(await IncidentPageRepo.list_for_incident(db, org_id, incident_id))
    latest = pages[-1] if pages else None

    ack_uid = assignment.assigned_to if assignment is not None else None
    esc_uid = latest.user_id if latest is not None else None
    esc_step = latest.step_index if latest is not None else None

    if ack_uid is not None:
        state, resp_uid = "assigned", ack_uid
    elif esc_uid is not None:
        state = "escalated" if (esc_step or 0) > 0 else "awaiting"
        resp_uid = esc_uid
    else:
        state, resp_uid = "unassigned", None

    # Previous responder = the most recent page target *before* the latest one,
    # skipping repeats of the current target.
    prev_uid = None
    for page in reversed(pages[:-1]) if len(pages) > 1 else []:
        if page.user_id != esc_uid:
            prev_uid = page.user_id
            break

    async def _name(uid):
        if uid is None:
            return None
        return _display_name(await UserRepo.get_by_id(db, uid))

    return {
        "responder_state": state,
        "responder_display_name": await _name(resp_uid),
        "acknowledged_by_display_name": await _name(ack_uid),
        "escalation_level": esc_step,
        "previous_responder_display_name": await _name(prev_uid),
    }


async def deliver_incident_event(
    factory: async_sessionmaker[AsyncSession],
    *,
    org_id: uuid.UUID,
    incident_id: uuid.UUID,
    event_type: str,
    base_url: str | None = None,
) -> None:
    """Post an incident card/message to every enabled Notification Channel that
    has the ``notifications`` capability.

    Honest delivery: each platform receives the same useful incident message
    with an authenticated incident link. Interactive action controls are only
    rendered when the *platform* advertises verified interactive callbacks
    (``capabilities.supports_interactive_actions``), which is never the case in
    v1 — so the message always routes the recipient into OpsMender to act.
    Delivery-only platforms (SMS, email, custom webhook, …) get the same
    message minus any card framing, which their adapters already handle.
    """
    if event_type not in INCIDENT_CHAT_EVENTS:
        return

    async with factory() as db:
        incident = await IncidentRepo.get_by_id(db, org_id, incident_id)
        if incident is None:
            return

        service_name: str | None = None
        team_name: str | None = None
        if incident.service_id is not None:
            service = await ServiceRepo.get_by_id(db, org_id, incident.service_id)
            if service is not None:
                service_name = service.name
                team = await TeamRepo.get_by_id(db, org_id, service.team_id)
                team_name = team.name if team is not None else None

        responder = await _resolve_incident_responder(db, org_id, incident.id)
        connectors = list(
            await BotConnectorRepo.list_all(db, org_id, enabled_only=True)
        )

    for connector in connectors:
        if not _has_capability(connector, "notifications"):
            continue
        if get_adapter(connector.platform) is None and connector.platform != "telegram":
            continue
        text = build_incident_message(
            incident,
            event_type=event_type,
            base_url=base_url,
            responder=responder,
            service_name=service_name,
            team_name=team_name,
            supports_actions=supports_interactive_actions(connector.platform),
        )
        for chat_id in _allowed_chat_ids(connector):
            await _deliver(
                factory,
                org_id=org_id,
                connector=connector,
                chat_id=chat_id,
                text=text,
                command_label=f"notify:{event_type}",
                session_id=None,
            )


def schedule_incident_event(
    factory: async_sessionmaker[AsyncSession],
    *,
    org_id: uuid.UUID,
    event_type: str,
    incident_id: uuid.UUID,
    base_url: str | None = None,
    task_registry: set[asyncio.Task] | None = None,
) -> asyncio.Task:
    task = asyncio.create_task(
        deliver_incident_event(
            factory,
            org_id=org_id,
            incident_id=incident_id,
            event_type=event_type,
            base_url=base_url,
        )
    )
    if task_registry is not None:
        task_registry.add(task)
        task.add_done_callback(task_registry.discard)
    return task


async def deliver_incident_text(
    factory: async_sessionmaker[AsyncSession],
    *,
    org_id: uuid.UUID,
    text: str,
    event_type: str,
) -> None:
    """Fan out a *pre-built* incident message to enabled Notification Channels
    with the ``notifications`` capability.

    Used when the caller has already resolved the message from a live session
    (e.g. the escalation engine, where the new page row may not be committed
    yet) so the background delivery does not have to re-read incident state.
    """
    async with factory() as db:
        connectors = list(
            await BotConnectorRepo.list_all(db, org_id, enabled_only=True)
        )

    for connector in connectors:
        if not _has_capability(connector, "notifications"):
            continue
        if get_adapter(connector.platform) is None and connector.platform != "telegram":
            continue
        for chat_id in _allowed_chat_ids(connector):
            await _deliver(
                factory,
                org_id=org_id,
                connector=connector,
                chat_id=chat_id,
                text=text,
                command_label=f"notify:{event_type}",
                session_id=None,
            )


def schedule_incident_text(
    factory: async_sessionmaker[AsyncSession],
    *,
    org_id: uuid.UUID,
    text: str,
    event_type: str,
    task_registry: set[asyncio.Task] | None = None,
) -> asyncio.Task:
    task = asyncio.create_task(
        deliver_incident_text(
            factory, org_id=org_id, text=text, event_type=event_type
        )
    )
    if task_registry is not None:
        task_registry.add(task)
        task.add_done_callback(task_registry.discard)
    return task


async def _resolve_relay_targets(
    factory: async_sessionmaker[AsyncSession],
    *,
    org_id: uuid.UUID,
    session_id: uuid.UUID,
) -> Iterable[tuple[BotConnector, str]]:
    async with factory() as db:
        stmt = (
            sqlalchemy.select(BotActionAudit)
            .where(
                BotActionAudit.org_id == org_id,
                BotActionAudit.session_id == session_id,
                BotActionAudit.command == "/chat",
                BotActionAudit.status == "ok",
            )
            .order_by(BotActionAudit.created_at.desc())
        )
        rows = (await db.execute(stmt)).scalars().all()

        seen: set[tuple[uuid.UUID, str]] = set()
        targets: list[tuple[BotConnector, str]] = []
        for row in rows:
            if row.chat_id is None:
                continue
            key = (row.connector_id, row.chat_id)
            if key in seen:
                continue
            connector = await BotConnectorRepo.get_by_id(db, org_id, row.connector_id)
            if connector is None or not connector.is_enabled:
                continue
            if not _has_capability(connector, "copilot_chat"):
                continue
            seen.add(key)
            targets.append((connector, row.chat_id))
        return targets


async def deliver_copilot_relay(
    factory: async_sessionmaker[AsyncSession],
    *,
    org_id: uuid.UUID,
    session_id: uuid.UUID,
    reply_text: str,
) -> None:
    targets = await _resolve_relay_targets(factory, org_id=org_id, session_id=session_id)
    formatted = _format_copilot_reply(reply_text)
    for connector, chat_id in targets:
        await _deliver(
            factory,
            org_id=org_id,
            connector=connector,
            chat_id=chat_id,
            text=formatted,
            command_label="copilot_relay",
            session_id=session_id,
        )


def schedule_copilot_relay(
    factory: async_sessionmaker[AsyncSession],
    *,
    org_id: uuid.UUID,
    task_registry: set[asyncio.Task] | None,
    session_id: uuid.UUID,
    reply_text: str,
) -> asyncio.Task:
    return asyncio.create_task(
        deliver_copilot_relay(
            factory,
            org_id=org_id,
            session_id=session_id,
            reply_text=reply_text,
        )
    )
