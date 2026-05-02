"""Outbound delivery of AIM events into chat connectors.

Two entry points:

- ``schedule_session_chat_event`` — fan out a session lifecycle event
  (``session.created``, ``session.awaiting_approval``, etc.) to every
  enabled chat connector with the ``notifications`` capability and a
  non-empty ``allowed_chat_ids`` config.

- ``schedule_copilot_relay`` — push a co-pilot assistant reply back into
  the Telegram chat(s) that originated ``/chat <session-id> ...`` for the
  given session, identified by querying the ``bot_action_audit`` table.

Both schedule background ``asyncio`` tasks that handle their own errors —
delivery failures are logged + audited but never raise into the caller.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Iterable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.bots.telegram import send_message as telegram_send
from backend.db.models import BotActionAudit, BotConnector
from backend.db.repos import (
    BotActionAuditRepo,
    BotConnectorRepo,
    IncidentRepo,
    SessionRepo,
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
    lines = [f"*AIM Session {label}*", f"Session: `{session_id}`"]
    if session is not None:
        lines.append(f"Tier: `{session.tier}`")
        lines.append(f"Status: `{session.status}`")
    if incident is not None:
        lines.append(f"Incident: `{incident.title}` ({incident.severity or 'unknown'})")
    return "\n".join(lines)


def _format_copilot_reply(text: str) -> str:
    return f"*Co-pilot reply:*\n{text}"


# ---------------------------------------------------------------------------
# Delivery internals
# ---------------------------------------------------------------------------


async def _deliver_to_telegram(
    factory: async_sessionmaker[AsyncSession],
    *,
    connector: BotConnector,
    chat_id: str,
    text: str,
    command_label: str,
    session_id: uuid.UUID | None,
) -> None:
    bot_token = (connector.credentials or {}).get("bot_token")
    ok, error = await telegram_send(
        bot_token=str(bot_token) if bot_token else "",
        chat_id=chat_id,
        text=text,
    )

    async with factory() as db:
        await BotActionAuditRepo.create(
            db,
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
                connector.id,
                status="error",
                error=(error or "")[:1000],
            )
        await db.commit()

    if not ok:
        log.warning(
            "telegram outbound failed connector=%s chat=%s: %s",
            connector.name,
            chat_id,
            error,
        )


# ---------------------------------------------------------------------------
# Session lifecycle fan-out
# ---------------------------------------------------------------------------


async def deliver_session_chat_event(
    factory: async_sessionmaker[AsyncSession],
    *,
    event_type: str,
    session_id: uuid.UUID,
) -> None:
    if event_type not in SESSION_CHAT_EVENTS:
        return

    async with factory() as db:
        session = await SessionRepo.get_by_id(db, session_id)
        incident = None
        if session and session.incident_id:
            incident = await IncidentRepo.get_by_id(db, session.incident_id)
        connectors = list(
            await BotConnectorRepo.list_all(db, enabled_only=True, platform="telegram")
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
        for chat_id in _allowed_chat_ids(connector):
            await _deliver_to_telegram(
                factory,
                connector=connector,
                chat_id=chat_id,
                text=text,
                command_label=f"notify:{event_type}",
                session_id=session_id,
            )


def schedule_session_chat_event(
    factory: async_sessionmaker[AsyncSession],
    *,
    task_registry: set[asyncio.Task] | None,
    event_type: str,
    session_id: uuid.UUID,
) -> asyncio.Task:
    task = asyncio.create_task(
        deliver_session_chat_event(
            factory,
            event_type=event_type,
            session_id=session_id,
        )
    )
    if task_registry is not None:
        task_registry.add(task)
        task.add_done_callback(task_registry.discard)
    return task


# ---------------------------------------------------------------------------
# Co-pilot reply relay-back
# ---------------------------------------------------------------------------


async def _resolve_relay_targets(
    factory: async_sessionmaker[AsyncSession],
    *,
    session_id: uuid.UUID,
) -> Iterable[tuple[BotConnector, str]]:
    """Find Telegram chats that originated ``/chat`` for this session.

    Returns the most recent originating ``(connector, chat_id)`` pair per
    distinct chat. We rely on the existing audit log so no extra schema
    is needed.
    """
    async with factory() as db:
        stmt = (
            select(BotActionAudit)
            .where(
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
            connector = await BotConnectorRepo.get_by_id(db, row.connector_id)
            if connector is None or not connector.is_enabled:
                continue
            if connector.platform != "telegram":
                continue
            if not _has_capability(connector, "copilot_chat"):
                continue
            seen.add(key)
            targets.append((connector, row.chat_id))
        return targets


async def deliver_copilot_relay(
    factory: async_sessionmaker[AsyncSession],
    *,
    session_id: uuid.UUID,
    reply_text: str,
) -> None:
    targets = await _resolve_relay_targets(factory, session_id=session_id)
    formatted = _format_copilot_reply(reply_text)
    for connector, chat_id in targets:
        await _deliver_to_telegram(
            factory,
            connector=connector,
            chat_id=chat_id,
            text=formatted,
            command_label="copilot_relay",
            session_id=session_id,
        )


def schedule_copilot_relay(
    factory: async_sessionmaker[AsyncSession],
    *,
    task_registry: set[asyncio.Task] | None,
    session_id: uuid.UUID,
    reply_text: str,
) -> asyncio.Task:
    task = asyncio.create_task(
        deliver_copilot_relay(
            factory,
            session_id=session_id,
            reply_text=reply_text,
        )
    )
    if task_registry is not None:
        task_registry.add(task)
        task.add_done_callback(task_registry.discard)
    return task


# ---------------------------------------------------------------------------
# Public surface
# ---------------------------------------------------------------------------


__all__ = [
    "SESSION_CHAT_EVENTS",
    "deliver_session_chat_event",
    "schedule_session_chat_event",
    "deliver_copilot_relay",
    "schedule_copilot_relay",
]
