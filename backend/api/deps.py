"""FastAPI dependency providers for AIM.

Centralises all injectable dependencies:

- ``get_db``  — yields an async SQLAlchemy session (set at startup)
- ``get_config`` — returns the config loader object

The actual session factory is wired in ``app.py`` via the lifespan
handler.  This module just holds the reference so route modules can
import it.
"""

from __future__ import annotations

from typing import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

# ---------------------------------------------------------------------------
# Session factory — set at startup by lifespan handler
# ---------------------------------------------------------------------------

_session_factory: async_sessionmaker[AsyncSession] | None = None


def set_session_factory(factory: async_sessionmaker[AsyncSession]) -> None:
    """Called once during app startup to provide the session factory."""
    global _session_factory
    _session_factory = factory


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency — yields a DB session and commits/rollbacks.

    Raises ``RuntimeError`` if called before ``set_session_factory()``.
    """
    if _session_factory is None:
        raise RuntimeError("Database session factory not initialised")
    async with _session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
