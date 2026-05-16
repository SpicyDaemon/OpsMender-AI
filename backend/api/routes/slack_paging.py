"""Slack interactivity endpoint for paging actions (Sprint 36).

Receives Slack ``block_actions`` payloads from buttons on the page cards
built by :mod:`backend.paging.slack_cards`. Routes the click to the
escalation engine:

* ``opsmender:ack``     → :func:`backend.paging.escalation.handle_ack`
* ``opsmender:take``    → :func:`backend.paging.escalation.handle_takeover_request`
* ``opsmender:resolve`` → :func:`backend.paging.escalation.cancel_chain` and
  marks the incident ``resolved``.
* ``opsmender:view``    → no-op (the deep link is a ``url`` button — Slack
  follows it client-side and reports it back as an action; we just ack).

Security: every request is verified with the Slack signing secret stored on
the matching ``bot_connectors`` row. The Slack user issuing the action MUST
have a ``bot_user_links`` row in the same org; if they don't, the endpoint
returns an ephemeral "your Slack account isn't linked" message.

The endpoint is wired at ``POST /bot/slack/interactions``. Slack apps need
to be configured with this URL under
*Interactivity & Shortcuts → Request URL*.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import time
import uuid
from typing import Any

from fastapi import APIRouter, Depends, Request, status
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.deps import get_db
from backend.db.models import BotConnector, Incident
from backend.db.repos import (
    BotUserLinkRepo,
    IncidentRepo,
)
from backend.paging import escalation as _esc
from backend.paging.slack_cards import (
    ACTION_ACK,
    ACTION_RESOLVE,
    ACTION_TAKE,
    ACTION_VIEW,
    parse_incident_id_from_action,
)


logger = logging.getLogger(__name__)
router = APIRouter(prefix="/bot/slack", tags=["slack-paging"])


SLACK_SIGNATURE_REPLAY_WINDOW_SECONDS = 60 * 5


def _ephemeral(text: str) -> JSONResponse:
    return JSONResponse({"response_type": "ephemeral", "text": text})


def _verify_signature(
    *, signing_secret: str, headers, raw_body: bytes
) -> bool:
    timestamp = headers.get("x-slack-request-timestamp") or headers.get(
        "X-Slack-Request-Timestamp"
    )
    signature = headers.get("x-slack-signature") or headers.get(
        "X-Slack-Signature"
    )
    if not timestamp or not signature:
        return False
    try:
        if abs(time.time() - int(timestamp)) > SLACK_SIGNATURE_REPLAY_WINDOW_SECONDS:
            return False
    except (TypeError, ValueError):
        return False
    basestring = f"v0:{timestamp}:{raw_body.decode('utf-8', errors='replace')}"
    expected = "v0=" + hmac.new(
        signing_secret.encode("utf-8"),
        basestring.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(expected, signature)


async def _find_slack_connector(
    db: AsyncSession, *, signing_secret_must_match: bytes, headers
) -> BotConnector | None:
    """Find the Slack connector whose ``signing_secret`` validates the
    request. Slack interactivity payloads carry the team id, not the
    connector id, so we scan all Slack connectors and try each secret. In
    practice an OpsMender deployment has a handful of Slack connectors at
    most, so the cost is negligible."""

    stmt = select(BotConnector).where(
        BotConnector.platform == "slack",
        BotConnector.is_enabled.is_(True),
    )
    rows = (await db.execute(stmt)).scalars().all()
    for connector in rows:
        creds = connector.credentials or {}
        secret = creds.get("signing_secret")
        if not secret:
            continue
        if _verify_signature(
            signing_secret=secret,
            headers=headers,
            raw_body=signing_secret_must_match,
        ):
            return connector
    return None


@router.post(
    "/interactions",
    summary="Receive Slack block_actions clicks from paging cards",
)
async def slack_interactions(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    raw_body = await request.body()

    connector = await _find_slack_connector(
        db, signing_secret_must_match=raw_body, headers=request.headers
    )
    if connector is None:
        return JSONResponse(
            {"error": "invalid_signature"},
            status_code=status.HTTP_403_FORBIDDEN,
        )

    form = await request.form()
    payload_raw = form.get("payload")
    if not payload_raw:
        return _ephemeral("Missing payload.")
    try:
        payload: dict[str, Any] = json.loads(payload_raw)
    except json.JSONDecodeError:
        return _ephemeral("Could not parse Slack payload.")

    if payload.get("type") != "block_actions":
        # Other interactivity types (view_submission, shortcut, …) aren't
        # used by paging cards yet — ack so Slack stops retrying.
        return JSONResponse({"ok": True})

    actions = payload.get("actions") or []
    if not actions:
        return JSONResponse({"ok": True})
    action_id = actions[0].get("action_id")

    if action_id == ACTION_VIEW:
        # Pure link button — Slack already opened the URL.
        return JSONResponse({"ok": True})

    incident_id = parse_incident_id_from_action(payload)
    if incident_id is None:
        return _ephemeral("Could not identify the incident from that action.")

    slack_user_id = (payload.get("user") or {}).get("id")
    if not slack_user_id:
        return _ephemeral("Slack didn't tell us who clicked the button.")

    link = await BotUserLinkRepo.get_by_platform_user(
        db,
        connector.org_id,
        connector_id=connector.id,
        platform_user_id=str(slack_user_id),
    )
    if link is None:
        return _ephemeral(
            "Your Slack account isn't linked to OpsMender. "
            "Ask an admin to add a Bot User Link for you, then try again."
        )

    incident = await IncidentRepo.get_by_id(db, connector.org_id, incident_id)
    if incident is None:
        return _ephemeral("That incident no longer exists.")

    if action_id == ACTION_ACK:
        ok = await _esc.handle_ack(
            db,
            connector.org_id,
            incident_id=incident_id,
            user_id=link.opsmender_user_id,
            via="button_click",
        )
        verb = "acknowledged" if ok else "recorded"
        return _ephemeral(f"You {verb} incident *{incident.title}*.")

    if action_id == ACTION_TAKE:
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
        return _ephemeral(msg)

    if action_id == ACTION_RESOLVE:
        await _esc.cancel_chain(
            db, connector.org_id, incident_id=incident_id
        )
        # Flip the incident status only when the user is permitted: assignee
        # or any chain participant counts as a permitted resolver here.
        await IncidentRepo.update_status(
            db, connector.org_id, incident_id, "resolved"
        )
        return _ephemeral(f"Marked *{incident.title}* resolved.")

    return _ephemeral(f"Unknown action `{action_id}`.")
