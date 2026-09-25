"""Chain execution engine (Sprint 34; acknowledgement lock per D-021).

State machine for ``incident_chain_states``:

```
[no row]
   │  start_chain(incident_id, chain_id) — fires step 0
   ▼
[running, step=N] ──tick: level timeout──► fires step N+1 (additive)
   │                                        └─ no step N+1: [exhausted]
   ├─ snooze(until) ──► [paused] ──tick: snooze ends──► fires step N+1
   ├─ acknowledge / Take / keypad 1 / chat ack ──► [acked] (live lock)
   │     ├─ assignee write ──► lock extended to last write + 15 min
   │     ├─ snooze(until) ──► lock lasts at least until the snooze ends
   │     ├─ release ──► fires step N+1
   │     └─ tick: 15 min without assignee activity ──► releases, fires N+1
   ├─ escalate_now (running, paused or acked) ──► fires step N+1
   └─ resolve or merge (any live state) ──► [cancelled]

handle_takeover_request ─► pending for five minutes; only the current
owner can confirm (handle_takeover_confirm); an admin can force it; an
unanswered request expires on the next tick without a transfer.
```

``acked`` is a live lock, not a finished chain: ``finished_at`` stays empty
until the chain is cancelled or exhausted. While a chain is live,
``next_step_due_at`` is when the next level fires unless someone intervenes.
A chain acknowledged before the lock existed kept its ``finished_at`` and
stays finished.

The logical marker is keyed by incident, user, level and round. A handoff or
definition edit increments the round; physical delivery rows retain it.
The only 15-minute deadline is the assignee's inactivity lock.

The engine never blocks on real notification delivery — it writes
``incident_pages`` rows with ``channel='recorded'`` and Sprint 35 wires the
actual channels.
"""

from __future__ import annotations

import dataclasses
import logging
import os
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from backend.db.repos import (
    BotConnectorRepo,
    chain_is_live,
    EscalationStepRepo,
    IncidentAssignmentRepo,
    IncidentChainStateRepo,
    IncidentPageRepo,
    IncidentRepo,
    EscalationChainRepo,
    RosterOverrideRepo,
    RosterRepo,
    ServiceEscalationChainRepo,
    TeamRepo,
    UserRepo,
)
from backend.notifications import CATEGORY_INCIDENT, emit_to_users
from backend.paging.dispatch import ChannelFactory, dispatch_page
from backend.paging.on_call import (
    OnCallContext,
    OnCallMember,
    OnCallOverride,
    on_call_at,
)
from backend.services.incident_timeline import record_lifecycle_comment

_log = logging.getLogger(__name__)


SOFT_TAKEOVER_WINDOW_SECONDS = 5 * 60
# Start-time cap on a chain nobody has touched (KI-010; removed in X1b).
HARD_INACTIVITY_TIMEOUT_SECONDS = 15 * 60
# D-021: an acknowledgement lock lapses 15 min after the assignee's last write.
ACK_LOCK_INACTIVITY_SECONDS = 15 * 60
CLOSED_INCIDENT_STATUSES = ("resolved", "merged")


@dataclasses.dataclass(slots=True)
class StepFireResult:
    step_index: int
    users_paged: list[uuid.UUID]
    eligible_targets: int = 0
    delivery_recorded: bool = True


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _aware(value: datetime | None) -> datetime | None:
    """Normalize a DB datetime to UTC-aware. SQLite stores naive timestamps."""

    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


async def _resolve_step_targets(
    db: AsyncSession,
    org_id: uuid.UUID,
    *,
    target_type: str,
    target_id: uuid.UUID,
    at: datetime,
) -> list[uuid.UUID]:
    """Expand an escalation_step target into a list of user_ids to page."""

    if target_type == "user":
        user = await UserRepo.get_by_id(db, target_id)
        if user is None or not user.is_active or user.deleted_at is not None:
            return []
        return (
            [target_id]
            if user.primary_org_id == org_id
            or await UserRepo.is_member(db, target_id, org_id)
            else []
        )
    if target_type == "team":
        members = await TeamRepo.list_members(db, org_id, target_id)
        eligible = []
        for member in members:
            user = await UserRepo.get_by_id(db, member.user_id)
            if user is not None and user.is_active and user.deleted_at is None:
                eligible.append(member.user_id)
        return eligible
    if target_type == "roster":
        roster = await RosterRepo.get_by_id(db, org_id, target_id)
        if roster is None or not roster.is_active:
            return []
        # Deactivated/soft-deleted users are never paged.
        members = await RosterRepo.list_members(db, org_id, target_id, active_only=True)
        overrides = await RosterOverrideRepo.list_for_roster(db, org_id, target_id)
        active_overrides = []
        for override in overrides:
            covering = await UserRepo.get_by_id(db, override.covering_user_id)
            if (
                covering is not None
                and covering.is_active
                and covering.deleted_at is None
            ):
                active_overrides.append(override)
        ctx = OnCallContext(
            members=[
                OnCallMember(user_id=m.user_id, position_index=m.position_index)
                for m in members
            ],
            overrides=[
                OnCallOverride(
                    covering_user_id=o.covering_user_id,
                    starts_at=o.starts_at,
                    ends_at=o.ends_at,
                )
                for o in active_overrides
            ],
            time_zone=roster.time_zone,
            pattern=roster.pattern,
            pattern_length=roster.pattern_length,
            handoff_time=roster.handoff_time,
            anchor_date=roster.anchor_date,
        )
        user_id = on_call_at(ctx, at)
        return [user_id] if user_id is not None else []
    return []


async def _fire_step(
    db: AsyncSession,
    org_id: uuid.UUID,
    *,
    incident_id: uuid.UUID,
    chain_id: uuid.UUID,
    round: int,
    step,
    at: datetime,
    channel_factory: ChannelFactory | None = None,
) -> StepFireResult:
    """Persist incident_pages for every user that step ``step`` targets.

    Additive: a user already paged for an earlier step is NOT paged again
    for the same step_index. We only deduplicate within the same step row.

    When ``channel_factory`` is provided, each newly-recorded page row is
    immediately fanned out to the dispatcher (Sprint 35), which records one
    additional ``incident_pages`` row per delivery attempt. When omitted,
    only the audit-anchor ``recorded`` row is written — preserving the
    Sprint 34 behavior.
    """

    user_ids = await _resolve_step_targets(
        db,
        org_id,
        target_type=step.target_type,
        target_id=step.target_id,
        at=at,
    )
    fired: list[uuid.UUID] = []
    delivered = False
    incident = None
    for uid in user_ids:
        if await IncidentPageRepo.already_paged(
            db,
            org_id,
            incident_id=incident_id,
            user_id=uid,
            step_index=step.step_index,
            round=round,
        ):
            continue
        try:
            async with db.begin_nested():
                page = await IncidentPageRepo.create(
                    db,
                    org_id,
                    incident_id=incident_id,
                    user_id=uid,
                    chain_id=chain_id,
                    step_index=step.step_index,
                    round=round,
                )
        except IntegrityError:
            # A competing claim already recorded this logical page. The
            # savepoint leaves the enclosing batch transaction usable.
            continue
        fired.append(uid)
        if channel_factory is not None:
            if incident is None:
                incident = await IncidentRepo.get_by_id(db, org_id, incident_id)
            user = await UserRepo.get_by_id(db, uid)
            if incident is not None and user is not None:
                dispatch = await dispatch_page(
                    db,
                    org_id,
                    incident=incident,
                    user=user,
                    page=page,
                    channel_factory=channel_factory,
                    at=at,
                )
                delivered = (
                    delivered
                    or getattr(dispatch, "staged", False)
                    or any(a.status == "sent" for a in dispatch.attempts)
                )
        else:
            delivered = True
    return StepFireResult(
        step_index=step.step_index,
        users_paged=fired,
        eligible_targets=len(user_ids),
        delivery_recorded=delivered,
    )


async def _notify_escalation(
    db: AsyncSession,
    org_id: uuid.UUID,
    incident_id: uuid.UUID,
    *,
    exhausted: bool = False,
) -> None:
    """Best-effort: post an escalation card to enabled Notification Channels.

    The message is built here from the live session (which already sees the
    just-fired page row) and handed to a fire-and-forget text delivery, so the
    background task never has to re-read state that the caller has not yet
    committed. Only spawns the task when an enabled ``notifications`` channel
    exists — keeps the hot path cheap and side-effect-free when there is
    nothing to deliver. Never raises into the escalation engine.
    """
    try:
        connectors = await BotConnectorRepo.list_all(db, org_id, enabled_only=True)
        if not any(
            "notifications" in (c.allowed_capabilities or []) for c in connectors
        ):
            return

        incident = await IncidentRepo.get_by_id(db, org_id, incident_id)
        if incident is None:
            return

        from backend.api.deps import get_current_session_factory
        from backend.bots.incident_card import build_incident_message
        from backend.bots.notifier import (
            _resolve_incident_team,
            _resolve_incident_responder,
            schedule_incident_text,
        )

        target_team_id, team_name, service_name = await _resolve_incident_team(
            db, org_id, incident
        )
        responder = await _resolve_incident_responder(db, org_id, incident_id)
        text = build_incident_message(
            incident,
            event_type=(
                "incident.escalation_exhausted" if exhausted else "incident.escalated"
            ),
            base_url=os.environ.get("OPSMENDER_PUBLIC_URL"),
            responder=responder,
            service_name=service_name,
            team_name=team_name,
            supports_actions=False,
        )
        if exhausted:
            text += "\nNo further responder level is configured."
        schedule_incident_text(
            get_current_session_factory(),
            org_id=org_id,
            text=text,
            event_type=(
                "incident.escalation_exhausted" if exhausted else "incident.escalated"
            ),
            team_id=target_team_id,
            incident_id=incident.id,
            rendered_status=incident.status,
        )
    except Exception:  # pragma: no cover - delivery is best-effort
        _log.warning("escalation channel notify skipped")


async def _exhaust_chain(
    db: AsyncSession, org_id: uuid.UUID, state, *, now: datetime, reason: str
) -> None:
    """Finish a run and create its sole exhaustion notice under the state lock."""
    if state.status == "exhausted":
        return
    state.status = "exhausted"
    state.finished_at = now
    state.next_step_due_at = None
    state.paused_until = None
    await record_lifecycle_comment(
        db,
        org_id,
        incident_id=state.incident_id,
        body=f"Escalation exhausted: {reason}",
    )
    if state.exhaustion_notified_at is None:
        state.exhaustion_notified_at = now
        chain = await EscalationChainRepo.get_by_id(db, org_id, state.chain_id)
        recipients: set[uuid.UUID] = set()
        if chain is not None:
            for member in await TeamRepo.list_members(db, org_id, chain.team_id):
                user = await UserRepo.get_by_id(db, member.user_id)
                if user is not None and user.is_active and user.deleted_at is None:
                    recipients.add(user.id)
        for page in await IncidentPageRepo.list_for_incident(
            db, org_id, state.incident_id
        ):
            if page.channel == "recorded":
                user = await UserRepo.get_by_id(db, page.user_id)
                if user is not None and user.is_active and user.deleted_at is None:
                    recipients.add(user.id)
        # The shared Inbox path honours each recipient's category mute.
        await emit_to_users(
            db,
            org_id,
            sorted(recipients),
            event_type="incident.escalation_exhausted",
            category=CATEGORY_INCIDENT,
            title="Escalation exhausted",
            body="No further escalation level is available. Review this incident.",
            link=f"/dashboard/incidents/detail?id={state.incident_id}",
            incident_id=state.incident_id,
        )
        await _notify_escalation(db, org_id, state.incident_id, exhausted=True)
    await db.flush()


async def select_chain_for_incident(
    db: AsyncSession,
    org_id: uuid.UUID,
    *,
    service_id: uuid.UUID | None,
    priority: str | None,
):
    """Pick the most-applicable chain for an incident.

    Looks at ``service_escalation_chains`` for the incident's service. If
    multiple match, the one whose ``applies_when`` filters on priority and
    matches wins; otherwise the first link is used.
    """

    if service_id is None:
        return None
    links = await ServiceEscalationChainRepo.list_for_service(db, org_id, service_id)
    if not links:
        return None
    matching = []
    defaults = []
    for link in links:
        chain = await EscalationChainRepo.get_by_id(db, org_id, link.chain_id)
        if chain is None or not chain.is_active:
            continue
        applies_when = link.applies_when or {}
        priorities = (
            applies_when.get("priorities") if isinstance(applies_when, dict) else None
        )
        if (
            priorities
            and priority is not None
            and priority.upper() in {str(p).upper() for p in priorities}
        ):
            matching.append(link)
        elif not priorities:
            defaults.append(link)
    return matching[0] if matching else (defaults[0] if defaults else None)


async def start_chain(
    db: AsyncSession,
    org_id: uuid.UUID,
    *,
    incident_id: uuid.UUID,
    chain_id: uuid.UUID,
    mode: str = "page",
    at: datetime | None = None,
    channel_factory: ChannelFactory | None = None,
) -> StepFireResult | None:
    """Create the chain state row, fire step 0 (and all steps if mode is
    ``escalate_immediate``), and schedule the next tick.
    """

    now = at or _utcnow()
    existing = await IncidentChainStateRepo.get_for_incident(
        db, org_id, incident_id, for_update=True
    )
    if existing is not None:
        return None

    state = await IncidentChainStateRepo.create(
        db,
        org_id,
        incident_id=incident_id,
        chain_id=chain_id,
    )
    state.started_at = now
    result = await _fire_next_level(
        db, org_id, state, now=now, channel_factory=channel_factory
    )
    if mode == "escalate_immediate":
        steps = await EscalationStepRepo.list_for_chain(db, org_id, chain_id)
        while (
            steps
            and chain_is_live(state)
            and state.current_step_index < steps[-1].step_index
        ):
            advanced = await _fire_next_level(
                db, org_id, state, now=now, channel_factory=channel_factory
            )
            if advanced is not None:
                result = advanced
    return result


async def restart_chain_for_handoff(
    db: AsyncSession,
    org_id: uuid.UUID,
    *,
    incident_id: uuid.UUID,
    chain_id: uuid.UUID,
    mode: str = "page",
    at: datetime | None = None,
    channel_factory: ChannelFactory | None = None,
) -> StepFireResult | None:
    """Restart routing for an incident after its owning service changes.

    Incident chain state is one-to-one with the incident, so handoff updates
    the existing state instead of creating a second response loop.
    """

    now = at or _utcnow()
    state = await IncidentChainStateRepo.get_for_incident(
        db, org_id, incident_id, for_update=True
    )
    if state is None:
        return await start_chain(
            db,
            org_id,
            incident_id=incident_id,
            chain_id=chain_id,
            mode=mode,
            at=now,
            channel_factory=channel_factory,
        )

    state.chain_id = chain_id
    state.status = "running"
    state.current_step_index = -1
    state.round += 1
    state.exhaustion_notified_at = None
    state.next_step_due_at = None
    state.paused_until = None
    state.last_activity_at = None
    state.pending_takeover_user_id = None
    state.pending_takeover_expires_at = None
    state.started_at = now
    state.finished_at = None
    state.hard_deadline_at = None
    result = await _fire_next_level(
        db, org_id, state, now=now, channel_factory=channel_factory
    )
    if mode == "escalate_immediate":
        steps = await EscalationStepRepo.list_for_chain(db, org_id, chain_id)
        while (
            steps
            and chain_is_live(state)
            and state.current_step_index < steps[-1].step_index
        ):
            advanced = await _fire_next_level(
                db, org_id, state, now=now, channel_factory=channel_factory
            )
            if advanced is not None:
                result = advanced
    return result


def _closed(incident) -> bool:
    return incident is None or incident.status in CLOSED_INCIDENT_STATUSES


def _lock_deadline(state, last_activity: datetime) -> datetime:
    """When an acknowledgement lock lapses: 15 min after the assignee's last
    write, or the end of a snooze, whichever is later."""
    deadline = last_activity + timedelta(seconds=ACK_LOCK_INACTIVITY_SECONDS)
    paused_until = _aware(state.paused_until)
    if paused_until is not None and paused_until > deadline:
        return paused_until
    return deadline


async def is_eligible_owner(
    db: AsyncSession, org_id: uuid.UUID, user_id: uuid.UUID
) -> bool:
    """An active, non-deleted member of the workspace can own an incident."""

    user = await UserRepo.get_by_id(db, user_id)
    if user is None or not user.is_active or user.deleted_at is not None:
        return False
    # The workspace is the user's primary org (see ``get_current_org``).
    return user.primary_org_id == org_id or await UserRepo.is_member(
        db, user_id, org_id
    )


async def _username(db: AsyncSession, user_id: uuid.UUID) -> str:
    user = await UserRepo.get_by_id(db, user_id)
    return user.username if user is not None else "another responder"


async def _fire_next_level(
    db: AsyncSession,
    org_id: uuid.UUID,
    state,
    *,
    now: datetime,
    channel_factory: ChannelFactory | None,
) -> StepFireResult | None:
    """Visit each remaining level once, skipping empty targets immediately."""
    steps = list(await EscalationStepRepo.list_for_chain(db, org_id, state.chain_id))
    state.paused_until = None
    for step in steps:
        if step.step_index <= state.current_step_index:
            continue
        result = await _fire_step(
            db,
            org_id,
            incident_id=state.incident_id,
            chain_id=state.chain_id,
            round=state.round,
            step=step,
            at=now,
            channel_factory=channel_factory,
        )
        state.current_step_index = step.step_index
        if result.eligible_targets == 0:
            await record_lifecycle_comment(
                db,
                org_id,
                incident_id=state.incident_id,
                body=f"Skipped escalation step {step.step_index + 1}: no eligible responders.",
            )
            continue
        state.status = "running"
        state.next_step_due_at = now + timedelta(seconds=step.timeout_seconds)
        await db.flush()
        if result.users_paged and not result.delivery_recorded:
            await record_lifecycle_comment(
                db,
                org_id,
                incident_id=state.incident_id,
                body=f"Escalation step {step.step_index + 1} has eligible responders, but delivery was suppressed or unavailable; its timeout remains active.",
            )
        if result.step_index >= 1 and result.users_paged:
            await record_lifecycle_comment(
                db,
                org_id,
                incident_id=state.incident_id,
                body=f"Escalated to step {result.step_index + 1}.",
            )
            await _notify_escalation(db, org_id, state.incident_id)
        return result
    await _exhaust_chain(
        db,
        org_id,
        state,
        now=now,
        reason="No eligible escalation level remains.",
    )
    return None


async def _tick_state(
    db: AsyncSession,
    org_id: uuid.UUID,
    *,
    incident_id: uuid.UUID,
    now: datetime,
    channel_factory: ChannelFactory | None,
) -> tuple[StepFireResult | None, bool]:
    """Advance one chain if anything about it is due.

    Returns the fired level (if any) and whether the state changed.
    """

    state = await IncidentChainStateRepo.get_for_incident(
        db, org_id, incident_id, for_update=True
    )
    if state is None:
        return None, False

    changed = False
    takeover_expires = _aware(state.pending_takeover_expires_at)
    if takeover_expires is not None and now >= takeover_expires:
        # An unanswered takeover request expires; ownership is unchanged.
        state.pending_takeover_user_id = None
        state.pending_takeover_expires_at = None
        await record_lifecycle_comment(
            db,
            org_id,
            incident_id=incident_id,
            body="The takeover request expired; ownership is unchanged.",
        )
        changed = True

    if not chain_is_live(state):
        if changed:
            await db.flush()
        return None, changed

    incident = await IncidentRepo.get_by_id(db, org_id, incident_id)
    if _closed(incident):
        # Every close path cancels the chain; this catches anything older.
        await IncidentChainStateRepo.cancel_live(db, org_id, incident_id, at=now)
        return None, True

    due = _aware(state.next_step_due_at)
    if state.status == "running":
        if due is None or now < due:
            if changed:
                await db.flush()
            return None, changed
        return (
            await _fire_next_level(
                db, org_id, state, now=now, channel_factory=channel_factory
            ),
            True,
        )

    if due is None or now < due:
        if changed:
            await db.flush()
        return None, changed

    if state.status == "paused":
        if state.paused_until is None:
            # A pre-upgrade snooze has no end time and stays paused.
            return None, changed
        await record_lifecycle_comment(
            db,
            org_id,
            incident_id=incident_id,
            body="The snooze ended; escalation resumed.",
        )
        return (
            await _fire_next_level(
                db, org_id, state, now=now, channel_factory=channel_factory
            ),
            True,
        )

    # Acknowledged, and the assignee has been inactive past the lock.
    active = await IncidentAssignmentRepo.get_active(db, org_id, incident_id)
    owner = (
        await _username(db, active.assigned_to) if active is not None else "the owner"
    )
    steps = await EscalationStepRepo.list_for_chain(db, org_id, state.chain_id)
    has_next = any(s.step_index > state.current_step_index for s in steps)
    if not has_next:
        # Nobody left to escalate to: the owner keeps the incident.
        await _exhaust_chain(
            db,
            org_id,
            state,
            now=now,
            reason=f"No activity from {owner} for 15 minutes; no further level exists.",
        )
        return None, True
    if active is not None:
        await IncidentAssignmentRepo.release(db, org_id, incident_id)
    await record_lifecycle_comment(
        db,
        org_id,
        incident_id=incident_id,
        body=(
            f"No activity from {owner} for 15 minutes; released the incident "
            "and resumed escalation."
        ),
    )
    return (
        await _fire_next_level(
            db, org_id, state, now=now, channel_factory=channel_factory
        ),
        True,
    )


async def tick(
    db: AsyncSession,
    org_id: uuid.UUID,
    *,
    incident_id: uuid.UUID,
    at: datetime | None = None,
    channel_factory: ChannelFactory | None = None,
) -> StepFireResult | None:
    """Advance the chain for ``incident_id`` if anything about it is due.

    Idempotent — safe to call repeatedly. Returns the fire result of the
    newly-fired step, or None if nothing fired.
    """

    result, _ = await _tick_state(
        db,
        org_id,
        incident_id=incident_id,
        now=at or _utcnow(),
        channel_factory=channel_factory,
    )
    return result


async def escalate_now(
    db: AsyncSession,
    org_id: uuid.UUID,
    *,
    incident_id: uuid.UUID,
    at: datetime | None = None,
    channel_factory: ChannelFactory | None = None,
) -> StepFireResult | None:
    """Immediately fire the next level of a live chain.

    Clears a snooze or an acknowledgement lock first. It does not change who
    owns the incident or its status.
    """

    now = at or _utcnow()
    state = await IncidentChainStateRepo.get_for_incident(
        db, org_id, incident_id, for_update=True
    )
    if not chain_is_live(state):
        return None
    incident = await IncidentRepo.get_by_id(db, org_id, incident_id)
    if _closed(incident):
        return None
    return await _fire_next_level(
        db, org_id, state, now=now, channel_factory=channel_factory
    )


@dataclasses.dataclass(slots=True)
class AckOutcome:
    # acknowledged | refreshed | owned_by_other | closed
    status: str
    # True when a live chain is now held by the assignee's acknowledgement lock.
    chain_locked: bool = False


async def acknowledge(
    db: AsyncSession,
    org_id: uuid.UUID,
    *,
    incident_id: uuid.UUID,
    assignee_id: uuid.UUID,
    actor_id: uuid.UUID | None = None,
    via: str = "web_ui",
    assigned_by: str = "self_ack",
    replace_owner: bool = False,
    note: str | None = None,
    at: datetime | None = None,
) -> AckOutcome:
    """Make ``assignee_id`` the owner and hold the chain under their lock.

    The one path for every ownership change: acknowledge, Take/assign, bulk
    acknowledge, phone keypad 1, chat actions, session takeover, soft
    takeover and admin force. ``actor_id`` is who acted (defaults to the
    assignee), so an operator assigning someone else never assigns
    themselves.

    Someone holding a live acknowledgement lock keeps it unless
    ``replace_owner`` is set (Take, a confirmed takeover, admin force).
    Resolved and merged incidents are never re-owned.
    """

    now = at or _utcnow()
    actor_id = actor_id or assignee_id
    state = await IncidentChainStateRepo.get_for_incident(
        db, org_id, incident_id, for_update=True
    )
    incident = await IncidentRepo.get_by_id(db, org_id, incident_id)
    if _closed(incident):
        return AckOutcome("closed")

    active = await IncidentAssignmentRepo.get_active(db, org_id, incident_id)
    locked_by_other = (
        active is not None
        and active.assigned_to != assignee_id
        and chain_is_live(state)
        and state.status == "acked"
    )
    if locked_by_other and not replace_owner:
        return AckOutcome("owned_by_other")

    new_owner = active is None or active.assigned_to != assignee_id
    await IncidentPageRepo.ack_all_unacked(
        db, org_id, incident_id=incident_id, user_id=assignee_id, via=via
    )
    if new_owner:
        await IncidentAssignmentRepo.assign(
            db,
            org_id,
            incident_id=incident_id,
            user_id=assignee_id,
            assigned_by=assigned_by,
        )
        if note is None:
            if actor_id == assignee_id:
                note = f"Acknowledged the incident (via {via})."
            else:
                note = (
                    f"Assigned the incident to {await _username(db, assignee_id)} "
                    f"(via {via})."
                )
        await record_lifecycle_comment(
            db,
            org_id,
            incident_id=incident_id,
            body=note,
            author_user_id=actor_id,
        )

    chain_locked = False
    if chain_is_live(state):
        if new_owner or state.status != "acked":
            # A new owner, or the first acknowledgement of a running or
            # snoozed chain, starts a fresh lock. The same owner acknowledging
            # again keeps their snooze and any pending takeover request.
            state.paused_until = None
            state.pending_takeover_user_id = None
            state.pending_takeover_expires_at = None
        state.status = "acked"
        state.finished_at = None
        state.last_activity_at = now
        state.hard_deadline_at = None
        state.next_step_due_at = _lock_deadline(state, now)
        chain_locked = True

    # Acknowledgement stops any staged notification escalation for this incident.
    from backend.paging import notification_escalation as _ne

    await _ne.stop_escalation(
        db, org_id, incident_id=incident_id, status="acked", at=now
    )
    await db.flush()
    return AckOutcome(
        "acknowledged" if new_owner else "refreshed", chain_locked=chain_locked
    )


async def handle_ack(
    db: AsyncSession,
    org_id: uuid.UUID,
    *,
    incident_id: uuid.UUID,
    user_id: uuid.UUID,
    via: str = "web_ui",
    at: datetime | None = None,
) -> bool:
    """Acknowledge as ``user_id``. Returns True if this took the chain under
    the user's acknowledgement lock."""

    outcome = await acknowledge(
        db, org_id, incident_id=incident_id, assignee_id=user_id, via=via, at=at
    )
    return outcome.status == "acknowledged" and outcome.chain_locked


async def record_assignee_activity(
    db: AsyncSession,
    org_id: uuid.UUID,
    *,
    incident_id: uuid.UUID,
    actor_id: uuid.UUID,
    at: datetime | None = None,
) -> bool:
    """Extend the acknowledgement lock after the assignee's own write.

    Call it only after an authorized, successful, incident-scoped write, in
    the same transaction. Writes by anyone else never count.
    """

    now = at or _utcnow()
    state = await IncidentChainStateRepo.get_for_incident(
        db, org_id, incident_id, for_update=True
    )
    if not chain_is_live(state) or state.status != "acked":
        return False
    active = await IncidentAssignmentRepo.get_active(db, org_id, incident_id)
    if active is None or active.assigned_to != actor_id:
        return False
    state.last_activity_at = now
    state.next_step_due_at = _lock_deadline(state, now)
    await db.flush()
    return True


async def release_ownership(
    db: AsyncSession,
    org_id: uuid.UUID,
    *,
    incident_id: uuid.UUID,
    actor_id: uuid.UUID,
    at: datetime | None = None,
    channel_factory: ChannelFactory | None = None,
) -> bool:
    """Release the owner. Under a live acknowledgement lock, escalation
    resumes at the next level (never level zero). Returns False if nobody
    owned the incident."""

    now = at or _utcnow()
    state = await IncidentChainStateRepo.get_for_incident(
        db, org_id, incident_id, for_update=True
    )
    if not await IncidentAssignmentRepo.release(db, org_id, incident_id):
        return False
    incident = await IncidentRepo.get_by_id(db, org_id, incident_id)
    resume = chain_is_live(state) and state.status == "acked" and not _closed(incident)
    await record_lifecycle_comment(
        db,
        org_id,
        incident_id=incident_id,
        body=(
            "Released the incident; escalation resumed."
            if resume
            else "Released the incident."
        ),
        author_user_id=actor_id,
    )
    if resume:
        state.last_activity_at = None
        await _fire_next_level(
            db, org_id, state, now=now, channel_factory=channel_factory
        )
    return True


async def snooze(
    db: AsyncSession,
    org_id: uuid.UUID,
    *,
    incident_id: uuid.UUID,
    actor_id: uuid.UUID,
    until: datetime,
    at: datetime | None = None,
) -> datetime | None:
    """Pause escalation until ``until``.

    Unacknowledged: the next level fires when the snooze ends.
    Acknowledged: the owner keeps the incident and the lock lasts at least
    until the snooze ends. Returns when escalation resumes, or None if there
    is no live chain to snooze.
    """

    now = at or _utcnow()
    if until <= now:
        raise ValueError("A snooze must end in the future")
    state = await IncidentChainStateRepo.get_for_incident(
        db, org_id, incident_id, for_update=True
    )
    if not chain_is_live(state):
        return None
    incident = await IncidentRepo.get_by_id(db, org_id, incident_id)
    if _closed(incident):
        return None

    state.paused_until = until
    state.hard_deadline_at = None
    if state.status == "acked":
        active = await IncidentAssignmentRepo.get_active(db, org_id, incident_id)
        if active is not None and active.assigned_to == actor_id:
            state.last_activity_at = now
        last = _aware(state.last_activity_at) or now
        state.next_step_due_at = _lock_deadline(state, last)
    else:
        state.status = "paused"
        state.next_step_due_at = until
    await db.flush()
    await record_lifecycle_comment(
        db,
        org_id,
        incident_id=incident_id,
        body=f"Snoozed escalation until {until.strftime('%Y-%m-%d %H:%M UTC')}.",
        author_user_id=actor_id,
    )
    return _aware(state.next_step_due_at)


async def handle_takeover_request(
    db: AsyncSession,
    org_id: uuid.UUID,
    *,
    incident_id: uuid.UUID,
    requester_id: uuid.UUID,
    at: datetime | None = None,
) -> str:
    """Ask the current owner to hand over the incident.

    Returns ``assigned`` (nobody owned it), ``noop`` (already yours),
    ``pending`` (the owner has five minutes to confirm), ``requires_admin``
    (no chain to hold the request), or ``closed``.
    """

    now = at or _utcnow()
    state = await IncidentChainStateRepo.get_for_incident(
        db, org_id, incident_id, for_update=True
    )
    incident = await IncidentRepo.get_by_id(db, org_id, incident_id)
    if _closed(incident):
        return "closed"
    active = await IncidentAssignmentRepo.get_active(db, org_id, incident_id)
    if active is None:
        await acknowledge(
            db,
            org_id,
            incident_id=incident_id,
            assignee_id=requester_id,
            via="take",
            at=now,
        )
        return "assigned"
    if active.assigned_to == requester_id:
        return "noop"
    if state is None:
        return "requires_admin"
    state.pending_takeover_user_id = requester_id
    state.pending_takeover_expires_at = now + timedelta(
        seconds=SOFT_TAKEOVER_WINDOW_SECONDS
    )
    await db.flush()
    await record_lifecycle_comment(
        db,
        org_id,
        incident_id=incident_id,
        body="Requested to take over the incident.",
        author_user_id=requester_id,
    )
    return "pending"


async def handle_takeover_confirm(
    db: AsyncSession,
    org_id: uuid.UUID,
    *,
    incident_id: uuid.UUID,
    actor_id: uuid.UUID,
    at: datetime | None = None,
) -> str:
    """The current owner hands the incident to the pending requester.

    Returns ``confirmed``, ``none`` (no pending request), ``expired``,
    ``not_owner`` (only the current owner can confirm), ``ineligible`` (the
    requester can no longer own incidents), or ``closed``.
    """

    now = at or _utcnow()
    state = await IncidentChainStateRepo.get_for_incident(
        db, org_id, incident_id, for_update=True
    )
    if state is None or state.pending_takeover_user_id is None:
        return "none"
    expires = _aware(state.pending_takeover_expires_at)
    if expires is not None and now >= expires:
        state.pending_takeover_user_id = None
        state.pending_takeover_expires_at = None
        await db.flush()
        return "expired"
    active = await IncidentAssignmentRepo.get_active(db, org_id, incident_id)
    if active is None or active.assigned_to != actor_id:
        return "not_owner"
    new_owner = state.pending_takeover_user_id
    state.pending_takeover_user_id = None
    state.pending_takeover_expires_at = None
    if not await is_eligible_owner(db, org_id, new_owner):
        await db.flush()
        return "ineligible"
    outcome = await acknowledge(
        db,
        org_id,
        incident_id=incident_id,
        assignee_id=new_owner,
        actor_id=actor_id,
        via="takeover",
        assigned_by="manual",
        replace_owner=True,
        note=f"Handed the incident to {await _username(db, new_owner)}.",
        at=now,
    )
    return "closed" if outcome.status == "closed" else "confirmed"


async def handle_force_takeover(
    db: AsyncSession,
    org_id: uuid.UUID,
    *,
    incident_id: uuid.UUID,
    admin_id: uuid.UUID,
    at: datetime | None = None,
) -> bool:
    """Admin force-takeover, recorded as ``admin_force`` on the assignment and
    on the incident timeline. Returns False for a resolved or merged
    incident."""

    outcome = await acknowledge(
        db,
        org_id,
        incident_id=incident_id,
        assignee_id=admin_id,
        via="admin_force",
        assigned_by="admin_force",
        replace_owner=True,
        note="Took over the incident (admin force).",
        at=at,
    )
    if outcome.status == "closed":
        return False
    state = await IncidentChainStateRepo.get_for_incident(db, org_id, incident_id)
    if state is not None:
        state.pending_takeover_user_id = None
        state.pending_takeover_expires_at = None
        await db.flush()
    return True


async def cancel_chain(
    db: AsyncSession,
    org_id: uuid.UUID,
    *,
    incident_id: uuid.UUID,
    at: datetime | None = None,
) -> bool:
    """Cancel a live chain (running, snoozed, or acknowledged)."""

    return await IncidentChainStateRepo.cancel_live(
        db, org_id, incident_id, at=at or _utcnow()
    )


async def tick_all_due(
    db: AsyncSession,
    *,
    at: datetime | None = None,
    channel_factory: ChannelFactory | None = None,
) -> int:
    """Scheduler entry point — act on every chain with something due.

    Returns the number of state rows that changed.
    """

    now = at or _utcnow()
    due = await IncidentChainStateRepo.list_due(db, now=now)
    changed_rows = 0
    for state in due:
        try:
            async with db.begin_nested():
                _, changed = await _tick_state(
                    db,
                    state.org_id,
                    incident_id=state.incident_id,
                    now=now,
                    channel_factory=channel_factory,
                )
            if changed:
                changed_rows += 1
        except Exception as exc:
            _log.warning(
                "escalation tick isolated a failed chain (%s)", type(exc).__name__
            )
    return changed_rows
