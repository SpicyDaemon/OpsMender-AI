"""Small PostgreSQL LISTEN/NOTIFY coordination bus."""

from __future__ import annotations

import asyncio
import inspect
import json
import logging
import re
from collections.abc import Awaitable, Callable
from typing import Any

logger = logging.getLogger(__name__)

_CHANNEL_RE = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_.-]{0,62}$")
_MAX_PAYLOAD_BYTES = 7900
_callback_tasks: set[asyncio.Task] = set()


def _validate_channel(channel: str) -> str:
    if not _CHANNEL_RE.fullmatch(channel):
        raise ValueError(f"Invalid PostgreSQL notification channel: {channel!r}")
    return channel


def _encode_payload(payload: Any) -> str:
    encoded = (
        payload
        if isinstance(payload, str)
        else json.dumps(payload, separators=(",", ":"), sort_keys=True)
    )
    if len(encoded.encode("utf-8")) > _MAX_PAYLOAD_BYTES:
        raise ValueError("PostgreSQL notification payload exceeds 7900 bytes")
    return encoded


async def publish(conn, channel: str, payload: Any) -> None:
    """Publish a JSON-compatible payload with ``pg_notify``."""

    await conn.execute(
        "SELECT pg_notify($1, $2)",
        _validate_channel(channel),
        _encode_payload(payload),
    )


async def subscribe(
    conn,
    channel: str,
    callback: Callable[[Any], Awaitable[None] | None],
) -> Callable[[], Awaitable[None]]:
    """Register a notification callback and return an async unsubscribe hook."""

    channel = _validate_channel(channel)

    def task_done(task: asyncio.Task) -> None:
        _callback_tasks.discard(task)
        if task.cancelled():
            return
        exc = task.exception()
        if exc is not None:
            logger.error(
                "PostgreSQL bus callback failed for channel=%s",
                channel,
                exc_info=(type(exc), exc, exc.__traceback__),
            )

    def listener(_connection, _pid: int, _channel: str, raw_payload: str) -> None:
        try:
            payload = json.loads(raw_payload)
        except json.JSONDecodeError:
            payload = raw_payload
        try:
            result = callback(payload)
            if inspect.isawaitable(result):
                task = asyncio.create_task(result)
                _callback_tasks.add(task)
                task.add_done_callback(task_done)
        except Exception:  # noqa: BLE001 - listener must remain registered
            logger.exception("PostgreSQL bus callback failed for channel=%s", channel)

    await conn.add_listener(channel, listener)

    async def unsubscribe() -> None:
        await conn.remove_listener(channel, listener)

    return unsubscribe


def asyncpg_dsn(database_url: str) -> str:
    """Convert the SQLAlchemy async URL into an asyncpg-compatible DSN."""

    if database_url.startswith("postgresql+asyncpg://"):
        return "postgresql://" + database_url.removeprefix("postgresql+asyncpg://")
    if database_url.startswith("postgres://") or database_url.startswith(
        "postgresql://"
    ):
        return database_url
    raise ValueError("Distributed mode requires a PostgreSQL database URL")
