"""Audit endpoints.

GET /audit — query audit entries with filtering and pagination.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.auth import get_current_org, get_current_user
from backend.api.deps import get_db
from backend.api.schemas import AuditEntryResponse, AuditListResponse
from backend.db.models import User
from backend.db.repos import AuditEntryRepo

router = APIRouter(prefix="/audit", tags=["audit"])


@router.get(
    "",
    response_model=AuditListResponse,
    summary="Query audit entries",
)
async def list_audit_entries(
    session_id: uuid.UUID | None = Query(None),
    tool_name: str | None = Query(None),
    entry_type: str | None = Query(None),
    permitted: bool | None = Query(None),
    start: datetime | None = Query(None, description="ISO-8601 start time"),
    end: datetime | None = Query(None, description="ISO-8601 end time"),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
    org_id: uuid.UUID = Depends(get_current_org),
    user: User = Depends(get_current_user),
):
    items = await AuditEntryRepo.query(
        db,
        org_id,
        session_id=session_id,
        tool_name=tool_name,
        entry_type=entry_type,
        permitted=permitted,
        start=start,
        end=end,
        limit=limit,
        offset=offset,
    )
    # Total count for pagination (same filters, no limit/offset)
    all_items = await AuditEntryRepo.query(
        db,
        org_id,
        session_id=session_id,
        tool_name=tool_name,
        entry_type=entry_type,
        permitted=permitted,
        start=start,
        end=end,
        limit=10_000,
        offset=0,
    )
    return AuditListResponse(items=list(items), total=len(all_items))
