"""Staged notification escalation.

Drives a priority's ordered routing stages for one (incident, user): stage 0
fires immediately, each later stage fires after the prior stage's delay if the
incident is still unacknowledged. Acknowledgement or resolution stops it.

Delivery is **channel-agnostic** — a stage targets a configured Notification
Channel (a ``BotConnector``, by id) routed through the unified connector
adapter ``send_message`` path, or a legacy delivery key
(``slack_dm``/``email``/...) routed through the paging channel factory. New
notification providers therefore require no routing changes: add a connector
and it becomes routable.

The architecture intentionally keeps the *channel* abstraction separate from
delivery so future chat-capable actions (Acknowledge / Resolve / Escalate /
Start Session from inside a notification) can be layered on without touching
routing — see docs/wiki/notification-preferences.md "Future direction".
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Awaitable, Callable, Protocol

from sqlalchemy.ext.asyncio import AsyncSession

import backend.bots  # noqa: F401  -- registers built-in connector adapters
from backend.bots.connectors import get_adapter
from backend.db.models import Incident, User
from backend.db.repos import (
    BotConnectorRepo,
    IncidentPageRepo,
    NotificationEscalationRepo,
    UserNotificationPrefRepo,
)
from backend.paging.routing import LEGACY_CHANNEL_KEYS, Stage, parse_stages

logger = logging.getLogger(__name__)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


class NotificationSender(Protocol):
    """Delivers one notification to one channel. Returns ``(status, error)``
    where status is ``sent`` | ``skipped`` | ``failed``."""

    def __call__(
        self,
        db: AsyncSession,
        org_id: uuid.UUID,
        *,
        channel_id: str,
        incident: Incident,
        user: User,
        subject: str,
        body: str,
    ) -> Awaitable[tuple[str, str | None]]: ...


def build_notification_sender(
    channel_factory: Callable[[str], object] | None = None,
) -> NotificationSender:
    """Default sender: configured Notification Channels (connectors) via the
    unified adapter ``send_message`` path, plus legacy delivery keys via the
    paging channel factory. Never raises."""

    from backend.paging.channel_factory import build_channel_factory

    cf = channel_factory or build_channel_factory()

    async def sender(
        db: AsyncSession,
        org_id: uuid.UUID,
        *,
        channel_id: str,
        incident: Incident,
        user: User,
        subject: str,
        body: str,
    ) -> tuple[str, str | None]:
        # Legacy delivery key → paging channel factory + the user's saved
        # destination for that key.
        if channel_id in LEGACY_CHANNEL_KEYS:
            prefs = await UserNotificationPrefRepo.get_for_user(db, org_id, user.id)
            addresses = dict(prefs.channels) if (prefs and prefs.channels) else {}
            if not addresses.get("email") and getattr(user, "email", None):
                addresses.setdefault("email", user.email)
            user_phone = getattr(user, "phone", None)
            if user_phone:
                addresses.setdefault("sms", user_phone)
                addresses.setdefault("voice", user_phone)
            recipient = addresses.get(channel_id)
            if not recipient:
                return ("skipped", "no_recipient")
            channel = None
            if channel_id in {"sms", "voice"}:
                from backend.paging.voice_settings import (
                    build_sms_channel,
                    build_voice_channel,
                    resolve_voice_settings,
                )

                settings = await resolve_voice_settings(db, org_id)
                if settings is not None:
                    channel = (
                        build_sms_channel(settings)
                        if channel_id == "sms"
                        else build_voice_channel(settings)
                    )
            if channel is None:
                channel = cf(channel_id)
            if channel is None:
                return ("skipped", "channel_unconfigured")
            try:
                attempt = await channel.send(
                    recipient=recipient, subject=subject, body=body
                )
                return (attempt.status, attempt.error)
            except Exception as exc:  # noqa: BLE001
                return ("failed", str(exc))

        # Otherwise a configured Notification Channel (connector id).
        try:
            connector_id = uuid.UUID(str(channel_id))
        except (ValueError, TypeError):
            return ("skipped", "unknown_channel")
        connector = await BotConnectorRepo.get_by_id(db, org_id, connector_id)
        if connector is None or not connector.is_enabled:
            return ("skipped", "channel_unconfigured")
        adapter = get_adapter(connector.platform)
        if adapter is None:
            return ("skipped", "no_adapter")
        chat_ids = (connector.config or {}).get("allowed_chat_ids") or []
        if not chat_ids:
            return ("skipped", "no_recipient")
        text = f"{subject}\n{body}" if body else subject
        try:
            ok, error = await adapter.send_message(
                connector, chat_id=str(chat_ids[0]), text=text
            )
            return ("sent" if ok else "failed", error)
        except Exception as exc:  # noqa: BLE001
            return ("failed", str(exc))

    return sender


async def _fire_stage(
    db: AsyncSession,
    org_id: uuid.UUID,
    *,
    incident: Incident,
    user: User,
    stage: Stage,
    stage_index: int,
    sender: NotificationSender,
) -> None:
    from backend.paging.page_text import (
        format_page_subject_body,
        org_name_for_page,
    )

    org_name = await org_name_for_page(db, org_id)
    subject, body = format_page_subject_body(incident, org_name=org_name)
    status, error = await sender(
        db,
        org_id,
        channel_id=stage.channel_id,
        incident=incident,
        user=user,
        subject=subject,
        body=body,
    )
    await IncidentPageRepo.create(
        db,
        org_id,
        incident_id=incident.id,
        user_id=user.id,
        step_index=stage_index,
        channel=stage.channel_id,
        delivery_status=status,
        delivery_error=error,
    )


async def start_escalation(
    db: AsyncSession,
    org_id: uuid.UUID,
    *,
    incident: Incident,
    user: User,
    stages: list[Stage],
    sender: NotificationSender,
    at: datetime | None = None,
) -> None:
    """Create state, fire stage 0 now, schedule stage 1 (if any).

    Idempotent per (incident, user): a second call is a no-op so the chain
    engine re-paging the same user doesn't double-start.
    """

    if not stages:
        return
    now = at or _utcnow()
    existing = await NotificationEscalationRepo.get(
        db, org_id, incident_id=incident.id, user_id=user.id
    )
    if existing is not None:
        return

    state = await NotificationEscalationRepo.create(
        db,
        org_id,
        incident_id=incident.id,
        user_id=user.id,
        priority=incident.priority,
        stages=[
            {"channel_id": s.channel_id, "delay_seconds": s.delay_seconds}
            for s in stages
        ],
    )
    await _fire_stage(
        db,
        org_id,
        incident=incident,
        user=user,
        stage=stages[0],
        stage_index=0,
        sender=sender,
    )
    state.current_stage = 0
    if len(stages) > 1:
        # The delay on the stage we just fired gates the *next* stage.
        state.next_stage_due_at = now + timedelta(seconds=stages[0].delay_seconds)
    else:
        state.status = "exhausted"
        state.finished_at = now
        state.next_stage_due_at = None
    await db.flush()


async def _advance(
    db: AsyncSession,
    org_id: uuid.UUID,
    state,
    *,
    sender: NotificationSender,
    at: datetime,
) -> bool:
    """Fire the next due stage for one running state. Returns True if fired."""
    if state.status != "running":
        return False
    due = _aware(state.next_stage_due_at)
    if due is None or at < due:
        return False

    stages = parse_stages(state.stages)
    next_idx = state.current_stage + 1
    if next_idx >= len(stages):
        state.status = "exhausted"
        state.finished_at = at
        state.next_stage_due_at = None
        await db.flush()
        return False

    from backend.db.repos import IncidentRepo

    incident = await IncidentRepo.get_by_id(db, org_id, state.incident_id)
    if incident is None:
        state.status = "cancelled"
        state.finished_at = at
        state.next_stage_due_at = None
        await db.flush()
        return False
    # Stop if the incident is already resolved.
    if incident.status == "resolved":
        state.status = "resolved"
        state.finished_at = at
        state.next_stage_due_at = None
        await db.flush()
        return False

    from backend.db.repos import UserRepo

    user = await UserRepo.get_by_id(db, state.user_id)
    if user is not None:
        await _fire_stage(
            db,
            org_id,
            incident=incident,
            user=user,
            stage=stages[next_idx],
            stage_index=next_idx,
            sender=sender,
        )
    state.current_stage = next_idx
    if next_idx + 1 < len(stages):
        state.next_stage_due_at = at + timedelta(seconds=stages[next_idx].delay_seconds)
    else:
        state.status = "exhausted"
        state.finished_at = at
        state.next_stage_due_at = None
    await db.flush()
    return True


async def tick_all_due(
    db: AsyncSession,
    *,
    sender: NotificationSender,
    at: datetime | None = None,
) -> int:
    """Advance every running escalation whose next stage is due. Returns the
    number of stages fired. Safe to call repeatedly (scheduler-driven)."""
    now = at or _utcnow()
    due = await NotificationEscalationRepo.list_due(db, now=now)
    fired = 0
    for state in due:
        try:
            if await _advance(db, state.org_id, state, sender=sender, at=now):
                fired += 1
        except Exception as exc:  # noqa: BLE001
            logger.warning("notification_escalation advance failed: %s", exc)
    return fired


async def stop_escalation(
    db: AsyncSession,
    org_id: uuid.UUID,
    *,
    incident_id: uuid.UUID,
    status: str = "acked",
    at: datetime | None = None,
    user_id: uuid.UUID | None = None,
) -> int:
    """Stop remaining stages for an incident (on ack/resolve). Returns the
    number of escalations stopped."""
    now = at or _utcnow()
    running = await NotificationEscalationRepo.list_running_for_incident(
        db, org_id, incident_id
    )
    stopped = 0
    for state in running:
        if user_id is not None and state.user_id != user_id:
            continue
        state.status = status
        state.finished_at = now
        state.next_stage_due_at = None
        stopped += 1
    if stopped:
        await db.flush()
    return stopped
