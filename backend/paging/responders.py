"""People asked to help with an incident, besides its owner.

An incident can have up to ``RESPONDER_LIMIT`` responders. Each one is paged
once through their own routing, like a chain level that targets a person, and
gets an Inbox notice saying who asked. Their page rows use the ``responder``
channel with no chain level, so they never count as chain pages.

People outside the handling team are asked with a request instead: they get
an Inbox notice and an email, and only they accept or decline. A pending
request holds one of the slots until it is answered, expires after
``REQUEST_TTL`` or the incident closes; the requester learns the outcome.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.auth.roles import request_role
from backend.db.models import (
    Incident,
    IncidentPage,
    IncidentResponder,
    IncidentResponderRequest,
    User,
)
from backend.db.repos import (
    IncidentAssignmentRepo,
    IncidentChainStateRepo,
    IncidentPageRepo,
    IncidentRepo,
    IncidentResponderRepo,
    IncidentResponderRequestRepo,
    TeamRepo,
    UserRepo,
    chain_is_live,
)
from backend.notifications import CATEGORY_INCIDENT, emit_notification
from backend.paging.dispatch import ChannelFactory, dispatch_page
from backend.paging.reassign import incident_team_id, on_incident_team
from backend.services.incident_timeline import record_lifecycle_comment

RESPONDER_LIMIT = 3
RESPONDER_CHANNEL = "responder"
MESSAGE_MAX_LENGTH = 500
REQUEST_TTL = timedelta(minutes=30)

logger = logging.getLogger(__name__)


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

    The caller checks permission and that the incident is open. Admins add
    anyone; operators add only members of the team handling the incident
    (anyone when it has no team). Raises ``ResponderError`` for anyone who
    can't be added or when the limit would be passed; nothing is added in
    that case.
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
    # Pending requests hold slots; adding someone who was asked answers theirs.
    pending = await IncidentResponderRequestRepo.list_pending_for_incident(
        db, org_id, incident.id
    )
    held = [row for row in pending if row.user_id not in wanted]
    owner = await IncidentAssignmentRepo.get_active(db, org_id, incident.id)
    team_id = (
        None
        if request_role(actor) == "admin"
        else await incident_team_id(db, org_id, incident)
    )
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
        if team_id is not None and not await TeamRepo.is_member(
            db, org_id, team_id, user_id
        ):
            team = await TeamRepo.get_by_id(db, org_id, team_id)
            where = "the team handling this incident"
            if team is not None:
                where = f"{team.name}, {where}"
            raise ResponderError(
                403,
                f"{name} isn't on {where}. Send them a request to join, "
                "or ask an admin to add them.",
            )
        users.append(user)
    _check_slots(len(current) + len(held), len(users))
    now = datetime.now(timezone.utc)
    for row in pending:
        if row.user_id in wanted:
            await IncidentResponderRequestRepo.finish(
                db, org_id, row.id, status="cancelled", at=now
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


def _check_slots(taken: int, adding: int) -> None:
    """Responders and pending requests share the limit."""
    if taken + adding > RESPONDER_LIMIT:
        left = RESPONDER_LIMIT - taken
        raise ResponderError(
            409,
            f"An incident can have up to {RESPONDER_LIMIT} responders, counting "
            "people asked who haven't answered yet. "
            + (
                "Remove one to add another."
                if left <= 0
                else f"You can add {left} more."
            ),
        )


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


# ---------------------------------------------------------------------------
# Requests to people outside the handling team
# ---------------------------------------------------------------------------


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


async def request_responders(
    db: AsyncSession,
    org_id: uuid.UUID,
    *,
    incident: Incident,
    user_ids: list[uuid.UUID],
    actor: User,
    message: str | None = None,
    at: datetime | None = None,
) -> list[IncidentResponderRequest]:
    """Ask people outside the handling team to join as responders.

    Each one gets an Inbox notice; the caller sends the emails after commit.
    The same people manage requests as manage responders. Raises
    ``ResponderError`` and asks nobody when anyone can't be asked or the
    requests would pass the limit.
    """

    now = at or datetime.now(timezone.utc)
    # Same lock as add_responders, so requests and adds share the limit.
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
    team_id = await incident_team_id(db, org_id, incident)
    current = await IncidentResponderRepo.list_for_incident(db, org_id, incident.id)
    current_ids = {row.user_id for row in current}
    pending = await IncidentResponderRequestRepo.list_pending_for_incident(
        db, org_id, incident.id
    )
    pending_ids = {row.user_id for row in pending}
    owner = await IncidentAssignmentRepo.get_active(db, org_id, incident.id)
    users: list[User] = []
    for user_id in wanted:
        user = await UserRepo.get_by_id(db, user_id)
        name = user.username if user is not None else "That person"
        if owner is not None and owner.assigned_to == user_id:
            raise ResponderError(409, f"{name} already owns this incident.")
        if user_id in current_ids:
            raise ResponderError(409, f"{name} is already a responder.")
        if user_id in pending_ids:
            raise ResponderError(409, f"{name} was already asked.")
        if not await can_respond(db, org_id, user):
            raise ResponderError(422, f"{name} can't respond to incidents.")
        if team_id is None or await TeamRepo.is_member(db, org_id, team_id, user_id):
            raise ResponderError(409, f"Add {name} directly; no request is needed.")
        users.append(user)
    _check_slots(len(current) + len(pending), len(users))

    created: list[IncidentResponderRequest] = []
    for user in users:
        created.append(
            await IncidentResponderRequestRepo.create(
                db,
                org_id,
                incident_id=incident.id,
                user_id=user.id,
                requested_by=actor.id,
                message=message,
                created_at=now,
                expires_at=now + REQUEST_TTL,
            )
        )
        await emit_notification(
            db,
            org_id,
            user.id,
            event_type="incident.responder_request",
            category=CATEGORY_INCIDENT,
            title=f"{actor.username} asked you to join: {incident.title}",
            body=(message + " " if message else "")
            + "Open the incident to accept or decline within 30 minutes.",
            link=f"/dashboard/incidents/detail?id={incident.id}",
            incident_id=incident.id,
        )
    await record_lifecycle_comment(
        db,
        org_id,
        incident_id=incident.id,
        body=(
            f"Asked {_names([user.username for user in users])} to join as "
            + ("a responder." if len(users) == 1 else "responders.")
            + (f" Message: {message}" if message else "")
        ),
        author_user_id=actor.id,
    )
    return created


async def send_request_emails(
    db: AsyncSession,
    org_id: uuid.UUID,
    *,
    requests: list[IncidentResponderRequest],
    incident: Incident,
    actor: User,
    base_url: str | None,
    config=None,
    channel=None,
) -> int:
    """Email each requested person, naming who asked. Best effort: skipped
    when email isn't configured, and a failed send doesn't undo the request.
    The incident link uses ``base_url`` (the configured public URL) and is
    left out without one. Returns how many were sent."""

    if channel is None:
        from backend.reports.email import build_email_channel, resolve_email_settings

        settings = await resolve_email_settings(db, org_id, config=config)
        if settings is None:
            return 0
        channel = build_email_channel(settings)
    link = (
        f"{base_url.rstrip('/')}/dashboard/incidents/detail?id={incident.id}"
        if base_url
        else None
    )
    sent = 0
    for request in requests:
        user = await UserRepo.get_by_id(db, request.user_id)
        if user is None or not user.email:
            continue
        lines = [
            f"Hi {user.username},",
            "",
            f"{actor.username} asked you to help as a responder with this incident:",
            "",
            f"  {incident.title}",
            "",
        ]
        if request.message:
            lines += [f"Message from {actor.username}: {request.message}", ""]
        lines += [
            "Open the incident in OpsMender to accept or decline. The request "
            f"expires in 30 minutes, at {_aware(request.expires_at):%H:%M} UTC.",
            "",
        ]
        if link:
            lines += [link, ""]
        try:
            attempt = await channel.send(
                recipient=user.email,
                subject=f"{actor.username} asked you to help with {incident.title}",
                body="\n".join(lines),
            )
        except Exception:  # noqa: BLE001
            logger.warning("responder request email failed", exc_info=True)
            continue
        if getattr(attempt, "status", None) == "sent":
            sent += 1
    return sent


async def answer_request(
    db: AsyncSession,
    org_id: uuid.UUID,
    *,
    incident_id: uuid.UUID,
    request_id: uuid.UUID,
    user: User,
    accept: bool,
    at: datetime | None = None,
) -> IncidentResponderRequest:
    """The requested person accepts (joining as a responder) or declines."""

    now = at or datetime.now(timezone.utc)
    incident = await IncidentRepo.get_by_id(db, org_id, incident_id, for_update=True)
    request = await IncidentResponderRequestRepo.get_by_id(db, org_id, request_id)
    if incident is None or request is None or request.incident_id != incident_id:
        raise ResponderError(404, "Request not found.")
    if request.user_id != user.id:
        asked = await UserRepo.get_by_id(db, request.user_id)
        name = asked.username if asked is not None else "the person asked"
        raise ResponderError(403, f"Only {name} can answer this request.")
    if request.status != "pending":
        raise ResponderError(409, f"This request was already {request.status}.")
    if now >= _aware(request.expires_at):
        await _expire(db, org_id, request, incident, at=now)
        raise ResponderError(409, "This request expired.")
    if incident.status in ("resolved", "merged"):
        raise ResponderError(409, f"The incident is {incident.status}.")
    if accept and not await can_respond(db, org_id, user):
        raise ResponderError(422, f"{user.username} can't respond to incidents.")

    status = "accepted" if accept else "declined"
    if not await IncidentResponderRequestRepo.finish(
        db, org_id, request.id, status=status, at=now
    ):
        raise ResponderError(409, "This request was already answered.")
    if accept:
        current = await IncidentResponderRepo.list_for_incident(db, org_id, incident.id)
        if user.id not in {row.user_id for row in current}:
            await IncidentResponderRepo.add(
                db,
                org_id,
                incident_id=incident.id,
                user_id=user.id,
                added_by=request.requested_by,
            )
    await record_lifecycle_comment(
        db,
        org_id,
        incident_id=incident.id,
        body=(
            "Accepted the request and joined the responders."
            if accept
            else "Declined the request to join the responders."
        ),
        author_user_id=user.id,
    )
    if request.requested_by is not None:
        await emit_notification(
            db,
            org_id,
            request.requested_by,
            event_type="incident.responder_request_answered",
            category=CATEGORY_INCIDENT,
            title=(
                f"{user.username} joined as a responder: {incident.title}"
                if accept
                else f"{user.username} declined to join: {incident.title}"
            ),
            link=f"/dashboard/incidents/detail?id={incident.id}",
            incident_id=incident.id,
        )
    await db.refresh(request)
    return request


async def _expire(
    db: AsyncSession,
    org_id: uuid.UUID,
    request: IncidentResponderRequest,
    incident: Incident | None,
    *,
    at: datetime,
) -> bool:
    if not await IncidentResponderRequestRepo.finish(
        db, org_id, request.id, status="expired", at=at
    ):
        return False
    asked = await UserRepo.get_by_id(db, request.user_id)
    name = asked.username if asked is not None else "The person asked"
    title = incident.title if incident is not None else "the incident"
    await record_lifecycle_comment(
        db,
        org_id,
        incident_id=request.incident_id,
        body=f"The request for {name} to join the responders expired after 30 minutes.",
    )
    if request.requested_by is not None:
        await emit_notification(
            db,
            org_id,
            request.requested_by,
            event_type="incident.responder_request_answered",
            category=CATEGORY_INCIDENT,
            title=f"{name} didn't answer within 30 minutes: {title}",
            link=f"/dashboard/incidents/detail?id={request.incident_id}",
            incident_id=request.incident_id,
        )
    return True


async def expire_due_requests(db: AsyncSession, *, at: datetime) -> int:
    """Expire pending requests past their 30 minutes (scheduler tick)."""

    expired = 0
    for request in await IncidentResponderRequestRepo.list_due(db, at=at):
        incident = await IncidentRepo.get_by_id(db, request.org_id, request.incident_id)
        if await _expire(db, request.org_id, request, incident, at=at):
            expired += 1
    return expired
