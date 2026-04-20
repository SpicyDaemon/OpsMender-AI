"""Saved agent team profile management endpoints."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from backend.agent.nodes import validate_agent_roles
from backend.api.auth import get_current_user, require_role
from backend.api.deps import get_db
from backend.api.schemas import (
    AgentTeamProfileListResponse,
    AgentTeamProfileResponse,
    AgentTeamProfileUpsert,
)
from backend.db.models import User
from backend.db.repos import AgentTeamProfileRepo

router = APIRouter(prefix="/agent-team-profiles", tags=["agent-team-profiles"])


def _validated_roles(roles: list[str]) -> list[str]:
    try:
        return validate_agent_roles(roles)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc


def _to_response(profile) -> AgentTeamProfileResponse:
    return AgentTeamProfileResponse.model_validate(profile)


@router.get("", response_model=AgentTeamProfileListResponse, summary="List agent team profiles")
async def list_agent_team_profiles(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    items = await AgentTeamProfileRepo.list_all(db)
    return AgentTeamProfileListResponse(
        items=[_to_response(item) for item in items],
        total=len(items),
    )


@router.post(
    "",
    response_model=AgentTeamProfileResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create an agent team profile",
)
async def create_agent_team_profile(
    body: AgentTeamProfileUpsert,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_role("admin")),
):
    try:
        profile = await AgentTeamProfileRepo.create(
            db,
            name=body.name,
            description=body.description,
            roles=_validated_roles(body.roles),
            is_active=body.is_active,
            is_default=body.is_default,
        )
        await db.commit()
        await db.refresh(profile)
        return _to_response(profile)
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Agent team profile name already exists",
        ) from exc


@router.put(
    "/{profile_id}",
    response_model=AgentTeamProfileResponse,
    summary="Update an agent team profile",
)
async def update_agent_team_profile(
    profile_id: uuid.UUID,
    body: AgentTeamProfileUpsert,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_role("admin")),
):
    existing = await AgentTeamProfileRepo.get_by_id(db, profile_id)
    if existing is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Agent team profile not found",
        )

    try:
        updated = await AgentTeamProfileRepo.update(
            db,
            profile_id,
            name=body.name,
            description=body.description,
            roles=_validated_roles(body.roles),
            is_active=body.is_active,
            is_default=body.is_default,
        )
        await db.commit()
        if updated is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Agent team profile not found",
            )
        await db.refresh(updated)
        return _to_response(updated)
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Agent team profile name already exists",
        ) from exc


@router.delete(
    "/{profile_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete an agent team profile",
)
async def delete_agent_team_profile(
    profile_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_role("admin")),
):
    deleted = await AgentTeamProfileRepo.delete(db, profile_id)
    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Agent team profile not found",
        )
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
