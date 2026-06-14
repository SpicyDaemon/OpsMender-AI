"""Slack interactivity + slash command endpoints for paging actions (Sprint 36).

Two endpoints share the same signing-secret verification, external identity
mapping, active-user checks, and Admin/Operator RBAC:

* ``POST /bot/slack/interactions`` — receives ``block_actions`` button
  clicks from the page card built by :mod:`backend.paging.slack_cards`.
* ``POST /bot/slack/commands`` — receives slash command invocations
  (``/ack``, ``/take``, ``/release``, ``/resolve``, ``/snooze``,
  ``/status``).

Both routes verify the Slack v0 HMAC against every enabled Slack
connector's ``signing_secret`` (5-minute replay window) and require the
clicker to have a verified ``bot_user_links`` row in the matched org.

Slack app configuration:

* *Interactivity & Shortcuts → Request URL* → ``/bot/slack/interactions``
* *Slash Commands → Request URL* (per command) → ``/bot/slack/commands``
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
import re
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.deps import get_db
from backend.bots.actions import (
    ExternalActorIdentity,
    IncidentActionError,
    resolve_authorized_external_actor,
)
from backend.bots.native_callbacks import (
    NormalizedNativeCallback,
    callback_error_message,
    callback_result_message,
    execute_normalized_callback,
)
from backend.db.models import BotConnector, Incident, IncidentChainState, IncidentPage
from backend.db.repos import (
    IncidentAssignmentRepo,
    IncidentChainStateRepo,
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

    raw_action = actions[0]
    idempotency_key = str(
        raw_action.get("action_ts")
        or payload.get("trigger_id")
        or hashlib.sha256(raw_body).hexdigest()
    )
    try:
        result = await execute_normalized_callback(
            db,
            connector=connector,
            callback=NormalizedNativeCallback(
                incident_id=incident_id,
                action_id=str(action_id),
                external_actor=ExternalActorIdentity(
                    platform_user_id=str(slack_user_id),
                    username=(payload.get("user") or {}).get("username")
                    or (payload.get("user") or {}).get("name"),
                    display_name=(payload.get("user") or {}).get("name"),
                ),
                idempotency_key=idempotency_key,
                channel_id=str((payload.get("channel") or {}).get("id") or "")
                or None,
                message_id=str((payload.get("message") or {}).get("ts") or "")
                or None,
            ),
        )
    except IncidentActionError as exc:
        message = callback_error_message(exc).replace(
            "Your external account",
            "Your Slack account",
        )
        return _ephemeral(message)

    incident = await IncidentRepo.get_by_id(db, connector.org_id, incident_id)
    title = incident.title if incident is not None else str(incident_id)
    return _ephemeral(callback_result_message(result, title))


# ---------------------------------------------------------------------------
# Slash commands (/ack, /take, /release, /resolve, /snooze, /status)
# ---------------------------------------------------------------------------


SLASH_COMMANDS = {
    "/ack",
    "/take",
    "/release",
    "/resolve",
    "/snooze",
    "/status",
}


_UUID_RE = re.compile(
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
)

_DURATION_RE = re.compile(r"^\s*(\d+)\s*([smhd])\s*$", re.IGNORECASE)


def _parse_duration(text: str) -> int | None:
    """Parse '30m' / '2h' / '90s' / '1d' → seconds. Returns ``None`` on
    failure or non-positive values."""

    m = _DURATION_RE.match(text or "")
    if not m:
        return None
    value = int(m.group(1))
    unit = m.group(2).lower()
    if value <= 0:
        return None
    return value * {"s": 1, "m": 60, "h": 3600, "d": 86400}[unit]


def _extract_incident_id(text: str) -> uuid.UUID | None:
    if not text:
        return None
    m = _UUID_RE.search(text)
    if m is None:
        return None
    try:
        return uuid.UUID(m.group(0))
    except ValueError:
        return None


async def _latest_user_incident_id(
    db: AsyncSession, *, org_id: uuid.UUID, user_id: uuid.UUID
) -> uuid.UUID | None:
    """Find the most recently paged incident for ``user_id`` whose chain is
    still ``running`` or ``paused``. Used when a slash command is invoked
    without an explicit incident id."""

    stmt = (
        select(IncidentPage.incident_id)
        .join(
            IncidentChainState,
            IncidentChainState.incident_id == IncidentPage.incident_id,
        )
        .where(
            IncidentPage.org_id == org_id,
            IncidentPage.user_id == user_id,
            IncidentChainState.status.in_(("running", "paused", "acked")),
        )
        .order_by(IncidentPage.sent_at.desc())
        .limit(1)
    )
    return (await db.execute(stmt)).scalar_one_or_none()


def _fmt_incident(inc: Incident) -> str:
    pri = inc.priority or "P?"
    return f"[{pri}] {inc.title}"


async def _handle_slash(
    db: AsyncSession,
    *,
    connector: BotConnector,
    command: str,
    text: str,
    slack_user_id: str,
) -> JSONResponse:
    try:
        actor = await resolve_authorized_external_actor(
            db,
            org_id=connector.org_id,
            connector=connector,
            identity=ExternalActorIdentity(platform_user_id=slack_user_id),
        )
    except IncidentActionError:
        return _ephemeral(
            "Your Slack account isn't linked to OpsMender. "
            "Ask an admin to verify your identity link and role, then try again."
        )

    incident_id = _extract_incident_id(text)
    if incident_id is None:
        incident_id = await _latest_user_incident_id(
            db, org_id=connector.org_id, user_id=actor.id
        )
    if incident_id is None and command != "/status":
        return _ephemeral(
            f"Usage: `{command} <incident-id>` "
            "(no active page found for your account)."
        )

    if command == "/status" and incident_id is None:
        # Org-level overview: list running chains.
        stmt = (
            select(IncidentChainState, Incident)
            .join(Incident, Incident.id == IncidentChainState.incident_id)
            .where(
                IncidentChainState.org_id == connector.org_id,
                IncidentChainState.status.in_(("running", "paused")),
            )
            .order_by(IncidentChainState.started_at.desc())
            .limit(10)
        )
        rows = (await db.execute(stmt)).all()
        if not rows:
            return _ephemeral("No active escalation chains. :tada:")
        lines = ["*Active pages:*"]
        for state, inc in rows:
            lines.append(
                f"• {_fmt_incident(inc)} — status `{state.status}`, "
                f"step {state.current_step_index}"
            )
        return _ephemeral("\n".join(lines))

    incident = await IncidentRepo.get_by_id(db, connector.org_id, incident_id)
    if incident is None:
        return _ephemeral("That incident no longer exists.")

    if command == "/ack":
        ok = await _esc.handle_ack(
            db,
            connector.org_id,
            incident_id=incident_id,
            user_id=actor.id,
            via="slash_command",
        )
        verb = "acknowledged" if ok else "recorded"
        return _ephemeral(f"You {verb} *{incident.title}*.")

    if command == "/take":
        result = await _esc.handle_takeover_request(
            db,
            connector.org_id,
            incident_id=incident_id,
            requester_id=actor.id,
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

    if command == "/release":
        released = await IncidentAssignmentRepo.release(
            db, connector.org_id, incident_id
        )
        if not released:
            return _ephemeral(f"*{incident.title}* has no active assignee.")
        return _ephemeral(
            f"Released *{incident.title}*. Escalation may resume on the next tick."
        )

    if command == "/resolve":
        await _esc.cancel_chain(
            db, connector.org_id, incident_id=incident_id
        )
        await IncidentRepo.update_status(
            db, connector.org_id, incident_id, "resolved"
        )
        return _ephemeral(f"Marked *{incident.title}* resolved.")

    if command == "/snooze":
        # First token of `text` after stripping any UUID is the duration.
        remainder = _UUID_RE.sub("", text or "").strip() or "30m"
        seconds = _parse_duration(remainder)
        if seconds is None:
            return _ephemeral(
                "Usage: `/snooze <duration>` — examples: `30m`, `2h`, `1d`."
            )
        state = await IncidentChainStateRepo.get_for_incident(
            db, connector.org_id, incident_id
        )
        if state is None or state.status not in ("running", "paused"):
            return _ephemeral(
                f"No active chain to snooze for *{incident.title}*."
            )
        now = datetime.now(timezone.utc)
        new_due = now + timedelta(seconds=seconds)
        state.next_step_due_at = new_due
        state.status = "paused"
        await db.flush()
        # Human-readable duration: re-render from the input.
        return _ephemeral(
            f"Snoozed *{incident.title}* for {remainder}. "
            f"Next step due {new_due.strftime('%Y-%m-%d %H:%M UTC')}."
        )

    if command == "/status":
        state = await IncidentChainStateRepo.get_for_incident(
            db, connector.org_id, incident_id
        )
        active = await IncidentAssignmentRepo.get_active(
            db, connector.org_id, incident_id
        )
        owner = f"<@{active.assigned_to}>" if active else "unassigned"
        if state is None:
            return _ephemeral(
                f"*{incident.title}* — status `{incident.status}`, no chain. "
                f"Owner: {owner}."
            )
        due = (
            state.next_step_due_at.strftime("%Y-%m-%d %H:%M UTC")
            if state.next_step_due_at
            else "—"
        )
        return _ephemeral(
            f"*{incident.title}* — chain `{state.status}`, "
            f"step {state.current_step_index}, next due {due}. "
            f"Owner: {owner}."
        )

    return _ephemeral(f"Unsupported command `{command}`.")


@router.post(
    "/commands",
    summary="Receive Slack slash command invocations for paging actions",
)
async def slack_commands(
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
    command = (form.get("command") or "").strip()
    text = (form.get("text") or "").strip()
    slack_user_id = (form.get("user_id") or "").strip()

    if not command:
        return _ephemeral("Missing command.")
    if command not in SLASH_COMMANDS:
        return _ephemeral(
            f"Unknown command `{command}`. "
            f"Supported: {', '.join(sorted(SLASH_COMMANDS))}."
        )
    if not slack_user_id:
        return _ephemeral("Slack didn't tell us who ran the command.")

    return await _handle_slash(
        db,
        connector=connector,
        command=command,
        text=text,
        slack_user_id=slack_user_id,
    )
