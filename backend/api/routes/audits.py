"""Auditor endpoints (Sprint 32).

Surfaces the Auditor data model and execution flow:

* ``GET  /audits/analyzers`` — list of analyzer keys available to the org.
* ``POST /audits/runs`` — kick off an audit run (admin / operator).
* ``GET  /audits/runs`` — paginated list of runs.
* ``GET  /audits/runs/{id}`` — run detail + findings.
* ``GET  /audits/findings`` — filterable list across runs.
* ``POST /audits/findings/{id}/remediate`` — spawn a session from a finding.
* ``POST /audits/findings/{id}/dismiss`` — operator marks a finding ignored.

The remediate flow is tier-gated: the spawned session inherits the runtime
tier from app state, just like ``opsmender run``.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.auth import get_current_org, get_current_user, require_role
from backend.api.deps import get_db, get_mcp_pool
from backend.api.schemas import (
    AuditAnalyzerListResponse,
    AuditAnalyzerResponse,
    AuditFindingDismissRequest,
    AuditFindingListResponse,
    AuditFindingRemediateResponse,
    AuditFindingResponse,
    AuditRunCreate,
    AuditRunDetailResponse,
    AuditRunListResponse,
    AuditRunResponse,
    AuditScheduleCreate,
    AuditScheduleListResponse,
    AuditScheduleResponse,
    AuditScheduleUpdate,
)
from backend.auditor import list_analyzers, run_audit
from backend.db.models import User
from backend.db.repos import (
    AuditFindingRepo,
    AuditRunRepo,
    AuditScheduleRepo,
    SessionRepo,
)
from backend.mcp.pool import MCPServerPool

router = APIRouter(prefix="/audits", tags=["audits"])


def _to_run_response(run) -> AuditRunResponse:
    return AuditRunResponse.model_validate(run)


def _to_finding_response(finding) -> AuditFindingResponse:
    return AuditFindingResponse.model_validate(finding)


@router.get(
    "/analyzers",
    response_model=AuditAnalyzerListResponse,
    summary="List available auditor analyzers",
)
async def list_audit_analyzers(user: User = Depends(get_current_user)):
    specs = list_analyzers()
    items = [
        AuditAnalyzerResponse(
            key=s.key, label=s.label, description=s.description
        )
        for s in specs
    ]
    return AuditAnalyzerListResponse(items=items, total=len(items))


@router.post(
    "/runs",
    response_model=AuditRunResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create and (optionally) execute an audit run",
)
async def create_audit_run(
    body: AuditRunCreate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    org_id: uuid.UUID = Depends(get_current_org),
    pool: MCPServerPool = Depends(get_mcp_pool),
    user: User = Depends(require_role("admin", "operator")),
):
    known = {s.key for s in list_analyzers()}
    unknown = [a for a in body.analyzers if a not in known]
    if unknown:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unknown analyzers: {', '.join(unknown)}",
        )

    run = await AuditRunRepo.create(
        db,
        org_id,
        analyzers=body.analyzers,
        created_by=user.id,
        status="queued",
    )
    await db.commit()
    await db.refresh(run)

    if body.execute:
        await run_audit(
            db,
            run_id=run.id,
            org_id=org_id,
            pool=pool,
            config=request.app.state.config,
            analyzer_params=body.analyzer_params or {},
        )
        await db.commit()
        refreshed = await AuditRunRepo.get_by_id(db, org_id, run.id)
        return _to_run_response(refreshed or run)

    return _to_run_response(run)


@router.get(
    "/runs",
    response_model=AuditRunListResponse,
    summary="List audit runs",
)
async def list_audit_runs(
    db: AsyncSession = Depends(get_db),
    org_id: uuid.UUID = Depends(get_current_org),
    user: User = Depends(get_current_user),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
):
    items = await AuditRunRepo.list_all(
        db, org_id, limit=limit, offset=offset
    )
    return AuditRunListResponse(
        items=[_to_run_response(item) for item in items],
        total=len(items),
    )


@router.get(
    "/runs/{run_id}",
    response_model=AuditRunDetailResponse,
    summary="Get an audit run with its findings",
)
async def get_audit_run(
    run_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    org_id: uuid.UUID = Depends(get_current_org),
    user: User = Depends(get_current_user),
):
    run = await AuditRunRepo.get_by_id(db, org_id, run_id)
    if run is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Audit run not found",
        )
    findings = await AuditFindingRepo.list_by_run(db, org_id, run_id)
    return AuditRunDetailResponse(
        run=_to_run_response(run),
        findings=[_to_finding_response(f) for f in findings],
    )


@router.get(
    "/findings",
    response_model=AuditFindingListResponse,
    summary="List audit findings (filterable)",
)
async def list_audit_findings(
    db: AsyncSession = Depends(get_db),
    org_id: uuid.UUID = Depends(get_current_org),
    user: User = Depends(get_current_user),
    status_filter: str | None = Query(default=None, alias="status"),
    severity: str | None = Query(default=None),
    analyzer: str | None = Query(default=None),
    run_id: uuid.UUID | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
):
    items = await AuditFindingRepo.list_filtered(
        db,
        org_id,
        status=status_filter,
        severity=severity,
        analyzer=analyzer,
        run_id=run_id,
        limit=limit,
        offset=offset,
    )
    return AuditFindingListResponse(
        items=[_to_finding_response(item) for item in items],
        total=len(items),
    )


@router.post(
    "/findings/{finding_id}/remediate",
    response_model=AuditFindingRemediateResponse,
    summary="Spawn a remediation session from a finding",
)
async def remediate_audit_finding(
    finding_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
    org_id: uuid.UUID = Depends(get_current_org),
    user: User = Depends(require_role("admin", "operator")),
):
    finding = await AuditFindingRepo.get_by_id(db, org_id, finding_id)
    if finding is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Audit finding not found",
        )
    if finding.status in {"resolved", "dismissed"}:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Finding already {finding.status}",
        )

    # Remediation runs as a normal, gated session. Default to Tier 2 (Advisory)
    # and normalize any legacy/invalid value (e.g. old Tier 3) so the session
    # never starts more autonomous than the operator intends.
    from backend.tiers.enforcement import normalize_tier

    tier = normalize_tier(int(getattr(request.app.state, "runtime_tier", 2)))
    session = await SessionRepo.create(
        db,
        org_id,
        tier=tier,
    )
    updated = await AuditFindingRepo.update_status(
        db,
        org_id,
        finding_id,
        status="remediating",
        session_id=session.id,
        session_id_provided=True,
    )
    await db.commit()
    return AuditFindingRemediateResponse(
        finding_id=finding_id,
        session_id=session.id,
        status=updated.status if updated else "remediating",
    )


@router.post(
    "/findings/{finding_id}/dismiss",
    response_model=AuditFindingResponse,
    summary="Dismiss an audit finding",
)
async def dismiss_audit_finding(
    finding_id: uuid.UUID,
    body: AuditFindingDismissRequest,
    db: AsyncSession = Depends(get_db),
    org_id: uuid.UUID = Depends(get_current_org),
    user: User = Depends(require_role("admin", "operator")),
):
    finding = await AuditFindingRepo.get_by_id(db, org_id, finding_id)
    if finding is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Audit finding not found",
        )
    updated = await AuditFindingRepo.update_status(
        db,
        org_id,
        finding_id,
        status="dismissed",
        dismiss_reason=body.reason,
    )
    await db.commit()
    return _to_finding_response(updated or finding)


# ---------------------------------------------------------------------------
# Scheduled audit runs (Sprint 39 step 2)
# ---------------------------------------------------------------------------


def _to_schedule_response(row) -> AuditScheduleResponse:
    return AuditScheduleResponse.model_validate(row)


@router.get(
    "/schedules",
    response_model=AuditScheduleListResponse,
    summary="List scheduled audit runs",
)
async def list_audit_schedules(
    db: AsyncSession = Depends(get_db),
    org_id: uuid.UUID = Depends(get_current_org),
    user: User = Depends(get_current_user),
):
    rows = await AuditScheduleRepo.list_for_org(db, org_id)
    return AuditScheduleListResponse(
        items=[_to_schedule_response(r) for r in rows], total=len(rows)
    )


@router.post(
    "/schedules",
    response_model=AuditScheduleResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a scheduled audit run",
)
async def create_audit_schedule(
    body: AuditScheduleCreate,
    db: AsyncSession = Depends(get_db),
    org_id: uuid.UUID = Depends(get_current_org),
    user: User = Depends(require_role("admin")),
):
    from datetime import datetime, timezone

    known = {s.key for s in list_analyzers()}
    unknown = [a for a in body.analyzers if a not in known]
    if unknown:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unknown analyzers: {', '.join(unknown)}",
        )

    # First run fires immediately on the next scheduler tick.
    row = await AuditScheduleRepo.create(
        db,
        org_id,
        name=body.name,
        description=body.description,
        analyzers=body.analyzers,
        mcp_server_name=body.mcp_server_name,
        focus_areas=body.focus_areas,
        interval_minutes=body.interval_minutes,
        is_active=body.is_active,
        next_run_at=datetime.now(timezone.utc),
        created_by=user.id,
    )
    await db.commit()
    return _to_schedule_response(row)


@router.get(
    "/schedules/{schedule_id}",
    response_model=AuditScheduleResponse,
    summary="Get a scheduled audit run",
)
async def get_audit_schedule(
    schedule_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    org_id: uuid.UUID = Depends(get_current_org),
    user: User = Depends(get_current_user),
):
    row = await AuditScheduleRepo.get_by_id(db, org_id, schedule_id)
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Audit schedule not found",
        )
    return _to_schedule_response(row)


@router.patch(
    "/schedules/{schedule_id}",
    response_model=AuditScheduleResponse,
    summary="Update a scheduled audit run",
)
async def update_audit_schedule(
    schedule_id: uuid.UUID,
    body: AuditScheduleUpdate,
    db: AsyncSession = Depends(get_db),
    org_id: uuid.UUID = Depends(get_current_org),
    user: User = Depends(require_role("admin")),
):
    row = await AuditScheduleRepo.get_by_id(db, org_id, schedule_id)
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Audit schedule not found",
        )
    if body.analyzers is not None:
        known = {s.key for s in list_analyzers()}
        unknown = [a for a in body.analyzers if a not in known]
        if unknown:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Unknown analyzers: {', '.join(unknown)}",
            )

    updated = await AuditScheduleRepo.update(
        db,
        row,
        name=body.name,
        description=body.description,
        analyzers=body.analyzers,
        mcp_server_name=body.mcp_server_name,
        focus_areas=body.focus_areas,
        interval_minutes=body.interval_minutes,
        is_active=body.is_active,
    )
    await db.commit()
    return _to_schedule_response(updated)


@router.delete(
    "/schedules/{schedule_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a scheduled audit run",
)
async def delete_audit_schedule(
    schedule_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    org_id: uuid.UUID = Depends(get_current_org),
    user: User = Depends(require_role("admin")),
):
    row = await AuditScheduleRepo.get_by_id(db, org_id, schedule_id)
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Audit schedule not found",
        )
    await AuditScheduleRepo.delete(db, row)
    await db.commit()
