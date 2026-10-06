"""Reassign an incident to another team.

An incident's team is its service's team until someone reassigns it. Then
``incidents.team_id`` names the team handling it, and that team's Escalation
Chain pages. The service never changes, so the incident still shows which
system alerted.
"""

from __future__ import annotations

import dataclasses
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.auth.roles import request_role
from backend.db.models import EscalationChain, Incident, IncidentPage, Team, User
from backend.db.repos import (
    EscalationChainRepo,
    IncidentAssignmentRepo,
    IncidentChainStateRepo,
    IncidentRepo,
    ServiceEscalationChainRepo,
    ServiceRepo,
    TeamRepo,
)
from backend.notifications import CATEGORY_INCIDENT, emit_to_users
from backend.paging.dispatch import ChannelFactory
from backend.services.incident_timeline import record_lifecycle_comment

PAGING_MODES = ("page", "escalate_immediate")
NOTE_MAX_LENGTH = 500


async def incident_team_id(
    db: AsyncSession, org_id: uuid.UUID, incident: Incident
) -> uuid.UUID | None:
    """The team handling ``incident``: a reassignment, else its service's team."""

    if incident.team_id is not None:
        return incident.team_id
    if incident.service_id is None:
        return None
    service = await ServiceRepo.get_by_id(db, org_id, incident.service_id)
    return None if service is None else service.team_id


async def on_incident_team(
    db: AsyncSession, org_id: uuid.UUID, incident: Incident, user_id: uuid.UUID
) -> bool | None:
    """Whether ``user_id`` is on the incident's team; None when it has no team."""

    team_id = await incident_team_id(db, org_id, incident)
    if team_id is None:
        return None
    return await TeamRepo.is_member(db, org_id, team_id, user_id)


async def paged_in_current_run(
    db: AsyncSession, org_id: uuid.UUID, incident: Incident, user_id: uuid.UUID
) -> bool:
    """Whether the incident's Escalation Chain paged ``user_id`` in its
    current run (a ``recorded`` page for the chain and round now running)."""

    state = await IncidentChainStateRepo.get_for_incident(db, org_id, incident.id)
    if state is None:
        return False
    page = (
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
    ).scalar_one_or_none()
    return page is not None


async def can_take(
    db: AsyncSession, org_id: uuid.UUID, incident: Incident, user: User
) -> bool:
    """Whether ``user`` may take or acknowledge ``incident`` for themselves.

    Admins always. Operators on the team handling it (any operator when it
    has no team), and anyone its Escalation Chain paged in the current run.
    """

    role = request_role(user)
    if role == "admin":
        return True
    if role != "operator":
        return False
    member = await on_incident_team(db, org_id, incident, user.id)
    if member is None or member:
        return True
    return await paged_in_current_run(db, org_id, incident, user.id)


TAKE_FORBIDDEN = (
    "Only an admin, a member of this incident's team or someone its "
    "Escalation Chain paged can take or acknowledge it."
)


async def can_reassign(
    db: AsyncSession, org_id: uuid.UUID, incident: Incident, user: User
) -> bool:
    """Admins, and operators on the incident's team (any operator when it has none)."""

    role = request_role(user)
    if role == "admin":
        return True
    if role != "operator":
        return False
    member = await on_incident_team(db, org_id, incident, user.id)
    return member is None or member


async def select_chain_for_team(
    db: AsyncSession,
    org_id: uuid.UUID,
    *,
    team_id: uuid.UUID,
    priority: str | None,
) -> EscalationChain | None:
    """The Escalation Chain that answers for ``team_id``.

    First a chain linked to one of the team's services for this priority,
    then one linked with no priority filter, then the team's oldest active
    chain.
    """

    chains = {
        chain.id: chain
        for chain in await EscalationChainRepo.list_all(db, org_id, team_id=team_id)
        if chain.is_active
    }
    if not chains:
        return None
    wanted = (priority or "").upper()
    matching: list[EscalationChain] = []
    defaults: list[EscalationChain] = []
    for service in await ServiceRepo.list_all(db, org_id, team_id=team_id):
        for link in await ServiceEscalationChainRepo.list_for_service(
            db, org_id, service.id
        ):
            chain = chains.get(link.chain_id)
            if chain is None:
                continue
            applies = link.applies_when if isinstance(link.applies_when, dict) else {}
            priorities = {str(p).upper() for p in applies.get("priorities") or []}
            if not priorities:
                defaults.append(chain)
            elif wanted in priorities:
                matching.append(chain)
    if matching:
        return matching[0]
    if defaults:
        return defaults[0]
    return min(chains.values(), key=lambda chain: chain.created_at)


@dataclasses.dataclass
class ReassignOption:
    team: Team
    chain: EscalationChain | None
    note: str | None


def pages_on_reassign(incident: Incident) -> bool:
    """P0/P1 incidents page the receiving team; P2/P3 only notify (D-3)."""

    return incident.response_mode in PAGING_MODES


async def reassign_options(
    db: AsyncSession, org_id: uuid.UUID, incident: Incident
) -> list[ReassignOption]:
    """Every other team, with the chain that would page or why none would."""

    current = await incident_team_id(db, org_id, incident)
    pages = pages_on_reassign(incident)
    options: list[ReassignOption] = []
    for team in await TeamRepo.list_all(db, org_id):
        if team.id == current:
            continue
        chain = None
        note = None
        if not pages:
            note = (
                f"{incident.priority or 'This'} incidents notify instead of paging. "
                "The team's members get an Inbox notice."
            )
        else:
            chain = await select_chain_for_team(
                db, org_id, team_id=team.id, priority=incident.priority
            )
            if chain is None:
                note = "This team has no active Escalation Chain, so nobody would be paged."
        options.append(ReassignOption(team=team, chain=chain, note=note))
    return options


@dataclasses.dataclass
class ReassignResult:
    team: Team
    chain: EscalationChain | None


async def reassign_to_team(
    db: AsyncSession,
    org_id: uuid.UUID,
    *,
    incident: Incident,
    team: Team,
    actor: User,
    note: str | None = None,
    channel_factory: ChannelFactory | None = None,
) -> ReassignResult:
    """Hand ``incident`` to ``team``: release the owner and page that team.

    The caller checks permission and that the incident is open. Raises
    ``ValueError`` when ``team`` already handles the incident.
    """

    from backend.paging import escalation
    from backend.paging import notification_escalation

    # Same lock order as every ownership change: chain state, then incident.
    state = await IncidentChainStateRepo.get_for_incident(
        db, org_id, incident.id, for_update=True
    )
    incident = await IncidentRepo.get_by_id(db, org_id, incident.id, for_update=True)
    if incident is None:
        raise ValueError("Incident not found.")
    if incident.status in ("resolved", "merged"):
        raise ValueError(f"The incident is {incident.status}.")
    if not await can_reassign(db, org_id, incident, actor):
        raise PermissionError("The actor is no longer on this incident's team.")
    previous_team_id = await incident_team_id(db, org_id, incident)
    if previous_team_id == team.id:
        raise ValueError(f"{team.name} already handles this incident.")
    previous_team = (
        await TeamRepo.get_by_id(db, org_id, previous_team_id)
        if previous_team_id is not None
        else None
    )
    service = (
        await ServiceRepo.get_by_id(db, org_id, incident.service_id)
        if incident.service_id is not None
        else None
    )
    # Back to the service's own team means no reassignment is left to record.
    incident.team_id = (
        None if service is not None and service.team_id == team.id else team.id
    )

    owner = await IncidentAssignmentRepo.get_active(db, org_id, incident.id)
    previous_owner_id = owner.assigned_to if owner is not None else None
    await IncidentAssignmentRepo.release(db, org_id, incident.id)
    if state is not None:
        state.pending_takeover_user_id = None
        state.pending_takeover_expires_at = None
    # The previous team stops being chased once the incident moves.
    await notification_escalation.stop_escalation(
        db, org_id, incident_id=incident.id, status="cancelled"
    )

    pages = pages_on_reassign(incident)
    chain = (
        await select_chain_for_team(
            db, org_id, team_id=team.id, priority=incident.priority
        )
        if pages
        else None
    )
    if not pages:
        outcome = (
            f"{incident.priority or 'This'} incidents notify instead of paging, "
            f"so {team.name}'s members got an Inbox notice."
        )
    elif chain is not None:
        outcome = f"Paging {chain.name} from the first level."
    else:
        outcome = f"{team.name} has no active Escalation Chain, so nobody was paged."
    # The timeline says why before the new team's first page appears.
    source = previous_team.name if previous_team is not None else "no team"
    await record_lifecycle_comment(
        db,
        org_id,
        incident_id=incident.id,
        body=(
            f"Reassigned from {source} to {team.name}. {outcome}"
            + (f" Note: {note}" if note else "")
        ),
        author_user_id=actor.id,
    )

    if chain is not None:
        await escalation.restart_chain_for_handoff(
            db,
            org_id,
            incident_id=incident.id,
            chain_id=chain.id,
            mode=incident.response_mode or "page",
            channel_factory=channel_factory,
        )
    elif pages:
        await escalation.cancel_chain(db, org_id, incident_id=incident.id)
    else:
        members = await TeamRepo.list_members(db, org_id, team.id)
        await emit_to_users(
            db,
            org_id,
            [m.user_id for m in members if m.user_id != actor.id],
            event_type="incident.reassigned",
            category=CATEGORY_INCIDENT,
            title=f"Reassigned to {team.name}: {incident.title}",
            body=(
                f"{actor.username} reassigned it to your team."
                + (f" Note: {note}" if note else "")
            ),
            link=f"/dashboard/incidents/detail?id={incident.id}",
            incident_id=incident.id,
        )

    if previous_owner_id is not None and previous_owner_id != actor.id:
        await emit_to_users(
            db,
            org_id,
            [previous_owner_id],
            event_type="incident.reassigned",
            category=CATEGORY_INCIDENT,
            title=f"Reassigned to {team.name}: {incident.title}",
            body=(
                f"{actor.username} reassigned it, so you no longer own it."
                + (f" Note: {note}" if note else "")
            ),
            link=f"/dashboard/incidents/detail?id={incident.id}",
            incident_id=incident.id,
        )
    await db.flush()
    return ReassignResult(team=team, chain=chain)
