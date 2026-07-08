"""Noise and response analytics endpoints."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.auth import get_current_org, require_role
from backend.api.deps import get_db
from backend.db.models import User
from backend.reports.analytics import (
    build_noise_report,
    build_response_report,
    render_noise_csv,
    render_response_csv,
)

router = APIRouter(prefix="/api/v1/analytics", tags=["analytics"])


def _aware(value: datetime | None, *, fallback: datetime) -> datetime:
    value = value or fallback
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


def _range(from_at: datetime | None, to_at: datetime | None) -> tuple[datetime, datetime]:
    now = datetime.now(timezone.utc)
    start = _aware(from_at, fallback=now - timedelta(days=30))
    end = _aware(to_at, fallback=now)
    if start > end:
        raise HTTPException(status_code=422, detail="'from' must be before 'to'")
    return start, end


@router.get("/noise")
async def get_noise_analytics(
    format: str = Query(default="json", pattern="^(json|csv)$"),
    from_at: datetime | None = Query(default=None, alias="from"),
    to_at: datetime | None = Query(default=None, alias="to"),
    service_id: uuid.UUID | None = None,
    db: AsyncSession = Depends(get_db),
    org_id: uuid.UUID = Depends(get_current_org),
    user: User = Depends(require_role("admin", "operator")),
):
    start, end = _range(from_at, to_at)
    report = await build_noise_report(
        db,
        org_id,
        from_at=start,
        to_at=end,
        service_id=service_id,
    )
    if format == "csv":
        return Response(
            content=render_noise_csv(report),
            media_type="text/csv; charset=utf-8",
            headers={
                "Content-Disposition": (
                    f'attachment; filename="opsmender-noise-{start.date()}-{end.date()}.csv"'
                )
            },
        )
    return report


@router.get("/response")
async def get_response_analytics(
    format: str = Query(default="json", pattern="^(json|csv)$"),
    from_at: datetime | None = Query(default=None, alias="from"),
    to_at: datetime | None = Query(default=None, alias="to"),
    service_id: uuid.UUID | None = None,
    db: AsyncSession = Depends(get_db),
    org_id: uuid.UUID = Depends(get_current_org),
    user: User = Depends(require_role("admin", "operator")),
):
    start, end = _range(from_at, to_at)
    report = await build_response_report(
        db,
        org_id,
        from_at=start,
        to_at=end,
        service_id=service_id,
    )
    if format == "csv":
        return Response(
            content=render_response_csv(report),
            media_type="text/csv; charset=utf-8",
            headers={
                "Content-Disposition": (
                    f'attachment; filename="opsmender-response-{start.date()}-{end.date()}.csv"'
                )
            },
        )
    return report
