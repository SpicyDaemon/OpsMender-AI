"""Saved workflow profile management endpoints."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from backend.agent.graph import validate_workflow_node_order
from backend.api.auth import get_current_org, get_current_user, require_role
from backend.api.deps import get_db
from backend.api.schemas import (
    SessionProfileTemplate,
    SessionProfileTemplateListResponse,
    WorkflowProfileListResponse,
    WorkflowProfileResponse,
    WorkflowProfileUpsert,
)
from backend.db.models import User
from backend.db.repos import WorkflowProfileRepo
from backend.workflow.templates import list_session_profile_templates

router = APIRouter(prefix="/workflow-profiles", tags=["workflow-profiles"])


@router.get(
    "/templates",
    response_model=SessionProfileTemplateListResponse,
    summary="List built-in Session Profile templates (starting points)",
)
async def list_templates(
    user: User = Depends(get_current_user),
):
    """Return the built-in Session Profile presets. These are not persisted — the
    caller loads one into the editor, tweaks it, and saves it as a profile."""
    items = [SessionProfileTemplate(**t) for t in list_session_profile_templates()]
    return SessionProfileTemplateListResponse(items=items, total=len(items))


def _validated_node_order(node_order: list[str]) -> list[str]:
    try:
        return validate_workflow_node_order(node_order)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc


def _to_response(profile) -> WorkflowProfileResponse:
    return WorkflowProfileResponse.model_validate(profile)


@router.get(
    "", response_model=WorkflowProfileListResponse, summary="List workflow profiles"
)
async def list_workflow_profiles(
    db: AsyncSession = Depends(get_db),
    org_id: uuid.UUID = Depends(get_current_org),
    user: User = Depends(get_current_user),
):
    items = await WorkflowProfileRepo.list_all(db, org_id)
    return WorkflowProfileListResponse(
        items=[_to_response(item) for item in items],
        total=len(items),
    )


@router.post(
    "",
    response_model=WorkflowProfileResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a workflow profile",
)
async def create_workflow_profile(
    body: WorkflowProfileUpsert,
    db: AsyncSession = Depends(get_db),
    org_id: uuid.UUID = Depends(get_current_org),
    user: User = Depends(require_role("admin")),
):
    try:
        profile = await WorkflowProfileRepo.create(
            db,
            org_id,
            name=body.name,
            description=body.description,
            node_order=_validated_node_order(body.node_order),
            workflow_enabled=body.workflow_enabled,
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
            detail="Workflow profile name already exists",
        ) from exc


@router.put(
    "/{profile_id}",
    response_model=WorkflowProfileResponse,
    summary="Update a workflow profile",
)
async def update_workflow_profile(
    profile_id: uuid.UUID,
    body: WorkflowProfileUpsert,
    db: AsyncSession = Depends(get_db),
    org_id: uuid.UUID = Depends(get_current_org),
    user: User = Depends(require_role("admin")),
):
    existing = await WorkflowProfileRepo.get_by_id(db, org_id, profile_id)
    if existing is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Workflow profile not found",
        )

    try:
        updated = await WorkflowProfileRepo.update(
            db,
            org_id,
            profile_id,
            name=body.name,
            description=body.description,
            node_order=_validated_node_order(body.node_order),
            workflow_enabled=body.workflow_enabled,
            is_active=body.is_active,
            is_default=body.is_default,
        )
        await db.commit()
        if updated is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Workflow profile not found",
            )
        await db.refresh(updated)
        return _to_response(updated)
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Workflow profile name already exists",
        ) from exc


@router.delete(
    "/{profile_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a workflow profile",
)
async def delete_workflow_profile(
    profile_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    org_id: uuid.UUID = Depends(get_current_org),
    user: User = Depends(require_role("admin")),
):
    deleted = await WorkflowProfileRepo.delete(db, org_id, profile_id)
    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Workflow profile not found",
        )
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
