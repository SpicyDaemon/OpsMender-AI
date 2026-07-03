"""Audit endpoints.

GET /audit — query audit entries with filtering and pagination.
"""

from __future__ import annotations

import csv
import io
import json
import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.auth import get_current_org, get_current_user
from backend.api.deps import get_db
from backend.api.schemas import AuditListResponse
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


_CSV_COLUMNS = [
    "timestamp",
    "entry_type",
    "tool_name",
    "tier",
    "permitted",
    "duration_ms",
    "session_id",
    "block_reason",
    "tool_parameters",
    "result",
]


@router.get(
    "/export.csv",
    summary="Export audit entries as CSV",
    response_class=Response,
)
async def export_audit_csv(
    session_id: uuid.UUID | None = Query(None),
    tool_name: str | None = Query(None),
    entry_type: str | None = Query(None),
    permitted: bool | None = Query(None),
    start: datetime | None = Query(None, description="ISO-8601 start time"),
    end: datetime | None = Query(None, description="ISO-8601 end time"),
    db: AsyncSession = Depends(get_db),
    org_id: uuid.UUID = Depends(get_current_org),
    user: User = Depends(get_current_user),
) -> Response:
    """Flatten the AI action-history audit to CSV for spreadsheets.

    The on-disk audit stays JSONL (nested ``tool_parameters`` / ``result``
    objects survive intact there); this export serializes those nested
    fields into JSON-string cells so the rest of the row reads cleanly in a
    spreadsheet. Honors the same filters as ``GET /audit``.
    """
    items = await AuditEntryRepo.query(
        db,
        org_id,
        session_id=session_id,
        tool_name=tool_name,
        entry_type=entry_type,
        permitted=permitted,
        start=start,
        end=end,
        limit=100_000,
        offset=0,
    )

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(_CSV_COLUMNS)
    for e in items:
        writer.writerow(
            [
                e.timestamp.isoformat() if e.timestamp else "",
                e.entry_type,
                e.tool_name or "",
                e.tier,
                "true" if e.permitted else "false",
                e.duration_ms if e.duration_ms is not None else "",
                str(e.session_id),
                e.block_reason or "",
                json.dumps(e.tool_parameters) if e.tool_parameters else "",
                json.dumps(e.result) if e.result else "",
            ]
        )

    return Response(
        content=buf.getvalue(),
        media_type="text/csv",
        headers={
            "Content-Disposition": "attachment; filename=opsmender-audit.csv"
        },
    )
