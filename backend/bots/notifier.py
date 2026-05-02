"""Outbound delivery of AIM events into chat connectors."""

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
from backend.bots.connectors import get_adapter
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
