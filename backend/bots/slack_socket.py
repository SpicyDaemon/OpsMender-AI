"""Slack Socket Mode: card clicks and slash commands over a socket OpsMender
opens to Slack, so no public URL is needed (M1-97).

A Slack connector whose ``config.inbound_mode`` is ``socket`` names the
environment variable that holds its app-level token (``xapp-``); the token is
never stored. One app process holds each such connector's socket at a time:
the holder keeps a lease row in ``runtime_config`` and renews it, and another
process takes over when the renewals stop. Every envelope is acknowledged at
once and then handled by the same code as the HTTP endpoints; the outcome is
posted to the payload's ``response_url``, so slow work never misses Slack's
three seconds.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import random
import socket
import uuid
from collections.abc import Awaitable, Callable, Mapping
from datetime import datetime, timezone
from typing import Any

import httpx
import websockets
from sqlalchemy import select

from backend.db.models import BotConnector
from backend.db.repos import RuntimeConfigRepo

logger = logging.getLogger(__name__)

INBOUND_HTTP = "http"
INBOUND_SOCKET = "socket"
DEFAULT_APP_TOKEN_ENV = "OPSMENDER_SLACK_APP_TOKEN"
APP_TOKEN_PREFIX = "xapp-"
OPEN_URL = "https://slack.com/api/apps.connections.open"
LEASE_PREFIX = "slack.socket_lease."
# A holder renews every RENEW_SECONDS; another process may take over once
# LEASE_SECONDS pass without a renewal.
LEASE_SECONDS = 60
RENEW_SECONDS = 20
BACKOFF_FIRST = 1.0
BACKOFF_MAX = 60.0
# The envelope types this module handles; others are acknowledged and dropped.
HANDLED = ("interactive", "slash_commands")

Handler = Callable[[str, dict[str, Any]], Awaitable[None]]


def uses_socket_mode(connector: BotConnector) -> bool:
    """Whether this connector receives Slack interactions over Socket Mode."""
    return (
        connector.platform == "slack"
        and (connector.config or {}).get("inbound_mode") == INBOUND_SOCKET
    )


def app_token_env(connector: BotConnector) -> str:
    """The name of the environment variable that holds the app-level token."""
    name = str((connector.config or {}).get("app_token_env") or "").strip()
    return name or DEFAULT_APP_TOKEN_ENV


def app_token(
    connector: BotConnector, env: Mapping[str, str] = os.environ
) -> str | None:
    """The connector's app-level token, or ``None`` when its variable is unset
    or does not hold an app-level token."""
    value = str(env.get(app_token_env(connector)) or "").strip()
    return value if value.startswith(APP_TOKEN_PREFIX) else None


def lease_key(connector_id: uuid.UUID) -> str:
    return f"{LEASE_PREFIX}{connector_id}"


class SocketModeError(Exception):
    """Slack would not open a socket; the message is Slack's error code."""


async def open_socket_url(token: str) -> str:
    """Ask Slack for a socket URL (``apps.connections.open``). The URL carries
    a one-time ticket, so it is never logged."""
    async with httpx.AsyncClient(timeout=10.0) as client:
        response = await client.post(
            OPEN_URL, headers={"Authorization": f"Bearer {token}"}
        )
    try:
        data = response.json()
    except ValueError:
        data = {}
    url = data.get("url") if isinstance(data, dict) else None
    if not (isinstance(data, dict) and data.get("ok")) or not isinstance(url, str):
        error = data.get("error") if isinstance(data, dict) else None
        raise SocketModeError(str(error or f"http_{response.status_code}"))
    return url


class SocketModeClient:
    """One connector's socket: open it, acknowledge every envelope, hand the
    ones OpsMender handles to ``handle``, and reconnect when it closes."""

    def __init__(
        self,
        token: str,
        handle: Handler,
        *,
        open_url: Callable[[str], Awaitable[str]] = open_socket_url,
        connect: Callable[[str], Any] = websockets.connect,
        backoff_first: float = BACKOFF_FIRST,
        backoff_max: float = BACKOFF_MAX,
    ) -> None:
        self._token = token
        self._handle = handle
        self._open_url = open_url
        self._connect = connect
        self._first = backoff_first
        self._max = backoff_max
        self._tasks: set[asyncio.Task] = set()

    async def run(self) -> None:
        """Serve sockets until cancelled. A socket that said hello resets the
        wait before the next one; a failure doubles it, up to the maximum."""
        delay = self._first
        while True:
            healthy = False
            try:
                url = await self._open_url(self._token)
                async with self._connect(url) as ws:
                    healthy = await self._serve(ws)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - logged, then retried
                logger.warning("slack socket mode: %s", _describe(exc))
            if healthy:
                delay = self._first
            # Jitter keeps several processes from reconnecting in step.
            await asyncio.sleep(delay * (0.5 + random.random() / 2))
            if not healthy:
                delay = min(delay * 2, self._max)

    async def _serve(self, ws) -> bool:
        """Serve one socket until Slack closes it or asks for a new one.
        Returns whether it said hello."""
        greeted = False
        async for raw in ws:
            try:
                message = json.loads(raw)
            except (TypeError, ValueError):
                continue
            if not isinstance(message, dict):
                continue
            kind = message.get("type")
            if kind == "hello":
                greeted = True
                continue
            if kind == "disconnect":
                return greeted
            envelope_id = message.get("envelope_id")
            if envelope_id:
                # Acknowledge first: Slack retries an envelope it does not
                # hear back about within three seconds.
                await ws.send(json.dumps({"envelope_id": envelope_id}))
            payload = message.get("payload")
            if kind in HANDLED and isinstance(payload, dict):
                task = asyncio.create_task(self._handle(kind, payload))
                self._tasks.add(task)
                task.add_done_callback(self._tasks.discard)
        return greeted


def _describe(exc: Exception) -> str:
    # A socket URL carries a ticket, so connection errors are named, not shown.
    if isinstance(exc, SocketModeError):
        return f"Slack refused the socket: {exc}"
    return type(exc).__name__


async def handle_envelope(
    app, factory, connector_id: uuid.UUID, kind: str, payload: dict[str, Any]
) -> None:
    """Run one acknowledged envelope through the HTTP endpoints' handlers and
    post the outcome to its ``response_url``."""
    from backend.api.routes.slack_paging import (
        _post_answer,
        process_command,
        process_interaction,
    )

    async with factory() as db:
        connector = await db.get(BotConnector, connector_id)
        if connector is None or not connector.is_enabled:
            return
        if not uses_socket_mode(connector):
            return
        if kind == "interactive":

            async def answer(response_url: Any, text: str) -> None:
                await db.commit()
                if isinstance(response_url, str) and response_url:
                    await _post_answer(response_url, text)

            await process_interaction(
                app,
                db,
                connector,
                payload,
                fallback_key=json.dumps(payload, sort_keys=True).encode("utf-8"),
                answer=answer,
            )
            await db.commit()
        elif kind == "slash_commands":
            response = await process_command(db, connector, payload)
            await db.commit()
            text = json.loads(response.body).get("text")
            response_url = payload.get("response_url")
            if text and isinstance(response_url, str) and response_url:
                await _post_answer(response_url, text)


class SlackSocketManager:
    """Run the sockets of the Slack connectors in Socket Mode whose lease
    this process holds; one process holds each connector's socket."""

    def __init__(
        self,
        app,
        factory,
        *,
        holder: str | None = None,
        lease_seconds: int = LEASE_SECONDS,
        renew_seconds: int = RENEW_SECONDS,
        env: Mapping[str, str] = os.environ,
        client_factory: Callable[..., SocketModeClient] = SocketModeClient,
    ) -> None:
        self._app = app
        self._factory = factory
        self.holder = (
            holder or f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:8]}"
        )
        self._lease = lease_seconds
        self._renew = renew_seconds
        self._env = env
        self._client_factory = client_factory
        self._clients: dict[uuid.UUID, asyncio.Task] = {}
        self._task: asyncio.Task | None = None

    async def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(
                self._loop(), name="opsmender-slack-socket-mode"
            )

    async def stop(self) -> None:
        """Close every socket and give up the leases, so another process can
        take them over at once."""
        tasks = [t for t in (self._task, *self._clients.values()) if t is not None]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        held = list(self._clients)
        self._task = None
        self._clients.clear()
        if not held:
            return
        try:
            async with self._factory() as db:
                for connector_id in held:
                    await RuntimeConfigRepo.release_lease(
                        db, lease_key(connector_id), self.holder
                    )
                await db.commit()
        except Exception as exc:  # noqa: BLE001 - the lease expires on its own
            logger.warning(
                "slack socket mode: lease release failed: %s", _describe(exc)
            )

    async def _loop(self) -> None:
        while True:
            try:
                await self.tick()
            except Exception as exc:  # noqa: BLE001 - logged, retried next tick
                logger.warning("slack socket mode: tick failed: %s", _describe(exc))
            await asyncio.sleep(self._renew)

    async def tick(self, now: datetime | None = None) -> set[uuid.UUID]:
        """Take or renew each lease, run a socket for each one held and close
        the others. Returns the connectors this process holds."""
        now = now or datetime.now(timezone.utc)
        held: dict[uuid.UUID, str] = {}
        async with self._factory() as db:
            rows = (
                (
                    await db.execute(
                        select(BotConnector).where(
                            BotConnector.platform == "slack",
                            BotConnector.is_enabled.is_(True),
                        )
                    )
                )
                .scalars()
                .all()
            )
            for connector in rows:
                if not uses_socket_mode(connector):
                    continue
                token = app_token(connector, self._env)
                if token is None:
                    continue
                if await RuntimeConfigRepo.take_lease(
                    db,
                    connector.org_id,
                    lease_key(connector.id),
                    self.holder,
                    now=now,
                    seconds=self._lease,
                ):
                    held[connector.id] = token
            await db.commit()
        for connector_id in list(self._clients):
            task = self._clients[connector_id]
            if connector_id not in held or task.done():
                task.cancel()
                del self._clients[connector_id]
        for connector_id, token in held.items():
            if connector_id not in self._clients:
                client = self._client_factory(token, self._handler(connector_id))
                self._clients[connector_id] = asyncio.create_task(
                    client.run(), name=f"opsmender-slack-socket-{connector_id}"
                )
        return set(held)

    def _handler(self, connector_id: uuid.UUID) -> Handler:
        async def handle(kind: str, payload: dict[str, Any]) -> None:
            try:
                await handle_envelope(
                    self._app, self._factory, connector_id, kind, payload
                )
            except Exception as exc:  # noqa: BLE001 - one envelope, logged
                logger.warning(
                    "slack socket mode: %s envelope failed: %s",
                    kind,
                    _describe(exc),
                )

        return handle
