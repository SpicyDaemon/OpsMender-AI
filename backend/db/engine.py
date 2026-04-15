"""Async SQLAlchemy engine and session factory.

Provides ``get_engine()`` and ``get_session()`` for the rest of the
application.  Configuration is driven by a database URL string
(``postgresql+asyncpg://…``).

Usage::

    engine = get_engine("postgresql+asyncpg://user:pass@localhost/aim")
    async with get_session(engine) as session:
        ...
"""

from __future__ import annotations

import socket
from urllib.parse import urlparse

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from backend.config_loader import DatabaseConfig


def _url_reachable(url: str, *, timeout: float = 0.25) -> bool:
    """Return ``True`` when the URL host:port appears reachable."""
    if url.startswith("sqlite"):
        return True

    parsed = urlparse(url)
    hostname = parsed.hostname
    if not hostname:
        return False

    port = parsed.port or 5432
    try:
        with socket.create_connection((hostname, port), timeout=timeout):
            return True
    except OSError:
        return False


def resolve_database_url(config: DatabaseConfig) -> str:
    """Resolve the effective DB URL with local fallbacks.

    Order:
    1. Explicit ``AIM_DATABASE_URL``
    2. Local Postgres if reachable
    3. Local SQLite file
    """
    if config.url:
        return config.url
    if _url_reachable(config.local_postgres_url):
        return config.local_postgres_url
    return config.sqlite_url


def get_engine(
    url: str,
    *,
    echo: bool = False,
    pool_size: int = 5,
) -> AsyncEngine:
    """Create an async SQLAlchemy engine from a database URL."""
    kwargs = {"echo": echo}
    if not url.startswith("sqlite"):
        kwargs["pool_size"] = pool_size
    return create_async_engine(url, **kwargs)


def get_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    """Return an async session factory bound to *engine*."""
    return async_sessionmaker(engine, expire_on_commit=False)
