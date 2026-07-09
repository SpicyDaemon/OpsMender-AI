"""Workspace-level AI session workflow settings."""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from backend.agent.graph import DEFAULT_WORKFLOW_NODE_ORDER, validate_workflow_node_order
from backend.db.models import WorkflowProfile
from backend.db.repos import WorkflowProfileRepo

DEFAULT_WORKFLOW_NAME = "Workspace workflow"
DEFAULT_WORKFLOW_DESCRIPTION = "Applies to every AI session in this workspace."


async def get_or_create_workflow_settings(
    db: AsyncSession,
    org_id: uuid.UUID,
) -> WorkflowProfile:
    profile = await WorkflowProfileRepo.get_default(db, org_id)
    if profile is not None:
        return profile

    existing = list(await WorkflowProfileRepo.list_all(db, org_id))
    if existing:
        first = existing[0]
        updated = await WorkflowProfileRepo.update(
            db,
            org_id,
            first.id,
            name=first.name,
            description=first.description,
            node_order=list(first.node_order or DEFAULT_WORKFLOW_NODE_ORDER),
            workflow_enabled=bool(first.workflow_enabled),
            is_active=True,
            is_default=True,
        )
        return updated or first

    return await WorkflowProfileRepo.create(
        db,
        org_id,
        name=DEFAULT_WORKFLOW_NAME,
        description=DEFAULT_WORKFLOW_DESCRIPTION,
        node_order=list(DEFAULT_WORKFLOW_NODE_ORDER),
        workflow_enabled=True,
        is_active=True,
        is_default=True,
    )


async def update_workflow_settings(
    db: AsyncSession,
    org_id: uuid.UUID,
    *,
    workflow_enabled: bool,
    node_order: list[str],
) -> WorkflowProfile:
    profile = await get_or_create_workflow_settings(db, org_id)
    updated = await WorkflowProfileRepo.update(
        db,
        org_id,
        profile.id,
        name=profile.name,
        description=profile.description,
        node_order=validate_workflow_node_order(node_order),
        workflow_enabled=workflow_enabled,
        is_active=True,
        is_default=True,
    )
    return updated or profile
