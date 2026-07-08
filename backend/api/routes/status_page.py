"""Status Page setup, publishing, and public read endpoints."""

from __future__ import annotations

import hashlib
import logging
import secrets
import uuid
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any, Sequence

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, Response, status
from fastapi.responses import HTMLResponse
from jose import JWTError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.auth import decode_access_token, get_current_org, require_role
from backend.api.deps import get_db
from backend.api.schemas import (
    PublicStatusComponent,
    PublicStatusIncident,
    PublicStatusIncidentUpdate,
    PublicStatusResponse,
    StatusPageComponentListResponse,
    StatusPageComponentReplaceRequest,
    StatusPageComponentResponse,
    StatusPageSettingsResponse,
    StatusPageSettingsUpdate,
    StatusPageSubscribeRequest,
    StatusPageSubscriberListResponse,
    StatusPageSubscriberResponse,
    StatusPageUpdateCreate,
    StatusPageUpdateListResponse,
    StatusPageUpdateResponse,
    StatusPageUptimeDay,
)
from backend.db.models import Incident, Organization, StatusPageSubscriber, User
from backend.db.repos import (
    AuditEntryRepo,
    IncidentRepo,
    MaintenanceWindowRepo,
    OrganizationRepo,
    ServiceRepo,
    SLATargetRepo,
    SLORepo,
    StatusPageComponentRepo,
    StatusPageSubscriberRepo,
    StatusPageUpdateRepo,
    UptimeSampleRepo,
    UserRepo,
)
from backend.ingest.rate_limiter import IngestRateLimiter
from backend.reports.email import build_email_channel, resolve_email_settings
from backend.statuspage.service import (
    aggregate_90d_uptime,
    component_status,
    latest_published_states,
    overall_status,
)


router = APIRouter(tags=["status-page"])
logger = logging.getLogger(__name__)

_CACHE_SECONDS = 15.0
_STATUS_CACHE: dict[uuid.UUID, tuple[float, PublicStatusResponse]] = {}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _new_token() -> tuple[str, str]:
    token = secrets.token_urlsafe(32)
    return token, _hash_token(token)


def _invalidate_status_cache(org_id: uuid.UUID) -> None:
    _STATUS_CACHE.pop(org_id, None)


def _base_url(request: Request) -> str:
    config = request.app.state.config
    configured = getattr(config.people, "public_base_url", None)
    if configured:
        return str(configured).rstrip("/")
    return str(request.base_url).rstrip("/")


def _settings_response(org: Organization) -> StatusPageSettingsResponse:
    return StatusPageSettingsResponse(
        enabled=org.status_page_enabled,
        visibility=org.status_page_visibility,
        title=org.status_page_title,
        description=org.status_page_description,
    )


async def _get_org_or_404(db: AsyncSession, org_id: uuid.UUID) -> Organization:
    org = await OrganizationRepo.get_by_id(db, org_id)
    if org is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Organization not found")
    return org


async def _single_org_or_404(db: AsyncSession) -> Organization:
    orgs = list(await OrganizationRepo.list_all(db))
    if not orgs:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Status page not found")
    return orgs[0]


async def _audit_status_page_change(
    db: AsyncSession,
    org_id: uuid.UUID,
    *,
    tool_name: str,
    tool_parameters: dict[str, Any],
    result: dict[str, Any] | None = None,
) -> None:
    await AuditEntryRepo.create(
        db,
        org_id,
        session_id=None,
        tier=0,
        entry_type="status_page_change",
        tool_name=tool_name,
        tool_parameters=tool_parameters,
        result=result or {"ok": True},
        permitted=True,
    )


async def _component_responses(
    db: AsyncSession,
    org_id: uuid.UUID,
) -> list[StatusPageComponentResponse]:
    components = list(await StatusPageComponentRepo.list_for_org(db, org_id))
    services = {service.id: service for service in await ServiceRepo.list_all(db, org_id)}
    return [
        StatusPageComponentResponse(
            id=component.id,
            service_id=component.service_id,
            service_name=services[component.service_id].name,
            display_name=component.display_name,
            sort_order=component.sort_order,
        )
        for component in components
        if component.service_id in services
    ]


async def _require_private_status_auth(
    request: Request,
    db: AsyncSession,
    org_id: uuid.UUID,
) -> None:
    authorization = request.headers.get("authorization") or ""
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Status page not found")
    try:
        payload = decode_access_token(token)
        user_id = uuid.UUID(str(payload.get("sub")))
    except (JWTError, TypeError, ValueError):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Status page not found")
    user = await UserRepo.get_by_id(db, user_id)
    if user is None or not user.is_active or user.primary_org_id != org_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Status page not found")


async def _check_status_rate_limit(request: Request) -> None:
    limiter: IngestRateLimiter | None = getattr(request.app.state, "status_page_limiter", None)
    if limiter is None:
        limiter = getattr(request.app.state, "ingest_limiter", None)
    if limiter is None:
        return
    forwarded = request.headers.get("x-forwarded-for", "")
    client_ip = forwarded.split(",", 1)[0].strip()
    if not client_ip and request.client is not None:
        client_ip = request.client.host
    key = uuid.uuid5(uuid.NAMESPACE_URL, f"status-page:{client_ip or 'unknown'}")
    result = await limiter.check(key)
    if not result.allowed:
        headers = {"Retry-After": str(int(result.retry_after or 1))}
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many requests",
            headers=headers,
        )


def _status_update_response(update) -> StatusPageUpdateResponse:
    return StatusPageUpdateResponse(
        id=update.id,
        incident_id=update.incident_id,
        state=update.state,
        body=update.body,
        author_user_id=update.author_user_id,
        published_at=update.published_at,
    )


def _public_incident(
    incident: Incident,
    updates,
) -> PublicStatusIncident:
    return PublicStatusIncident(
        id=incident.id,
        title=incident.title,
        priority=incident.priority,
        updates=[
            PublicStatusIncidentUpdate(
                state=update.state,
                body=update.body,
                published_at=update.published_at,
            )
            for update in updates
        ],
    )


async def _build_public_status(
    db: AsyncSession,
    org: Organization,
) -> PublicStatusResponse:
    now = _now()
    components = list(await StatusPageComponentRepo.list_for_org(db, org.id))
    services = {service.id: service for service in await ServiceRepo.list_all(db, org.id)}
    service_ids = [component.service_id for component in components if component.service_id in services]

    incidents: Sequence[Incident] = []
    if service_ids:
        stmt = (
            select(Incident)
            .where(
                Incident.org_id == org.id,
                Incident.service_id.in_(service_ids),
                Incident.status.in_(["open", "in_progress", "resolved"]),
            )
            .order_by(Incident.updated_at.desc(), Incident.created_at.desc())
            .limit(500)
        )
        incidents = (await db.execute(stmt)).scalars().all()

    incident_ids = [incident.id for incident in incidents]
    updates = list(await StatusPageUpdateRepo.list_for_incidents(db, org.id, incident_ids))
    published_states = latest_published_states(updates)
    updates_by_incident = defaultdict(list)
    for update in updates:
        updates_by_incident[update.incident_id].append(update)

    active_windows = await MaintenanceWindowRepo.list_active_at(db, org.id, now)
    target_to_service: dict[uuid.UUID, uuid.UUID] = {}
    samples_by_target = {}
    if service_ids:
        targets = await SLATargetRepo.list_all(db, org.id, active_only=True)
        active_slos = await SLORepo.list_all(db, org.id, active_only=True)
        active_slo_target_ids = {slo.target_id for slo in active_slos}
        since = now - timedelta(days=90)
        for target in targets:
            if target.id not in active_slo_target_ids:
                continue
            if target.service_id not in service_ids:
                continue
            target_to_service[target.id] = target.service_id
            samples_by_target[target.id] = await UptimeSampleRepo.query_1h_window(
                db,
                org.id,
                target.id,
                since=since,
                until=now,
            )
    uptime_by_service = aggregate_90d_uptime(
        target_to_service=target_to_service,
        samples_by_target=samples_by_target,
        now=now,
    )

    public_components: list[PublicStatusComponent] = []
    statuses: list[str] = []
    for component in components:
        service = services.get(component.service_id)
        if service is None:
            continue
        derived_status = component_status(
            service_id=component.service_id,
            service_team_id=service.team_id,
            incidents=incidents,
            published_states=published_states,
            active_windows=active_windows,
        )
        statuses.append(derived_status)
        uptime_days = uptime_by_service.get(component.service_id)
        public_components.append(
            PublicStatusComponent(
                service_id=component.service_id,
                display_name=component.display_name or service.name,
                status=derived_status,
                uptime_90d=[
                    StatusPageUptimeDay(date=day.date, pct=day.pct)
                    for day in uptime_days
                ]
                if uptime_days
                else None,
            )
        )

    active_incidents: list[PublicStatusIncident] = []
    recently_resolved: list[PublicStatusIncident] = []
    recent_cutoff = now - timedelta(days=14)
    for incident in incidents:
        incident_updates = updates_by_incident.get(incident.id, [])
        if not incident_updates:
            continue
        latest = incident_updates[0]
        if incident.status in {"open", "in_progress"} and latest.state != "resolved":
            active_incidents.append(_public_incident(incident, incident_updates))
        elif latest.state == "resolved" and _aware(latest.published_at) >= recent_cutoff:
            recently_resolved.append(_public_incident(incident, incident_updates))

    return PublicStatusResponse(
        title=org.status_page_title or org.name,
        description=org.status_page_description,
        overall_status=overall_status(statuses),
        components=public_components,
        active_incidents=active_incidents,
        recently_resolved=recently_resolved,
    )


async def _send_email_batch(
    session_factory,
    config,
    org_id: uuid.UUID,
    *,
    recipients: Sequence[str],
    subject: str,
    body: str,
) -> None:
    if session_factory is None or not recipients:
        return
    try:
        async with session_factory() as db:
            settings = await resolve_email_settings(db, org_id, config=config)
        if settings is None:
            return
        channel = build_email_channel(settings)
        for recipient in recipients:
            attempt = await channel.send(recipient=recipient, subject=subject, body=body)
            if attempt.status != "sent":
                logger.warning(
                    "status_page: email delivery failed for %s: %s",
                    recipient,
                    attempt.error,
                )
    except Exception as exc:  # noqa: BLE001
        logger.warning("status_page: email delivery failed: %s", exc)


async def _send_confirmation_email(
    session_factory,
    config,
    org_id: uuid.UUID,
    *,
    email: str,
    confirm_url: str,
) -> None:
    await _send_email_batch(
        session_factory,
        config,
        org_id,
        recipients=[email],
        subject="Confirm your OpsMender status page subscription",
        body=(
            "Confirm your subscription to status updates:\n\n"
            f"{confirm_url}\n\n"
            "You can ignore this email if you did not request it."
        ),
    )


@router.get(
    "/api/v1/status-page/settings",
    response_model=StatusPageSettingsResponse,
)
async def get_status_page_settings(
    db: AsyncSession = Depends(get_db),
    org_id: uuid.UUID = Depends(get_current_org),
    user: User = Depends(require_role("admin", "operator")),
):
    org = await _get_org_or_404(db, org_id)
    return _settings_response(org)


@router.patch(
    "/api/v1/status-page/settings",
    response_model=StatusPageSettingsResponse,
)
async def update_status_page_settings(
    payload: StatusPageSettingsUpdate,
    db: AsyncSession = Depends(get_db),
    org_id: uuid.UUID = Depends(get_current_org),
    user: User = Depends(require_role("admin")),
):
    visibility = payload.visibility
    title = payload.title.strip() if payload.title is not None else None
    description = (
        payload.description.strip() if payload.description is not None else None
    )
    update_values: dict[str, Any] = {
        "status_page_enabled": payload.enabled,
        "status_page_visibility": visibility,
    }
    if "title" in payload.model_fields_set:
        update_values["status_page_title"] = title or None
    if "description" in payload.model_fields_set:
        update_values["status_page_description"] = description or None
    org = await OrganizationRepo.update(db, org_id, **update_values)
    if org is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Organization not found")
    await _audit_status_page_change(
        db,
        org_id,
        tool_name="status_page.settings",
        tool_parameters=payload.model_dump(exclude_unset=True),
        result={"enabled": org.status_page_enabled, "visibility": org.status_page_visibility},
    )
    await db.commit()
    _invalidate_status_cache(org_id)
    return _settings_response(org)


@router.get(
    "/api/v1/status-page/components",
    response_model=StatusPageComponentListResponse,
)
async def list_status_page_components(
    db: AsyncSession = Depends(get_db),
    org_id: uuid.UUID = Depends(get_current_org),
    user: User = Depends(require_role("admin", "operator")),
):
    items = await _component_responses(db, org_id)
    return StatusPageComponentListResponse(items=items, total=len(items))


@router.put(
    "/api/v1/status-page/components",
    response_model=StatusPageComponentListResponse,
)
async def replace_status_page_components(
    payload: StatusPageComponentReplaceRequest,
    db: AsyncSession = Depends(get_db),
    org_id: uuid.UUID = Depends(get_current_org),
    user: User = Depends(require_role("admin")),
):
    services = {service.id: service for service in await ServiceRepo.list_all(db, org_id)}
    seen: set[uuid.UUID] = set()
    rows: list[dict[str, Any]] = []
    for component in payload.components:
        if component.service_id in seen:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="A service can only appear once on the status page.",
            )
        if component.service_id not in services:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Service not found",
            )
        seen.add(component.service_id)
        rows.append(
            {
                "service_id": component.service_id,
                "display_name": (
                    component.display_name.strip() if component.display_name else None
                ),
            }
        )
    await StatusPageComponentRepo.replace_for_org(db, org_id, rows)
    await _audit_status_page_change(
        db,
        org_id,
        tool_name="status_page.components",
        tool_parameters={"service_ids": [str(row["service_id"]) for row in rows]},
        result={"count": len(rows)},
    )
    await db.commit()
    _invalidate_status_cache(org_id)
    items = await _component_responses(db, org_id)
    return StatusPageComponentListResponse(items=items, total=len(items))


@router.get(
    "/api/v1/status-page/subscribers",
    response_model=StatusPageSubscriberListResponse,
)
async def list_status_page_subscribers(
    db: AsyncSession = Depends(get_db),
    org_id: uuid.UUID = Depends(get_current_org),
    user: User = Depends(require_role("admin")),
):
    subscribers = await StatusPageSubscriberRepo.list_for_org(db, org_id)
    items = [
        StatusPageSubscriberResponse(
            id=subscriber.id,
            email=subscriber.email,
            confirmed_at=subscriber.confirmed_at,
            created_at=subscriber.created_at,
        )
        for subscriber in subscribers
    ]
    return StatusPageSubscriberListResponse(items=items, total=len(items))


@router.delete("/api/v1/status-page/subscribers/{subscriber_id}", status_code=204)
async def delete_status_page_subscriber(
    subscriber_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    org_id: uuid.UUID = Depends(get_current_org),
    user: User = Depends(require_role("admin")),
):
    removed = await StatusPageSubscriberRepo.delete(db, org_id, subscriber_id)
    if not removed:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Subscriber not found")
    await _audit_status_page_change(
        db,
        org_id,
        tool_name="status_page.subscriber.delete",
        tool_parameters={"subscriber_id": str(subscriber_id)},
    )
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get(
    "/api/v1/incidents/{incident_id}/status-updates",
    response_model=StatusPageUpdateListResponse,
)
async def list_incident_status_updates(
    incident_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    org_id: uuid.UUID = Depends(get_current_org),
    user: User = Depends(require_role("admin", "operator")),
):
    incident = await IncidentRepo.get_by_id(db, org_id, incident_id)
    if incident is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Incident not found")
    updates = await StatusPageUpdateRepo.list_for_incident(db, org_id, incident_id)
    items = [_status_update_response(update) for update in updates]
    return StatusPageUpdateListResponse(items=items, total=len(items))


@router.post(
    "/api/v1/incidents/{incident_id}/status-updates",
    response_model=StatusPageUpdateResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_incident_status_update(
    incident_id: uuid.UUID,
    payload: StatusPageUpdateCreate,
    request: Request,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    org_id: uuid.UUID = Depends(get_current_org),
    user: User = Depends(require_role("admin", "operator")),
):
    org = await _get_org_or_404(db, org_id)
    if not org.status_page_enabled:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Status page is disabled")
    incident = await IncidentRepo.get_by_id(db, org_id, incident_id)
    if incident is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Incident not found")
    if incident.service_id is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Incident is not linked to a service component.",
        )
    component = await StatusPageComponentRepo.get_by_service(db, org_id, incident.service_id)
    if component is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Incident service is not on the status page.",
        )
    update = await StatusPageUpdateRepo.create(
        db,
        org_id,
        incident_id=incident_id,
        state=payload.state,
        body=payload.body.strip(),
        author_user_id=user.id,
    )
    await _audit_status_page_change(
        db,
        org_id,
        tool_name="status_page.update.publish",
        tool_parameters={"incident_id": str(incident_id), "state": payload.state},
        result={"update_id": str(update.id)},
    )
    subscribers = await StatusPageSubscriberRepo.list_confirmed(db, org_id)
    await db.commit()
    _invalidate_status_cache(org_id)
    if subscribers:
        status_url = f"{_base_url(request)}/status"
        background_tasks.add_task(
            _send_email_batch,
            request.app.state.session_factory,
            request.app.state.config,
            org_id,
            recipients=[subscriber.email for subscriber in subscribers],
            subject=f"Status update: {incident.title}",
            body=(
                f"{incident.title}\n\n"
                f"State: {payload.state.replace('_', ' ')}\n\n"
                f"{payload.body.strip()}\n\n"
                f"View status: {status_url}"
            ),
        )
    return _status_update_response(update)


@router.get("/api/v1/status", response_model=PublicStatusResponse)
async def get_public_status_page(
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_db),
):
    await _check_status_rate_limit(request)
    org = await _single_org_or_404(db)
    if not org.status_page_enabled:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Status page not found")
    if org.status_page_visibility == "private":
        await _require_private_status_auth(request, db, org.id)

    cached = _STATUS_CACHE.get(org.id)
    now_ts = datetime.now(timezone.utc).timestamp()
    if cached is not None and cached[0] > now_ts:
        response.headers["Cache-Control"] = (
            "public, max-age=15"
            if org.status_page_visibility == "public"
            else "private, max-age=15"
        )
        response.headers["X-Status-Page-Cache"] = "hit"
        return cached[1]

    payload = await _build_public_status(db, org)
    _STATUS_CACHE[org.id] = (now_ts + _CACHE_SECONDS, payload)
    response.headers["Cache-Control"] = (
        "public, max-age=15"
        if org.status_page_visibility == "public"
        else "private, max-age=15"
    )
    response.headers["X-Status-Page-Cache"] = "miss"
    return payload


@router.post("/api/v1/status/subscribe", status_code=status.HTTP_202_ACCEPTED)
async def subscribe_to_status_page(
    payload: StatusPageSubscribeRequest,
    request: Request,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
):
    org = await _single_org_or_404(db)
    if org.status_page_enabled:
        existing = await StatusPageSubscriberRepo.get_by_email(db, org.id, payload.email)
        if existing is None:
            confirm_token, confirm_hash = _new_token()
            _, unsubscribe_hash = _new_token()
            await StatusPageSubscriberRepo.create(
                db,
                org.id,
                email=payload.email,
                confirm_token_hash=confirm_hash,
                unsubscribe_token_hash=unsubscribe_hash,
            )
            await db.commit()
            confirm_url = f"{_base_url(request)}/api/v1/status/confirm?token={confirm_token}"
            background_tasks.add_task(
                _send_confirmation_email,
                request.app.state.session_factory,
                request.app.state.config,
                org.id,
                email=payload.email,
                confirm_url=confirm_url,
            )
        else:
            await db.commit()
    return {"ok": True}


@router.get("/api/v1/status/confirm", response_class=HTMLResponse)
async def confirm_status_subscription(
    token: str,
    db: AsyncSession = Depends(get_db),
):
    subscriber = await StatusPageSubscriberRepo.get_by_confirm_hash(db, _hash_token(token))
    if subscriber is not None:
        await StatusPageSubscriberRepo.mark_confirmed(db, subscriber, now=_now())
        await db.commit()
    return HTMLResponse(
        "<!doctype html><title>Status subscription</title>"
        "<main><h1>Subscription confirmed</h1>"
        "<p>You will receive status updates for this workspace.</p></main>"
    )


@router.get("/api/v1/status/unsubscribe", response_class=HTMLResponse)
async def unsubscribe_status_subscription(
    token: str,
    db: AsyncSession = Depends(get_db),
):
    subscriber: StatusPageSubscriber | None = (
        await StatusPageSubscriberRepo.get_by_unsubscribe_hash(db, _hash_token(token))
    )
    if subscriber is not None:
        await db.delete(subscriber)
        await db.commit()
    return HTMLResponse(
        "<!doctype html><title>Status subscription</title>"
        "<main><h1>Unsubscribed</h1>"
        "<p>You will no longer receive status updates for this workspace.</p></main>"
    )
