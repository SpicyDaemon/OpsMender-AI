"""Workspace AI session workflow settings."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.auth import get_current_org, require_role
from backend.api.deps import get_db
from backend.api.schemas import WorkflowSettingsResponse, WorkflowSettingsUpdate
from backend.db.models import User
from backend.db.repos import AuditEntryRepo
from backend.workflow.settings import (
    get_or_create_workflow_settings,
    update_workflow_settings,
)

router = APIRouter(prefix="/api/v1/workflow-settings", tags=["workflow-settings"])


def _to_response(profile) -> WorkflowSettingsResponse:
    return WorkflowSettingsResponse(
        workflow_enabled=bool(profile.workflow_enabled),
        node_order=list(profile.node_order or []),
    )


@router.get("", response_model=WorkflowSettingsResponse)
async def get_workflow_settings(
    db: AsyncSession = Depends(get_db),
    org_id: uuid.UUID = Depends(get_current_org),
    user: User = Depends(require_role("admin")),
):
    profile = await get_or_create_workflow_settings(db, org_id)
    await db.commit()
    return _to_response(profile)


@router.put("", response_model=WorkflowSettingsResponse)
async def put_workflow_settings(
    body: WorkflowSettingsUpdate,
    db: AsyncSession = Depends(get_db),
    org_id: uuid.UUID = Depends(get_current_org),
    user: User = Depends(require_role("admin")),
):
    try:
        profile = await update_workflow_settings(
            db,
            org_id,
            workflow_enabled=body.workflow_enabled,
            node_order=body.node_order,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc
    await AuditEntryRepo.create(
        db,
        org_id,
        session_id=None,
        tier=0,
        entry_type="workflow_settings_update",
        tool_name="workflow_settings",
        tool_parameters={
            "actor_user_id": str(user.id),
            "workflow_enabled": body.workflow_enabled,
            "node_order": body.node_order,
        },
        result={"ok": True},
    )
    await db.commit()
    await db.refresh(profile)
    return _to_response(profile)
