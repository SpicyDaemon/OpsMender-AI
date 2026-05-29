"""SLA / SLO / Maintenance Window API endpoints (Sprint 25)."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.auth import get_current_org, get_current_user, require_role
from backend.api.deps import get_db
from backend.api.schemas import (
    MaintenanceWindowCreate,
    MaintenanceWindowListResponse,
    MaintenanceWindowResponse,
    MaintenanceWindowUpdate,
    SLATargetCreate,
    SLATargetListResponse,
    SLATargetResponse,
    SLATargetUpdate,
    SLOCreate,
    SLOListResponse,
    SLOResponse,
    SLOStatusResponse,
    SLOUpdate,
    UptimeResponse,
)
from backend.db.models import User
from backend.db.repos import (
    MaintenanceWindowRepo,
    SLATargetRepo,
    SLORepo,
    UptimeSampleRepo,
)
from backend.sla.poller import validate_expected_status_config

router = APIRouter(tags=["reliability"])

# ======================================================================
# SLA Targets
# ======================================================================

_targets_prefix = "/sla-targets"


def _validate_sla_target_payload(kind: str | None, config: dict | None) -> None:
    if kind != "http" or config is None:
        return
    try:
        validate_expected_status_config(config)
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc


@router.get(
    _targets_prefix,
    response_model=SLATargetListResponse,
    summary="List SLA targets",
)
async def list_sla_targets(
    db: AsyncSession = Depends(get_db),
    org_id: uuid.UUID = Depends(get_current_org),
    user: User = Depends(get_current_user),
):
    items = await SLATargetRepo.list_all(db, org_id)
    return SLATargetListResponse(
        items=[SLATargetResponse.model_validate(t) for t in items],
        total=len(items),
    )


@router.get(
    _targets_prefix + "/{target_id}",
    response_model=SLATargetResponse,
    summary="Get a single SLA target",
)
async def get_sla_target(
    target_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    org_id: uuid.UUID = Depends(get_current_org),
    user: User = Depends(get_current_user),
):
    target = await SLATargetRepo.get_by_id(db, org_id, target_id)
    if target is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "SLA target not found")
    return SLATargetResponse.model_validate(target)


@router.post(
    _targets_prefix,
    response_model=SLATargetResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create an SLA target",
)
async def create_sla_target(
    body: SLATargetCreate,
    db: AsyncSession = Depends(get_db),
    org_id: uuid.UUID = Depends(get_current_org),
    user: User = Depends(require_role("admin")),
):
    _validate_sla_target_payload(body.kind, body.config)
    try:
        target = await SLATargetRepo.create(
            db,
            org_id,
            name=body.name,
            kind=body.kind,
            config=body.config,
            owner_team=body.owner_team,
            is_active=body.is_active,
        )
        await db.commit()
        await db.refresh(target)
        return SLATargetResponse.model_validate(target)
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "SLA target name already exists",
        ) from exc


@router.put(
    _targets_prefix + "/{target_id}",
    response_model=SLATargetResponse,
    summary="Update an SLA target",
)
async def update_sla_target(
    target_id: uuid.UUID,
    body: SLATargetUpdate,
    db: AsyncSession = Depends(get_db),
    org_id: uuid.UUID = Depends(get_current_org),
    user: User = Depends(require_role("admin")),
):
    existing = await SLATargetRepo.get_by_id(db, org_id, target_id)
    if existing is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "SLA target not found")

    next_kind = body.kind if body.kind is not None else existing.kind
    next_config = body.config if "config" in body.model_fields_set else existing.config
    _validate_sla_target_payload(next_kind, next_config)

    try:
        updated = await SLATargetRepo.update(
            db,
            org_id,
            target_id,
            name=body.name,
            kind=body.kind,
            config=body.config,
            config_provided="config" in body.model_fields_set,
            owner_team=body.owner_team,
            owner_team_provided="owner_team" in body.model_fields_set,
            is_active=body.is_active,
        )
        await db.commit()
        if updated is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "SLA target not found")
        await db.refresh(updated)
        return SLATargetResponse.model_validate(updated)
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "SLA target name already exists",
        ) from exc


@router.delete(
    _targets_prefix + "/{target_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete an SLA target",
)
async def delete_sla_target(
    target_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    org_id: uuid.UUID = Depends(get_current_org),
    user: User = Depends(require_role("admin")),
):
    deleted = await SLATargetRepo.delete(db, org_id, target_id)
    if not deleted:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "SLA target not found")
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    _targets_prefix + "/{target_id}/probe",
    response_model=dict,
    summary="Manually probe an SLA target",
)
async def probe_sla_target(
    target_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
    org_id: uuid.UUID = Depends(get_current_org),
    user: User = Depends(require_role("admin", "operator")),
):
    target = await SLATargetRepo.get_by_id(db, org_id, target_id)
    if target is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "SLA target not found")

    poller = getattr(request.app.state, "sla_poller", None)
    if poller is None:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "SLA poller not initialised",
        )

    up, latency_ms = await poller._probe_target(target)

    # Record the sample
    await UptimeSampleRepo.create(
        db,
        org_id,
        target_id=target.id,
        up=up,
        latency_ms=latency_ms,
        source="manual",
    )
    await db.commit()

    return {"up": up, "latency_ms": latency_ms}


@router.get(
    _targets_prefix + "/{target_id}/incidents",
    response_model=list[dict],
    summary="Get incidents linked to an SLA target",
)
async def get_sla_target_incidents(
    target_id: uuid.UUID,
    limit: int = 100,
    offset: int = 0,
    db: AsyncSession = Depends(get_db),
    org_id: uuid.UUID = Depends(get_current_org),
    user: User = Depends(require_role("admin", "operator", "viewer")),
):
    target = await SLATargetRepo.get_by_id(db, org_id, target_id)
    if target is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "SLA target not found")

    from backend.db.repos import IncidentRepo

    incidents = await IncidentRepo.list_by_target(
        db, org_id, target_id, limit=limit, offset=offset
    )

    return [
        {
            "id": i.id,
            "title": i.title,
            "description": i.description,
            "status": i.status,
            "severity": i.severity,
            "external_source": i.external_source,
            "external_id": i.external_id,
            "created_at": i.created_at,
        }
        for i in incidents
    ]


# -- Uptime query ----------------------------------------------------------

WINDOW_MAP = {
    "7d": timedelta(days=7),
    "30d": timedelta(days=30),
    "90d": timedelta(days=90),
    "1y": timedelta(days=365),
}


@router.get(
    _targets_prefix + "/{target_id}/uptime",
    response_model=UptimeResponse,
    summary="Get uptime statistics for an SLA target",
)
async def get_target_uptime(
    target_id: uuid.UUID,
    window: str = Query("30d", pattern="^(7d|30d|90d|1y)$"),
    db: AsyncSession = Depends(get_db),
    org_id: uuid.UUID = Depends(get_current_org),
    user: User = Depends(get_current_user),
):
    target = await SLATargetRepo.get_by_id(db, org_id, target_id)
    if target is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "SLA target not found")

    now = datetime.now(timezone.utc)
    since = now - WINDOW_MAP[window]

    stats = await UptimeSampleRepo.compute_uptime(
        db, org_id, target_id, since=since, until=now
    )

    return UptimeResponse(
        target_id=target_id,
        uptime_pct=stats["uptime_pct"],
        total_samples=stats["total_samples"],
        up_samples=stats["up_samples"],
        downtime_seconds=stats["downtime_seconds"],
        suppressed_seconds=stats["suppressed_seconds"],
    )


# ======================================================================
# SLOs
# ======================================================================

_slos_prefix = "/slos"


@router.get(
    _slos_prefix,
    response_model=SLOListResponse,
    summary="List SLOs",
)
async def list_slos(
    db: AsyncSession = Depends(get_db),
    org_id: uuid.UUID = Depends(get_current_org),
    user: User = Depends(get_current_user),
):
    items = await SLORepo.list_all(db, org_id)
    return SLOListResponse(
        items=[SLOResponse.model_validate(s) for s in items],
        total=len(items),
    )


@router.post(
    _slos_prefix,
    response_model=SLOResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create an SLO",
)
async def create_slo(
    body: SLOCreate,
    db: AsyncSession = Depends(get_db),
    org_id: uuid.UUID = Depends(get_current_org),
    user: User = Depends(require_role("admin")),
):
    # Validate target exists
    target = await SLATargetRepo.get_by_id(db, org_id, body.target_id)
    if target is None:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"SLA target {body.target_id} not found",
        )

    slo = await SLORepo.create(
        db,
        org_id,
        target_id=body.target_id,
        name=body.name,
        objective_pct=body.objective_pct,
        window_seconds=body.window_seconds,
        burn_alert_threshold=body.burn_alert_threshold,
        is_active=body.is_active,
    )
    await db.commit()
    await db.refresh(slo)
    return SLOResponse.model_validate(slo)


@router.put(
    _slos_prefix + "/{slo_id}",
    response_model=SLOResponse,
    summary="Update an SLO",
)
async def update_slo(
    slo_id: uuid.UUID,
    body: SLOUpdate,
    db: AsyncSession = Depends(get_db),
    org_id: uuid.UUID = Depends(get_current_org),
    user: User = Depends(require_role("admin")),
):
    existing = await SLORepo.get_by_id(db, org_id, slo_id)
    if existing is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "SLO not found")

    updated = await SLORepo.update(
        db,
        org_id,
        slo_id,
        name=body.name,
        objective_pct=body.objective_pct,
        window_seconds=body.window_seconds,
        burn_alert_threshold=body.burn_alert_threshold,
        burn_alert_threshold_provided="burn_alert_threshold" in body.model_fields_set,
        is_active=body.is_active,
    )
    await db.commit()
    if updated is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "SLO not found")
    await db.refresh(updated)
    return SLOResponse.model_validate(updated)


@router.delete(
    _slos_prefix + "/{slo_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete an SLO",
)
async def delete_slo(
    slo_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    org_id: uuid.UUID = Depends(get_current_org),
    user: User = Depends(require_role("admin")),
):
    deleted = await SLORepo.delete(db, org_id, slo_id)
    if not deleted:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "SLO not found")
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get(
    _slos_prefix + "/{slo_id}/status",
    response_model=SLOStatusResponse,
    summary="Get SLO compliance status",
)
async def get_slo_status(
    slo_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    org_id: uuid.UUID = Depends(get_current_org),
    user: User = Depends(get_current_user),
):
    slo = await SLORepo.get_by_id(db, org_id, slo_id)
    if slo is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "SLO not found")

    now = datetime.now(timezone.utc)
    since = now - timedelta(seconds=slo.window_seconds)

    stats = await UptimeSampleRepo.compute_uptime(
        db, org_id, slo.target_id, since=since, until=now
    )

    actual_pct = stats["uptime_pct"]
    objective = slo.objective_pct

    # Error budget: how much downtime is allowed
    # e.g. 99.9% SLO means 0.1% error budget
    error_budget_total = 100.0 - objective
    error_used = 100.0 - actual_pct
    if error_budget_total > 0:
        error_budget_remaining_pct = max(
            0.0, ((error_budget_total - error_used) / error_budget_total) * 100.0
        )
    else:
        error_budget_remaining_pct = 0.0 if error_used > 0 else 100.0

    # Burn rate: ratio of actual error rate to allowed error rate
    # burn_rate = 1.0 means consuming budget at exactly the expected pace
    # burn_rate > 1.0 means consuming faster than allowed
    if error_budget_total > 0:
        burn_rate = error_used / error_budget_total
    else:
        burn_rate = float("inf") if error_used > 0 else 0.0

    compliant = actual_pct >= objective

    return SLOStatusResponse(
        slo_id=slo.id,
        target_id=slo.target_id,
        name=slo.name,
        objective_pct=objective,
        actual_pct=round(actual_pct, 4),
        error_budget_remaining_pct=round(error_budget_remaining_pct, 4),
        burn_rate=round(burn_rate, 4),
        compliant=compliant,
    )


# ======================================================================
# Maintenance Windows
# ======================================================================

_mw_prefix = "/maintenance-windows"


@router.get(
    _mw_prefix,
    response_model=MaintenanceWindowListResponse,
    summary="List maintenance windows",
)
async def list_maintenance_windows(
    db: AsyncSession = Depends(get_db),
    org_id: uuid.UUID = Depends(get_current_org),
    user: User = Depends(get_current_user),
):
    items = await MaintenanceWindowRepo.list_all(db, org_id)
    return MaintenanceWindowListResponse(
        items=[MaintenanceWindowResponse.model_validate(mw) for mw in items],
        total=len(items),
    )


@router.post(
    _mw_prefix,
    response_model=MaintenanceWindowResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a maintenance window",
)
async def create_maintenance_window(
    body: MaintenanceWindowCreate,
    db: AsyncSession = Depends(get_db),
    org_id: uuid.UUID = Depends(get_current_org),
    user: User = Depends(require_role("admin")),
):
    if body.ends_at <= body.starts_at:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "ends_at must be after starts_at",
        )

    scope_ids = list(body.scope_ids or [])
    if body.scope_id is not None and body.scope_id not in scope_ids:
        scope_ids.insert(0, body.scope_id)
    scope_id = scope_ids[0] if scope_ids else body.scope_id
    target_ids = [str(v) for v in scope_ids] if scope_ids else body.target_ids

    mw = await MaintenanceWindowRepo.create(
        db,
        org_id,
        name=body.name,
        reason=body.reason,
        description=body.description,
        starts_at=body.starts_at,
        ends_at=body.ends_at,
        rrule=body.rrule,
        target_ids=target_ids,
        scope_type=body.scope_type,
        scope_id=scope_id,
        created_by=user.id,
    )
    await db.commit()
    await db.refresh(mw)
    return MaintenanceWindowResponse.model_validate(mw)


@router.put(
    _mw_prefix + "/{mw_id}",
    response_model=MaintenanceWindowResponse,
    summary="Update a maintenance window",
)
async def update_maintenance_window(
    mw_id: uuid.UUID,
    body: MaintenanceWindowUpdate,
    db: AsyncSession = Depends(get_db),
    org_id: uuid.UUID = Depends(get_current_org),
    user: User = Depends(require_role("admin")),
):
    existing = await MaintenanceWindowRepo.get_by_id(db, org_id, mw_id)
    if existing is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Maintenance window not found")

    scope_ids = body.scope_ids
    scope_id = body.scope_id
    target_ids = body.target_ids
    if scope_ids is not None:
        ordered = list(scope_ids)
        if scope_id is not None and scope_id not in ordered:
            ordered.insert(0, scope_id)
        scope_id = ordered[0] if ordered else None
        target_ids = [str(v) for v in ordered]

    updated = await MaintenanceWindowRepo.update(
        db,
        org_id,
        mw_id,
        name=body.name,
        reason=body.reason,
        reason_provided="reason" in body.model_fields_set,
        description=body.description,
        description_provided="description" in body.model_fields_set,
        starts_at=body.starts_at,
        ends_at=body.ends_at,
        rrule=body.rrule,
        rrule_provided="rrule" in body.model_fields_set,
        target_ids=target_ids,
        scope_type=body.scope_type,
        scope_id=scope_id,
        scope_id_provided=(
            "scope_id" in body.model_fields_set or "scope_ids" in body.model_fields_set
        ),
    )
    await db.commit()
    if updated is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Maintenance window not found")
    await db.refresh(updated)
    return MaintenanceWindowResponse.model_validate(updated)


@router.delete(
    _mw_prefix + "/{mw_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a maintenance window",
)
async def delete_maintenance_window(
    mw_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    org_id: uuid.UUID = Depends(get_current_org),
    user: User = Depends(require_role("admin")),
):
    deleted = await MaintenanceWindowRepo.delete(db, org_id, mw_id)
    if not deleted:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Maintenance window not found")
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
