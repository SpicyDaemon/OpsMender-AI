"""Audit run orchestrator (Sprint 32).

``run_audit`` takes a queued ``AuditRun`` row, fans out across the requested
analyzers, persists each ``FindingDraft`` as an ``AuditFinding``, and
transitions the run to ``completed`` (or ``failed`` if every analyzer raised).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from backend.auditor.base import AnalyzerContext, FindingDraft
from backend.auditor.registry import get_analyzer
from backend.config_loader import AppConfig
from backend.db.repos import AuditFindingRepo, AuditRunRepo
from backend.mcp.pool import MCPServerPool


async def run_audit(
    db: AsyncSession,
    *,
    run_id: uuid.UUID,
    org_id: uuid.UUID,
    pool: MCPServerPool,
    config: AppConfig,
    analyzer_params: dict[str, dict] | None = None,
) -> int:
    """Execute the analyzers attached to ``run_id``.

    Returns the number of persisted findings. Per-analyzer failures are
    captured into a single ``info``-severity finding tagged with the
    analyzer key so the operator can see what went wrong without the whole
    run being marked failed.
    """

    run = await AuditRunRepo.get_by_id(db, org_id, run_id)
    if run is None:
        raise ValueError(f"Audit run not found: {run_id}")

    analyzer_keys = list(run.analyzers or [])
    if not analyzer_keys:
        await AuditRunRepo.update_status(
            db,
            org_id,
            run_id,
            status="failed",
            started_at=datetime.now(timezone.utc),
            finished_at=datetime.now(timezone.utc),
            error="No analyzers configured",
            finding_count=0,
        )
        return 0

    started = datetime.now(timezone.utc)
    await AuditRunRepo.update_status(
        db, org_id, run_id, status="running", started_at=started
    )

    analyzer_params = analyzer_params or {}
    total: int = 0
    all_errored = True

    for key in analyzer_keys:
        analyzer = get_analyzer(key)
        if analyzer is None:
            await AuditFindingRepo.create(
                db,
                org_id,
                run_id=run_id,
                analyzer=key,
                severity="info",
                message=f"Unknown analyzer: {key}",
            )
            total += 1
            continue

        ctx = AnalyzerContext(
            db=db,
            org_id=org_id,
            pool=pool,
            config=config,
            params=analyzer_params.get(key, {}),
        )
        try:
            drafts = await analyzer.run(ctx)
            all_errored = False
        except Exception as exc:  # noqa: BLE001 - analyzers are 3rd-party-ish
            drafts = [
                FindingDraft(
                    analyzer=key,
                    severity="info",
                    message=f"Analyzer {key} failed: {exc}",
                )
            ]

        for draft in drafts:
            await AuditFindingRepo.create(
                db,
                org_id,
                run_id=run_id,
                analyzer=draft.analyzer or key,
                severity=draft.normalized_severity(),
                message=draft.message,
                category=draft.category,
                resource=draft.resource,
                suggested_fix=draft.suggested_fix,
            )
            total += 1

    finished = datetime.now(timezone.utc)
    status = "failed" if all_errored else "completed"
    await AuditRunRepo.update_status(
        db,
        org_id,
        run_id,
        status=status,
        finished_at=finished,
        finding_count=total,
    )
    return total
