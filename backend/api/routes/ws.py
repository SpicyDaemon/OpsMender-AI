"""WebSocket endpoint for live session streaming.

WS /sessions/{session_id}/stream

Authenticates via ``?token=<JWT>`` query parameter (WebSocket does not
support Authorization headers on connect).

Sends JSON messages to the client as events occur:
- ``node_transition`` — workflow node changed
- ``tool_call``       — MCP tool call started/completed/blocked
- ``approval_requested`` / ``approval_resolved`` — Tier 1 approval lifecycle
- ``session_end``     — session finished
- ``error``           — something went wrong

This sprint establishes the WebSocket plumbing.  Actual workflow event
publishing is integrated in Sprint 9+ once the session runner is
API-driven.
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect, status
from jose import JWTError

from backend.api.auth import decode_access_token
from backend.api.schemas import WSMessage

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
# WebSocket route
# ---------------------------------------------------------------------------

@router.websocket("/sessions/{session_id}/stream")
async def session_stream(
    websocket: WebSocket,
    session_id: uuid.UUID,
    token: str = Query(...),
):
    # Authenticate via query-param JWT
    try:
        payload = decode_access_token(token)
        user_id = payload.get("sub")
        if user_id is None:
            await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
            return
    except (JWTError, ValueError):
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    await websocket.accept()

    # Subscribe to session events
    queue = get_channel(session_id)
    try:
        while True:
            # Wait for the next event
            try:
                msg = await asyncio.wait_for(queue.get(), timeout=30.0)
                await websocket.send_json(msg)
                # If session ended, close cleanly
                if msg.get("type") == "session_end":
                    break
            except asyncio.TimeoutError:
                # Send a ping to keep the connection alive
                await websocket.send_json({"type": "ping", "data": {}})
    except WebSocketDisconnect:
        pass
    finally:
        remove_channel(session_id, queue)
