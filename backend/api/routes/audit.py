"""Audit endpoints.

GET /audit - query audit entries with filtering and pagination.
"""

from __future__ import annotations

import csv
import io
import json
import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.auth import get_current_org, get_current_user, require_role
from backend.api.deps import get_db
from backend.api.schemas import AuditEntryResponse, AuditListResponse
from backend.audit.logger import AuditEntryType
from backend.audit.tool_results import protect_tool_result
from backend.auth.roles import request_role
from backend.db.models import AuditEntry, User
from backend.db.repos import AuditEntryRepo

router = APIRouter(prefix="/audit", tags=["audit"])

_TOOL_RESULT_TYPES = {
    AuditEntryType.TOOL_CALL_END.value,
    AuditEntryType.WORKFLOW_STEP_COMPLETED.value,
    AuditEntryType.WORKFLOW_STEP_BLOCKED.value,
    AuditEntryType.WORKFLOW_STEP_FAILED.value,
}


def _visible_result(entry: AuditEntry, *, can_read: bool = True) -> dict | None:
    if not can_read or entry.result is None:
        return None
    if entry.entry_type in _TOOL_RESULT_TYPES:
        # Earlier versions could store raw tool results.
        return protect_tool_result(entry.result)
    return entry.result


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
    can_read = request_role(user) in {"admin", "operator"}
    return AuditListResponse(
        items=[
            AuditEntryResponse.model_validate(entry).model_copy(
                update={"result": _visible_result(entry, can_read=can_read)}
            )
            for entry in items
        ],
        total=len(all_items),
    )


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
    user: User = Depends(require_role("admin", "operator")),
) -> Response:
    """Flatten the AI action-history audit to CSV for spreadsheets.

    Admins and operators only (O-07); Viewers still read the Activity page.

    Tool parameters and protected results are serialized into JSON-string
    cells. Honors the same filters as ``GET /audit``.
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
        result = _visible_result(e)
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
                json.dumps(result) if result else "",
            ]
        )

    return Response(
        content=buf.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=opsmender-audit.csv"},
    )
