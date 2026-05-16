"""Chain execution engine (Sprint 34).

State machine for ``incident_chain_states``:

```
[no row]
   │  start_chain(incident_id, chain_id)
   ▼
[running, step=-1]
   │  tick()  — fires step 0 immediately
   ▼
[running, step=N]
   │
   ├─ handle_ack(incident_id, user_id, via)  ─►  [acked, finished]
   │
   ├─ tick()  ─►  next_step_due_at passed?
   │     │
   │     ├─ step N+1 exists:  fires step N+1 (additive — step N stays paged)
   │     │
   │     └─ no more steps:    [exhausted, finished]
   │
   ├─ handle_takeover_request(requester) ─► sets pending_takeover_*
   ├─ handle_takeover_confirm() (within 5 min) ─► assignment swapped
   ├─ handle_force_takeover(admin) ─► assignment swapped, audit-logged
   └─ cancel_chain(incident_id) ─► [cancelled, finished]
```

Additive page semantics (D-021 #5): once a user has been paged on step N,
they stay paged for the rest of the chain. ``IncidentPage.already_paged``
keys on ``(incident_id, user_id, step_index)`` so a re-fire of the same
step is idempotent. Pages from earlier steps are NOT re-issued for higher
steps; the audit log records every fire-event uniquely.

Hard inactivity timeout (15 min) is tracked on
``incident_chain_states.hard_deadline_at``. When ticked past it, the chain
moves to ``exhausted`` even if a step is mid-timeout.

The engine never blocks on real notification delivery — it writes
``incident_pages`` rows with ``channel='recorded'`` and Sprint 35 wires the
actual channels.
"""

from __future__ import annotations

import dataclasses
import uuid
from datetime import datetime, timedelta, timezone
from typing import Iterable

from sqlalchemy.ext.asyncio import AsyncSession

from backend.db.repos import (
    EscalationStepRepo,
    IncidentAssignmentRepo,
    IncidentChainStateRepo,
    IncidentPageRepo,
    IncidentRepo,
    RosterOverrideRepo,
    RosterRepo,
    ServiceEscalationChainRepo,
    TeamRepo,
    UserRepo,
)
from backend.paging.dispatch import ChannelFactory, dispatch_page
from backend.paging.on_call import (
    OnCallContext,
    OnCallMember,
    OnCallOverride,
    on_call_at,
)


SOFT_TAKEOVER_WINDOW_SECONDS = 5 * 60
HARD_INACTIVITY_TIMEOUT_SECONDS = 15 * 60


@dataclasses.dataclass(slots=True)
class StepFireResult:
    step_index: int
    users_paged: list[uuid.UUID]


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
        return [target_id]
    if target_type == "team":
        members = await TeamRepo.list_members(db, org_id, target_id)
        return [m.user_id for m in members]
    if target_type == "roster":
        roster = await RosterRepo.get_by_id(db, org_id, target_id)
        if roster is None:
            return []
        members = await RosterRepo.list_members(db, org_id, target_id)
        overrides = await RosterOverrideRepo.list_for_roster(db, org_id, target_id)
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
                for o in overrides
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
    incident = None
    for uid in user_ids:
        if await IncidentPageRepo.already_paged(
            db,
            org_id,
            incident_id=incident_id,
            user_id=uid,
            step_index=step.step_index,
        ):
            continue
        page = await IncidentPageRepo.create(
            db,
            org_id,
            incident_id=incident_id,
            user_id=uid,
            chain_id=chain_id,
            step_index=step.step_index,
        )
        fired.append(uid)
        if channel_factory is not None:
            if incident is None:
                incident = await IncidentRepo.get_by_id(
                    db, org_id, incident_id
                )
            user = await UserRepo.get_by_id(db, uid)
            if incident is not None and user is not None:
                await dispatch_page(
                    db,
                    org_id,
                    incident=incident,
                    user=user,
                    page=page,
                    channel_factory=channel_factory,
                    at=at,
                )
    return StepFireResult(step_index=step.step_index, users_paged=fired)


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
    links = await ServiceEscalationChainRepo.list_for_service(
        db, org_id, service_id
    )
    if not links:
        return None
    if priority is None:
        return links[0]
    matching = []
    for link in links:
        applies_when = link.applies_when or {}
        priorities = applies_when.get("priorities") if isinstance(applies_when, dict) else None
        if priorities and priority in {str(p).upper() for p in priorities}:
            matching.append(link)
    if matching:
        return matching[0]
    return links[0]


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
        db, org_id, incident_id
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
    state.hard_deadline_at = now + timedelta(seconds=HARD_INACTIVITY_TIMEOUT_SECONDS)

    steps = await EscalationStepRepo.list_for_chain(db, org_id, chain_id)
    if not steps:
        state.status = "exhausted"
        state.finished_at = now
        await db.flush()
        return None

    if mode == "escalate_immediate":
        last_result: StepFireResult | None = None
        for step in steps:
            last_result = await _fire_step(
                db,
                org_id,
                incident_id=incident_id,
                chain_id=chain_id,
                step=step,
                at=now,
                channel_factory=channel_factory,
            )
            state.current_step_index = step.step_index
        state.next_step_due_at = None
        await db.flush()
        return last_result

    # page mode: fire step 0, schedule next.
    step0 = steps[0]
    result = await _fire_step(
        db,
        org_id,
        incident_id=incident_id,
        chain_id=chain_id,
        step=step0,
        at=now,
        channel_factory=channel_factory,
    )
    state.current_step_index = step0.step_index
    if len(steps) > 1:
        state.next_step_due_at = now + timedelta(seconds=step0.timeout_seconds)
    else:
        state.next_step_due_at = None
    await db.flush()
    return result


async def tick(
    db: AsyncSession,
    org_id: uuid.UUID,
    *,
    incident_id: uuid.UUID,
    at: datetime | None = None,
    channel_factory: ChannelFactory | None = None,
) -> StepFireResult | None:
    """Advance the chain for ``incident_id`` if its timer has expired.

    Idempotent — safe to call repeatedly. Returns the fire result of the
    newly-fired step, or None if nothing happened.
    """

    now = at or _utcnow()
    state = await IncidentChainStateRepo.get_for_incident(
        db, org_id, incident_id
    )
    if state is None or state.status != "running":
        return None

    hard_deadline = _aware(state.hard_deadline_at)
    next_due = _aware(state.next_step_due_at)
    # Hard inactivity timeout.
    if hard_deadline is not None and now >= hard_deadline:
        state.status = "exhausted"
        state.finished_at = now
        state.next_step_due_at = None
        await db.flush()
        return None

    if next_due is None or now < next_due:
        return None

    steps = list(
        await EscalationStepRepo.list_for_chain(db, org_id, state.chain_id)
    )
    next_idx = state.current_step_index + 1
    next_step = next((s for s in steps if s.step_index == next_idx), None)
    if next_step is None:
        state.status = "exhausted"
        state.finished_at = now
        state.next_step_due_at = None
        await db.flush()
        return None

    result = await _fire_step(
        db,
        org_id,
        incident_id=incident_id,
        chain_id=state.chain_id,
        step=next_step,
        at=now,
        channel_factory=channel_factory,
    )
    state.current_step_index = next_step.step_index
    has_more = any(s.step_index > next_step.step_index for s in steps)
    if has_more:
        state.next_step_due_at = now + timedelta(seconds=next_step.timeout_seconds)
    else:
        state.next_step_due_at = None
    await db.flush()
    return result


async def handle_ack(
    db: AsyncSession,
    org_id: uuid.UUID,
    *,
    incident_id: uuid.UUID,
    user_id: uuid.UUID,
    via: str = "web_ui",
    at: datetime | None = None,
) -> bool:
    """Ack the chain for ``user_id``. Pauses the chain and makes the acker
    the active assignee.

    Returns True if the chain transitioned to ``acked``.
    """

    now = at or _utcnow()
    state = await IncidentChainStateRepo.get_for_incident(
        db, org_id, incident_id
    )
    if state is None:
        # No chain running — still record the ack on any unacked pages.
        await IncidentPageRepo.ack_all_unacked(
            db, org_id, incident_id=incident_id, user_id=user_id, via=via
        )
        await IncidentAssignmentRepo.assign(
            db,
            org_id,
            incident_id=incident_id,
            user_id=user_id,
            assigned_by="self_ack",
        )
        return False

    if state.status not in ("running", "paused"):
        return False

    await IncidentPageRepo.ack_all_unacked(
        db, org_id, incident_id=incident_id, user_id=user_id, via=via
    )
    await IncidentAssignmentRepo.assign(
        db,
        org_id,
        incident_id=incident_id,
        user_id=user_id,
        assigned_by="self_ack",
    )
    state.status = "acked"
    state.finished_at = now
    state.next_step_due_at = None
    await db.flush()
    return True


async def handle_takeover_request(
    db: AsyncSession,
    org_id: uuid.UUID,
    *,
    incident_id: uuid.UUID,
    requester_id: uuid.UUID,
    at: datetime | None = None,
) -> str:
    """Start the soft-takeover window. The current assignee has
    ``SOFT_TAKEOVER_WINDOW_SECONDS`` to confirm — auto-release on timeout."""

    now = at or _utcnow()
    active = await IncidentAssignmentRepo.get_active(db, org_id, incident_id)
    if active is None:
        # No current owner — straight-up assign the requester.
        await IncidentAssignmentRepo.assign(
            db,
            org_id,
            incident_id=incident_id,
            user_id=requester_id,
            assigned_by="self_ack",
        )
        return "assigned"
    if active.assigned_to == requester_id:
        return "noop"

    state = await IncidentChainStateRepo.get_for_incident(
        db, org_id, incident_id
    )
    if state is None:
        # Chain ended — but ownership still exists. Defer to admin force.
        return "requires_admin"
    state.pending_takeover_user_id = requester_id
    state.pending_takeover_expires_at = now + timedelta(
        seconds=SOFT_TAKEOVER_WINDOW_SECONDS
    )
    await db.flush()
    return "pending"


async def handle_takeover_confirm(
    db: AsyncSession,
    org_id: uuid.UUID,
    *,
    incident_id: uuid.UUID,
    at: datetime | None = None,
) -> bool:
    """Confirm a soft-takeover. Called by the current owner OR on auto-expiry
    by the scheduler.
    """

    now = at or _utcnow()
    state = await IncidentChainStateRepo.get_for_incident(
        db, org_id, incident_id
    )
    if state is None or state.pending_takeover_user_id is None:
        return False
    expires = _aware(state.pending_takeover_expires_at)
    if expires is not None and now > expires:
        # Window expired — auto-confirm per spec.
        pass
    new_owner = state.pending_takeover_user_id
    await IncidentAssignmentRepo.assign(
        db,
        org_id,
        incident_id=incident_id,
        user_id=new_owner,
        assigned_by="manual",
    )
    state.pending_takeover_user_id = None
    state.pending_takeover_expires_at = None
    await db.flush()
    return True


async def handle_force_takeover(
    db: AsyncSession,
    org_id: uuid.UUID,
    *,
    incident_id: uuid.UUID,
    admin_id: uuid.UUID,
    at: datetime | None = None,
) -> bool:
    """Admin force-takeover. Always succeeds. Recorded as ``admin_force``."""

    await IncidentAssignmentRepo.assign(
        db,
        org_id,
        incident_id=incident_id,
        user_id=admin_id,
        assigned_by="admin_force",
    )
    state = await IncidentChainStateRepo.get_for_incident(
        db, org_id, incident_id
    )
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
    """Cancel a running chain (e.g., incident resolved before ack)."""

    now = at or _utcnow()
    state = await IncidentChainStateRepo.get_for_incident(
        db, org_id, incident_id
    )
    if state is None or state.status not in ("running", "paused"):
        return False
    state.status = "cancelled"
    state.finished_at = now
    state.next_step_due_at = None
    await db.flush()
    return True


async def tick_all_due(
    db: AsyncSession,
    *,
    at: datetime | None = None,
    channel_factory: ChannelFactory | None = None,
) -> int:
    """Scheduler entry point — advance every chain whose timer has expired.

    Returns the number of state rows that advanced.
    """

    now = at or _utcnow()
    due = await IncidentChainStateRepo.list_due(db, now=now)
    advanced = 0
    for state in due:
        result = await tick(
            db,
            state.org_id,
            incident_id=state.incident_id,
            at=now,
            channel_factory=channel_factory,
        )
        if result is not None or state.status != "running":
            advanced += 1
    return advanced
