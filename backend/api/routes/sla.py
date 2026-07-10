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
    SLORecommendation,
    SLORecommendationsResponse,
    SLASummaryResponse,
    SLATargetCreate,
    SLATargetListResponse,
    SLATargetResponse,
    SLATargetUpdate,
    SLOCreate,
    SLOListResponse,
    SLOResponse,
    SLOStatusResponse,
    SLOUpdate,
    ResponseTimeResponse,
    ResponseTimeSeriesPoint,
    UptimeEpisode,
    UptimeResponse,
    UptimeSeriesPoint,
)
from backend.db.models import SLATarget as SLATargetModel, User
from backend.db.repos import (
    MaintenanceWindowRepo,
    ServiceRepo,
    SLATargetRepo,
    SLORepo,
    TeamRepo,
    UptimeSampleRepo,
)
from backend.sla import metrics
from backend.sla import response_time
from backend.sla.poller import validate_expected_status_config
from backend.sla.recommendations import evaluate_slo_recommendation

router = APIRouter(tags=["reliability"])


# -- Target enrichment ------------------------------------------------------


def _derive_url_and_type(target: SLATargetModel) -> tuple[str | None, str | None]:
    """Pull the monitored URL + monitor type (http/https) from target config."""
    config = target.config or {}
    url = config.get("url") if isinstance(config, dict) else None
    if not url or target.kind != "http":
        return url, None
    monitor_type = "https" if str(url).lower().startswith("https") else "http"
    return url, monitor_type


async def _resolve_service_meta(
    db: AsyncSession,
    org_id: uuid.UUID,
    service_id: uuid.UUID | None,
) -> tuple[str | None, uuid.UUID | None, str | None]:
    """Return (service_name, team_id, team_name) for a target's service link."""
    if service_id is None:
        return None, None, None
    service = await ServiceRepo.get_by_id(db, org_id, service_id)
    if service is None:
        return None, None, None
    team = await TeamRepo.get_by_id(db, org_id, service.team_id)
    return service.name, service.team_id, (team.name if team is not None else None)


async def _enrich_target(
    db: AsyncSession,
    org_id: uuid.UUID,
    target: SLATargetModel,
    *,
    now: datetime,
    slo_count: int,
) -> SLATargetResponse:
    """Build an SLATargetResponse with current status + 30-day uptime."""
    url, monitor_type = _derive_url_and_type(target)
    latest = await UptimeSampleRepo.latest_sample(db, org_id, target.id)
    if latest is None:
        current_status = "unknown"
        last_check_at = None
    else:
        current_status = "up" if latest.up else "down"
        last_check_at = latest.observed_at

    stats = await UptimeSampleRepo.compute_uptime(
        db, org_id, target.id, since=now - timedelta(days=30), until=now
    )
    uptime_30d = stats["uptime_pct"] if latest is not None else None
    service_name, team_id, team_name = await _resolve_service_meta(
        db, org_id, target.service_id
    )

    return SLATargetResponse.model_validate(target).model_copy(
        update={
            "url": url,
            "monitor_type": monitor_type,
            "current_status": current_status,
            "last_check_at": last_check_at,
            "uptime_30d_pct": uptime_30d,
            "active_slo_count": slo_count,
            "service_name": service_name,
            "team_id": team_id,
            "team_name": team_name,
        }
    )


def _compute_slo_status(slo, stats: dict) -> dict:
    """Compute SLO compliance fields from a stats dict (shared by status +
    recommendations). Returns actual_pct, error_budget_remaining_pct,
    burn_rate, compliant."""
    actual_pct = stats["uptime_pct"]
    objective = slo.objective_pct
    error_budget_total = 100.0 - objective
    error_used = 100.0 - actual_pct
    if error_budget_total > 0:
        error_budget_remaining_pct = max(
            0.0, ((error_budget_total - error_used) / error_budget_total) * 100.0
        )
        burn_rate = error_used / error_budget_total
    else:
        error_budget_remaining_pct = 0.0 if error_used > 0 else 100.0
        burn_rate = float("inf") if error_used > 0 else 0.0
    return {
        "actual_pct": round(actual_pct, 4),
        "error_budget_remaining_pct": round(error_budget_remaining_pct, 4),
        "burn_rate": round(burn_rate, 4),
        "compliant": actual_pct >= objective,
    }


async def _service_validated(
    db: AsyncSession, org_id: uuid.UUID, service_id: uuid.UUID | None
) -> None:
    """Raise 400 when a provided service_id doesn't belong to the org."""
    if service_id is None:
        return
    service = await ServiceRepo.get_by_id(db, org_id, service_id)
    if service is None:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, f"Service {service_id} not found"
        )


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
    now = datetime.now(timezone.utc)

    # One SLO query for the whole org; count active SLOs per target.
    slos = await SLORepo.list_all(db, org_id)
    slo_counts: dict[uuid.UUID, int] = {}
    for slo in slos:
        if slo.is_active:
            slo_counts[slo.target_id] = slo_counts.get(slo.target_id, 0) + 1

    enriched = [
        await _enrich_target(db, org_id, t, now=now, slo_count=slo_counts.get(t.id, 0))
        for t in items
    ]
    return SLATargetListResponse(items=enriched, total=len(enriched))


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
    slos = await SLORepo.list_by_target(db, org_id, target_id, active_only=True)
    slo_count = len(slos)
    return await _enrich_target(
        db, org_id, target, now=datetime.now(timezone.utc), slo_count=slo_count
    )


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
    await _service_validated(db, org_id, body.service_id)
    try:
        target = await SLATargetRepo.create(
            db,
            org_id,
            name=body.name,
            kind=body.kind,
            config=body.config,
            owner_team=body.owner_team,
            service_id=body.service_id,
            is_active=body.is_active,
        )
        await db.commit()
        await db.refresh(target)
        return await _enrich_target(
            db, org_id, target, now=datetime.now(timezone.utc), slo_count=0
        )
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
    if "service_id" in body.model_fields_set:
        await _service_validated(db, org_id, body.service_id)

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
            service_id=body.service_id,
            service_id_provided="service_id" in body.model_fields_set,
            is_active=body.is_active,
        )
        await db.commit()
        if updated is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "SLA target not found")
        await db.refresh(updated)
        slos = await SLORepo.list_by_target(db, org_id, target_id, active_only=True)
        return await _enrich_target(
            db, org_id, updated, now=datetime.now(timezone.utc), slo_count=len(slos)
        )
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
    "24h": timedelta(hours=24),
    "7d": timedelta(days=7),
    "30d": timedelta(days=30),
    "90d": timedelta(days=90),
    "365d": timedelta(days=365),
    "1y": timedelta(days=365),
}

# How many strip buckets each window renders into (kept modest so the timeline
# stays a lightweight strip, not a dense chart).
_WINDOW_BUCKETS = {
    "24h": 48,  # half-hour blocks
    "7d": 42,
    "30d": 30,  # daily blocks
    "90d": 45,
    "365d": 52,  # weekly blocks
    "1y": 52,
}

RESPONSE_TIME_WINDOWS = {
    "15m": timedelta(minutes=15),
    "30m": timedelta(minutes=30),
    "1h": timedelta(hours=1),
    "6h": timedelta(hours=6),
    "12h": timedelta(hours=12),
    "24h": timedelta(hours=24),
    "7d": timedelta(days=7),
    "30d": timedelta(days=30),
    "90d": timedelta(days=90),
    "365d": timedelta(days=365),
}

_RESPONSE_TIME_BUCKETS = {
    "15m": 15,
    "30m": 30,
    "1h": 30,
    "6h": 36,
    "12h": 48,
    "24h": 48,
    "7d": 56,
    "30d": 60,
    "90d": 60,
    "365d": 73,
}


@router.get(
    _targets_prefix + "/{target_id}/uptime",
    response_model=UptimeResponse,
    summary="Get uptime statistics for an SLA target",
)
async def get_target_uptime(
    target_id: uuid.UUID,
    window: str = Query("30d", pattern="^(24h|7d|30d|90d|365d|1y)$"),
    start: datetime | None = Query(
        None, description="Custom range start (ISO-8601); overrides window"
    ),
    end: datetime | None = Query(None, description="Custom range end (ISO-8601)"),
    db: AsyncSession = Depends(get_db),
    org_id: uuid.UUID = Depends(get_current_org),
    user: User = Depends(get_current_user),
):
    target = await SLATargetRepo.get_by_id(db, org_id, target_id)
    if target is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "SLA target not found")

    now = datetime.now(timezone.utc)
    if start is not None:
        # Custom range mode. End defaults to now; validate ordering.
        until = end or now
        since = start
        if since >= until:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "start must be before end")
        buckets = 48
    else:
        until = now
        since = now - WINDOW_MAP[window]
        buckets = _WINDOW_BUCKETS.get(window, 48)

    samples = await UptimeSampleRepo.query_window(
        db, org_id, target_id, since=since, until=until
    )
    stats = metrics.uptime_stats(samples)
    series = metrics.history_series(samples, since=since, until=until, buckets=buckets)
    # Newest outage first for the table.
    episodes = list(reversed(metrics.downtime_episodes(samples)))

    return UptimeResponse(
        target_id=target_id,
        uptime_pct=stats["uptime_pct"],
        total_samples=stats["total_samples"],
        up_samples=stats["up_samples"],
        downtime_seconds=stats["downtime_seconds"],
        suppressed_seconds=stats["suppressed_seconds"],
        mtbf_seconds=metrics.mtbf_seconds(samples),
        down_events=metrics.count_down_events(samples),
        series=[UptimeSeriesPoint(**p) for p in series],
        episodes=[UptimeEpisode(**e) for e in episodes],
    )


@router.get(
    _targets_prefix + "/{target_id}/response-time",
    response_model=ResponseTimeResponse,
    summary="Get response-time history for an SLA target",
)
async def get_target_response_time(
    target_id: uuid.UUID,
    window: str = Query(
        "24h",
        pattern="^(15m|30m|1h|6h|12h|24h|7d|30d|90d|365d)$",
    ),
    db: AsyncSession = Depends(get_db),
    org_id: uuid.UUID = Depends(get_current_org),
    user: User = Depends(get_current_user),
):
    target = await SLATargetRepo.get_by_id(db, org_id, target_id)
    if target is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "SLA target not found")

    until = datetime.now(timezone.utc)
    since = until - RESPONSE_TIME_WINDOWS[window]
    points: list[response_time.LatencyPoint] = []

    if window in {"90d", "365d"}:
        rollups = await UptimeSampleRepo.query_1h_window(
            db, org_id, target_id, since=since, until=until
        )
        points.extend(
            {
                "ts": row.bucket_start,
                "avg_latency_ms": float(row.avg_latency_ms),
                "min_latency_ms": row.min_latency_ms,
                "max_latency_ms": row.max_latency_ms,
                "samples": row.latency_samples,
            }
            for row in rollups
            if row.avg_latency_ms is not None
            and row.min_latency_ms is not None
            and row.max_latency_ms is not None
            and row.latency_samples > 0
        )
        # Hourly rollups intentionally exclude the current partial hour. Add
        # its raw samples so the right edge of the graph stays fresh.
        raw_since = max(since, until.replace(minute=0, second=0, microsecond=0))
    elif window in {"7d", "30d"}:
        rollups_5m = await UptimeSampleRepo.query_5m_window(
            db, org_id, target_id, since=since, until=until
        )
        points.extend(
            {
                "ts": row.bucket_start,
                "avg_latency_ms": float(row.avg_latency_ms),
                "min_latency_ms": row.min_latency_ms,
                "max_latency_ms": row.max_latency_ms,
                "samples": row.latency_samples,
            }
            for row in rollups_5m
            if row.avg_latency_ms is not None
            and row.min_latency_ms is not None
            and row.max_latency_ms is not None
            and row.latency_samples > 0
        )
        raw_since = max(
            since,
            until.replace(
                minute=(until.minute // 5) * 5,
                second=0,
                microsecond=0,
            ),
        )
    else:
        raw_since = since

    raw_samples = await UptimeSampleRepo.query_window(
        db, org_id, target_id, since=raw_since, until=until
    )
    points.extend(
        {
            "ts": sample.observed_at,
            "avg_latency_ms": float(sample.latency_ms),
            "min_latency_ms": sample.latency_ms,
            "max_latency_ms": sample.latency_ms,
            "samples": 1,
        }
        for sample in raw_samples
        if sample.latency_ms is not None
    )

    series = response_time.bucket_series(
        points,
        since=since,
        until=until,
        buckets=_RESPONSE_TIME_BUCKETS[window],
    )
    summary = response_time.summarize(series)
    return ResponseTimeResponse(
        target_id=target_id,
        window=window,
        **summary,
        series=[ResponseTimeSeriesPoint(**point) for point in series],
    )


@router.get(
    "/sla-summary",
    response_model=SLASummaryResponse,
    summary="Org-level reliability rollup for the dashboard summary row",
)
async def get_sla_summary(
    db: AsyncSession = Depends(get_db),
    org_id: uuid.UUID = Depends(get_current_org),
    user: User = Depends(get_current_user),
):
    targets = await SLATargetRepo.list_all(db, org_id)
    now = datetime.now(timezone.utc)

    up = down = unknown = 0
    uptimes: list[float] = []
    for t in targets:
        latest = await UptimeSampleRepo.latest_sample(db, org_id, t.id)
        if latest is None:
            unknown += 1
            continue
        if latest.up:
            up += 1
        else:
            down += 1
        stats = await UptimeSampleRepo.compute_uptime(
            db, org_id, t.id, since=now - timedelta(days=30), until=now
        )
        uptimes.append(stats["uptime_pct"])

    avg_uptime = round(sum(uptimes) / len(uptimes), 4) if uptimes else None

    # An "SLO warning" is an active SLO whose current actual uptime over its
    # window is below objective. Warning only — never creates an incident.
    slos = await SLORepo.list_all(db, org_id, active_only=True)
    warnings = 0
    for slo in slos:
        stats = await UptimeSampleRepo.compute_uptime(
            db,
            org_id,
            slo.target_id,
            since=now - timedelta(seconds=slo.window_seconds),
            until=now,
        )
        if stats["uptime_pct"] < float(slo.objective_pct):
            warnings += 1

    return SLASummaryResponse(
        total_targets=len(targets),
        targets_up=up,
        targets_down=down,
        targets_unknown=unknown,
        avg_uptime_30d_pct=avg_uptime,
        active_slo_warnings=warnings,
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
    computed = _compute_slo_status(slo, stats)

    return SLOStatusResponse(
        slo_id=slo.id,
        target_id=slo.target_id,
        name=slo.name,
        objective_pct=slo.objective_pct,
        actual_pct=computed["actual_pct"],
        error_budget_remaining_pct=computed["error_budget_remaining_pct"],
        burn_rate=computed["burn_rate"],
        compliant=computed["compliant"],
    )


@router.get(
    "/sla-recommendations",
    response_model=SLORecommendationsResponse,
    summary="Advisory recommendations for breaching / at-risk SLOs",
)
async def get_slo_recommendations(
    db: AsyncSession = Depends(get_db),
    org_id: uuid.UUID = Depends(get_current_org),
    user: User = Depends(get_current_user),
):
    """Deterministic, advisory-only recommendations for active SLOs that are
    breaching or at risk. Never creates incidents or pages anyone — it surfaces
    what to look at and (when the target is linked to a Service) who owns it.
    """
    now = datetime.now(timezone.utc)
    slos = await SLORepo.list_all(db, org_id, active_only=True)
    # Cache target + service lookups so a busy org isn't N+1'd.
    target_cache: dict[uuid.UUID, SLATargetModel | None] = {}
    items: list[SLORecommendation] = []

    for slo in slos:
        if slo.target_id not in target_cache:
            target_cache[slo.target_id] = await SLATargetRepo.get_by_id(
                db, org_id, slo.target_id
            )
        target = target_cache[slo.target_id]
        if target is None:
            continue

        stats = await UptimeSampleRepo.compute_uptime(
            db,
            org_id,
            slo.target_id,
            since=now - timedelta(seconds=slo.window_seconds),
            until=now,
        )
        computed = _compute_slo_status(slo, stats)

        latest = await UptimeSampleRepo.latest_sample(db, org_id, slo.target_id)
        target_status = "unknown" if latest is None else ("up" if latest.up else "down")
        service_name, team_id, team_name = await _resolve_service_meta(
            db, org_id, target.service_id
        )

        verdict = evaluate_slo_recommendation(
            slo_name=slo.name,
            target_name=target.name,
            objective_pct=slo.objective_pct,
            actual_pct=computed["actual_pct"],
            error_budget_remaining_pct=computed["error_budget_remaining_pct"],
            burn_rate=computed["burn_rate"],
            compliant=computed["compliant"],
            target_status=target_status,
            service_name=service_name,
            team_name=team_name,
        )
        if verdict is None:
            continue

        items.append(
            SLORecommendation(
                slo_id=slo.id,
                slo_name=slo.name,
                target_id=target.id,
                target_name=target.name,
                severity=verdict.severity,
                objective_pct=slo.objective_pct,
                actual_pct=computed["actual_pct"],
                error_budget_remaining_pct=computed["error_budget_remaining_pct"],
                burn_rate=computed["burn_rate"],
                target_status=target_status,
                service_id=target.service_id,
                service_name=service_name,
                team_id=team_id,
                team_name=team_name,
                headline=verdict.headline,
                actions=verdict.actions,
            )
        )

    # Critical first, then warnings; stable within severity.
    order = {"critical": 0, "warning": 1}
    items.sort(key=lambda r: order.get(r.severity, 99))
    return SLORecommendationsResponse(items=items, total=len(items), generated_at=now)


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
    user: User = Depends(require_role("admin", "operator")),
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

    # Admin-created windows are approved immediately; operator requests are
    # pending until an admin explicitly approves them.
    is_admin = user.role == "admin"
    now = datetime.now(timezone.utc)

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
        approved=is_admin,
        approved_by=user.id if is_admin else None,
        approved_at=now if is_admin else None,
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


@router.post(
    _mw_prefix + "/{mw_id}/approve",
    response_model=MaintenanceWindowResponse,
    summary="Approve a pending maintenance window request (admin only)",
)
async def approve_maintenance_window(
    mw_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    org_id: uuid.UUID = Depends(get_current_org),
    user: User = Depends(require_role("admin")),
):
    mw = await MaintenanceWindowRepo.get_by_id(db, org_id, mw_id)
    if mw is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Maintenance window not found")
    if mw.approved:
        raise HTTPException(
            status.HTTP_409_CONFLICT, "Maintenance window is already approved"
        )
    now = datetime.now(timezone.utc)
    mw.approved = True
    mw.approved_by = user.id
    mw.approved_at = now
    await db.commit()
    await db.refresh(mw)
    return MaintenanceWindowResponse.model_validate(mw)


@router.post(
    _mw_prefix + "/{mw_id}/reject",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Reject and delete a pending maintenance window request (admin only)",
)
async def reject_maintenance_window(
    mw_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    org_id: uuid.UUID = Depends(get_current_org),
    user: User = Depends(require_role("admin")),
):
    mw = await MaintenanceWindowRepo.get_by_id(db, org_id, mw_id)
    if mw is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Maintenance window not found")
    if mw.approved:
        raise HTTPException(
            status.HTTP_409_CONFLICT, "Cannot reject an already-approved window"
        )
    deleted = await MaintenanceWindowRepo.delete(db, org_id, mw_id)
    if not deleted:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Maintenance window not found")
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
