"""Detector rule management endpoints."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.auth import get_current_user, require_role
from backend.api.deps import get_db, get_mcp_pool
from backend.api.schemas import (
    DetectorHistoryListResponse,
    DetectorHistoryResponse,
    DetectorRuleCreate,
    DetectorRuleListResponse,
    DetectorRuleResponse,
    DetectorTemplateListResponse,
    DetectorTemplateResponse,
    DetectorRuleUpdate,
    DetectorRunResponse,
)
from backend.db.models import User
from backend.db.repos import (
    DetectorHistoryRepo,
    DetectorRuleRepo,
    MCPServerRepo,
    ModelConfigRepo,
)
from backend.detector.runner import run_detector_rule
from backend.detector.templates import list_detector_templates
from backend.mcp.pool import MCPServerPool

router = APIRouter(prefix="/detectors", tags=["detectors"])


def _to_rule_response(item) -> DetectorRuleResponse:
    return DetectorRuleResponse.model_validate(item)


async def _validate_refs(
    db: AsyncSession,
    *,
    mcp_server_id: uuid.UUID | None,
    model_config_id: uuid.UUID | None,
) -> None:
    if mcp_server_id is not None:
        server = await MCPServerRepo.get_by_id(db, mcp_server_id)
        if server is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"MCP server {mcp_server_id} not found",
            )
    if model_config_id is not None:
        cfg = await ModelConfigRepo.get_by_id(db, model_config_id)
        if cfg is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Model config {model_config_id} not found",
            )


@router.get(
    "",
    response_model=DetectorRuleListResponse,
    summary="List detector rules",
)
async def list_detector_rules(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    items = await DetectorRuleRepo.list_all(db)
    return DetectorRuleListResponse(
        items=[_to_rule_response(item) for item in items],
        total=len(items),
    )


@router.get(
    "/templates",
    response_model=DetectorTemplateListResponse,
    summary="List built-in detector rule templates",
)
async def get_detector_templates(
    user: User = Depends(get_current_user),
):
    items = [
        DetectorTemplateResponse(
            key=item.key,
            label=item.label,
            description=item.description,
            prompt_template=item.prompt_template,
            severity_default=item.severity_default,
            interval_seconds=item.interval_seconds,
        )
        for item in list_detector_templates()
    ]
    return DetectorTemplateListResponse(items=items, total=len(items))


@router.post(
    "",
    response_model=DetectorRuleResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a detector rule",
)
async def create_detector_rule(
    body: DetectorRuleCreate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_role("admin")),
):
    await _validate_refs(
        db,
        mcp_server_id=body.mcp_server_id,
        model_config_id=body.model_config_id,
    )
    try:
        rule = await DetectorRuleRepo.create(
            db,
            name=body.name,
            mcp_server_id=body.mcp_server_id,
            prompt_template=body.prompt_template,
            model_config_id=body.model_config_id,
            interval_seconds=body.interval_seconds,
            severity_default=body.severity_default,
            is_active=body.is_active,
        )
        await db.commit()
        await db.refresh(rule)
        return _to_rule_response(rule)
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Detector rule name already exists",
        ) from exc


@router.put(
    "/{rule_id}",
    response_model=DetectorRuleResponse,
    summary="Update a detector rule",
)
async def update_detector_rule(
    rule_id: uuid.UUID,
    body: DetectorRuleUpdate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_role("admin")),
):
    existing = await DetectorRuleRepo.get_by_id(db, rule_id)
    if existing is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Detector rule not found",
        )

    next_mcp_server_id = (
        body.mcp_server_id
        if "mcp_server_id" in body.model_fields_set
        else existing.mcp_server_id
    )
    next_model_config_id = (
        body.model_config_id
        if "model_config_id" in body.model_fields_set
        else existing.model_config_id
    )
    await _validate_refs(
        db,
        mcp_server_id=next_mcp_server_id,
        model_config_id=next_model_config_id,
    )

    try:
        updated = await DetectorRuleRepo.update(
            db,
            rule_id,
            name=body.name,
            mcp_server_id=next_mcp_server_id,
            prompt_template=body.prompt_template,
            model_config_id=next_model_config_id,
            model_config_id_provided="model_config_id" in body.model_fields_set,
            interval_seconds=body.interval_seconds,
            severity_default=body.severity_default,
            is_active=body.is_active,
        )
        await db.commit()
        if updated is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Detector rule not found",
            )
        await db.refresh(updated)
        return _to_rule_response(updated)
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Detector rule name already exists",
        ) from exc


@router.delete(
    "/{rule_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a detector rule",
)
async def delete_detector_rule(
    rule_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_role("admin")),
):
    deleted = await DetectorRuleRepo.delete(db, rule_id)
    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Detector rule not found",
        )
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/{rule_id}/run",
    response_model=DetectorRunResponse,
    summary="Run a detector rule immediately",
)
async def run_detector(
    rule_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
    pool: MCPServerPool = Depends(get_mcp_pool),
    user: User = Depends(require_role("admin", "operator")),
):
    rule = await DetectorRuleRepo.get_by_id(db, rule_id)
    if rule is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Detector rule not found",
        )

    result = await run_detector_rule(
        db,
        rule=rule,
        pool=pool,
        config=request.app.state.config,
        budget_guard=getattr(request.app.state, "detector_budget", None),
    )
    return DetectorRunResponse(
        success=result.success,
        issue_detected=result.issue_detected,
        incident_id=result.incident_id,
        error=result.error,
    )


@router.get(
    "/{rule_id}/history",
    response_model=DetectorHistoryListResponse,
    summary="List run history for a detector rule",
)
async def list_detector_history(
    rule_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    rule = await DetectorRuleRepo.get_by_id(db, rule_id)
    if rule is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Detector rule not found",
        )

    items = await DetectorHistoryRepo.list_by_rule(db, rule_id)
    return DetectorHistoryListResponse(
        items=[DetectorHistoryResponse.model_validate(item) for item in items],
        total=len(items),
    )
