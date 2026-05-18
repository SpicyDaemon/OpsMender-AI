"""Microsoft Teams bot-activity endpoint for paging actions (Sprint 37 step 4).

When a user clicks Acknowledge / Take Over / Resolve on the adaptive
card built by :mod:`backend.paging.teams_cards`, Teams POSTs an Activity
envelope to the bot's messaging endpoint. The action's ``data`` ends up
in the activity ``value`` field. This route is the OpsMender side of
that hop:

1. Verify the Bot Framework JWT in ``Authorization`` against the
   matching ``bot_connectors`` row's ``bot_app_id``.
2. Pull ``activity.value`` (action + incident_id) plus
   ``activity.from.aadObjectId`` (the Azure AD object id of the user
   who clicked).
3. Resolve OpsMender's user via ``BotUserLinkRepo.get_by_platform_user``;
   unknown Teams users get a friendly text reply.
4. Route by ``action`` → escalation engine, same handlers the Slack
   interactions endpoint calls.

Slack and Teams reuse the same ``opsmender:{ack,take,resolve}`` action
identifiers so the routing branches stay aligned.
"""

from __future__ import annotations

import logging
import uuid
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
from backend.db.models import BotConnector
from backend.db.repos import BotUserLinkRepo, IncidentRepo
from backend.paging import escalation as _esc
from backend.paging.teams_cards import (
    ACTION_ACK,
    ACTION_RESOLVE,
    ACTION_TAKE,
    ACTION_VIEW,
    parse_incident_id_from_action,
)


logger = logging.getLogger(__name__)
router = APIRouter(prefix="/bot/teams", tags=["teams-paging"])


def _reply(text: str) -> JSONResponse:
    """Build a minimal Activity reply. Teams renders ``text`` as a
    message in the same chat. We don't bother with proactive messaging
    here — the activity reply is enough to show the user their click
    landed."""

    return JSONResponse(
        {
            "type": "message",
            "text": text,
        }
    )


async def _find_teams_connector_for_activity(
    db: AsyncSession,
    *,
    authorization: str | None,
    recipient_id: str | None,
) -> BotConnector | None:
    """Locate the Teams connector that the inbound activity belongs to.

    The activity's ``recipient.id`` is the bot's user id (``28:`` prefix
    followed by the bot's app id). We extract the trailing app id and
    match it against ``bot_connectors.credentials['bot_app_id']``.
    """

    if not recipient_id:
        return None
    bot_app_id = recipient_id.split(":", 1)[-1]

    stmt = select(BotConnector).where(
        BotConnector.platform == "teams",
        BotConnector.is_enabled.is_(True),
    )
    rows = (await db.execute(stmt)).scalars().all()
    for connector in rows:
        creds = connector.credentials or {}
        candidate = creds.get("bot_app_id") or creds.get("client_id")
        if candidate and candidate == bot_app_id:
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


@router.post(
    "/activity",
    summary="Receive Teams adaptive-card actions",
)
async def teams_activity(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    try:
        payload: dict[str, Any] = await request.json()
    except ValueError:
        return JSONResponse(
            {"error": "invalid_json"},
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    authorization = request.headers.get("authorization") or request.headers.get(
        "Authorization"
    )
    recipient_id = (payload.get("recipient") or {}).get("id")
    connector = await _find_teams_connector_for_activity(
        db,
        authorization=authorization,
        recipient_id=recipient_id,
    )
    if connector is None:
        return JSONResponse(
            {"error": "unauthorized"},
            status_code=status.HTTP_403_FORBIDDEN,
        )

    value = payload.get("value")
    # Adaptive-card Action.Submit data can land as either a dict or a
    # wrapped ``{"value": {...}}`` shape depending on the channel.
    incident_id = parse_incident_id_from_action(value or {})
    if incident_id is None:
        return _reply(
            "Couldn't identify the incident from that action."
        )

    action = (value or {}).get("action") or ((value or {}).get("value") or {}).get(
        "action"
    )
    if action == ACTION_VIEW:
        return _reply("Opening OpsMender…")

    from_field = payload.get("from") or {}
    teams_user_id = from_field.get("aadObjectId") or from_field.get("id")
    if not teams_user_id:
        return _reply("Teams didn't tell us who clicked.")

    link = await BotUserLinkRepo.get_by_platform_user(
        db,
        connector.org_id,
        connector_id=connector.id,
        platform_user_id=str(teams_user_id),
    )
    if link is None:
        return _reply(
            "Your Teams account isn't linked to OpsMender. "
            "Ask an admin to add a Bot User Link for you, then try again."
        )

    incident = await IncidentRepo.get_by_id(db, connector.org_id, incident_id)
    if incident is None:
        return _reply("That incident no longer exists.")

    if action == ACTION_ACK:
        ok = await _esc.handle_ack(
            db,
            connector.org_id,
            incident_id=incident_id,
            user_id=link.opsmender_user_id,
            via="card_action",
        )
        verb = "acknowledged" if ok else "recorded"
        return _reply(f"You {verb} *{incident.title}*.")

    if action == ACTION_TAKE:
        result = await _esc.handle_takeover_request(
            db,
            connector.org_id,
            incident_id=incident_id,
            requester_id=link.opsmender_user_id,
        )
        if result == "assigned":
            msg = f"You're now assigned to *{incident.title}*."
        elif result == "pending":
            msg = (
                f"Take-over requested for *{incident.title}*. "
                "Current owner has 5 minutes to confirm."
            )
        elif result == "noop":
            msg = f"You already own *{incident.title}*."
        else:
            msg = (
                f"Take-over for *{incident.title}* requires an admin "
                "(chain ended)."
            )
        return _reply(msg)

    if action == ACTION_RESOLVE:
        await _esc.cancel_chain(
            db, connector.org_id, incident_id=incident_id
        )
        await IncidentRepo.update_status(
            db, connector.org_id, incident_id, "resolved"
        )
        return _reply(f"Marked *{incident.title}* resolved.")

    return _reply(f"Unknown action `{action}`.")
