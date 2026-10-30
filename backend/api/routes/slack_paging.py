"""Slack interactivity + slash command endpoints for paging actions (Sprint 36).

Two endpoints share the same signing-secret verification, external identity
mapping, active-user checks, and Admin/Operator RBAC:

* ``POST /bot/slack/interactions`` - receives ``block_actions`` button
  clicks from the page card built by :mod:`backend.paging.slack_cards`.
* ``POST /bot/slack/commands`` - receives slash command invocations
  (``/ack``, ``/take``, ``/release``, ``/resolve``, ``/snooze``,
  ``/status``; Slack keeps ``/status`` and ``/snooze`` for itself, so a
  workspace registers ``/opsmender-status`` and ``/snooze-escalation``, which
  map onto the same handlers).

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
from collections.abc import Awaitable, Callable, Mapping
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends, Request, Response, status
from fastapi.responses import JSONResponse
import re
from datetime import datetime, timedelta, timezone

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.deps import get_db
from backend.auth.roles import request_role
from backend.bots.actions import (
    ExternalActorIdentity,
    IncidentActionError,
    resolve_authorized_external_actor,
)
from backend.bots.connectors import get_adapter
from backend.bots.connectors.credentials import readable
from backend.bots.native_callbacks import (
    NormalizedNativeCallback,
    callback_error_message,
    callback_result_message,
    dispatch_native_session_result,
    execute_normalized_callback,
    refresh_incident_card,
)
from backend.db.models import BotConnector, Incident, IncidentChainState, IncidentPage
from backend.db.repos import (
    LIVE_CHAIN_STATUSES,
    IncidentAssignmentRepo,
    IncidentChainStateRepo,
    IncidentRepo,
    UserRepo,
)
from backend.paging import escalation as _esc
from backend.paging import reassign as _reassign
from backend.paging.slack_cards import (
    ACTION_VIEW,
    parse_incident_id_from_action,
)


logger = logging.getLogger(__name__)
router = APIRouter(prefix="/bot/slack", tags=["slack-paging"])


SLACK_SIGNATURE_REPLAY_WINDOW_SECONDS = 60 * 5


def _ephemeral(text: str) -> JSONResponse:
    return JSONResponse({"response_type": "ephemeral", "text": text})


async def _post_answer(response_url: str, text: str) -> None:
    try:
        ok, error = await get_adapter("slack").respond_to_interaction(
            response_url=response_url, text=text
        )
    except Exception as exc:  # the action is already done; only log
        ok, error = False, type(exc).__name__
    if not ok:
        logger.warning("Slack response_url answer failed: %s", error)


async def _answer(
    db: AsyncSession,
    background_tasks: BackgroundTasks,
    response_url: Any,
    text: str,
) -> Response:
    """Answer the person who tapped a card button.

    Slack shows nothing for the HTTP body of a ``block_actions`` request, so
    the text goes to the payload's ``response_url`` as an ephemeral message,
    after this empty 200. The request's work is committed first: the post
    runs before the request session would commit, and it must neither tell
    the person about a change that is not durable yet nor hold its locks
    for the Slack round trip. A failed post is logged and never touches the
    action, which is already applied and audited. A payload without a
    ``response_url`` keeps the ephemeral body.
    """
    if not isinstance(response_url, str) or not response_url:
        return _ephemeral(text)
    await db.commit()
    background_tasks.add_task(_post_answer, response_url, text)
    return Response(status_code=status.HTTP_200_OK)


def _verify_signature(*, signing_secret: str, headers, raw_body: bytes) -> bool:
    timestamp = headers.get("x-slack-request-timestamp") or headers.get(
        "X-Slack-Request-Timestamp"
    )
    signature = headers.get("x-slack-signature") or headers.get("X-Slack-Signature")
    if not timestamp or not signature:
        return False
    try:
        if abs(time.time() - int(timestamp)) > SLACK_SIGNATURE_REPLAY_WINDOW_SECONDS:
            return False
    except (TypeError, ValueError):
        return False
    basestring = f"v0:{timestamp}:{raw_body.decode('utf-8', errors='replace')}"
    expected = (
        "v0="
        + hmac.new(
            signing_secret.encode("utf-8"),
            basestring.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
    )
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
        # Sealed since M1-113; a legacy plain row still reads, and a value
        # this server's key cannot open is skipped, never used as a secret.
        secret = readable(creds.get("signing_secret"))
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
    background_tasks: BackgroundTasks,
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
    except (json.JSONDecodeError, RecursionError):
        return _ephemeral("Could not parse Slack payload.")

    async def answer(response_url: Any, text: str) -> Response:
        return await _answer(db, background_tasks, response_url, text)

    return await process_interaction(
        request.app, db, connector, payload, fallback_key=raw_body, answer=answer
    )


AnswerFn = Callable[[Any, str], Awaitable["Response | None"]]


async def process_interaction(
    app,
    db: AsyncSession,
    connector: BotConnector,
    payload: dict[str, Any],
    *,
    fallback_key: bytes,
    answer: AnswerFn,
) -> Response | None:
    """Run a ``block_actions`` click from a paging card, once the caller
    knows which Slack connector it came through.

    Two callers: the signed HTTP request above, and a Socket Mode envelope
    (``backend/bots/slack_socket.py``, M1-97), which the app-level token
    already authenticates. ``answer`` tells the person the outcome;
    ``fallback_key`` keys the idempotency check when Slack sends neither an
    action timestamp nor a trigger id.
    """
    if payload.get("type") != "block_actions":
        # Other interactivity types (view_submission, shortcut, …) aren't
        # used by paging cards yet - ack so Slack stops retrying.
        return JSONResponse({"ok": True})

    actions = payload.get("actions") or []
    if not actions:
        return JSONResponse({"ok": True})
    action_id = actions[0].get("action_id")

    if action_id == ACTION_VIEW:
        # Pure link button - Slack already opened the URL.
        return JSONResponse({"ok": True})

    response_url = payload.get("response_url")
    incident_id = parse_incident_id_from_action(payload)
    if incident_id is None:
        return await answer(
            response_url, "Could not identify the incident from that action."
        )

    slack_user_id = (payload.get("user") or {}).get("id")
    if not slack_user_id:
        return await answer(
            response_url, "Slack didn't tell us who clicked the button."
        )

    raw_action = actions[0]
    idempotency_key = str(
        raw_action.get("action_ts")
        or payload.get("trigger_id")
        or hashlib.sha256(fallback_key).hexdigest()
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
                channel_id=str((payload.get("channel") or {}).get("id") or "") or None,
                message_id=str((payload.get("message") or {}).get("ts") or "") or None,
            ),
            config=app.state.config,
        )
    except IncidentActionError as exc:
        message = callback_error_message(exc).replace(
            "Your external account",
            "Your Slack account",
        )
        return await answer(response_url, message)

    await dispatch_native_session_result(
        app,
        db,
        org_id=connector.org_id,
        result=result,
    )
    await refresh_incident_card(db, org_id=connector.org_id, result=result)
    incident = await IncidentRepo.get_by_id(db, connector.org_id, incident_id)
    title = incident.title if incident is not None else str(incident_id)
    return await answer(response_url, callback_result_message(result, title))


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

# Slack keeps /status (and refused /snooze) for its own built-in commands, so
# a workspace registers these spellings instead; each maps onto the handler of
# the plain name (M1-83). The alias is resolved after the signature check.
SLASH_COMMAND_ALIASES = {
    "/opsmender-status": "/status",
    "/snooze-escalation": "/snooze",
}


def _supported_commands() -> str:
    """The supported names, each with the alias a workspace may register."""
    names = []
    for name in sorted(SLASH_COMMANDS):
        aliases = sorted(
            alias for alias, target in SLASH_COMMAND_ALIASES.items() if target == name
        )
        names.append(f"{name} (or {', '.join(aliases)})" if aliases else name)
    return ", ".join(names)


_UUID_RE = re.compile(
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
)

_DURATION_RE = re.compile(r"^\s*(\d+)\s*([smhd])\s*$", re.IGNORECASE)


# A snooze longer than a week is not a pause any more; resolve instead. The cap
# also keeps the resume time inside the representable date range.
_MAX_SNOOZE_SECONDS = 7 * 86400


def _parse_duration(text: str) -> int | None:
    """Parse '30m' / '2h' / '90s' / '1d' → seconds. Returns ``None`` on
    failure, non-positive values, or anything over seven days."""

    m = _DURATION_RE.match(text or "")
    if not m:
        return None
    value = int(m.group(1))
    unit = m.group(2).lower()
    if value <= 0:
        return None
    seconds = value * {"s": 1, "m": 60, "h": 3600, "d": 86400}[unit]
    return seconds if seconds <= _MAX_SNOOZE_SECONDS else None


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


# Nothing times out an exhausted chain while its incident stays open, so a
# command without an id takes one only while the user's page on it is this
# recent. Older, a bare /resolve or /release could act on a forgotten incident.
# The window counts from the page, not from when the chain ran out: a handoff
# can restart a chain and run it out again without paging this user.
EXHAUSTED_PAGE_WINDOW = timedelta(hours=24)


async def _latest_user_incident_id(
    db: AsyncSession, *, org_id: uuid.UUID, user_id: uuid.UUID
) -> uuid.UUID | None:
    """Find the most recently paged incident for ``user_id`` that a slash
    command without an explicit incident id should act on.

    The incident must be open. Its chain must still be able to page (running,
    paused or under an acknowledgement lock, as ``chain_is_live`` decides), at
    any age. Or its chain is exhausted and this page was sent within
    ``EXHAUSTED_PAGE_WINDOW``. An incident with no chain state, a cancelled
    chain, or a chain acknowledged before the lock existed (finished) never
    qualifies. The newest qualifying page wins.

    M1-95: in the 2026-10-24 live round ``/ack`` answered "no active page
    found" once a one-level chain was exhausted, though the incident was open
    and held."""

    exhausted_since = datetime.now(timezone.utc) - EXHAUSTED_PAGE_WINDOW
    stmt = (
        select(IncidentPage.incident_id)
        .join(
            IncidentChainState,
            IncidentChainState.incident_id == IncidentPage.incident_id,
        )
        .join(Incident, Incident.id == IncidentPage.incident_id)
        .where(
            IncidentPage.org_id == org_id,
            IncidentPage.user_id == user_id,
            Incident.status.not_in(_esc.CLOSED_INCIDENT_STATUSES),
            or_(
                and_(
                    IncidentChainState.status.in_(LIVE_CHAIN_STATUSES),
                    IncidentChainState.finished_at.is_(None),
                ),
                and_(
                    IncidentChainState.status == "exhausted",
                    IncidentPage.sent_at >= exhausted_since,
                ),
            ),
        )
        .order_by(IncidentPage.sent_at.desc())
        .limit(1)
    )
    return (await db.execute(stmt)).scalar_one_or_none()


def _fmt_incident(inc: Incident) -> str:
    pri = inc.priority or "P?"
    return f"[{pri}] {inc.title}"


async def _owner_name(
    db: AsyncSession, org_id: uuid.UUID, incident_id: uuid.UUID
) -> str:
    assignment = await IncidentAssignmentRepo.get_active(db, org_id, incident_id)
    owner = (
        await UserRepo.get_by_id(db, assignment.assigned_to)
        if assignment is not None
        else None
    )
    return owner.username if owner is not None else "another responder"


async def _ask_owner_hint(
    db: AsyncSession, org_id: uuid.UUID, incident_id: uuid.UUID
) -> str:
    """How to ask the owner for the incident. A takeover request waits on the
    Escalation Chain, and P2/P3 notify incidents have none."""
    if await IncidentChainStateRepo.get_for_incident(db, org_id, incident_id) is None:
        return "Ask them to release it, or use Force take in OpsMender with a reason."
    return "Use `/take` to request a takeover."


async def _refresh_cards(
    db: AsyncSession,
    org_id: uuid.UUID,
    incident_id: uuid.UUID,
    event_type: str | None = None,
) -> None:
    """Commit a change, then bring the channel cards up to date the way the
    other paths do after the same change. An ownership change edits them in
    place; with ``event_type`` (an acknowledgement or a resolve) the incident
    event a card tap emits for that change is delivered instead."""
    from backend.api.routes.incidents import _notify_channels, _refresh_channel_cards

    await db.commit()
    if event_type is None:
        await _refresh_channel_cards(db, incident_id, org_id)
    else:
        await _notify_channels(db, incident_id, org_id, event_type)


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
            f"Usage: `{command} <incident-id>` (no active page found for your account)."
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
                f"• {_fmt_incident(inc)}: status `{state.status}`, "
                f"step {state.current_step_index}"
            )
        return _ephemeral("\n".join(lines))

    incident = await IncidentRepo.get_by_id(db, connector.org_id, incident_id)
    if incident is None:
        return _ephemeral("That incident no longer exists.")

    if command in ("/ack", "/take") and not await _reassign.can_take(
        db, connector.org_id, incident, actor
    ):
        return _ephemeral(
            f"You can't take *{incident.title}*: {_reassign.TAKE_FORBIDDEN}"
        )
    if command == "/resolve" and not await _reassign.can_resolve(
        db, connector.org_id, incident, actor
    ):
        return _ephemeral(
            f"You can't resolve *{incident.title}*: {_reassign.RESOLVE_FORBIDDEN}"
        )

    if command == "/ack":
        outcome = await _esc.acknowledge(
            db,
            connector.org_id,
            incident_id=incident_id,
            assignee_id=actor.id,
            via="slash_command",
        )
        if outcome.status == "owned_by_other":
            owner_name = await _owner_name(db, connector.org_id, incident_id)
            hint = await _ask_owner_hint(db, connector.org_id, incident_id)
            return _ephemeral(f"*{incident.title}* is owned by {owner_name}. {hint}")
        if outcome.status == "acknowledged":
            # A new owner: the same card update a tap on Acknowledge gives.
            await _refresh_cards(
                db, connector.org_id, incident_id, "incident.acknowledged"
            )
        verb = "acknowledged" if outcome.chain_locked else "recorded"
        return _ephemeral(f"You {verb} *{incident.title}*.")

    if command == "/take":
        result = await _esc.handle_takeover_request(
            db,
            connector.org_id,
            incident_id=incident_id,
            requester_id=actor.id,
        )
        if result == "assigned":
            await _refresh_cards(db, connector.org_id, incident_id)
            msg = f"You're now assigned to *{incident.title}*."
        elif result == "pending":
            msg = (
                f"Take-over requested for *{incident.title}*. "
                "Current owner has 5 minutes to confirm."
            )
        elif result == "noop":
            msg = f"You already own *{incident.title}*."
        elif result == "closed":
            msg = f"*{incident.title}* is already {incident.status}."
        else:
            # requires_admin: no Escalation Chain holds the incident, so a
            # takeover request has nowhere to wait for the owner's answer.
            owner_name = await _owner_name(db, connector.org_id, incident_id)
            msg = (
                f"*{incident.title}* is owned by {owner_name}, and without an "
                "Escalation Chain a takeover request can't wait for their answer. "
                "Ask them to release it, or use Force take in OpsMender with a reason."
            )
        return _ephemeral(msg)

    if command == "/release":
        from backend.paging.channel_factory import build_channel_factory

        # Same rule as the web: only the owner or an admin releases.
        active = await IncidentAssignmentRepo.get_active(
            db, connector.org_id, incident_id
        )
        if (
            active is not None
            and active.assigned_to != actor.id
            and request_role(actor) != "admin"
        ):
            return _ephemeral(
                f"Only the owner or an admin can release *{incident.title}*."
            )
        state = await IncidentChainStateRepo.get_for_incident(
            db, connector.org_id, incident_id
        )
        was_locked = state is not None and state.status == "acked"
        released = await _esc.release_ownership(
            db,
            connector.org_id,
            incident_id=incident_id,
            actor_id=actor.id,
            channel_factory=build_channel_factory(),
        )
        if not released:
            return _ephemeral(f"*{incident.title}* has no active assignee.")
        await _refresh_cards(db, connector.org_id, incident_id)
        if was_locked and state.finished_at is None:
            return _ephemeral(
                f"Released *{incident.title}*. Escalation resumed at the next level."
            )
        return _ephemeral(f"Released *{incident.title}*.")

    if command == "/resolve":
        # Read before the write, which can refresh this instance.
        was_resolved = incident.status == "resolved"
        await _esc.cancel_chain(db, connector.org_id, incident_id=incident_id)
        await IncidentRepo.update_status(db, connector.org_id, incident_id, "resolved")
        if not was_resolved:
            await _refresh_cards(db, connector.org_id, incident_id, "incident.resolved")
        return _ephemeral(f"Marked *{incident.title}* resolved.")

    if command == "/snooze":
        # First token of `text` after stripping any UUID is the duration.
        remainder = _UUID_RE.sub("", text or "").strip() or "30m"
        seconds = _parse_duration(remainder)
        if seconds is None:
            return _ephemeral(
                "Usage: `/snooze <duration>`, for example `30m`, `2h` or `1d` (up to `7d`)."
            )
        now = datetime.now(timezone.utc)
        # One snooze path for Slack and (later) the web: an unacknowledged
        # chain resumes at the next level when the snooze ends; an
        # acknowledged one keeps its owner (KI-013).
        resumes_at = await _esc.snooze(
            db,
            connector.org_id,
            incident_id=incident_id,
            actor_id=actor.id,
            until=now + timedelta(seconds=seconds),
            at=now,
        )
        if resumes_at is None:
            return _ephemeral(f"No active chain to snooze for *{incident.title}*.")
        # Human-readable duration: re-render from the input.
        return _ephemeral(
            f"Snoozed *{incident.title}* for {remainder}. "
            f"Escalation resumes {resumes_at.strftime('%Y-%m-%d %H:%M UTC')} "
            "unless someone acts."
        )

    if command == "/status":
        state = await IncidentChainStateRepo.get_for_incident(
            db, connector.org_id, incident_id
        )
        active = await IncidentAssignmentRepo.get_active(
            db, connector.org_id, incident_id
        )
        owner = (
            await _owner_name(db, connector.org_id, incident_id)
            if active
            else "unassigned"
        )
        owner = owner.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        if state is None:
            return _ephemeral(
                f"*{incident.title}*: status `{incident.status}`, no chain. "
                f"Owner: {owner}."
            )
        due = (
            state.next_step_due_at.strftime("%Y-%m-%d %H:%M UTC")
            if state.next_step_due_at
            else "not scheduled"
        )
        return _ephemeral(
            f"*{incident.title}*: chain `{state.status}`, "
            f"step {state.current_step_index}, next check {due}. "
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
    return await process_command(db, connector, form)


async def process_command(
    db: AsyncSession, connector: BotConnector, fields: Mapping[str, Any]
) -> JSONResponse:
    """Run a slash command, once the caller knows which Slack connector it
    came through. ``fields`` is the HTTP form or a Socket Mode envelope's
    payload (M1-97); Slack sends the same keys either way."""
    command = str(fields.get("command") or "").strip()
    command = SLASH_COMMAND_ALIASES.get(command, command)
    text = str(fields.get("text") or "").strip()
    slack_user_id = str(fields.get("user_id") or "").strip()

    if not command:
        return _ephemeral("Missing command.")
    if command not in SLASH_COMMANDS:
        return _ephemeral(
            f"Unknown command `{command}`. Supported: {_supported_commands()}."
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
