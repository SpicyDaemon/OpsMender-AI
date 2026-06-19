"""On-demand and scheduled incident reports."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.auth import get_current_org, require_role
from backend.api.deps import get_db
from backend.api.schemas import (
    ReportScheduleListResponse,
    ReportScheduleResponse,
    ReportScheduleUpsert,
)
from backend.db.models import User
from backend.db.repos import ReportScheduleRepo
from backend.reports.service import build_incident_report, render_report

router = APIRouter(prefix="/reports", tags=["reports"])


def _aware(value: datetime | None, *, fallback: datetime) -> datetime:
    value = value or fallback
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


@router.get("/incidents")
async def export_incident_report(
    format: str = Query(default="csv", pattern="^(csv|pdf)$"),
    from_at: datetime | None = Query(default=None, alias="from"),
    to_at: datetime | None = Query(default=None, alias="to"),
    status_filter: str | None = Query(default=None, alias="status"),
    severity: str | None = None,
    priority: str | None = None,
    service_id: uuid.UUID | None = None,
    team_id: uuid.UUID | None = None,
    db: AsyncSession = Depends(get_db),
    org_id: uuid.UUID = Depends(get_current_org),
    user: User = Depends(require_role("admin", "operator")),
):
    now = datetime.now(timezone.utc)
    start = _aware(from_at, fallback=now - timedelta(days=30))
    end = _aware(to_at, fallback=now)
    if start >= end:
        raise HTTPException(status_code=400, detail="'from' must be before 'to'")
    report = await build_incident_report(
        db,
        org_id,
        from_at=start,
        to_at=end,
        filters={
            "status": status_filter,
            "severity": severity,
            "priority": priority,
            "service_id": str(service_id) if service_id else None,
            "team_id": str(team_id) if team_id else None,
        },
    )
    content, media_type = render_report(report, format)
    filename = f"opsmender-incidents-{start.date()}-{end.date()}.{format}"
    return Response(
        content=content,
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get(
    "/schedules",
    response_model=ReportScheduleListResponse,
)
async def list_report_schedules(
    db: AsyncSession = Depends(get_db),
    org_id: uuid.UUID = Depends(get_current_org),
    user: User = Depends(require_role("admin")),
):
    items = await ReportScheduleRepo.list_for_org(db, org_id)
    return ReportScheduleListResponse(items=items, total=len(items))


@router.post(
    "/schedules",
    response_model=ReportScheduleResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_report_schedule(
    body: ReportScheduleUpsert,
    db: AsyncSession = Depends(get_db),
    org_id: uuid.UUID = Depends(get_current_org),
    user: User = Depends(require_role("admin")),
):
    try:
        row = await ReportScheduleRepo.create(
            db,
            org_id,
            name=body.name,
            cadence=body.cadence,
            recipients=sorted(set(body.recipients)),
            filters=body.filters,
            format=body.format,
            next_run_at=_aware(body.next_run_at, fallback=datetime.now(timezone.utc)),
            enabled=body.enabled,
        )
        await db.commit()
        await db.refresh(row)
        return row
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(status_code=409, detail="Report schedule name already exists") from exc


@router.put(
    "/schedules/{schedule_id}",
    response_model=ReportScheduleResponse,
)
async def update_report_schedule(
    schedule_id: uuid.UUID,
    body: ReportScheduleUpsert,
    db: AsyncSession = Depends(get_db),
    org_id: uuid.UUID = Depends(get_current_org),
    user: User = Depends(require_role("admin")),
):
    row = await ReportScheduleRepo.get_by_id(db, org_id, schedule_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Report schedule not found")
    await ReportScheduleRepo.update(
        db,
        row,
        name=body.name,
        cadence=body.cadence,
        recipients=sorted(set(body.recipients)),
        filters=body.filters,
        format=body.format,
        next_run_at=_aware(body.next_run_at, fallback=datetime.now(timezone.utc)),
        enabled=body.enabled,
    )
    await db.commit()
    await db.refresh(row)
    return row


@router.delete("/schedules/{schedule_id}", status_code=204)
async def delete_report_schedule(
    schedule_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    org_id: uuid.UUID = Depends(get_current_org),
    user: User = Depends(require_role("admin")),
):
    if not await ReportScheduleRepo.delete(db, org_id, schedule_id):
        raise HTTPException(status_code=404, detail="Report schedule not found")
    await db.commit()
