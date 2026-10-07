"""WebSocket endpoint for live session streaming.

WS /sessions/{session_id}/stream

Authenticates via ``?token=<JWT>`` query parameter (WebSocket does not
support Authorization headers on connect).

Sends JSON messages to the client as events occur:
- ``node_transition`` - workflow node changed
- ``tool_call`` - MCP tool call started/completed/blocked
- ``approval_requested`` / ``approval_resolved`` - Tier 1 approval lifecycle
- ``session_end`` - session finished
- ``error`` - something went wrong

This sprint establishes the WebSocket plumbing.  Actual workflow event
publishing is integrated in Sprint 9+ once the session runner is
API-driven.
"""

from __future__ import annotations

import asyncio
import uuid

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect, status

from backend.api.auth import user_for_session_token
from backend.api.deps import get_current_session_factory
from backend.api.schemas import WSMessage
from backend.auth.api_tokens import API_TOKEN_PREFIX
from backend.db.models import User
from backend.db.repos import SessionRepo

router = APIRouter(tags=["websocket"])

# ---------------------------------------------------------------------------
# In-memory channel registry (per session)
# ---------------------------------------------------------------------------

_channels: dict[uuid.UUID, set[asyncio.Queue]] = {}


def get_channel(session_id: uuid.UUID) -> asyncio.Queue:
    """Create and register a new subscriber queue for *session_id*."""
    q: asyncio.Queue = asyncio.Queue()
    _channels.setdefault(session_id, set()).add(q)
    return q


def remove_channel(session_id: uuid.UUID, q: asyncio.Queue) -> None:
    """Unregister a subscriber queue."""
    subs = _channels.get(session_id)
    if subs:
        subs.discard(q)
        if not subs:
            del _channels[session_id]


async def publish(session_id: uuid.UUID, message: WSMessage) -> None:
    """Broadcast a message to all subscribers of *session_id*."""
    subs = _channels.get(session_id, set())
    for q in subs:
        await q.put(message.model_dump())


# ---------------------------------------------------------------------------
# In-memory channel registry (per user) - powers the notification bell
# ---------------------------------------------------------------------------

_user_channels: dict[uuid.UUID, set[asyncio.Queue]] = {}


def get_user_channel(user_id: uuid.UUID) -> asyncio.Queue:
    """Create and register a new subscriber queue for *user_id*."""
    q: asyncio.Queue = asyncio.Queue()
    _user_channels.setdefault(user_id, set()).add(q)
    return q


def remove_user_channel(user_id: uuid.UUID, q: asyncio.Queue) -> None:
    """Unregister a per-user subscriber queue."""
    subs = _user_channels.get(user_id)
    if subs:
        subs.discard(q)
        if not subs:
            del _user_channels[user_id]


async def publish_user(user_id: uuid.UUID, message: WSMessage) -> None:
    """Broadcast a message to every live connection of *user_id*.

    Best-effort and in-memory: if the user has no open tab the message is
    simply dropped - the persisted notification still shows on next load.
    """
    subs = _user_channels.get(user_id, set())
    for q in subs:
        await q.put(message.model_dump())


# ---------------------------------------------------------------------------
# WebSocket route
# ---------------------------------------------------------------------------


# An open stream rechecks its account at least this often, and before it
# sends anything after that long, so it closes soon after the account is
# deactivated or its password changes (R-27).
RECHECK_SECONDS = 15.0
# Close code for a stream whose account no longer signs in.
ACCOUNT_ENDED = 4401


async def _session_user(token: str) -> User | None:
    """The active user a stream's token speaks for. Checked in a short
    database session so a long-lived stream doesn't hold a connection."""
    async with get_current_session_factory()() as db:
        return await user_for_session_token(db, token)


async def _in_workspace(user: User, session_id: uuid.UUID) -> bool:
    """Whether the AI session belongs to the caller's workspace."""
    if user.primary_org_id is None:
        return False
    async with get_current_session_factory()() as db:
        session = await SessionRepo.get_by_id(db, user.primary_org_id, session_id)
    return session is not None


async def _relay(
    websocket: WebSocket, queue: asyncio.Queue, token: str, *, stop_on=None
):
    """Send the queue's events (and keep-alive pings) until the client
    leaves, ``stop_on`` sees an ending event, or the account stops signing
    in: deactivated, deleted or its password changed."""
    loop = asyncio.get_running_loop()
    checked = loop.time()
    while True:
        try:
            msg = await asyncio.wait_for(queue.get(), timeout=RECHECK_SECONDS)
        except asyncio.TimeoutError:
            msg = {"type": "ping", "data": {}}
        if loop.time() - checked >= RECHECK_SECONDS:
            if await _session_user(token) is None:
                await websocket.close(code=ACCOUNT_ENDED)
                return
            checked = loop.time()
        await websocket.send_json(msg)
        if stop_on is not None and stop_on(msg):
            return


@router.websocket("/sessions/{session_id}/stream")
async def session_stream(
    websocket: WebSocket,
    session_id: uuid.UUID,
    token: str = Query(...),
):
    # Authenticate via query-param JWT
    if token.startswith(API_TOKEN_PREFIX):
        await websocket.close(code=4401)
        return
    user = await _session_user(token)
    if user is None:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return
    # Only sessions in the caller's workspace; viewers may follow them (O-07).
    if not await _in_workspace(user, session_id):
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    await websocket.accept()

    # Subscribe to session events; close cleanly when the session ends.
    queue = get_channel(session_id)
    try:
        await _relay(
            websocket,
            queue,
            token,
            stop_on=lambda msg: msg.get("type") == "session_end",
        )
    except WebSocketDisconnect:
        pass
    finally:
        remove_channel(session_id, queue)


@router.websocket("/notifications/stream")
async def notifications_stream(
    websocket: WebSocket,
    token: str = Query(...),
):
    """Live per-user notification stream powering the bell.

    Subscribes the connection to the authenticated user's channel (keyed by
    the token's ``sub``), so a client can only ever receive its own
    notifications. Emits ``notification`` messages and ``ping`` keep-alives.
    """
    if token.startswith(API_TOKEN_PREFIX):
        await websocket.close(code=4401)
        return
    user = await _session_user(token)
    if user is None:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return
    user_id = user.id

    await websocket.accept()

    queue = get_user_channel(user_id)
    try:
        await _relay(websocket, queue, token)
    except WebSocketDisconnect:
        pass
    finally:
        remove_user_channel(user_id, queue)
