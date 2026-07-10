"""Paging endpoints (Sprint 33).

Combines the team / service / roster / priority-rule / on-call surface in
a single router because they are all admin-config CRUD with identical
auth and pagination patterns. Incident-level paging actions live in
``backend/api/routes/incidents.py`` (Take Over / Release / panel) to keep
incident operations together.
"""

from __future__ import annotations

import uuid
import secrets
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.auth import (
    get_current_org,
    get_current_user,
    reject_api_tokens,
    require_role,
)
from backend.api.deps import get_db
from backend.api.schemas import (
    ChainWhereUsedItem,
    ChainWhereUsedResponse,
    OnCallRangeItem,
    OnCallRangeResponse,
    OnCallResolveResponse,
    EscalationCalendarDay,
    EscalationCalendarLevel,
    EscalationCalendarResponse,
    EscalationChainCreate,
    EscalationChainListResponse,
    EscalationChainResponse,
    EscalationChainUpdate,
    EscalationStepCreate,
    EscalationStepListResponse,
    EscalationStepReorderRequest,
    EscalationStepResponse,
    EscalationStepUpdate,
    TeamCalendarChain,
    TeamCalendarMaintenance,
    TeamOnCallCalendarDay,
    TeamOnCallCalendarResponse,
    UserNotificationPrefResponse,
    UserNotificationPrefUpdate,
    PriorityRuleCreate,
    PriorityRuleListResponse,
    PriorityRuleResponse,
    PriorityRuleUpdate,
    RosterCreate,
    RosterListResponse,
    RosterMemberAdd,
    RosterMemberListResponse,
    RosterMemberResponse,
    RosterOverrideCreate,
    RosterOverrideListResponse,
    RosterOverrideResponse,
    RosterReorderRequest,
    RosterResponse,
    RosterUpdate,
    ServiceCreate,
    ServiceEscalationChainCreate,
    ServiceEscalationChainListResponse,
    ServiceEscalationChainResponse,
    ServiceListResponse,
    ServiceResponse,
    ServiceUpdate,
    TeamCreate,
    TeamListResponse,
    TeamMemberAdd,
    TeamMemberListResponse,
    TeamMemberResponse,
    TeamResponse,
    TeamUpdate,
)
from backend.db.models import User
from backend.db.repos import (
    EscalationChainRepo,
    EscalationStepRepo,
    IngestTokenRepo,
    IntegrationConnectorRepo,
    MCPServerRepo,
    MaintenanceWindowRepo,
    ModelConfigRepo,
    PriorityRuleRepo,
    RosterOverrideRepo,
    RosterRepo,
    ServiceEscalationChainRepo,
    ServiceRepo,
    TeamRepo,
    UserNotificationPrefRepo,
    UserRepo,
)
from backend.ingest.service import hash_token
from backend.paging.on_call import (
    OnCallContext,
    OnCallMember,
    OnCallOverride,
    on_call_at,
)


router = APIRouter(tags=["paging"])


def _new_service_intake_token() -> str:
    return f"svc_{secrets.token_urlsafe(32)}"


async def _validate_mcp_servers(
    db: AsyncSession,
    org_id: uuid.UUID,
    ids: list[uuid.UUID],
) -> list[str]:
    seen: set[uuid.UUID] = set()
    ordered: list[str] = []
    for server_id in ids:
        if server_id in seen:
            continue
        seen.add(server_id)
        server = await MCPServerRepo.get_by_id(db, org_id, server_id)
        if server is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="MCP server not found",
            )
        ordered.append(str(server_id))
    return ordered


async def _validate_allowed_integrations(
    db: AsyncSession,
    org_id: uuid.UUID,
    ids: list[uuid.UUID],
) -> list[str]:
    """Validate a service's strict integration allowlist.

    Every id must be an integration connector in this org. Order is preserved
    and duplicates are dropped. An empty list is valid (it means the service
    may use no integrations)."""

    seen: set[uuid.UUID] = set()
    ordered: list[str] = []
    for connector_id in ids:
        if connector_id in seen:
            continue
        seen.add(connector_id)
        connector = await IntegrationConnectorRepo.get_by_id(db, org_id, connector_id)
        if connector is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Allowed integration not found",
            )
        ordered.append(str(connector_id))
    return ordered


async def _validate_service_models(
    db: AsyncSession,
    org_id: uuid.UUID,
    ids: list[uuid.UUID],
    *,
    existing_ids: set[uuid.UUID] | None = None,
) -> list[str]:
    if len(ids) > 3:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="A service can have at most 3 models",
        )
    if len(set(ids)) != len(ids):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Models cannot contain duplicates",
        )
    ordered: list[str] = []
    for config_id in ids:
        model = await ModelConfigRepo.get_by_id(db, org_id, config_id)
        if model is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Model not found",
            )
        if not model.is_active and config_id not in (existing_ids or set()):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Model must be enabled",
            )
        ordered.append(str(config_id))
    return ordered


# ---------------------------------------------------------------------------
# Teams
# ---------------------------------------------------------------------------


@router.get(
    "/teams",
    response_model=TeamListResponse,
    summary="List teams",
)
async def list_teams(
    db: AsyncSession = Depends(get_db),
    org_id: uuid.UUID = Depends(get_current_org),
    user: User = Depends(get_current_user),
):
    items = await TeamRepo.list_all(db, org_id)
    return TeamListResponse(
        items=[TeamResponse.model_validate(t) for t in items],
        total=len(items),
    )


@router.post(
    "/teams",
    response_model=TeamResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a team",
)
async def create_team(
    body: TeamCreate,
    db: AsyncSession = Depends(get_db),
    org_id: uuid.UUID = Depends(get_current_org),
    user: User = Depends(require_role("admin")),
):
    try:
        team = await TeamRepo.create(
            db,
            org_id,
            name=body.name,
            slug=body.slug,
            description=body.description,
            created_by=user.id,
        )
        await db.commit()
        await db.refresh(team)
        return TeamResponse.model_validate(team)
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Team slug already exists",
        ) from exc


@router.put(
    "/teams/{team_id}",
    response_model=TeamResponse,
    summary="Update a team",
)
async def update_team(
    team_id: uuid.UUID,
    body: TeamUpdate,
    db: AsyncSession = Depends(get_db),
    org_id: uuid.UUID = Depends(get_current_org),
    user: User = Depends(require_role("admin")),
):
    description_provided = "description" in body.model_fields_set
    updated = await TeamRepo.update(
        db,
        org_id,
        team_id,
        name=body.name,
        description=body.description,
        description_provided=description_provided,
    )
    if updated is None:
        raise HTTPException(status_code=404, detail="Team not found")
    await db.commit()
    return TeamResponse.model_validate(updated)


@router.delete(
    "/teams/{team_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a team",
)
async def delete_team(
    team_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    org_id: uuid.UUID = Depends(get_current_org),
    user: User = Depends(require_role("admin")),
):
    deleted = await TeamRepo.delete(db, org_id, team_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Team not found")
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get(
    "/teams/{team_id}/members",
    response_model=TeamMemberListResponse,
    summary="List team members",
)
async def list_team_members(
    team_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    org_id: uuid.UUID = Depends(get_current_org),
    user: User = Depends(get_current_user),
):
    if await TeamRepo.get_by_id(db, org_id, team_id) is None:
        raise HTTPException(status_code=404, detail="Team not found")
    members = await TeamRepo.list_members(db, org_id, team_id)
    return TeamMemberListResponse(
        items=[TeamMemberResponse.model_validate(m) for m in members],
        total=len(members),
    )


@router.post(
    "/teams/{team_id}/members",
    response_model=TeamMemberResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Add a member to a team",
)
async def add_team_member(
    team_id: uuid.UUID,
    body: TeamMemberAdd,
    db: AsyncSession = Depends(get_db),
    org_id: uuid.UUID = Depends(get_current_org),
    user: User = Depends(require_role("admin")),
):
    if await TeamRepo.get_by_id(db, org_id, team_id) is None:
        raise HTTPException(status_code=404, detail="Team not found")
    try:
        member = await TeamRepo.add_member(
            db, org_id, team_id, user_id=body.user_id, role=body.role
        )
        await db.commit()
        await db.refresh(member)
        return TeamMemberResponse.model_validate(member)
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="User already a member",
        ) from exc


@router.delete(
    "/teams/{team_id}/members/{user_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Remove a team member",
)
async def remove_team_member(
    team_id: uuid.UUID,
    user_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    org_id: uuid.UUID = Depends(get_current_org),
    user: User = Depends(require_role("admin")),
):
    removed = await TeamRepo.remove_member(db, org_id, team_id, user_id)
    if not removed:
        raise HTTPException(status_code=404, detail="Member not found")
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ---------------------------------------------------------------------------
# Services
# ---------------------------------------------------------------------------


@router.get(
    "/services",
    response_model=ServiceListResponse,
    summary="List services",
)
async def list_services(
    db: AsyncSession = Depends(get_db),
    org_id: uuid.UUID = Depends(get_current_org),
    user: User = Depends(get_current_user),
    team_id: uuid.UUID | None = Query(default=None),
):
    items = await ServiceRepo.list_all(db, org_id, team_id=team_id)
    changed = False
    for svc in items:
        if not svc.intake_token:
            intake_token = _new_service_intake_token()
            await ServiceRepo.update(
                db,
                org_id,
                svc.id,
                intake_token=intake_token,
            )
            await IngestTokenRepo.create(
                db,
                org_id,
                name=f"service-intake:{svc.id}:{secrets.token_hex(4)}",
                provider="auto",
                token_hash=hash_token(intake_token),
                service_id=svc.id,
            )
            changed = True
    if changed:
        await db.commit()
        items = await ServiceRepo.list_all(db, org_id, team_id=team_id)
    return ServiceListResponse(
        items=[ServiceResponse.model_validate(s) for s in items],
        total=len(items),
    )


@router.post(
    "/services",
    response_model=ServiceResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a service",
)
async def create_service(
    body: ServiceCreate,
    db: AsyncSession = Depends(get_db),
    org_id: uuid.UUID = Depends(get_current_org),
    user: User = Depends(require_role("admin")),
):
    if await TeamRepo.get_by_id(db, org_id, body.team_id) is None:
        raise HTTPException(status_code=400, detail="Owning team not found")
    mcp_server_ids = await _validate_mcp_servers(db, org_id, body.mcp_server_ids)
    model_config_ids = await _validate_service_models(db, org_id, body.model_config_ids)
    allowed_integration_connector_ids = await _validate_allowed_integrations(
        db, org_id, body.allowed_integration_connector_ids
    )
    intake_token = _new_service_intake_token()
    try:
        svc = await ServiceRepo.create(
            db,
            org_id,
            team_id=body.team_id,
            name=body.name,
            slug=body.slug,
            description=body.description,
            priority=body.priority,
            alert_grouping=body.alert_grouping,
            intake_token=intake_token,
            mcp_server_ids=mcp_server_ids,
            model_config_ids=model_config_ids,
            allowed_integration_connector_ids=allowed_integration_connector_ids,
            integration_action_overrides=body.integration_action_overrides,
            ai_default_tier=body.ai_default_tier,
            external_refs=body.external_refs,
            is_active=body.is_active,
        )
        await IngestTokenRepo.create(
            db,
            org_id,
            name=f"service:{svc.id}",
            provider="auto",
            token_hash=hash_token(intake_token),
            service_id=svc.id,
        )
        await db.commit()
        await db.refresh(svc)
        return ServiceResponse.model_validate(svc)
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Service slug already exists",
        ) from exc


@router.put(
    "/services/{service_id}",
    response_model=ServiceResponse,
    summary="Update a service",
)
async def update_service(
    service_id: uuid.UUID,
    body: ServiceUpdate,
    db: AsyncSession = Depends(get_db),
    org_id: uuid.UUID = Depends(get_current_org),
    user: User = Depends(require_role("admin")),
):
    if (
        body.team_id is not None
        and await TeamRepo.get_by_id(db, org_id, body.team_id) is None
    ):
        raise HTTPException(status_code=400, detail="Owning team not found")
    mcp_server_ids = None
    if body.mcp_server_ids is not None:
        mcp_server_ids = await _validate_mcp_servers(db, org_id, body.mcp_server_ids)
    model_config_ids = None
    if body.model_config_ids is not None:
        current_service = await ServiceRepo.get_by_id(db, org_id, service_id)
        if current_service is None:
            raise HTTPException(status_code=404, detail="Service not found")
        existing_model_ids: set[uuid.UUID] = set()
        for raw_id in current_service.model_config_ids or []:
            try:
                existing_model_ids.add(uuid.UUID(str(raw_id)))
            except (TypeError, ValueError):
                continue
        model_config_ids = await _validate_service_models(
            db,
            org_id,
            body.model_config_ids,
            existing_ids=existing_model_ids,
        )
    allowed_integration_connector_ids = None
    if body.allowed_integration_connector_ids is not None:
        allowed_integration_connector_ids = await _validate_allowed_integrations(
            db, org_id, body.allowed_integration_connector_ids
        )
    updated = await ServiceRepo.update(
        db,
        org_id,
        service_id,
        team_id=body.team_id,
        name=body.name,
        description=body.description,
        description_provided="description" in body.model_fields_set,
        priority=body.priority,
        alert_grouping=body.alert_grouping,
        mcp_server_ids=mcp_server_ids,
        mcp_server_ids_provided=("mcp_server_ids" in body.model_fields_set),
        model_config_ids=model_config_ids,
        model_config_ids_provided=("model_config_ids" in body.model_fields_set),
        allowed_integration_connector_ids=allowed_integration_connector_ids,
        allowed_integration_connector_ids_provided=(
            "allowed_integration_connector_ids" in body.model_fields_set
        ),
        integration_action_overrides=body.integration_action_overrides,
        integration_action_overrides_provided=(
            "integration_action_overrides" in body.model_fields_set
        ),
        ai_default_tier=body.ai_default_tier,
        ai_default_tier_provided="ai_default_tier" in body.model_fields_set,
        external_refs=body.external_refs,
        external_refs_provided="external_refs" in body.model_fields_set,
        is_active=body.is_active,
    )
    if updated is None:
        raise HTTPException(status_code=404, detail="Service not found")
    await db.commit()
    return ServiceResponse.model_validate(updated)


@router.delete(
    "/services/{service_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a service",
)
async def delete_service(
    service_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    org_id: uuid.UUID = Depends(get_current_org),
    user: User = Depends(require_role("admin")),
):
    deleted = await ServiceRepo.delete(db, org_id, service_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Service not found")
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ---------------------------------------------------------------------------
# Rosters
# ---------------------------------------------------------------------------

# Roles that may be placed on an on-call rotation. Viewers are read-only and
# cannot operate, so they are never eligible.
_ROSTER_ELIGIBLE_ROLES = {"admin", "operator"}


async def _validate_roster_eligible_user(
    db: AsyncSession,
    org_id: uuid.UUID,
    *,
    team_id: uuid.UUID,
    user_id: uuid.UUID,
    subject: str = "Roster members",
) -> None:
    """Enforce that ``user_id`` may be used on a roster owned by ``team_id``.

    Shared source of truth for both rotation members and coverage overrides:
    the user must exist, be active, not be deleted, hold an Admin/Operator
    role, and belong to the roster's owning team. Raises a clear 400 on the
    first failed check. This is the authoritative server-side guard — direct
    API calls cannot create invalid rosters even if the frontend filter is
    bypassed. ``subject`` tailors the team-membership message (e.g. "Roster
    override users").
    """
    target_user = await UserRepo.get_by_id(db, user_id)
    if (
        target_user is None
        or target_user.deleted_at is not None
        or target_user.primary_org_id != org_id
    ):
        raise HTTPException(status_code=400, detail="User not found")
    if not target_user.is_active:
        raise HTTPException(
            status_code=400,
            detail=f"User {target_user.username} is not active.",
        )
    if target_user.role not in _ROSTER_ELIGIBLE_ROLES:
        raise HTTPException(
            status_code=400,
            detail=(
                f"User {target_user.username} is a {target_user.role}; only "
                "Admin or Operator users can be added to an on-call roster."
            ),
        )
    if not await TeamRepo.is_member(db, org_id, team_id, user_id):
        team = await TeamRepo.get_by_id(db, org_id, team_id)
        team_name = team.name if team is not None else str(team_id)
        raise HTTPException(
            status_code=400,
            detail=(
                f"{subject} must belong to the selected team. "
                f"User {target_user.username} is not a member of team {team_name}."
            ),
        )


@router.get(
    "/rosters",
    response_model=RosterListResponse,
    summary="List rosters",
)
async def list_rosters(
    db: AsyncSession = Depends(get_db),
    org_id: uuid.UUID = Depends(get_current_org),
    user: User = Depends(get_current_user),
    team_id: uuid.UUID | None = Query(default=None),
):
    items = await RosterRepo.list_all(db, org_id, team_id=team_id)
    return RosterListResponse(
        items=[RosterResponse.model_validate(r) for r in items],
        total=len(items),
    )


@router.post(
    "/rosters",
    response_model=RosterResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a roster",
)
async def create_roster(
    body: RosterCreate,
    db: AsyncSession = Depends(get_db),
    org_id: uuid.UUID = Depends(get_current_org),
    user: User = Depends(require_role("admin")),
):
    if await TeamRepo.get_by_id(db, org_id, body.team_id) is None:
        raise HTTPException(status_code=400, detail="Owning team not found")
    roster = await RosterRepo.create(
        db,
        org_id,
        team_id=body.team_id,
        name=body.name,
        anchor_date=body.anchor_date,
        description=body.description,
        time_zone=body.time_zone,
        pattern=body.pattern,
        pattern_length=body.pattern_length,
        coverage_start_time=body.coverage_start_time,
        coverage_end_time=body.coverage_end_time,
        handoff_time=body.coverage_start_time,
        handoff_day=body.handoff_day,
        is_active=body.is_active,
    )
    await db.commit()
    await db.refresh(roster)
    return RosterResponse.model_validate(roster)


@router.put(
    "/rosters/{roster_id}",
    response_model=RosterResponse,
    summary="Update a roster",
)
async def update_roster(
    roster_id: uuid.UUID,
    body: RosterUpdate,
    db: AsyncSession = Depends(get_db),
    org_id: uuid.UUID = Depends(get_current_org),
    user: User = Depends(require_role("admin")),
):
    existing = await RosterRepo.get_by_id(db, org_id, roster_id)
    if existing is None:
        raise HTTPException(status_code=404, detail="Roster not found")
    fields = body.model_dump(exclude_unset=True)

    # Reparenting the roster to a different team must not strand its current
    # rotation members under a team they don't belong to. Reject the update
    # unless every existing member is also eligible for the new team.
    new_team_id = fields.get("team_id")
    if new_team_id is not None and new_team_id != existing.team_id:
        if await TeamRepo.get_by_id(db, org_id, new_team_id) is None:
            raise HTTPException(status_code=400, detail="Owning team not found")
        for member in await RosterRepo.list_members(db, org_id, roster_id):
            await _validate_roster_eligible_user(
                db, org_id, team_id=new_team_id, user_id=member.user_id
            )

    updated = await RosterRepo.update(db, org_id, roster_id, **fields)
    if updated is None:
        raise HTTPException(status_code=404, detail="Roster not found")
    await db.commit()
    return RosterResponse.model_validate(updated)


@router.delete(
    "/rosters/{roster_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a roster",
)
async def delete_roster(
    roster_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    org_id: uuid.UUID = Depends(get_current_org),
    user: User = Depends(require_role("admin")),
):
    deleted = await RosterRepo.delete(db, org_id, roster_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Roster not found")
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get(
    "/rosters/{roster_id}/members",
    response_model=RosterMemberListResponse,
    summary="List roster members",
)
async def list_roster_members(
    roster_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    org_id: uuid.UUID = Depends(get_current_org),
    user: User = Depends(get_current_user),
):
    if await RosterRepo.get_by_id(db, org_id, roster_id) is None:
        raise HTTPException(status_code=404, detail="Roster not found")
    members = await RosterRepo.list_members(db, org_id, roster_id)
    return RosterMemberListResponse(
        items=[RosterMemberResponse.model_validate(m) for m in members],
        total=len(members),
    )


@router.post(
    "/rosters/{roster_id}/members",
    response_model=RosterMemberResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Add a roster member",
)
async def add_roster_member(
    roster_id: uuid.UUID,
    body: RosterMemberAdd,
    db: AsyncSession = Depends(get_db),
    org_id: uuid.UUID = Depends(get_current_org),
    user: User = Depends(require_role("admin")),
):
    roster = await RosterRepo.get_by_id(db, org_id, roster_id)
    if roster is None:
        raise HTTPException(status_code=404, detail="Roster not found")
    await _validate_roster_eligible_user(
        db, org_id, team_id=roster.team_id, user_id=body.user_id
    )
    try:
        member = await RosterRepo.add_member(
            db,
            org_id,
            roster_id=roster_id,
            user_id=body.user_id,
            position_index=body.position_index,
        )
        await db.commit()
        await db.refresh(member)
        return RosterMemberResponse.model_validate(member)
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="User already a roster member or position taken",
        ) from exc


@router.delete(
    "/rosters/{roster_id}/members/{user_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Remove a roster member",
)
async def remove_roster_member(
    roster_id: uuid.UUID,
    user_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    org_id: uuid.UUID = Depends(get_current_org),
    user: User = Depends(require_role("admin")),
):
    removed = await RosterRepo.remove_member(db, org_id, roster_id, user_id)
    if not removed:
        raise HTTPException(status_code=404, detail="Member not found")
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/rosters/{roster_id}/members/reorder",
    response_model=RosterMemberListResponse,
    summary="Reorder roster members",
)
async def reorder_roster_members(
    roster_id: uuid.UUID,
    body: RosterReorderRequest,
    db: AsyncSession = Depends(get_db),
    org_id: uuid.UUID = Depends(get_current_org),
    user: User = Depends(require_role("admin")),
):
    if await RosterRepo.get_by_id(db, org_id, roster_id) is None:
        raise HTTPException(status_code=404, detail="Roster not found")
    await RosterRepo.reorder_members(
        db, org_id, roster_id, ordered_user_ids=body.ordered_user_ids
    )
    await db.commit()
    members = await RosterRepo.list_members(db, org_id, roster_id)
    return RosterMemberListResponse(
        items=[RosterMemberResponse.model_validate(m) for m in members],
        total=len(members),
    )


@router.get(
    "/rosters/{roster_id}/overrides",
    response_model=RosterOverrideListResponse,
    summary="List roster overrides",
)
async def list_roster_overrides(
    roster_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    org_id: uuid.UUID = Depends(get_current_org),
    user: User = Depends(get_current_user),
):
    if await RosterRepo.get_by_id(db, org_id, roster_id) is None:
        raise HTTPException(status_code=404, detail="Roster not found")
    items = await RosterOverrideRepo.list_for_roster(db, org_id, roster_id)
    return RosterOverrideListResponse(
        items=[RosterOverrideResponse.model_validate(o) for o in items],
        total=len(items),
    )


@router.post(
    "/rosters/{roster_id}/overrides",
    response_model=RosterOverrideResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a roster override",
)
async def create_roster_override(
    roster_id: uuid.UUID,
    body: RosterOverrideCreate,
    db: AsyncSession = Depends(get_db),
    org_id: uuid.UUID = Depends(get_current_org),
    user: User = Depends(require_role("admin", "operator")),
):
    roster = await RosterRepo.get_by_id(db, org_id, roster_id)
    if roster is None:
        raise HTTPException(status_code=404, detail="Roster not found")
    # A coverage override must satisfy the same eligibility rule as a rotation
    # member: active, non-deleted Admin/Operator who belongs to the roster's team.
    await _validate_roster_eligible_user(
        db,
        org_id,
        team_id=roster.team_id,
        user_id=body.covering_user_id,
        subject="Roster override users",
    )
    if body.ends_at <= body.starts_at:
        raise HTTPException(status_code=400, detail="ends_at must be > starts_at")
    ov = await RosterOverrideRepo.create(
        db,
        org_id,
        roster_id=roster_id,
        covering_user_id=body.covering_user_id,
        starts_at=body.starts_at,
        ends_at=body.ends_at,
        reason=body.reason,
        created_by=user.id,
    )
    await db.commit()
    await db.refresh(ov)
    return RosterOverrideResponse.model_validate(ov)


@router.delete(
    "/rosters/{roster_id}/overrides/{override_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a roster override",
)
async def delete_roster_override(
    roster_id: uuid.UUID,
    override_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    org_id: uuid.UUID = Depends(get_current_org),
    user: User = Depends(require_role("admin", "operator")),
):
    deleted = await RosterOverrideRepo.delete(db, org_id, override_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Override not found")
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get(
    "/rosters/{roster_id}/on-call",
    response_model=OnCallResolveResponse,
    summary="Resolve who is on call at a given time",
)
async def resolve_on_call(
    roster_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    org_id: uuid.UUID = Depends(get_current_org),
    user: User = Depends(get_current_user),
    at: datetime | None = Query(default=None),
):
    roster = await RosterRepo.get_by_id(db, org_id, roster_id)
    if roster is None:
        raise HTTPException(status_code=404, detail="Roster not found")
    if not roster.is_active:
        when = at or datetime.now()
        return OnCallResolveResponse(roster_id=roster_id, at=when, user_id=None)
    # On-call resolution excludes deactivated/soft-deleted users.
    members = await RosterRepo.list_members(db, org_id, roster_id, active_only=True)
    overrides = await RosterOverrideRepo.list_for_roster(db, org_id, roster_id)
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
        coverage_start_time=roster.coverage_start_time,
        coverage_end_time=roster.coverage_end_time,
        handoff_time=roster.handoff_time,
        anchor_date=roster.anchor_date,
    )
    when = at or datetime.now()
    user_id = on_call_at(ctx, when)
    return OnCallResolveResponse(roster_id=roster_id, at=when, user_id=user_id)


@router.get(
    "/rosters/{roster_id}/on-call/range",
    response_model=OnCallRangeResponse,
    summary="Resolve on-call assignments across a time range (calendar view)",
)
async def resolve_on_call_range(
    roster_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    org_id: uuid.UUID = Depends(get_current_org),
    user: User = Depends(get_current_user),
    from_at: datetime = Query(..., alias="from"),
    to_at: datetime = Query(..., alias="to"),
    step_hours: int = Query(default=24, ge=1, le=168),
):
    """Bulk on-call resolution. Powers the Rosters calendar view.

    Returns one item per ``step_hours``-aligned sample point between
    ``from`` and ``to``. Each item flags whether an active override is
    the source so the calendar UI can mark override days distinctly.

    Capped at 200 samples to keep the response bounded; an operator can
    always page the range if they need finer granularity.
    """
    if to_at <= from_at:
        raise HTTPException(status_code=400, detail="`to` must be > `from`")
    span = to_at - from_at
    total_steps = int(span.total_seconds() // (step_hours * 3600)) + 1
    if total_steps > 200:
        raise HTTPException(
            status_code=400,
            detail="Requested range produces too many samples; narrow it or increase step_hours",
        )

    def _aware(dt: datetime) -> datetime:
        # SQLite drops tzinfo on persisted aware datetimes. Normalize to
        # UTC-aware so comparisons against the query-param cursor (which is
        # always aware) succeed on both Postgres and SQLite.
        return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)

    roster = await RosterRepo.get_by_id(db, org_id, roster_id)
    if roster is None:
        raise HTTPException(status_code=404, detail="Roster not found")
    if not roster.is_active:
        return OnCallRangeResponse(
            roster_id=roster_id,
            from_at=from_at,
            to_at=to_at,
            step_hours=step_hours,
            items=[],
        )
    # On-call resolution excludes deactivated/soft-deleted users.
    members = await RosterRepo.list_members(db, org_id, roster_id, active_only=True)
    overrides = await RosterOverrideRepo.list_for_roster(db, org_id, roster_id)
    ctx = OnCallContext(
        members=[
            OnCallMember(user_id=m.user_id, position_index=m.position_index)
            for m in members
        ],
        overrides=[
            OnCallOverride(
                covering_user_id=o.covering_user_id,
                starts_at=_aware(o.starts_at),
                ends_at=_aware(o.ends_at),
            )
            for o in overrides
        ],
        time_zone=roster.time_zone,
        pattern=roster.pattern,
        pattern_length=roster.pattern_length,
        coverage_start_time=roster.coverage_start_time,
        coverage_end_time=roster.coverage_end_time,
        handoff_time=roster.handoff_time,
        anchor_date=roster.anchor_date,
    )

    items: list[OnCallRangeItem] = []
    cursor = (
        from_at if from_at.tzinfo is not None else from_at.replace(tzinfo=timezone.utc)
    )
    end_cursor = (
        to_at if to_at.tzinfo is not None else to_at.replace(tzinfo=timezone.utc)
    )
    step = timedelta(hours=step_hours)
    # For a daily (or coarser) calendar view, resolve each day at a time *inside*
    # the roster's coverage window rather than at the cursor's raw time-of-day.
    # Otherwise a calendar that starts at local midnight samples 00:00 every day,
    # which is outside a 09:00–17:00 window, so every cell resolves to nobody.
    roster_tz = ZoneInfo(roster.time_zone)
    align_to_coverage = step_hours % 24 == 0
    while cursor <= end_cursor:
        sample = (
            _calendar_sample_at(roster, cursor.astimezone(roster_tz).date())
            if align_to_coverage
            else cursor
        )
        user_id = on_call_at(ctx, sample)
        active = next(
            (o for o in overrides if _aware(o.starts_at) <= sample < _aware(o.ends_at)),
            None,
        )
        items.append(
            OnCallRangeItem(
                at=cursor,
                user_id=user_id,
                is_override=active is not None,
                override_id=active.id if active is not None else None,
            )
        )
        cursor = cursor + step

    return OnCallRangeResponse(
        roster_id=roster_id,
        from_at=from_at,
        to_at=to_at,
        step_hours=step_hours,
        items=items,
    )


# ---------------------------------------------------------------------------
# Priority rules
# ---------------------------------------------------------------------------


@router.get(
    "/priority-rules",
    response_model=PriorityRuleListResponse,
    summary="List priority rules",
)
async def list_priority_rules(
    db: AsyncSession = Depends(get_db),
    org_id: uuid.UUID = Depends(get_current_org),
    user: User = Depends(get_current_user),
):
    items = await PriorityRuleRepo.list_all(db, org_id)
    return PriorityRuleListResponse(
        items=[PriorityRuleResponse.model_validate(r) for r in items],
        total=len(items),
    )


@router.post(
    "/priority-rules",
    response_model=PriorityRuleResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a priority rule",
)
async def create_priority_rule(
    body: PriorityRuleCreate,
    db: AsyncSession = Depends(get_db),
    org_id: uuid.UUID = Depends(get_current_org),
    user: User = Depends(require_role("admin")),
):
    rule = await PriorityRuleRepo.create(
        db,
        org_id,
        name=body.name,
        condition=body.condition,
        priority=body.priority,
        rule_index=body.rule_index,
        response_mode=body.response_mode,
        is_active=body.is_active,
    )
    await db.commit()
    await db.refresh(rule)
    return PriorityRuleResponse.model_validate(rule)


@router.put(
    "/priority-rules/{rule_id}",
    response_model=PriorityRuleResponse,
    summary="Update a priority rule",
)
async def update_priority_rule(
    rule_id: uuid.UUID,
    body: PriorityRuleUpdate,
    db: AsyncSession = Depends(get_db),
    org_id: uuid.UUID = Depends(get_current_org),
    user: User = Depends(require_role("admin")),
):
    fields = body.model_dump(exclude_unset=True)
    updated = await PriorityRuleRepo.update(db, org_id, rule_id, **fields)
    if updated is None:
        raise HTTPException(status_code=404, detail="Priority rule not found")
    await db.commit()
    return PriorityRuleResponse.model_validate(updated)


@router.delete(
    "/priority-rules/{rule_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a priority rule",
)
async def delete_priority_rule(
    rule_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    org_id: uuid.UUID = Depends(get_current_org),
    user: User = Depends(require_role("admin")),
):
    deleted = await PriorityRuleRepo.delete(db, org_id, rule_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Priority rule not found")
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ---------------------------------------------------------------------------
# Escalation chains (Sprint 34)
# ---------------------------------------------------------------------------

_CALENDAR_RANGE_DAYS = {
    "today": 1,
    "7d": 7,
    "30d": 30,
    "90d": 90,
}


def _user_display_name(user: User | None, fallback_id: uuid.UUID | None = None) -> str:
    if user is None:
        if fallback_id is None:
            return "Deleted user"
        return f"Deleted user {str(fallback_id)[:8]}"
    full_name = " ".join(
        part for part in (user.first_name, user.last_name) if part
    ).strip()
    return full_name or user.username or user.email


def _parse_time(value: str) -> time:
    parts = value.split(":")
    if len(parts) < 2:
        raise ValueError(f"Invalid time value: {value!r}")
    return time(int(parts[0]), int(parts[1]))


def _calendar_sample_at(roster, day: date) -> datetime:
    """Return a timestamp inside the roster coverage window starting on *day*."""
    tz = ZoneInfo(roster.time_zone)
    start = _parse_time(roster.coverage_start_time)
    end = _parse_time(roster.coverage_end_time)
    start_at = datetime.combine(day, start, tzinfo=tz)
    if start == end:
        return start_at + timedelta(hours=12)
    end_day = day if start < end else day + timedelta(days=1)
    end_at = datetime.combine(end_day, end, tzinfo=tz)
    return start_at + ((end_at - start_at) / 2)


def _aware_utc(dt: datetime) -> datetime:
    # SQLite drops tzinfo from aware datetimes; normalize for comparisons.
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


async def _resolve_roster_calendar_level(
    *,
    db: AsyncSession,
    org_id: uuid.UUID,
    step,
    day: date,
    level: int,
) -> EscalationCalendarLevel:
    roster = await RosterRepo.get_by_id(db, org_id, step.target_id)
    if roster is None:
        return EscalationCalendarLevel(
            level=level,
            target_type="roster",
            target_id=step.target_id,
            target_name=f"Missing roster {str(step.target_id)[:8]}",
            status="unknown",
            warnings=["Roster target was not found."],
        )

    base = {
        "level": level,
        "target_type": "roster",
        "target_id": step.target_id,
        "target_name": roster.name,
        "coverage_start": roster.coverage_start_time,
        "coverage_end": roster.coverage_end_time,
        "coverage_time_zone": roster.time_zone,
    }
    if not roster.is_active:
        return EscalationCalendarLevel(
            **base,
            status="disabled_roster",
            warnings=["Roster is disabled."],
        )

    all_members = await RosterRepo.list_members(db, org_id, roster.id)
    if not all_members:
        return EscalationCalendarLevel(
            **base,
            status="empty_roster",
            warnings=["Roster has no members."],
        )

    active_members = await RosterRepo.list_members(
        db, org_id, roster.id, active_only=True
    )
    if not active_members:
        return EscalationCalendarLevel(
            **base,
            status="inactive_user",
            warnings=["Roster has no active members."],
        )

    overrides = await RosterOverrideRepo.list_for_roster(db, org_id, roster.id)
    ctx = OnCallContext(
        members=[
            OnCallMember(user_id=m.user_id, position_index=m.position_index)
            for m in active_members
        ],
        overrides=[
            OnCallOverride(
                covering_user_id=o.covering_user_id,
                starts_at=_aware_utc(o.starts_at),
                ends_at=_aware_utc(o.ends_at),
            )
            for o in overrides
        ],
        time_zone=roster.time_zone,
        pattern=roster.pattern,
        pattern_length=roster.pattern_length,
        coverage_start_time=roster.coverage_start_time,
        coverage_end_time=roster.coverage_end_time,
        handoff_time=roster.handoff_time,
        anchor_date=roster.anchor_date,
    )
    sample_at = _calendar_sample_at(roster, day)
    resolved_user_id = on_call_at(ctx, sample_at)
    if resolved_user_id is None:
        return EscalationCalendarLevel(
            **base,
            status="outside_coverage",
            warnings=["No active user resolves for this coverage window."],
        )

    resolved_user = await UserRepo.get_by_id(db, resolved_user_id)
    if resolved_user is None or resolved_user.deleted_at is not None:
        return EscalationCalendarLevel(
            **base,
            resolved_user_id=resolved_user_id,
            resolved_user_name=_user_display_name(resolved_user, resolved_user_id),
            resolved_user_email=resolved_user.email if resolved_user else None,
            status="deleted_user",
            warnings=["Resolved user no longer exists."],
        )
    if not resolved_user.is_active:
        return EscalationCalendarLevel(
            **base,
            resolved_user_id=resolved_user.id,
            resolved_user_name=_user_display_name(resolved_user),
            resolved_user_email=resolved_user.email,
            status="inactive_user",
            warnings=["Resolved user is inactive."],
        )

    warnings: list[str] = []
    if len(active_members) < len(all_members):
        warnings.append("Inactive roster members were skipped.")
    return EscalationCalendarLevel(
        **base,
        resolved_user_id=resolved_user.id,
        resolved_user_name=_user_display_name(resolved_user),
        resolved_user_email=resolved_user.email,
        status="covered",
        warnings=warnings,
    )


async def _resolve_user_calendar_level(
    *,
    db: AsyncSession,
    step,
    level: int,
) -> EscalationCalendarLevel:
    target_user = await UserRepo.get_by_id(db, step.target_id)
    base = {
        "level": level,
        "target_type": "user",
        "target_id": step.target_id,
        "target_name": _user_display_name(target_user, step.target_id),
    }
    if target_user is None or target_user.deleted_at is not None:
        return EscalationCalendarLevel(
            **base,
            resolved_user_id=step.target_id,
            resolved_user_name=_user_display_name(target_user, step.target_id),
            resolved_user_email=target_user.email if target_user else None,
            status="deleted_user",
            warnings=["User target was not found."],
        )
    if not target_user.is_active:
        return EscalationCalendarLevel(
            **base,
            resolved_user_id=target_user.id,
            resolved_user_name=_user_display_name(target_user),
            resolved_user_email=target_user.email,
            status="inactive_user",
            warnings=["User target is inactive."],
        )
    return EscalationCalendarLevel(
        **base,
        resolved_user_id=target_user.id,
        resolved_user_name=_user_display_name(target_user),
        resolved_user_email=target_user.email,
        status="covered",
        warnings=[],
    )


@router.get(
    "/escalation-chains",
    response_model=EscalationChainListResponse,
    summary="List escalation chains",
)
async def list_escalation_chains(
    db: AsyncSession = Depends(get_db),
    org_id: uuid.UUID = Depends(get_current_org),
    user: User = Depends(get_current_user),
    team_id: uuid.UUID | None = Query(default=None),
):
    items = await EscalationChainRepo.list_all(db, org_id, team_id=team_id)
    return EscalationChainListResponse(
        items=[EscalationChainResponse.model_validate(c) for c in items],
        total=len(items),
    )


@router.post(
    "/escalation-chains",
    response_model=EscalationChainResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create an escalation chain",
)
async def create_escalation_chain(
    body: EscalationChainCreate,
    db: AsyncSession = Depends(get_db),
    org_id: uuid.UUID = Depends(get_current_org),
    user: User = Depends(require_role("admin")),
):
    if await TeamRepo.get_by_id(db, org_id, body.team_id) is None:
        raise HTTPException(status_code=400, detail="Owning team not found")
    chain = await EscalationChainRepo.create(
        db,
        org_id,
        team_id=body.team_id,
        name=body.name,
        description=body.description,
        is_active=body.is_active,
    )
    await db.commit()
    await db.refresh(chain)
    return EscalationChainResponse.model_validate(chain)


@router.put(
    "/escalation-chains/{chain_id}",
    response_model=EscalationChainResponse,
    summary="Update an escalation chain",
)
async def update_escalation_chain(
    chain_id: uuid.UUID,
    body: EscalationChainUpdate,
    db: AsyncSession = Depends(get_db),
    org_id: uuid.UUID = Depends(get_current_org),
    user: User = Depends(require_role("admin")),
):
    fields = body.model_dump(exclude_unset=True)
    updated = await EscalationChainRepo.update(db, org_id, chain_id, **fields)
    if updated is None:
        raise HTTPException(status_code=404, detail="Chain not found")
    await db.commit()
    return EscalationChainResponse.model_validate(updated)


@router.delete(
    "/escalation-chains/{chain_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete an escalation chain",
)
async def delete_escalation_chain(
    chain_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    org_id: uuid.UUID = Depends(get_current_org),
    user: User = Depends(require_role("admin")),
):
    deleted = await EscalationChainRepo.delete(db, org_id, chain_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Chain not found")
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get(
    "/escalation-chains/{chain_id}/steps",
    response_model=EscalationStepListResponse,
    summary="List steps in an escalation chain",
)
async def list_escalation_steps(
    chain_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    org_id: uuid.UUID = Depends(get_current_org),
    user: User = Depends(get_current_user),
):
    if await EscalationChainRepo.get_by_id(db, org_id, chain_id) is None:
        raise HTTPException(status_code=404, detail="Chain not found")
    items = await EscalationStepRepo.list_for_chain(db, org_id, chain_id)
    return EscalationStepListResponse(
        items=[EscalationStepResponse.model_validate(s) for s in items],
        total=len(items),
    )


@router.post(
    "/escalation-chains/{chain_id}/steps",
    response_model=EscalationStepResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Add a step to an escalation chain",
)
async def add_escalation_step(
    chain_id: uuid.UUID,
    body: EscalationStepCreate,
    db: AsyncSession = Depends(get_db),
    org_id: uuid.UUID = Depends(get_current_org),
    user: User = Depends(require_role("admin")),
):
    if await EscalationChainRepo.get_by_id(db, org_id, chain_id) is None:
        raise HTTPException(status_code=404, detail="Chain not found")
    # Light target_id validation — Sprint 34 only checks the type vs. obvious
    # tables we have repos for.
    if body.target_type == "roster":
        if await RosterRepo.get_by_id(db, org_id, body.target_id) is None:
            raise HTTPException(status_code=400, detail="Target roster not found")
    elif body.target_type == "team":
        if await TeamRepo.get_by_id(db, org_id, body.target_id) is None:
            raise HTTPException(status_code=400, detail="Target team not found")
    elif body.target_type == "user":
        if await UserRepo.get_by_id(db, body.target_id) is None:
            raise HTTPException(status_code=400, detail="Target user not found")
    try:
        step = await EscalationStepRepo.create(
            db,
            org_id,
            chain_id=chain_id,
            step_index=body.step_index,
            target_type=body.target_type,
            target_id=body.target_id,
            timeout_seconds=body.timeout_seconds,
            notify_channels=body.notify_channels,
        )
        await db.commit()
        await db.refresh(step)
        return EscalationStepResponse.model_validate(step)
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Step index already used in this chain",
        ) from exc


@router.delete(
    "/escalation-chains/{chain_id}/steps/{step_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete an escalation step",
)
async def delete_escalation_step(
    chain_id: uuid.UUID,
    step_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    org_id: uuid.UUID = Depends(get_current_org),
    user: User = Depends(require_role("admin")),
):
    deleted = await EscalationStepRepo.delete(db, org_id, step_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Step not found")
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.patch(
    "/escalation-chains/{chain_id}/steps/{step_id}",
    response_model=EscalationStepResponse,
    summary="Update fields on an escalation step (inline timeout/channel edit)",
)
async def update_escalation_step(
    chain_id: uuid.UUID,
    step_id: uuid.UUID,
    body: EscalationStepUpdate,
    db: AsyncSession = Depends(get_db),
    org_id: uuid.UUID = Depends(get_current_org),
    user: User = Depends(require_role("admin")),
):
    if await EscalationChainRepo.get_by_id(db, org_id, chain_id) is None:
        raise HTTPException(status_code=404, detail="Chain not found")
    updated = await EscalationStepRepo.update_fields(
        db,
        org_id,
        step_id,
        timeout_seconds=body.timeout_seconds,
        notify_channels=body.notify_channels,
        notify_channels_set=body.notify_channels_set,
    )
    if updated is None or updated.chain_id != chain_id:
        raise HTTPException(status_code=404, detail="Step not found")
    await db.commit()
    await db.refresh(updated)
    return EscalationStepResponse.model_validate(updated)


@router.post(
    "/escalation-chains/{chain_id}/reorder-steps",
    response_model=EscalationStepListResponse,
    summary="Bulk reorder an escalation chain's steps",
)
async def reorder_escalation_steps(
    chain_id: uuid.UUID,
    body: EscalationStepReorderRequest,
    db: AsyncSession = Depends(get_db),
    org_id: uuid.UUID = Depends(get_current_org),
    user: User = Depends(require_role("admin")),
):
    if await EscalationChainRepo.get_by_id(db, org_id, chain_id) is None:
        raise HTTPException(status_code=404, detail="Chain not found")

    # Validate every id belongs to this chain and the set is exhaustive.
    existing = await EscalationStepRepo.list_for_chain(db, org_id, chain_id)
    existing_ids = {s.id for s in existing}
    requested = set(body.step_ids)
    if requested != existing_ids:
        raise HTTPException(
            status_code=400,
            detail="step_ids must be a permutation of the chain's existing steps",
        )

    items = await EscalationStepRepo.reorder(db, org_id, chain_id, body.step_ids)
    await db.commit()
    return EscalationStepListResponse(
        items=[EscalationStepResponse.model_validate(i) for i in items],
        total=len(items),
    )


@router.get(
    "/escalation-chains/{chain_id}/calendar",
    response_model=EscalationCalendarResponse,
    summary="Resolve escalation-chain on-call coverage over a calendar range",
)
async def escalation_chain_calendar(
    chain_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    org_id: uuid.UUID = Depends(get_current_org),
    user: User = Depends(require_role("admin", "operator")),
    range_: str = Query(default="7d", alias="range", pattern="^(today|7d|30d|90d)$"),
    start: date | None = Query(default=None),
):
    chain = await EscalationChainRepo.get_by_id(db, org_id, chain_id)
    if chain is None:
        raise HTTPException(status_code=404, detail="Chain not found")

    span_days = _CALENDAR_RANGE_DAYS[range_]
    start_day = start or datetime.now(timezone.utc).date()
    end_day = start_day + timedelta(days=span_days - 1)
    team = await TeamRepo.get_by_id(db, org_id, chain.team_id)
    steps = await EscalationStepRepo.list_for_chain(db, org_id, chain_id)

    days: list[EscalationCalendarDay] = []
    for offset in range(span_days):
        day = start_day + timedelta(days=offset)
        levels: list[EscalationCalendarLevel] = []
        for idx, step in enumerate(steps, start=1):
            if step.target_type == "roster":
                levels.append(
                    await _resolve_roster_calendar_level(
                        db=db,
                        org_id=org_id,
                        step=step,
                        day=day,
                        level=idx,
                    )
                )
            elif step.target_type == "user":
                levels.append(
                    await _resolve_user_calendar_level(
                        db=db,
                        step=step,
                        level=idx,
                    )
                )
            else:
                levels.append(
                    EscalationCalendarLevel(
                        level=idx,
                        target_type=step.target_type,
                        target_id=step.target_id,
                        target_name=f"Unsupported target {str(step.target_id)[:8]}",
                        status="unknown",
                        warnings=[
                            "Team targets are planned for a later release.",
                        ],
                    )
                )
        days.append(EscalationCalendarDay(date=day, levels=levels))

    return EscalationCalendarResponse(
        chain_id=chain.id,
        chain_name=chain.name,
        team_id=chain.team_id,
        team_name=team.name if team is not None else None,
        start=start_day,
        end=end_day,
        range=range_,
        days=days,
    )


@router.get(
    "/teams/{team_id}/on-call-calendar",
    response_model=TeamOnCallCalendarResponse,
    summary="Resolve a team's on-call coverage (all chains + levels) over a range",
)
async def team_on_call_calendar(
    team_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    org_id: uuid.UUID = Depends(get_current_org),
    user: User = Depends(require_role("admin", "operator", "viewer")),
    start: date | None = Query(default=None),
    days: int = Query(default=42, ge=1, le=366),
):
    """All-chains on-call coverage for one team across a day range — powers the
    team "On Call Schedule" month grid. Readable by any authenticated user;
    editing (overrides / maintenance windows) is gated on the mutating routes.
    Global + team-scoped maintenance windows blank that day's coverage.
    """

    team = await TeamRepo.get_by_id(db, org_id, team_id)
    if team is None:
        raise HTTPException(status_code=404, detail="Team not found")

    start_day = start or datetime.now(timezone.utc).date()
    end_day = start_day + timedelta(days=days - 1)

    chains = await EscalationChainRepo.list_all(db, org_id, team_id=team_id)
    steps_by_chain = {
        chain.id: await EscalationStepRepo.list_for_chain(db, org_id, chain.id)
        for chain in chains
    }

    out_days: list[TeamOnCallCalendarDay] = []
    for offset in range(days):
        day = start_day + timedelta(days=offset)
        # Suppression: check midday for global + team-scoped active windows.
        sample_dt = datetime.combine(day, time(12, 0), tzinfo=timezone.utc)
        windows = await MaintenanceWindowRepo.list_active_at(
            db, org_id, sample_dt, scope_type="team", scope_id=team_id
        )
        suppressed = len(windows) > 0
        maintenance = [
            TeamCalendarMaintenance(id=w.id, name=w.name, scope_type=w.scope_type)
            for w in windows
        ]

        chain_entries: list[TeamCalendarChain] = []
        for chain in chains:
            levels: list[EscalationCalendarLevel] = []
            for idx, step in enumerate(steps_by_chain[chain.id], start=1):
                if step.target_type == "roster":
                    level = await _resolve_roster_calendar_level(
                        db=db, org_id=org_id, step=step, day=day, level=idx
                    )
                elif step.target_type == "user":
                    level = await _resolve_user_calendar_level(
                        db=db, step=step, level=idx
                    )
                else:
                    level = EscalationCalendarLevel(
                        level=idx,
                        target_type=step.target_type,
                        target_id=step.target_id,
                        target_name=f"Unsupported target {str(step.target_id)[:8]}",
                        status="unknown",
                        warnings=["Team targets are planned for a later release."],
                    )
                if suppressed and level.status != "unknown":
                    level.resolved_user_id = None
                    level.resolved_user_name = None
                    level.resolved_user_email = None
                    level.status = "maintenance"
                    level.warnings = [
                        *level.warnings,
                        "Suppressed by maintenance window.",
                    ]
                levels.append(level)
            chain_entries.append(
                TeamCalendarChain(
                    chain_id=chain.id, chain_name=chain.name, levels=levels
                )
            )

        out_days.append(
            TeamOnCallCalendarDay(
                date=day,
                chains=chain_entries,
                maintenance=maintenance,
                suppressed=suppressed,
            )
        )

    return TeamOnCallCalendarResponse(
        team_id=team.id,
        team_name=team.name,
        start=start_day,
        end=end_day,
        days=out_days,
    )


@router.get(
    "/escalation-chains/{chain_id}/services",
    response_model=ChainWhereUsedResponse,
    summary="List services that use this escalation chain",
)
async def chain_where_used(
    chain_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    org_id: uuid.UUID = Depends(get_current_org),
    user: User = Depends(get_current_user),
):
    if await EscalationChainRepo.get_by_id(db, org_id, chain_id) is None:
        raise HTTPException(status_code=404, detail="Chain not found")
    links = await ServiceEscalationChainRepo.list_for_chain(db, org_id, chain_id)

    # Enrich each link with service + team names so the UI doesn't have to
    # round-trip per row.
    items: list[ChainWhereUsedItem] = []
    for link in links:
        svc = await ServiceRepo.get_by_id(db, org_id, link.service_id)
        if svc is None:
            # Service was deleted but the link wasn't — skip rather than leak.
            continue
        team = await TeamRepo.get_by_id(db, org_id, svc.team_id)
        items.append(
            ChainWhereUsedItem(
                service_id=svc.id,
                service_name=svc.name,
                team_id=team.id if team else None,
                team_name=team.name if team else None,
                applies_when=link.applies_when,
            )
        )
    return ChainWhereUsedResponse(chain_id=chain_id, items=items, total=len(items))


@router.get(
    "/services/{service_id}/escalation-chains",
    response_model=ServiceEscalationChainListResponse,
    summary="List escalation chains attached to a service",
)
async def list_service_escalation_chains(
    service_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    org_id: uuid.UUID = Depends(get_current_org),
    user: User = Depends(get_current_user),
):
    if await ServiceRepo.get_by_id(db, org_id, service_id) is None:
        raise HTTPException(status_code=404, detail="Service not found")
    links = await ServiceEscalationChainRepo.list_for_service(db, org_id, service_id)
    return ServiceEscalationChainListResponse(
        items=[ServiceEscalationChainResponse.model_validate(link) for link in links],
        total=len(links),
    )


@router.post(
    "/services/{service_id}/escalation-chains",
    response_model=ServiceEscalationChainResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Attach an escalation chain to a service",
)
async def link_service_escalation_chain(
    service_id: uuid.UUID,
    body: ServiceEscalationChainCreate,
    db: AsyncSession = Depends(get_db),
    org_id: uuid.UUID = Depends(get_current_org),
    user: User = Depends(require_role("admin")),
):
    if await ServiceRepo.get_by_id(db, org_id, service_id) is None:
        raise HTTPException(status_code=404, detail="Service not found")
    if await EscalationChainRepo.get_by_id(db, org_id, body.chain_id) is None:
        raise HTTPException(status_code=400, detail="Chain not found")
    try:
        row = await ServiceEscalationChainRepo.link(
            db,
            org_id,
            service_id=service_id,
            chain_id=body.chain_id,
            applies_when=body.applies_when,
        )
        await db.commit()
        await db.refresh(row)
        return ServiceEscalationChainResponse.model_validate(row)
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Chain already linked to this service",
        ) from exc


@router.delete(
    "/services/{service_id}/escalation-chains/{chain_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Detach an escalation chain from a service",
)
async def unlink_service_escalation_chain(
    service_id: uuid.UUID,
    chain_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    org_id: uuid.UUID = Depends(get_current_org),
    user: User = Depends(require_role("admin")),
):
    removed = await ServiceEscalationChainRepo.unlink(
        db, org_id, service_id=service_id, chain_id=chain_id
    )
    if not removed:
        raise HTTPException(status_code=404, detail="Link not found")
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ---------------------------------------------------------------------------
# User notification preferences (Sprint 35)
# ---------------------------------------------------------------------------


@router.get(
    "/users/me/notification-preferences",
    response_model=UserNotificationPrefResponse,
    summary="Get the current user's notification preferences for the active org",
)
async def get_my_notification_preferences(
    db: AsyncSession = Depends(get_db),
    org_id: uuid.UUID = Depends(get_current_org),
    user: User = Depends(reject_api_tokens),
):
    pref = await UserNotificationPrefRepo.get_for_user(db, org_id, user.id)
    if pref is None:
        pref = await UserNotificationPrefRepo.upsert(
            db,
            org_id,
            user.id,
            channels={},
            routing={},
            quiet_hours=None,
            quiet_hours_provided=True,
        )
        await db.commit()
        await db.refresh(pref)
    return UserNotificationPrefResponse.model_validate(pref)


@router.put(
    "/users/me/notification-preferences",
    response_model=UserNotificationPrefResponse,
    summary="Update the current user's notification preferences for the active org",
)
async def update_my_notification_preferences(
    body: UserNotificationPrefUpdate,
    db: AsyncSession = Depends(get_db),
    org_id: uuid.UUID = Depends(get_current_org),
    user: User = Depends(reject_api_tokens),
):
    pref = await UserNotificationPrefRepo.upsert(
        db,
        org_id,
        user.id,
        channels=body.channels,
        routing=body.routing,
        quiet_hours=body.quiet_hours,
        quiet_hours_provided="quiet_hours" in body.model_fields_set,
    )
    await db.commit()
    await db.refresh(pref)
    return UserNotificationPrefResponse.model_validate(pref)


@router.post(
    "/users/me/notification-preferences/test",
    summary="Send a test notification to the current user's routed channels",
)
async def test_my_notification_preferences(
    db: AsyncSession = Depends(get_db),
    org_id: uuid.UUID = Depends(get_current_org),
    user: User = Depends(reject_api_tokens),
):
    """Attempt a one-off test delivery to every channel the operator has
    routed (across any priority), using their saved destinations and the
    configured channel factory. Channels without a destination or without
    workspace credentials are reported as skipped rather than failing the
    request. Never raises on per-channel delivery errors.
    """
    from backend.paging.channel_factory import build_channel_factory
    from backend.paging.dispatch import CHANNEL_KEYS

    pref = await UserNotificationPrefRepo.get_for_user(db, org_id, user.id)
    routing = pref.routing if (pref is not None and pref.routing) else {}
    keys: set[str] = set()
    if isinstance(routing, dict):
        for value in routing.values():
            if isinstance(value, list):
                for item in value:
                    if isinstance(item, str) and item in CHANNEL_KEYS:
                        keys.add(item)
                    elif isinstance(item, dict):
                        channel_id = item.get("channel_id")
                        if isinstance(channel_id, str) and channel_id in CHANNEL_KEYS:
                            keys.add(channel_id)

    addresses: dict[str, str] = (
        dict(pref.channels) if (pref is not None and pref.channels) else {}
    )
    if not addresses.get("email") and getattr(user, "email", None):
        addresses["email"] = user.email
    user_phone = getattr(user, "phone", None)
    if user_phone:
        addresses.setdefault("sms", user_phone)
        addresses.setdefault("voice", user_phone)
    if not keys and addresses.get("email"):
        keys = {"email"}

    factory = build_channel_factory()
    subject = "OpsMender test notification"
    body_text = (
        "This is a test notification confirming your My Routing settings. "
        "If you received this, the channel is configured correctly."
    )

    results: list[dict[str, str | None]] = []
    for key in sorted(keys):
        recipient = addresses.get(key)
        if not recipient:
            results.append(
                {"channel": key, "status": "skipped", "detail": "no_recipient"}
            )
            continue
        channel = None
        if key in {"sms", "voice"}:
            from backend.paging.voice_settings import (
                build_sms_channel,
                build_voice_channel,
                resolve_voice_settings,
            )

            settings = await resolve_voice_settings(db, org_id)
            if settings is not None:
                channel = (
                    build_sms_channel(settings)
                    if key == "sms"
                    else build_voice_channel(settings)
                )
        if channel is None:
            channel = factory(key)
        if channel is None:
            results.append(
                {
                    "channel": key,
                    "status": "skipped",
                    "detail": "channel_unconfigured",
                }
            )
            continue
        try:
            attempt = await channel.send(
                recipient=recipient, subject=subject, body=body_text
            )
            results.append(
                {"channel": key, "status": attempt.status, "detail": attempt.error}
            )
        except Exception as exc:  # never fail the request on delivery error
            results.append({"channel": key, "status": "failed", "detail": str(exc)})

    return {"results": results, "tested": len(results)}
