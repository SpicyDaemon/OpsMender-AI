"""Tell the covered teams and admins when an operator's Maintenance Window
becomes active (O-05).

A window an operator creates drops alerts and holds pages for the services
it covers, so the people who answer for those services, and the admins, get
one Inbox notice when it starts: at once if it is active when created or
approved, otherwise from the scheduler when its start comes. Each window is
announced once.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.db.models import (
    MaintenanceWindow,
    Roster,
    Service,
    TeamMember,
    User,
    UserOrganization,
)
from backend.notifications import CATEGORY_RELIABILITY, emit_notification
from backend.paging.maintenance import window_active_at

LINK = "/dashboard/paging/maintenance-windows"


async def _covered_teams(
    db: AsyncSession, window: MaintenanceWindow
) -> set[uuid.UUID] | None:
    """The teams a window covers, or None for every team (global)."""
    ids = window.scope_ids
    if window.scope_type == "team":
        return set(ids)
    if window.scope_type == "service":
        rows = await db.execute(
            select(Service.team_id).where(
                Service.org_id == window.org_id, Service.id.in_(ids)
            )
        )
        return {team_id for team_id in rows.scalars() if team_id is not None}
    if window.scope_type == "roster":
        rows = await db.execute(
            select(Roster.team_id).where(
                Roster.org_id == window.org_id, Roster.id.in_(ids)
            )
        )
        return set(rows.scalars())
    return None


async def _recipients(db: AsyncSession, window: MaintenanceWindow) -> list[uuid.UUID]:
    """Active admins, and active members of the covered teams, but not the
    window's creator."""
    teams = await _covered_teams(db, window)
    admins = select(UserOrganization.user_id).where(
        UserOrganization.org_id == window.org_id, UserOrganization.role == "admin"
    )
    members = select(TeamMember.user_id).where(TeamMember.org_id == window.org_id)
    if teams is not None:
        members = members.where(TeamMember.team_id.in_(teams))
    rows = await db.execute(
        select(User.id)
        .where(
            User.is_active.is_(True),
            User.deleted_at.is_(None),
            (User.id.in_(admins)) | (User.id.in_(members)),
        )
        .order_by(User.id)
    )
    return [user_id for user_id in rows.scalars() if user_id != window.created_by]


async def announce(db: AsyncSession, window: MaintenanceWindow, *, at: datetime) -> int:
    """Send the one notice for ``window`` if it is due: created by an
    operator, approved, active at ``at`` and not yet announced. Returns the
    number of people told."""
    if (
        not window.announce_on_start
        or window.start_announced_at is not None
        or not window.approved
        or not window_active_at(window, at)
    ):
        return 0
    creator = await db.get(User, window.created_by) if window.created_by else None
    who = (creator.username or creator.email) if creator is not None else "An operator"
    recipients = await _recipients(db, window)
    for user_id in recipients:
        await emit_notification(
            db,
            window.org_id,
            user_id,
            event_type="maintenance.started",
            category=CATEGORY_RELIABILITY,
            title=f"Maintenance window started: {window.name}",
            body=(
                f"{who} started this window. Alerts it covers are dropped and "
                "its pages held until it ends."
            ),
            link=LINK,
        )
    window.start_announced_at = at
    await db.flush()
    return len(recipients)


async def announce_started(db: AsyncSession, *, at: datetime) -> int:
    """Announce every operator window whose start has come. Scheduler use."""
    rows = await db.execute(
        select(MaintenanceWindow).where(
            MaintenanceWindow.announce_on_start.is_(True),
            MaintenanceWindow.start_announced_at.is_(None),
            MaintenanceWindow.approved.is_(True),
            MaintenanceWindow.starts_at <= at,
            (MaintenanceWindow.ends_at > at) | MaintenanceWindow.rrule.is_not(None),
        )
    )
    told = 0
    for window in rows.scalars():
        told += await announce(db, window, at=at)
    return told
