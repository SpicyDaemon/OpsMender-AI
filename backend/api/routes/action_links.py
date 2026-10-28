"""Signed action links (M1-99): the two calls the confirm page makes.

Both take the token in the body, never in the URL, so it stays out of access
logs. Preview changes nothing. Confirm runs the action once under the
signed-in person's role and records it in the audit log, whether it ran or
was refused.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.auth import get_current_org, get_current_user
from backend.api.deps import get_db
from backend.api.schemas import (
    ActionLinkPreviewResponse,
    ActionLinkRequest,
    ActionLinkResultResponse,
)
from backend.auth.roles import request_role
from backend.bots.action_links import (
    REFUSALS,
    SESSION_REFUSED,
    read_link,
    use_link,
)
from backend.bots.actions import IncidentActionError
from backend.bots.native_callbacks import (
    ERROR_MESSAGES,
    dispatch_native_session_result,
    refresh_incident_card,
)
from backend.db.models import User
from backend.db.repos import AuditEntryRepo
from backend.paging.channel_factory import build_channel_factory

router = APIRouter(prefix="/action-links", tags=["action-links"])

ACTING_ROLES = {"admin", "operator"}
AUDIT_ENTRY_TYPE = "action_link"

ACTION_LABELS = {
    "acknowledge": "Acknowledge",
    "resolve": "Resolve",
    "escalate": "Escalate",
    "start_ai_session": "Start AI session",
}

STATE_MESSAGES = {
    "invalid": "This link is not valid.",
    "channel_off": "The Notification Channel that posted this link is turned off.",
    "used": "This link was already used.",
    "expired": "This link has expired. Open the incident to act there.",
}

REFUSAL_MESSAGES = {
    **ERROR_MESSAGES,
    **{code: STATE_MESSAGES[state] for state, code in REFUSALS.items()},
}

REFUSAL_STATUS = {
    "invalid_action_token": 400,
    "incident_not_found": 404,
    "link_used": 409,
    "connector_disabled": 409,
    SESSION_REFUSED: 409,
    "expired_action_token": 410,
}

# Results that changed something; the others found it already done.
CHANGED = {
    "acknowledged",
    "resolved",
    "escalated",
    "chain_exhausted",
    "session_started",
    "session_queued",
}

RESULT_MESSAGES = {
    "acknowledged": "You acknowledged the incident.",
    "already_acknowledged": "You already hold this incident.",
    "already_owned": "Another responder already owns this incident.",
    "resolved": "You resolved the incident.",
    "already_resolved": "The incident is already resolved.",
    "escalated": "The incident was escalated.",
    "no_escalation_target": (
        "Nothing to escalate: the incident is closed or has no running Escalation "
        "Chain."
    ),
    "chain_exhausted": (
        "No further Escalation Chain level had anyone to page, so the chain ended "
        "and its team was told."
    ),
    "session_started": "An AI session started.",
    "session_queued": "An AI session is queued.",
    "already_active": (
        "This incident already has an AI session (running, queued or waiting "
        "for an approval)."
    ),
}


@router.post(
    "/preview",
    response_model=ActionLinkPreviewResponse,
    summary="Check a signed action link without acting",
)
async def preview_action_link(
    body: ActionLinkRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    org_id: uuid.UUID = Depends(get_current_org),
    user: User = Depends(get_current_user),
):
    link = await read_link(db, request.app.state.config, body.token, org_id=org_id)
    if link.state == "invalid":
        return ActionLinkPreviewResponse(
            state="invalid", message=STATE_MESSAGES["invalid"]
        )
    claims, incident = link.claims, link.incident
    return ActionLinkPreviewResponse(
        state=link.state,
        message=STATE_MESSAGES.get(link.state),
        action=claims.action,
        action_label=ACTION_LABELS[claims.action],
        incident_id=incident.id,
        incident_title=incident.title,
        incident_status=incident.status,
        incident_severity=incident.severity,
        channel_name=link.connector.name,
        expires_at=claims.expires_at,
        can_act=link.state == "ready" and request_role(user) in ACTING_ROLES,
        used_at=link.use.created_at if link.use is not None else None,
        used_result=link.use.result_status if link.use is not None else None,
    )


@router.post(
    "/confirm",
    response_model=ActionLinkResultResponse,
    summary="Run a signed action link's action once",
)
async def confirm_action_link(
    body: ActionLinkRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    org_id: uuid.UUID = Depends(get_current_org),
    user: User = Depends(get_current_user),
):
    config = request.app.state.config
    user_id = user.id
    link = await read_link(db, config, body.token, org_id=org_id)
    claims = link.claims if link.state != "invalid" else None
    audit = {
        "incident_id": str(claims.incident_id) if claims else None,
        "channel_id": str(claims.connector_id) if claims else None,
        "user_id": str(user_id),
    }
    try:
        # A narrowed API token acts with its own role, not the account's.
        if request_role(user) not in ACTING_ROLES:
            raise IncidentActionError("actor_not_authorized")
        result = await use_link(
            db,
            config,
            body.token,
            org_id=org_id,
            user_id=user_id,
            channel_factory=build_channel_factory(),
        )
    except IncidentActionError as exc:
        code = str(exc)
        detail = REFUSAL_MESSAGES.get(code, f"Action refused: {code}.")
        if code == SESSION_REFUSED and exc.__cause__ is not None:
            detail = f"The AI session did not start: {exc.__cause__}"
        await AuditEntryRepo.create(
            db,
            org_id,
            session_id=None,
            tier=0,
            entry_type=AUDIT_ENTRY_TYPE,
            tool_name=claims.action if claims else None,
            tool_parameters=audit,
            permitted=False,
            block_reason=code,
        )
        # Returned, not raised, so the request still commits the audit entry.
        return JSONResponse(
            status_code=REFUSAL_STATUS.get(code, 403),
            content={"detail": detail},
        )
    await AuditEntryRepo.create(
        db,
        org_id,
        session_id=result.session_id,
        tier=0,
        entry_type=AUDIT_ENTRY_TYPE,
        tool_name=result.action,
        tool_parameters=audit,
        result={"status": result.status},
    )
    await dispatch_native_session_result(request.app, db, org_id=org_id, result=result)
    await refresh_incident_card(db, org_id=org_id, result=result)
    return ActionLinkResultResponse(
        action=result.action,
        status=result.status,
        changed=result.status in CHANGED,
        message=RESULT_MESSAGES.get(result.status, result.status.replace("_", " ")),
        incident_id=result.incident_id,
        session_id=result.session_id,
    )
