"""Load a Roster's on-call context from the database.

The single loader behind every "who is on call" answer: the paging engine,
the on-call API, the Roster and chain calendars, and the Services table.
"""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from backend.db.repos import RosterOverrideRepo, RosterRepo, UserRepo
from backend.paging.on_call import OnCallContext, build_context


async def load_on_call_context(
    db: AsyncSession, org_id: uuid.UUID, roster
) -> OnCallContext:
    """Active members, and Overrides whose covering user can still be paged."""

    members = await RosterRepo.list_members(db, org_id, roster.id, active_only=True)
    overrides = []
    for override in await RosterOverrideRepo.list_for_roster(db, org_id, roster.id):
        covering = await UserRepo.get_by_id(db, override.covering_user_id)
        if covering is not None and covering.is_active and covering.deleted_at is None:
            overrides.append(override)
    return build_context(roster, members, overrides)
