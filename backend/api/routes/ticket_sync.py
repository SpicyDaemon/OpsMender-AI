"""Signed inbound webhooks for Jira and ServiceNow ticket synchronization."""

from __future__ import annotations

import hashlib
import hmac
import json
import uuid
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.deps import get_db
from backend.api.schemas import TicketSyncWebhookResponse
from backend.api.session_runner import stop_incident_sessions
from backend.db.repos import (
    IncidentCommentRepo,
    IncidentRepo,
    IntegrationConnectorRepo,
    TicketSyncStateRepo,
)
from backend.services.ticket_sync import normalized_status_map, reverse_status

router = APIRouter(prefix="/webhooks/ticket-sync", tags=["ticket-sync"])


def _jira_signature_valid(secret: str, body: bytes, provided: str | None) -> bool:
    if not provided:
        return False
    expected = (
        "sha256="
        + hmac.new(
            secret.encode("utf-8"),
            body,
            hashlib.sha256,
        ).hexdigest()
    )
    candidate = provided if provided.startswith("sha256=") else f"sha256={provided}"
    return hmac.compare_digest(expected, candidate)


def _jira_fields(payload: dict[str, Any]) -> tuple[str | None, str | None]:
    issue = payload.get("issue") if isinstance(payload.get("issue"), dict) else {}
    fields = issue.get("fields") if isinstance(issue.get("fields"), dict) else {}
    status_value = (
        fields.get("status") if isinstance(fields.get("status"), dict) else {}
    )
    return (
        str(issue.get("key") or issue.get("id") or "") or None,
        str(status_value.get("name") or status_value.get("id") or "") or None,
    )


def _servicenow_fields(payload: dict[str, Any]) -> tuple[str | None, str | None]:
    value = (
        payload.get("result") if isinstance(payload.get("result"), dict) else payload
    )
    current = value.get("current") if isinstance(value.get("current"), dict) else value
    return (
        str(current.get("sys_id") or value.get("sys_id") or "") or None,
        str(current.get("state") or value.get("state") or "") or None,
    )


@router.post(
    "/{connector_id}",
    response_model=TicketSyncWebhookResponse,
)
async def receive_ticket_sync(
    connector_id: uuid.UUID,
    request: Request,
    webhook_token: str | None = Query(default=None),
    x_hub_signature: str | None = Header(default=None, alias="X-Hub-Signature"),
    x_hub_signature_256: str | None = Header(
        default=None,
        alias="X-Hub-Signature-256",
    ),
    db: AsyncSession = Depends(get_db),
):
    connector = await IntegrationConnectorRepo.get_by_id_unscoped(db, connector_id)
    if (
        connector is None
        or connector.kind not in {"jira", "servicenow"}
        or not connector.is_enabled
        or not connector.config.get("ticket_sync_enabled")
    ):
        raise HTTPException(status_code=404, detail="Ticket sync connector not found")

    try:
        auth = IntegrationConnectorRepo.decrypt_auth(connector)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Ticket sync credentials cannot be decrypted",
        ) from exc

    raw = await request.body()
    if connector.kind == "jira":
        secret = str(auth.get("webhook_secret") or "")
        if not secret or not _jira_signature_valid(
            secret,
            raw,
            x_hub_signature_256 or x_hub_signature,
        ):
            raise HTTPException(status_code=401, detail="Invalid webhook signature")
    else:
        expected = str(auth.get("webhook_token") or "")
        if (
            not expected
            or not webhook_token
            or not hmac.compare_digest(
                expected,
                webhook_token,
            )
        ):
            raise HTTPException(status_code=401, detail="Invalid webhook token")

    try:
        payload = json.loads(raw)
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail="Invalid JSON payload") from exc
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="Invalid webhook payload")

    external_id, external_status = (
        _jira_fields(payload)
        if connector.kind == "jira"
        else _servicenow_fields(payload)
    )
    if not external_id or not external_status:
        raise HTTPException(
            status_code=422,
            detail="Webhook payload is missing ticket id or status",
        )

    sync_state = await TicketSyncStateRepo.get_by_external_ticket(
        db,
        connector.org_id,
        connector.id,
        external_id,
    )
    if sync_state is None:
        raise HTTPException(status_code=404, detail="Ticket sync state not found")
    status_map = normalized_status_map(connector.kind, sync_state.status_map)
    incident_status = reverse_status(status_map, external_status)
    if incident_status is None:
        raise HTTPException(
            status_code=422,
            detail=f"External status '{external_status}' is not mapped",
        )

    incident = await IncidentRepo.get_by_id(
        db,
        sync_state.org_id,
        sync_state.incident_id,
    )
    if incident is None:
        raise HTTPException(status_code=404, detail="Incident not found")
    if incident.status == "merged":
        raise HTTPException(
            status_code=409,
            detail="Merged incidents cannot be updated by ticket sync",
        )
    previous = incident.status
    updated = previous != incident_status
    if updated:
        await IncidentRepo.update_status(
            db,
            sync_state.org_id,
            sync_state.incident_id,
            incident_status,
        )
        await IncidentCommentRepo.create(
            db,
            sync_state.org_id,
            incident_id=sync_state.incident_id,
            author_user_id=None,
            source="ticket_sync",
            body=(
                f"Ticket sync ({connector.kind}) moved incident status "
                f"from {previous} to {incident_status} after external ticket "
                f"{external_id} changed to {external_status}."
            ),
        )
        if incident_status == "resolved":
            await stop_incident_sessions(
                request.app,
                db,
                sync_state.org_id,
                sync_state.incident_id,
                reason=f"Incident resolved by ticket sync ({connector.kind})",
            )
    await TicketSyncStateRepo.mark_synced(db, sync_state, direction="inbound")
    await db.commit()
    return TicketSyncWebhookResponse(
        incident_id=sync_state.incident_id,
        status=incident_status,
        updated=updated,
    )
