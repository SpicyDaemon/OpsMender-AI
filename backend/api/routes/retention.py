"""Sprint 53 — data retention / garbage collection routes.

Endpoints (admin-only for writes; admin-or-operator can read):

- ``GET    /retention``         — per-category status, defaults, last-run stamps, storage estimates.
- ``PUT    /retention``         — bulk set TTLs (ttl_days = null disables a category).
- ``POST   /retention/run``     — manual one-shot pruner run for the active org.
"""

from __future__ import annotations

import uuid

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.auth import get_current_org, require_role
from backend.api.deps import get_db
from backend.api.schemas import (
    RetentionCategoryConfig,
    RetentionCategoryStorage,
    RetentionRunReportItem,
    RetentionRunReportResponse,
    RetentionStatusResponse,
    RetentionUpdateRequest,
)
from backend.db.models import User
from backend.db.repos import (
    DEFAULT_RETENTION_TTL_DAYS,
    RETENTION_CATEGORIES,
    RetentionConfigRepo,
)
from backend.retention.pruner import (
    estimate_storage_for_org,
    prune_org,
)
from backend.retention.scheduler import retention_enabled_from_env
from backend.services.audit_archiver import AuditArchiver

router = APIRouter(prefix="/retention", tags=["retention"])


def _to_config_items(
    rows: dict[str, dict],
    *,
    audit_default_ttl_days: int = DEFAULT_RETENTION_TTL_DAYS,
) -> list[RetentionCategoryConfig]:
    items: list[RetentionCategoryConfig] = []
    for category in RETENTION_CATEGORIES:
        row = rows.get(category)
        if row is None:
            default_ttl_days = (
                audit_default_ttl_days
                if category == "audit_entries"
                else DEFAULT_RETENTION_TTL_DAYS
            )
            items.append(
                RetentionCategoryConfig(
                    category=category,
                    ttl_days=default_ttl_days,
                    last_pruned_at=None,
                    last_pruned_count=None,
                    is_default=True,
                )
            )
        else:
            items.append(
                RetentionCategoryConfig(
                    category=category,
                    ttl_days=row["ttl_days"],
                    last_pruned_at=row["last_pruned_at"],
                    last_pruned_count=row["last_pruned_count"],
                    is_default=False,
                )
            )
    return items


@router.get(
    "",
    response_model=RetentionStatusResponse,
    summary="Per-category TTL + storage estimate for the active org",
)
async def get_retention_status(
    request: Request,
    db: AsyncSession = Depends(get_db),
    org_id: uuid.UUID = Depends(get_current_org),
    # Admin or operator can read; viewers don't need this.
    user: User = Depends(require_role("admin", "operator")),
):
    existing = await RetentionConfigRepo.list_for_org(db, org_id)
    rows_by_category = {
        r.category: {
            "ttl_days": r.ttl_days,
            "last_pruned_at": r.last_pruned_at,
            "last_pruned_count": r.last_pruned_count,
        }
        for r in existing
    }
    storage = await estimate_storage_for_org(db, org_id)
    storage_items = [
        RetentionCategoryStorage(
            category=category,
            row_count=data["row_count"],
            estimated_bytes=data["estimated_bytes"],
            avg_bytes_per_row=data["avg_bytes_per_row"],
            non_prunable=bool(data.get("non_prunable", False)),
        )
        for category, data in storage.items()
    ]
    return RetentionStatusResponse(
        default_ttl_days=DEFAULT_RETENTION_TTL_DAYS,
        scheduler_enabled=retention_enabled_from_env(),
        last_run_at=None,  # Scheduler-instance state isn't request-scoped.
        configs=_to_config_items(
            rows_by_category,
            audit_default_ttl_days=request.app.state.config.audit.retention_days,
        ),
        storage=storage_items,
    )


@router.put(
    "",
    response_model=RetentionStatusResponse,
    summary="Update per-category retention TTLs (admin only)",
)
async def update_retention(
    request: Request,
    body: RetentionUpdateRequest,
    db: AsyncSession = Depends(get_db),
    org_id: uuid.UUID = Depends(get_current_org),
    user: User = Depends(require_role("admin")),
):
    # Validate categories up front so a bad payload doesn't half-apply.
    for item in body.configs:
        if item.category not in RETENTION_CATEGORIES:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Unknown retention category: {item.category}",
            )
        if item.ttl_days is not None and item.ttl_days < 1:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="ttl_days must be NULL (disabled) or >= 1",
            )

    for item in body.configs:
        await RetentionConfigRepo.upsert(
            db,
            org_id,
            category=item.category,
            ttl_days=item.ttl_days,
            updated_by_user_id=user.id,
        )
    await db.commit()
    # Reuse the GET handler's projection logic.
    return await get_retention_status(request=request, db=db, org_id=org_id, user=user)


@router.post(
    "/run",
    response_model=RetentionRunReportResponse,
    summary="Run the pruner immediately for the active org (admin only)",
)
async def run_retention_now(
    request: Request,
    db: AsyncSession = Depends(get_db),
    org_id: uuid.UUID = Depends(get_current_org),
    user: User = Depends(require_role("admin")),
):
    started_at = datetime.now(timezone.utc)
    audit_ttl = await RetentionConfigRepo.effective_ttl_days(
        db,
        org_id,
        "audit_entries",
        default_ttl_days=request.app.state.config.audit.retention_days,
    )
    archive_report = await AuditArchiver(
        request.app.state.config.audit
    ).archive_and_prune(db, org_id=org_id)
    report = await prune_org(db, org_id, skip_categories={"audit_entries"})
    items = [
        RetentionRunReportItem(
            category="audit_entries",
            ttl_days=audit_ttl,
            cutoff=(
                started_at - timedelta(days=audit_ttl)
                if audit_ttl is not None
                else None
            ),
            deleted_count=archive_report.deleted_count,
            skipped_reason="disabled" if audit_ttl is None else None,
            error="; ".join(archive_report.errors) or None,
        ),
        *[
            RetentionRunReportItem(
                category=r.category,
                ttl_days=r.ttl_days,
                cutoff=r.cutoff,
                deleted_count=r.deleted_count,
                skipped_reason=r.skipped_reason,
                error=r.error,
            )
            for r in report.results
            if r.category != "audit_entries"
        ],
    ]
    finished_at = datetime.now(timezone.utc)
    return RetentionRunReportResponse(
        started_at=started_at,
        finished_at=finished_at,
        total_deleted=sum(item.deleted_count for item in items),
        total_errors=sum(1 for item in items if item.error is not None),
        items=items,
    )
