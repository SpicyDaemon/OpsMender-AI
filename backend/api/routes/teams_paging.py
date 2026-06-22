"""Microsoft Teams Bot Framework activity endpoint for verified actions."""

from __future__ import annotations

import hashlib
import json
import logging
from typing import Any

from fastapi import APIRouter, Depends, Request, status
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.deps import get_db
from backend.auth.bot_framework import (
    BotFrameworkAuthError,
    verify_bot_framework_token,
)
from backend.bots.actions import ExternalActorIdentity, IncidentActionError
from backend.bots.native_callbacks import (
    NormalizedNativeCallback,
    callback_error_message,
    callback_result_message,
    dispatch_native_session_result,
    execute_normalized_callback,
)
from backend.db.models import BotConnector
from backend.db.repos import IncidentRepo
from backend.paging.teams_cards import ACTION_VIEW, parse_incident_id_from_action


logger = logging.getLogger(__name__)
router = APIRouter(prefix="/bot/teams", tags=["teams-paging"])


def _reply(text: str) -> JSONResponse:
    return JSONResponse({"type": "message", "text": text})


def _bot_app_id(connector: BotConnector) -> str:
    return str(
        (connector.config or {}).get("bot_app_id")
        or (connector.credentials or {}).get("bot_app_id")
        or ""
    ).strip()


async def _find_teams_connector_for_activity(
    db: AsyncSession,
    *,
    authorization: str | None,
    recipient_id: str | None,
) -> BotConnector | None:
    """Match the activity recipient to an enabled connector and verify JWT."""

    if not recipient_id:
        return None
    recipient_app_id = recipient_id.split(":", 1)[-1]
    rows = (
        await db.execute(
            select(BotConnector).where(
                BotConnector.platform == "teams",
                BotConnector.is_enabled.is_(True),
            )
        )
    ).scalars().all()
    for connector in rows:
        candidate = _bot_app_id(connector)
        if not candidate or candidate != recipient_app_id:
            continue
        try:
            await verify_bot_framework_token(
                authorization=authorization,
                expected_audience=candidate,
            )
        except BotFrameworkAuthError as exc:
            logger.warning("Teams JWT verify failed: %s", exc)
            return None
        return connector
    return None


@router.post("/activity", summary="Receive Teams adaptive-card actions")
async def teams_activity(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    raw_body = await request.body()
    try:
        payload: dict[str, Any] = json.loads(raw_body)
    except (TypeError, ValueError):
        return JSONResponse(
            {"error": "invalid_json"},
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    connector = await _find_teams_connector_for_activity(
        db,
        authorization=request.headers.get("authorization"),
        recipient_id=(payload.get("recipient") or {}).get("id"),
    )
    if connector is None:
        return JSONResponse(
            {"error": "unauthorized"},
            status_code=status.HTTP_403_FORBIDDEN,
        )

    value = payload.get("value")
    if not isinstance(value, dict):
        value = {}
    incident_id = parse_incident_id_from_action(value)
    if incident_id is None:
        return _reply("Couldn't identify the incident from that action.")

    wrapped_value = value.get("value")
    if not isinstance(wrapped_value, dict):
        wrapped_value = {}
    action_id = value.get("action") or wrapped_value.get("action")
    if action_id == ACTION_VIEW:
        return _reply("Opening OpsMender...")

    from_field = payload.get("from") or {}
    teams_user_id = from_field.get("aadObjectId") or from_field.get("id")
    if not teams_user_id:
        return _reply("Teams didn't tell us who clicked.")

    conversation_id = str(
        (payload.get("conversation") or {}).get("id") or ""
    ) or None
    message_id = str(payload.get("replyToId") or payload.get("id") or "") or None
    idempotency_key = str(
        payload.get("id") or hashlib.sha256(raw_body).hexdigest()
    )
    try:
        result = await execute_normalized_callback(
            db,
            connector=connector,
            callback=NormalizedNativeCallback(
                incident_id=incident_id,
                action_id=str(action_id or ""),
                external_actor=ExternalActorIdentity(
                    platform_user_id=str(teams_user_id),
                    username=from_field.get("name"),
                    display_name=from_field.get("name"),
                ),
                idempotency_key=idempotency_key,
                channel_id=conversation_id,
                message_id=message_id,
            ),
            config=request.app.state.config,
        )
    except IncidentActionError as exc:
        message = callback_error_message(exc).replace(
            "Your external account",
            "Your Teams account",
        )
        return _reply(message)

    await dispatch_native_session_result(
        request.app,
        db,
        org_id=connector.org_id,
        result=result,
    )
    incident = await IncidentRepo.get_by_id(db, connector.org_id, incident_id)
    title = incident.title if incident is not None else str(incident_id)
    return _reply(callback_result_message(result, title))
