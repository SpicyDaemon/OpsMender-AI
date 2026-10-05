"""People asked to help with an incident, besides its owner.

An incident can have up to ``RESPONDER_LIMIT`` responders. Each one is paged
once through their own routing, like a chain level that targets a person, and
gets an Inbox notice saying who asked. Their page rows use the ``responder``
channel with no chain level, so they never count as chain pages.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.auth.roles import request_role
from backend.db.models import Incident, IncidentPage, IncidentResponder, User
from backend.db.repos import (
    IncidentAssignmentRepo,
    IncidentChainStateRepo,
    IncidentPageRepo,
    IncidentRepo,
    IncidentResponderRepo,
    UserRepo,
    chain_is_live,
)
from backend.notifications import CATEGORY_INCIDENT, emit_notification
from backend.paging.dispatch import ChannelFactory, dispatch_page
from backend.paging.reassign import on_incident_team
from backend.services.incident_timeline import record_lifecycle_comment

RESPONDER_LIMIT = 3
RESPONDER_CHANNEL = "responder"
MESSAGE_MAX_LENGTH = 500


class ResponderError(Exception):
    """A request the API should refuse, with the status and message to show."""

    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


def _names(names: list[str]) -> str:
    if len(names) <= 1:
        return "".join(names)
    return ", ".join(names[:-1]) + f" and {names[-1]}"


async def can_manage_responders(
    db: AsyncSession, org_id: uuid.UUID, incident: Incident, user: User
) -> bool:
    """Admins, the owner, and operators on the incident's team (any operator
    when it has none)."""

    role = request_role(user)
    if role == "admin":
        return True
    if role != "operator":
        return False
    owner = await IncidentAssignmentRepo.get_active(db, org_id, incident.id)
    if owner is not None and owner.assigned_to == user.id:
        return True
    member = await on_incident_team(db, org_id, incident, user.id)
    return member is None or member


async def can_respond(db: AsyncSession, org_id: uuid.UUID, user: User | None) -> bool:
    """Active admins and operators in the workspace. Viewers can't respond."""

    from backend.paging.escalation import is_eligible_owner

    return (
        user is not None
        and user.role in ("admin", "operator")
        and await is_eligible_owner(db, org_id, user.id)
    )


async def add_responders(
    db: AsyncSession,
    org_id: uuid.UUID,
    *,
    incident: Incident,
    user_ids: list[uuid.UUID],
    actor: User,
    message: str | None = None,
    channel_factory: ChannelFactory | None = None,
) -> list[IncidentResponder]:
    """Add people as responders and ask each one for help.

    The caller checks permission and that the incident is open. Raises
    ``ResponderError`` for anyone who can't be added or when the limit
    would be passed; nothing is added in that case.
    """

    # Concurrent adds wait here, so the limit holds.
    incident = await IncidentRepo.get_by_id(db, org_id, incident.id, for_update=True)
    if incident is None:
        raise ResponderError(404, "Incident not found.")
    if incident.status in ("resolved", "merged"):
        raise ResponderError(409, f"The incident is {incident.status}.")
    if not await can_manage_responders(db, org_id, incident, actor):
        raise ResponderError(
            403, "You can no longer change this incident's responders."
        )
    wanted = list(dict.fromkeys(user_ids))
    if not wanted:
        raise ResponderError(422, "Choose at least one person.")
    current = await IncidentResponderRepo.list_for_incident(db, org_id, incident.id)
    current_ids = {row.user_id for row in current}
    owner = await IncidentAssignmentRepo.get_active(db, org_id, incident.id)
    users: list[User] = []
    for user_id in wanted:
        user = await UserRepo.get_by_id(db, user_id)
        name = user.username if user is not None else "That person"
        if owner is not None and owner.assigned_to == user_id:
            raise ResponderError(409, f"{name} already owns this incident.")
        if user_id in current_ids:
            raise ResponderError(409, f"{name} is already a responder.")
        if not await can_respond(db, org_id, user):
            raise ResponderError(422, f"{name} can't respond to incidents.")
        users.append(user)
    if len(current) + len(users) > RESPONDER_LIMIT:
        left = RESPONDER_LIMIT - len(current)
        raise ResponderError(
            409,
            f"An incident can have up to {RESPONDER_LIMIT} responders. "
            + (
                "Remove one to add another."
                if left <= 0
                else f"You can add {left} more."
            ),
        )

    state = await IncidentChainStateRepo.get_for_incident(db, org_id, incident.id)
    round_ = state.round if state is not None else 0
    added: list[IncidentResponder] = []
    for user in users:
        added.append(
            await IncidentResponderRepo.add(
                db, org_id, incident_id=incident.id, user_id=user.id, added_by=actor.id
            )
        )
        page = await IncidentPageRepo.create(
            db,
            org_id,
            incident_id=incident.id,
            user_id=user.id,
            round=round_,
            channel=RESPONDER_CHANNEL,
        )
        if channel_factory is not None:
            await dispatch_page(
                db,
                org_id,
                incident=incident,
                user=user,
                page=page,
                channel_factory=channel_factory,
                requested_by_person=True,
            )
        await emit_notification(
            db,
            org_id,
            user.id,
            event_type="incident.responder_requested",
            category=CATEGORY_INCIDENT,
            title=f"{actor.username} asked for your help: {incident.title}",
            body=message or "Open the incident to join the response.",
            link=f"/dashboard/incidents/detail?id={incident.id}",
            incident_id=incident.id,
        )
    await record_lifecycle_comment(
        db,
        org_id,
        incident_id=incident.id,
        body=(
            f"Asked {_names([user.username for user in users])} to help."
            + (f" Message: {message}" if message else "")
        ),
        author_user_id=actor.id,
    )
    return added


async def remove_responder(
    db: AsyncSession,
    org_id: uuid.UUID,
    *,
    incident: Incident,
    user_id: uuid.UUID,
    actor: User,
) -> bool:
    """Remove one responder. Returns False when they weren't a responder."""

    from backend.paging import notification_escalation

    state = await IncidentChainStateRepo.get_for_incident(
        db, org_id, incident.id, for_update=True
    )
    incident = await IncidentRepo.get_by_id(db, org_id, incident.id, for_update=True)
    if incident is None:
        raise ResponderError(404, "Incident not found.")
    if incident.status in ("resolved", "merged"):
        raise ResponderError(409, f"The incident is {incident.status}.")
    if actor.id != user_id and not await can_manage_responders(
        db, org_id, incident, actor
    ):
        raise ResponderError(
            403, "You can no longer change this incident's responders."
        )
    if not await IncidentResponderRepo.remove(
        db, org_id, incident_id=incident.id, user_id=user_id
    ):
        return False
    # Staged routing is shared by incident and person. A chain page can keep
    # that person's stages running even after their extra responder role ends.
    chain_paged_user = False
    if chain_is_live(state):
        chain_paged_user = (
            await db.execute(
                select(IncidentPage.id)
                .where(
                    IncidentPage.org_id == org_id,
                    IncidentPage.incident_id == incident.id,
                    IncidentPage.user_id == user_id,
                    IncidentPage.chain_id == state.chain_id,
                    IncidentPage.round == state.round,
                    IncidentPage.channel == "recorded",
                )
                .limit(1)
            )
        ).scalar_one_or_none() is not None
    if not chain_paged_user:
        await notification_escalation.stop_escalation(
            db, org_id, incident_id=incident.id, user_id=user_id, status="cancelled"
        )
    if user_id == actor.id:
        body = "Left the responders."
    else:
        user = await UserRepo.get_by_id(db, user_id)
        name = user.username if user is not None else "a responder"
        body = f"Removed {name} from the responders."
    await record_lifecycle_comment(
        db, org_id, incident_id=incident.id, body=body, author_user_id=actor.id
    )
    return True
