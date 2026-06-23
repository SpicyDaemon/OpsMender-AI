"""Admin management for external integration connectors."""

from __future__ import annotations

import time
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

import backend.integrations  # noqa: F401 - register bundled adapters
from backend.api.auth import get_current_org, require_role
from backend.api.deps import get_db
from backend.api.schemas import (
    IntegrationCapabilityResponse,
    IntegrationConnectorListResponse,
    IntegrationConnectorResponse,
    IntegrationConnectorUpsert,
    IntegrationFieldOptionResponse,
    IntegrationFieldResponse,
    IntegrationKindListResponse,
    IntegrationKindResponse,
    IntegrationTestResponse,
)
from backend.db.models import IntegrationConnector, User
from backend.db.repos import (
    AUTH_UNSET,
    IntegrationConnectorRepo,
    TicketSyncStateRepo,
)
from backend.integrations.base import IntegrationFieldSpec
from backend.integrations.registry import (
    config_fields,
    credential_fields_by_auth,
    get_adapter,
    get_kind,
    list_kinds,
)

router = APIRouter(prefix="/integrations", tags=["integrations"])


def _field_response(field: IntegrationFieldSpec) -> IntegrationFieldResponse:
    return IntegrationFieldResponse(
        name=field.name,
        label=field.label,
        kind=field.kind,
        group=field.group,
        required=field.required,
        helper=field.helper,
        placeholder=field.placeholder,
        doc_url=field.doc_url,
        options=[
            IntegrationFieldOptionResponse(value=value, label=label)
            for value, label in field.options
        ],
        default=field.default,
    )


def _response(row: IntegrationConnector) -> IntegrationConnectorResponse:
    auth_keys: list[str] = []
    if row.auth_encrypted:
        try:
            auth_keys = sorted(IntegrationConnectorRepo.decrypt_auth(row))
        except (ValueError, TypeError):
            auth_keys = []
    return IntegrationConnectorResponse(
        id=row.id,
        org_id=row.org_id,
        kind=row.kind,
        name=row.name,
        base_url=row.base_url,
        auth_type=row.auth_type,
        auth_keys=auth_keys,
        has_auth=bool(row.auth_encrypted),
        config=row.config or {},
        is_enabled=row.is_enabled,
        status=row.status,
        last_checked_at=row.last_checked_at,
        last_error=row.last_error,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _validate_body(body: IntegrationConnectorUpsert) -> None:
    definition = get_kind(body.kind)
    if definition is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unsupported integration kind: {body.kind}",
        )
    if body.auth_type not in definition.auth_types:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"Authentication type '{body.auth_type}' is not supported "
                f"for {definition.label}"
            ),
        )
    if body.auth is not None and body.clear_auth:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Provide auth or clear_auth, not both",
        )


@router.get("/kinds", response_model=IntegrationKindListResponse)
async def list_integration_kinds(
    user: User = Depends(require_role("admin")),
):
    items: list[IntegrationKindResponse] = []
    for definition in list_kinds():
        adapter = get_adapter(definition.kind)
        items.append(
            IntegrationKindResponse(
                kind=definition.kind,
                label=definition.label,
                supports_base_url=definition.supports_base_url,
                auth_types=list(definition.auth_types),
                adapter_available=adapter is not None,
                capabilities=[
                    IntegrationCapabilityResponse(
                        action=capability.action,
                        description=capability.description,
                        classification=capability.classification,
                        mutating=capability.mutating,
                        always_requires_approval=(
                            capability.always_requires_approval
                        ),
                    )
                    for capability in (() if adapter is None else adapter.capabilities)
                ],
                credential_fields={
                    auth_type: [_field_response(field) for field in fields]
                    for auth_type, fields in credential_fields_by_auth(
                        definition.kind
                    ).items()
                },
                config_fields=[
                    _field_response(field)
                    for field in config_fields(definition.kind)
                ],
            )
        )
    return IntegrationKindListResponse(items=items, total=len(items))


@router.get("", response_model=IntegrationConnectorListResponse)
async def list_integration_connectors(
    kind: str | None = Query(default=None),
    enabled_only: bool = Query(default=False),
    db: AsyncSession = Depends(get_db),
    org_id: uuid.UUID = Depends(get_current_org),
    user: User = Depends(require_role("admin")),
):
    rows = await IntegrationConnectorRepo.list_for_org(
        db, org_id, enabled_only=enabled_only, kind=kind
    )
    return IntegrationConnectorListResponse(
        items=[_response(row) for row in rows],
        total=len(rows),
    )


@router.post(
    "",
    response_model=IntegrationConnectorResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_integration_connector(
    body: IntegrationConnectorUpsert,
    db: AsyncSession = Depends(get_db),
    org_id: uuid.UUID = Depends(get_current_org),
    user: User = Depends(require_role("admin")),
):
    _validate_body(body)
    try:
        row = await IntegrationConnectorRepo.create(
            db,
            org_id,
            kind=body.kind,
            name=body.name,
            base_url=body.base_url,
            auth_type=body.auth_type,
            auth=body.auth,
            config=body.config,
            is_enabled=body.is_enabled,
        )
        await db.commit()
        await db.refresh(row)
        return _response(row)
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Integration connector name already exists",
        ) from exc


@router.put(
    "/{connector_id}",
    response_model=IntegrationConnectorResponse,
)
async def update_integration_connector(
    connector_id: uuid.UUID,
    body: IntegrationConnectorUpsert,
    db: AsyncSession = Depends(get_db),
    org_id: uuid.UUID = Depends(get_current_org),
    user: User = Depends(require_role("admin")),
):
    _validate_body(body)
    row = await IntegrationConnectorRepo.get_by_id(db, org_id, connector_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Integration connector not found")
    auth: dict | None | object
    if body.clear_auth:
        auth = None
    elif body.auth is not None:
        auth = body.auth
    else:
        auth = AUTH_UNSET
    try:
        await IntegrationConnectorRepo.update(
            db,
            row,
            kind=body.kind,
            name=body.name,
            base_url=body.base_url,
            auth_type=body.auth_type,
            auth=auth,
            config=body.config,
            is_enabled=body.is_enabled,
        )
        status_map = body.config.get("status_map")
        if isinstance(status_map, dict):
            await TicketSyncStateRepo.update_status_map_for_connector(
                db,
                row.id,
                status_map,
            )
        await db.commit()
        await db.refresh(row)
        return _response(row)
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Integration connector name already exists",
        ) from exc


@router.delete("/{connector_id}", status_code=204)
async def delete_integration_connector(
    connector_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    org_id: uuid.UUID = Depends(get_current_org),
    user: User = Depends(require_role("admin")),
):
    if not await IntegrationConnectorRepo.delete(db, org_id, connector_id):
        raise HTTPException(status_code=404, detail="Integration connector not found")
    await db.commit()
    return Response(status_code=204)


@router.post(
    "/{connector_id}/test",
    response_model=IntegrationTestResponse,
)
async def test_integration_connector(
    connector_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    org_id: uuid.UUID = Depends(get_current_org),
    user: User = Depends(require_role("admin")),
):
    row = await IntegrationConnectorRepo.get_by_id(db, org_id, connector_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Integration connector not found")
    adapter = get_adapter(row.kind)
    start = time.monotonic()
    if adapter is None:
        result_error = f"No adapter is installed for {row.kind}"
        await IntegrationConnectorRepo.mark_status(
            db, row, status="error", error=result_error
        )
        await db.commit()
        return IntegrationTestResponse(success=False, detail=result_error)
    try:
        auth = IntegrationConnectorRepo.decrypt_auth(row)
    except ValueError as exc:
        result_error = str(exc)
        await IntegrationConnectorRepo.mark_status(
            db, row, status="error", error=result_error
        )
        await db.commit()
        return IntegrationTestResponse(success=False, detail=result_error)
    result = await adapter.safe_invoke("test_connection", row, auth)
    latency_ms = int((time.monotonic() - start) * 1000)
    detail = (
        str(result.data.get("detail") or "Connection successful.")
        if result.ok
        else (result.error or "Connection failed.")
    )
    await IntegrationConnectorRepo.mark_status(
        db,
        row,
        status="healthy" if result.ok else "error",
        error=None if result.ok else detail,
    )
    await db.commit()
    return IntegrationTestResponse(
        success=result.ok,
        detail=detail,
        latency_ms=latency_ms,
    )
