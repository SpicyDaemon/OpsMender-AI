"""Paging notification dispatcher (Sprint 35).

Takes a recorded ``incident_pages`` row (created by the escalation engine in
Sprint 34) and fans the page out to a user across configured delivery
channels (Slack DM, Teams DM, email, SMS).

Pipeline per page:

1. **Maintenance-window evaluator.** If a window matches the incident's
   service scope (or is global) and is active at ``now``:
   * ``response_mode == "page"`` → suppressed entirely; flips
     ``incidents.suppressed_by_maintenance_window_id``.
   * ``response_mode == "escalate_immediate"`` → never downgraded (D-021 #1).
   * ``response_mode == "notify"`` / fallback → delivery proceeds.
2. **Channel resolution.** Reads ``UserNotificationPref.routing[priority]``;
   falls back to ``["email"]`` if the user has no prefs row.
3. **Quiet hours.** If the user's ``quiet_hours`` window is active at ``now``
   AND the incident's priority is below ``min_priority_to_break``, the page
   is suppressed (still recorded with ``delivery_status='skipped'``).
4. **Dedup.** For each candidate channel, checks if a sent/failed delivery
   to the same (incident, user, channel) already exists within
   ``organizations.notification_dedup_window_minutes``.
5. **Delivery.** Calls the channel's ``send`` method. Every attempt — sent,
   failed, or skipped — is persisted as a fresh ``incident_pages`` row with
   the channel key in the ``channel`` column. The original ``recorded`` row
   stays untouched as the chain-engine audit anchor.
"""

from __future__ import annotations

import dataclasses
import os
import uuid
import zoneinfo
from datetime import datetime, timedelta, timezone
from typing import Awaitable, Callable, ClassVar, Protocol

from sqlalchemy.ext.asyncio import AsyncSession

from backend.db.models import Incident, IncidentPage, MaintenanceWindow, User
from backend.db.repos import (
    IncidentPageRepo,
    MaintenanceWindowRepo,
    OrganizationRepo,
    UserNotificationPrefRepo,
)


CHANNEL_KEYS: tuple[str, ...] = (
    "slack_dm",
    "teams_dm",
    "teams_dm_graph",
    "email",
    "sms",
    "voice",
)
DEFAULT_CHANNELS: tuple[str, ...] = ("email",)
PRIORITY_RANK: dict[str, int] = {"P0": 0, "P1": 1, "P2": 2, "P3": 3}


@dataclasses.dataclass(slots=True)
class DeliveryAttempt:
    channel: str
    status: str  # sent | failed | skipped
    error: str | None = None


@dataclasses.dataclass(slots=True)
class DispatchResult:
    user_id: uuid.UUID
    incident_id: uuid.UUID
    suppressed: bool = False
    suppression_reason: str | None = None
    suppressed_by_window_id: uuid.UUID | None = None
    # True when delivery was handed to the staged notification-escalation
    # engine instead of the immediate channel-factory fan-out.
    staged: bool = False
    attempts: list[DeliveryAttempt] = dataclasses.field(default_factory=list)


class Channel(Protocol):
    key: ClassVar[str]

    async def send(
        self,
        *,
        recipient: str,
        subject: str,
        body: str,
        blocks: list[dict] | None = None,
    ) -> DeliveryAttempt: ...


ChannelFactory = Callable[[str], "Channel | None"]
"""Caller-provided lookup. Given a channel key (e.g. ``"slack_dm"``), return
a configured Channel for this org or ``None`` if the org has not wired that
channel up yet."""


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# Maintenance window
# ---------------------------------------------------------------------------


async def evaluate_maintenance_window(
    db: AsyncSession,
    org_id: uuid.UUID,
    *,
    incident: Incident,
    at: datetime,
) -> MaintenanceWindow | None:
    """Return the first active maintenance window that applies to this
    incident's scope, or None. Global windows always apply; scoped windows
    must match ``incident.service_id``.

    D-021 #1: ``escalate_immediate`` is never downgraded — callers should
    short-circuit before invoking this if the response mode equals
    ``escalate_immediate``.
    """

    service_id = incident.service_id
    windows = await MaintenanceWindowRepo.list_active_at(db, org_id, at)
    for window in windows:
        if window.scope_type == "global":
            return window
        if service_id is not None and window.scope_type == "service":
            service_ids = set(str(v) for v in (window.target_ids or []))
            if window.scope_id is not None:
                service_ids.add(str(window.scope_id))
            if str(service_id) in service_ids:
                return window
    return None


# ---------------------------------------------------------------------------
# Quiet hours
# ---------------------------------------------------------------------------


def quiet_hours_block(
    quiet_hours: dict | None, *, priority: str | None, at: datetime
) -> bool:
    """Returns True if the user's quiet-hours window is active at ``at`` AND
    the incident priority does NOT meet the ``min_priority_to_break``
    threshold (lower-numbered priority strings break through — P0 is the most
    urgent)."""

    if not quiet_hours:
        return False
    # P0 (Critical) always pages through quiet hours; only P1-P3 can be
    # suppressed. This is an explicit guarantee independent of any stored
    # ``min_priority_to_break`` value.
    if priority == "P0":
        return False
    start = quiet_hours.get("weekday_start")
    end = quiet_hours.get("weekday_end")
    if not start or not end:
        return False

    min_priority = quiet_hours.get("min_priority_to_break")
    if min_priority and priority:
        incident_rank = PRIORITY_RANK.get(priority, 99)
        break_rank = PRIORITY_RANK.get(min_priority, -1)
        if incident_rank <= break_rank:
            return False

    tz_name = quiet_hours.get("time_zone", "UTC")
    try:
        tz = zoneinfo.ZoneInfo(tz_name)
    except Exception:
        tz = timezone.utc
    local = at.astimezone(tz)
    # Optional days-of-week restriction. ``days`` is a list of Python weekday
    # integers (Mon=0 .. Sun=6); when present and non-empty, quiet hours only
    # apply on those days. Absent/empty means every day (backward compatible).
    days = quiet_hours.get("days")
    if isinstance(days, list) and days:
        try:
            allowed = {int(d) for d in days}
        except (TypeError, ValueError):
            allowed = set()
        if allowed and local.weekday() not in allowed:
            return False
    try:
        s_h, s_m = (int(part) for part in start.split(":"))
        e_h, e_m = (int(part) for part in end.split(":"))
    except (ValueError, AttributeError):
        return False
    now_min = local.hour * 60 + local.minute
    start_min = s_h * 60 + s_m
    end_min = e_h * 60 + e_m
    if start_min <= end_min:
        return start_min <= now_min < end_min
    # Window wraps midnight.
    return now_min >= start_min or now_min < end_min


# ---------------------------------------------------------------------------
# Channel resolution
# ---------------------------------------------------------------------------


def resolve_channels(
    routing: dict | None,
    *,
    priority: str | None,
    default: tuple[str, ...] = DEFAULT_CHANNELS,
) -> list[str]:
    """Pick the channels to fan out to for a given priority."""

    if routing and priority and priority in routing:
        raw = routing[priority]
        if isinstance(raw, list):
            return [c for c in raw if c in CHANNEL_KEYS]
    return [c for c in default if c in CHANNEL_KEYS]


# ---------------------------------------------------------------------------
# Dispatcher
# ---------------------------------------------------------------------------


async def dispatch_page(
    db: AsyncSession,
    org_id: uuid.UUID,
    *,
    incident: Incident,
    user: User,
    page: IncidentPage,
    channel_factory: ChannelFactory,
    at: datetime | None = None,
) -> DispatchResult:
    """Fan out a recorded incident_pages row across the channels configured
    for this user. Each delivery attempt becomes a new incident_pages row
    keyed by channel.
    """

    now = at or _utcnow()
    result = DispatchResult(user_id=user.id, incident_id=incident.id)

    response_mode = incident.response_mode or "notify"

    # 1. Maintenance-window suppression
    if response_mode != "escalate_immediate":
        mw = await evaluate_maintenance_window(
            db, org_id, incident=incident, at=now
        )
        if mw is not None:
            result.suppressed = True
            result.suppression_reason = "maintenance_window"
            result.suppressed_by_window_id = mw.id
            if incident.suppressed_by_maintenance_window_id is None:
                incident.suppressed_by_maintenance_window_id = mw.id
                await db.flush()
            return result

    # 2. Prefs + channels
    prefs = await UserNotificationPrefRepo.get_for_user(db, org_id, user.id)
    org = await OrganizationRepo.get_by_id(db, org_id)
    dedup_window = (
        org.notification_dedup_window_minutes
        if org is not None
        else 10
    )

    quiet_hours = prefs.quiet_hours if prefs is not None else None
    if quiet_hours_block(quiet_hours, priority=incident.priority, at=now):
        result.suppressed = True
        result.suppression_reason = "quiet_hours"
        return result

    routing = prefs.routing if prefs is not None else None

    # Staged routing (new shape: ordered {channel_id, delay_seconds} stages)
    # delegates delivery to the notification-escalation engine, which fires
    # stage 0 now and schedules the rest with delays + ack/resolve stop.
    # Legacy routing (list of channel keys) keeps the immediate fan-out below.
    from backend.paging.routing import parse_stages, routing_is_staged

    priority_routing = (
        routing.get(incident.priority)
        if (routing and incident.priority)
        else None
    )
    if routing_is_staged(priority_routing):
        from backend.paging import notification_escalation as _ne

        sender = _ne.build_notification_sender(channel_factory)
        await _ne.start_escalation(
            db,
            org_id,
            incident=incident,
            user=user,
            stages=parse_stages(priority_routing),
            sender=sender,
            at=now,
        )
        result.staged = True
        return result

    channel_keys = resolve_channels(routing, priority=incident.priority)
    addresses: dict[str, str] = (
        dict(prefs.channels) if (prefs is not None and prefs.channels) else {}
    )

    if not addresses.get("email") and getattr(user, "email", None):
        addresses.setdefault("email", user.email)

    from backend.paging.slack_cards import build_page_card_blocks

    subject = f"OpsMender: {incident.title or 'Incident page'}"
    body_lines = [
        f"Priority: {incident.priority or 'P?'}",
        f"Status: {incident.status}",
        f"Incident: {incident.id}",
    ]
    if incident.description:
        body_lines.append("")
        body_lines.append(incident.description)
    body = "\n".join(body_lines)

    # 3. Per-channel delivery
    cutoff = now - timedelta(minutes=dedup_window)
    for key in channel_keys:
        recent = await IncidentPageRepo.has_recent_delivery(
            db,
            org_id,
            incident_id=incident.id,
            user_id=user.id,
            channel=key,
            after=cutoff,
        )
        if recent:
            attempt = DeliveryAttempt(key, "skipped", "dedup")
            result.attempts.append(attempt)
            await IncidentPageRepo.create(
                db,
                org_id,
                incident_id=incident.id,
                user_id=user.id,
                chain_id=page.chain_id,
                step_index=page.step_index,
                channel=key,
                delivery_status="skipped",
                delivery_error="dedup",
            )
            continue

        recipient = addresses.get(key)
        if not recipient:
            attempt = DeliveryAttempt(key, "skipped", "no_recipient")
        else:
            channel = channel_factory(key)
            if channel is None:
                attempt = DeliveryAttempt(key, "skipped", "channel_unconfigured")
            else:
                blocks: list[dict] | None = None
                if key == "slack_dm":
                    blocks = build_page_card_blocks(
                        incident, base_url=os.environ.get("OPSMENDER_PUBLIC_URL")
                    )
                elif key == "teams_dm_graph":
                    from backend.paging.teams_cards import (
                        build_page_card_adaptive,
                        wrap_card_as_attachment,
                    )

                    blocks = [
                        wrap_card_as_attachment(
                            build_page_card_adaptive(
                                incident,
                                base_url=os.environ.get("OPSMENDER_PUBLIC_URL"),
                            )
                        )
                    ]
                try:
                    attempt = await channel.send(
                        recipient=recipient,
                        subject=subject,
                        body=body,
                        blocks=blocks,
                    )
                except Exception as exc:  # noqa: BLE001
                    attempt = DeliveryAttempt(key, "failed", str(exc))

        result.attempts.append(attempt)
        await IncidentPageRepo.create(
            db,
            org_id,
            incident_id=incident.id,
            user_id=user.id,
            chain_id=page.chain_id,
            step_index=page.step_index,
            channel=key,
            delivery_status=attempt.status,
            delivery_error=attempt.error,
        )

    return result
