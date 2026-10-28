"""Slack Socket Mode: card clicks and slash commands over a socket OpsMender
opens to Slack, so no public URL is needed (M1-97).

A Slack connector whose ``config.inbound_mode`` is ``socket`` names the
environment variable that holds its app-level token (``xapp-``); the token is
never stored. Each Socket Mode connector needs its own Slack app: Slack hands
each envelope to one of an app's open sockets, so two connectors on one app
would each see a random share of the clicks. Connectors that share an
app-level token or a bot token get no socket, and a socket whose app-level
token turns out to belong to another app than the connector's bot token is
closed; Check configuration and the doctor say why.

One app process holds each connector's socket at a time. The holder keeps a
lease row in ``runtime_config`` and renews it while its socket is connected or
still connecting. A holder that cannot renew closes its sockets before the
lease can pass to another process, and a holder that cannot connect gives the
lease up, so another process can try. The holder also keeps a state row the
doctor reads: connected or not, and any problem. Every envelope is
acknowledged at once and then handled by the same code as the HTTP endpoints;
the outcome is posted to the payload's ``response_url``, so slow work never
misses Slack's three seconds.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import random
import socket
import time
import traceback
import uuid
from collections.abc import Awaitable, Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
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
STATE_PREFIX = "slack.socket_state."
AUTH_TEST_URL = "https://slack.com/api/auth.test"
BOTS_INFO_URL = "https://slack.com/api/bots.info"
# Slack's answers that are not about the token: it did not refuse it.
NOT_A_REFUSAL = {"ratelimited", "internal_error", "fatal_error", "service_unavailable"}
# On shutdown, envelopes being handled get this long to finish.
DRAIN_SECONDS = 10.0
# Closing an old socket never holds up the next one for long.
CLOSE_TIMEOUT = 2.0
# When Slack asks for a new socket the next one opens at once, but not more
# often than this, so a server that keeps asking cannot spin the client.
QUICK_RECONNECT_GAP = 30.0
BACKOFF_FIRST = 1.0
BACKOFF_MAX = 60.0
# The envelope types this module handles; others are acknowledged and dropped.
HANDLED = ("interactive", "slash_commands")
FAILURE_TEXT = (
    "OpsMender could not finish that. Try again, or use the OpsMender web app."
)

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


def bot_token(connector: BotConnector) -> str | None:
    value = str((connector.credentials or {}).get("bot_token") or "").strip()
    return value or None


def token_conflicts(
    connectors: Iterable[BotConnector], env: Mapping[str, str] = os.environ
) -> dict[uuid.UUID, list[str]]:
    """For each Socket Mode connector that shares its Slack app with another
    of the given connectors (the same app-level token, or the same bot token,
    which one app has per workspace), the names of those others."""
    groups: dict[tuple[str, str], list[BotConnector]] = {}
    for connector in connectors:
        if not uses_socket_mode(connector):
            continue
        for kind, value in (
            ("app", app_token(connector, env)),
            ("bot", bot_token(connector)),
        ):
            if value is not None:
                groups.setdefault((kind, value), []).append(connector)
    others: dict[uuid.UUID, set[str]] = {}
    for group in groups.values():
        for connector in group if len(group) > 1 else ():
            others.setdefault(connector.id, set()).update(
                other.name for other in group if other is not connector
            )
    return {connector_id: sorted(names) for connector_id, names in others.items()}


async def socket_connectors(db) -> list[BotConnector]:
    """The enabled Slack connectors in Socket Mode."""
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
    return [connector for connector in rows if uses_socket_mode(connector)]


def lease_key(connector_id: uuid.UUID) -> str:
    return f"{LEASE_PREFIX}{connector_id}"


def state_key(connector_id: uuid.UUID) -> str:
    return f"{STATE_PREFIX}{connector_id}"


class SocketModeError(Exception):
    """Slack would not open a socket; the message is Slack's error code."""

    @property
    def refused(self) -> bool:
        """Whether Slack refused the token, rather than being busy or down."""
        code = str(self)
        return code not in NOT_A_REFUSAL and not code.startswith("http_")


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


async def bot_app_id(token: str) -> str | None:
    """The Slack app a bot token belongs to (``auth.test`` then ``bots.info``),
    or ``None`` when Slack does not say."""
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            headers = {"Authorization": f"Bearer {token}"}
            who = (await client.post(AUTH_TEST_URL, headers=headers)).json()
            bot_id = who.get("bot_id") if isinstance(who, dict) else None
            if not bot_id:
                return None
            info = (
                await client.get(BOTS_INFO_URL, headers=headers, params={"bot": bot_id})
            ).json()
    except (httpx.HTTPError, ValueError):
        return None
    bot = info.get("bot") if isinstance(info, dict) else None
    app_id = bot.get("app_id") if isinstance(bot, dict) else None
    return app_id if isinstance(app_id, str) and app_id else None


def _connect(url: str):
    return websockets.connect(url, close_timeout=CLOSE_TIMEOUT)


class SocketModeClient:
    """One connector's socket: open it, acknowledge every envelope, hand the
    ones OpsMender handles to ``handle``, and reconnect when it closes."""

    def __init__(
        self,
        token: str,
        handle: Handler,
        *,
        open_url: Callable[[str], Awaitable[str]] = open_socket_url,
        connect: Callable[[str], Any] = _connect,
        backoff_first: float = BACKOFF_FIRST,
        backoff_max: float = BACKOFF_MAX,
        sleep: Callable[[float], Awaitable[None]] | None = None,
    ) -> None:
        self._token = token
        self._handle = handle
        self._open_url = open_url
        self._connect = connect
        self._first = backoff_first
        self._max = backoff_max
        self._sleep = sleep or asyncio.sleep
        # Envelopes being handled, kept so they are not garbage collected.
        self.tasks: set[asyncio.Task] = set()
        # True while a socket that said hello is open.
        self.connected = False
        self._greeted = False
        self._last_quick = float("-inf")
        # Set by the manager: does the app a socket greets for belong to this
        # connector? A socket for another app is closed and the client stops.
        self.check_app: Callable[[str], Awaitable[bool]] | None = None
        self.wrong_app: str | None = None

    async def run(self) -> None:
        """Serve sockets until cancelled, or until a socket greets for another
        app than the connector's. When Slack asks for a new socket the next one
        opens at once (at most once per ``QUICK_RECONNECT_GAP``). After any other
        close of a socket that said hello the wait goes back to the first one; a
        failure before hello doubles it, up to the maximum."""
        delay = self._first
        while self.wrong_app is None:
            self._greeted = False
            asked_to_reconnect = False
            try:
                url = await self._open_url(self._token)
                async with self._connect(url) as ws:
                    asked_to_reconnect = await self._serve(ws)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - logged, then retried
                logger.warning("slack socket mode: %s", _describe(exc))
            finally:
                self.connected = False
            if self._greeted:
                delay = self._first
                quick = time.monotonic()
                if (
                    asked_to_reconnect
                    and quick - self._last_quick >= QUICK_RECONNECT_GAP
                ):
                    self._last_quick = quick
                    continue
            # Jitter keeps several processes from reconnecting in step.
            await self._sleep(delay * (0.5 + random.random() / 2))
            if not self._greeted:
                delay = min(delay * 2, self._max)

    async def _serve(self, ws) -> bool:
        """Serve one socket until Slack closes it or asks for a new one.
        Returns whether Slack asked for a new one."""
        async for raw in ws:
            try:
                message = json.loads(raw)
            except (TypeError, ValueError):
                continue
            if not isinstance(message, dict):
                continue
            kind = message.get("type")
            if kind == "hello":
                info = message.get("connection_info")
                app_id = info.get("app_id") if isinstance(info, dict) else None
                if isinstance(app_id, str) and app_id and self.check_app is not None:
                    if not await self.check_app(app_id):
                        self.wrong_app = app_id
                        return False
                self._greeted = self.connected = True
                continue
            if kind == "disconnect":
                return True
            envelope_id = message.get("envelope_id")
            if envelope_id:
                # Acknowledge first: Slack retries an envelope it does not
                # hear back about within three seconds.
                await ws.send(json.dumps({"envelope_id": envelope_id}))
            payload = message.get("payload")
            if kind in HANDLED and isinstance(payload, dict):
                task = asyncio.create_task(self._handle(kind, payload))
                self.tasks.add(task)
                task.add_done_callback(self.tasks.discard)
        return False


def _describe(exc: Exception) -> str:
    # A socket URL carries a ticket, so connection errors are named, not shown.
    if isinstance(exc, SocketModeError):
        if exc.refused:
            return f"Slack refused the socket: {exc}"
        return f"Slack did not open a socket: {exc}"
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


async def _report_failure(payload: dict[str, Any]) -> None:
    """Tell the person an acknowledged envelope failed; Slack shows nothing
    otherwise."""
    from backend.api.routes.slack_paging import _post_answer

    response_url = payload.get("response_url")
    if not (isinstance(response_url, str) and response_url):
        return
    try:
        await _post_answer(response_url, FAILURE_TEXT)
    except Exception as exc:  # noqa: BLE001 - best effort
        logger.warning(
            "slack socket mode: failure reply not sent: %s", type(exc).__name__
        )


@dataclass
class _Socket:
    client: SocketModeClient
    task: asyncio.Task
    token: str
    renewed: datetime
    # The last tick that found the socket connected, or when it started.
    seen_connected: datetime


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
        # A holder closes its sockets half a renewal before the lease runs out,
        # so they are gone before another process can take it over.
        self._fence = timedelta(seconds=lease_seconds - renew_seconds / 2)
        # A socket not connected for a lease gives it up, and the process rests
        # a little over one renewal: long enough for another process to try.
        self._grace = timedelta(seconds=lease_seconds)
        self._rest = timedelta(seconds=renew_seconds + 5)
        self._env = env
        self._client_factory = client_factory
        self._sockets: dict[uuid.UUID, _Socket] = {}
        # Connectors whose socket gave up, and until when not to take them.
        self._resting: dict[uuid.UUID, datetime] = {}
        self._inflight: set[asyncio.Task] = set()
        self._conflicts: dict[uuid.UUID, list[str]] = {}
        # Connectors whose socket greeted for another app, with the tokens it
        # was found with: no socket until one of them changes.
        self._wrong_app: dict[uuid.UUID, tuple[str, str | None, str]] = {}
        self._app_ids: dict[str, str | None] = {}
        self._bots: dict[uuid.UUID, str | None] = {}
        self._task: asyncio.Task | None = None

    async def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(
                self._loop(), name="opsmender-slack-socket-mode"
            )

    async def stop(self) -> None:
        """Stop reading envelopes, let the ones being handled finish for a
        moment, then give up the leases so another process can take the
        sockets at once."""
        sockets = list(self._sockets.items())
        self._sockets.clear()
        tasks = [s.task for _, s in sockets]
        if self._task is not None:
            tasks.append(self._task)
            self._task = None
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        # The sockets are closed: another process may take them over while
        # the envelopes already read finish here.
        if sockets:
            try:
                async with self._factory() as db:
                    for connector_id, _ in sockets:
                        await RuntimeConfigRepo.release_lease(
                            db, lease_key(connector_id), self.holder
                        )
                    await db.commit()
            except Exception as exc:  # noqa: BLE001 - the lease expires on its own
                logger.warning(
                    "slack socket mode: lease release failed: %s", _describe(exc)
                )
        # Handlers register when they start; one created just now has not yet.
        inflight = set(self._inflight).union(
            *(getattr(s.client, "tasks", ()) for _, s in sockets)
        )
        if inflight:
            _, late = await asyncio.wait(inflight, timeout=DRAIN_SECONDS)
            for task in late:
                task.cancel()
            await asyncio.gather(*late, return_exceptions=True)

    async def _loop(self) -> None:
        while True:
            started = time.monotonic()
            try:
                # A tick that hangs on the database counts as a failed renewal,
                # and never runs past the moment a socket must be closed.
                await asyncio.wait_for(self.tick(), timeout=self.tick_budget())
            except Exception as exc:  # noqa: BLE001 - logged, retried next tick
                logger.warning("slack socket mode: tick failed: %s", _describe(exc))
            self.fence()
            await asyncio.sleep(max(0.0, self._renew - (time.monotonic() - started)))

    def tick_budget(self, now: datetime | None = None) -> float:
        """Seconds a tick may take: a renewal, or less when a socket's fence
        comes sooner (never less than one second)."""
        now = now or datetime.now(timezone.utc)
        budget = float(self._renew)
        for held in self._sockets.values():
            budget = min(budget, (held.renewed + self._fence - now).total_seconds())
        return max(budget, 1.0)

    def fence(self, now: datetime | None = None) -> set[uuid.UUID]:
        """Close every socket whose lease has not been renewed for
        ``lease_seconds - renew_seconds / 2``: another process may take the
        lease soon, and two sockets for one app split its clicks. Returns
        them."""
        now = now or datetime.now(timezone.utc)
        closed = {
            connector_id
            for connector_id, held in self._sockets.items()
            if now - held.renewed >= self._fence
        }
        for connector_id in closed:
            self._sockets.pop(connector_id).task.cancel()
            logger.warning(
                "slack socket mode: closed the socket of %s, its lease could "
                "not be renewed",
                connector_id,
            )
        return closed

    async def tick(self, now: datetime | None = None) -> set[uuid.UUID]:
        """Take or renew each lease, run a socket for each one held and close
        the others. Returns the connectors this process holds."""
        now = now or datetime.now(timezone.utc)
        for connector_id, held in self._sockets.items():
            if getattr(held.client, "connected", False):
                held.seen_connected = now
        # Holding a lease without a connection would keep out a process that
        # can connect: give it up, and rest before taking it again.
        given_up = {
            connector_id
            for connector_id, held in self._sockets.items()
            if now - held.seen_connected >= self._grace
        }
        for connector_id in given_up:
            self._resting[connector_id] = now + self._rest
        wrong_app = {
            connector_id: held
            for connector_id, held in self._sockets.items()
            if getattr(held.client, "wrong_app", None)
        }
        held_tokens: dict[uuid.UUID, str] = {}
        async with self._factory() as db:
            connectors = await socket_connectors(db)
            conflicts = token_conflicts(connectors, self._env)
            if conflicts != self._conflicts:
                for connector_id, others in conflicts.items():
                    logger.warning(
                        "slack socket mode: %s shares its app-level token with %s; "
                        "neither gets a socket",
                        connector_id,
                        ", ".join(others),
                    )
                self._conflicts = conflicts
            states: dict[uuid.UUID, dict[str, Any]] = {}
            for connector in connectors:
                token = app_token(connector, self._env)
                if token is None or connector.id in conflicts:
                    continue
                fingerprint = (token, bot_token(connector), app_token_env(connector))
                if connector.id in wrong_app:
                    held = wrong_app[connector.id]
                    self._wrong_app[connector.id] = fingerprint
                    states[connector.id] = {
                        "connected": False,
                        "problem": "wrong_app",
                        "socket_app": held.client.wrong_app,
                    }
                    logger.warning(
                        "slack socket mode: the app-level token of %s belongs to "
                        "Slack app %s, not to the app of its bot token; no socket",
                        connector.id,
                        held.client.wrong_app,
                    )
                    continue
                if self._wrong_app.get(connector.id) == fingerprint:
                    continue
                self._wrong_app.pop(connector.id, None)
                if connector.id in given_up:
                    states[connector.id] = {
                        "connected": False,
                        "problem": "not_connecting",
                    }
                    continue
                resting_until = self._resting.get(connector.id)
                if resting_until is not None:
                    if resting_until > now:
                        continue
                    del self._resting[connector.id]
                if await RuntimeConfigRepo.take_lease(
                    db,
                    connector.org_id,
                    lease_key(connector.id),
                    self.holder,
                    now=now,
                    seconds=self._lease,
                ):
                    held_tokens[connector.id] = token
                    held = self._sockets.get(connector.id)
                    connected = held is not None and bool(
                        getattr(held.client, "connected", False)
                    )
                    states.setdefault(
                        connector.id, {"connected": connected, "problem": None}
                    )
                    self._bots[connector.id] = bot_token(connector)
            for connector_id, state in states.items():
                connector_org = next(
                    c.org_id for c in connectors if c.id == connector_id
                )
                await RuntimeConfigRepo.set(
                    db,
                    connector_org,
                    key=state_key(connector_id),
                    value=json.dumps({"holder": self.holder, **state}),
                )
            for connector_id in self._sockets:
                if connector_id not in held_tokens:
                    await RuntimeConfigRepo.release_lease(
                        db, lease_key(connector_id), self.holder
                    )
            await db.commit()
        for connector_id in list(self._sockets):
            held = self._sockets[connector_id]
            token = held_tokens.get(connector_id)
            if token is None or held.task.done() or held.token != token:
                held.task.cancel()
                del self._sockets[connector_id]
            else:
                held.renewed = now
        for connector_id, token in held_tokens.items():
            if connector_id not in self._sockets:
                client = self._client_factory(token, self._handler(connector_id))
                client.check_app = self._app_checker(connector_id)
                self._sockets[connector_id] = _Socket(
                    client=client,
                    task=asyncio.create_task(
                        client.run(), name=f"opsmender-slack-socket-{connector_id}"
                    ),
                    token=token,
                    renewed=now,
                    seen_connected=now,
                )
        return set(held_tokens)

    def _app_checker(self, connector_id: uuid.UUID) -> Callable[[str], Awaitable[bool]]:
        async def check(app_id: str) -> bool:
            bot = self._bots.get(connector_id)
            if bot is None:
                return True
            if bot not in self._app_ids:
                self._app_ids[bot] = await bot_app_id(bot)
            expected = self._app_ids[bot]
            # Unknown is not wrong: a failed lookup never closes a socket.
            return expected is None or expected == app_id

        return check

    def _handler(self, connector_id: uuid.UUID) -> Handler:
        async def handle(kind: str, payload: dict[str, Any]) -> None:
            task = asyncio.current_task()
            if task is not None:
                self._inflight.add(task)
            try:
                await handle_envelope(
                    self._app, self._factory, connector_id, kind, payload
                )
            except Exception as exc:  # noqa: BLE001 - one envelope, logged
                # The location, not the message: a message can carry the
                # payload's response URL.
                logger.warning(
                    "slack socket mode: %s envelope failed: %s\n%s",
                    kind,
                    type(exc).__name__,
                    "".join(traceback.format_tb(exc.__traceback__)),
                )
                await _report_failure(payload)
            finally:
                if task is not None:
                    self._inflight.discard(task)

        return handle
