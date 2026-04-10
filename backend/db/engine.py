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

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)


def get_engine(
    url: str,
    *,
    echo: bool = False,
    pool_size: int = 5,
) -> AsyncEngine:
    """Create an async SQLAlchemy engine from a database URL."""
    return create_async_engine(url, echo=echo, pool_size=pool_size)


def get_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    """Return an async session factory bound to *engine*."""
    return async_sessionmaker(engine, expire_on_commit=False)
