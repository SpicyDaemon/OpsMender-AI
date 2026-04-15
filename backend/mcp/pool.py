"""Dynamic MCP server pool.

The pool is the single lookup point for MCP servers at runtime.  Every
query hits the database fresh, so a server added via ``POST /mcp-servers``
is immediately visible to anything that holds a pool reference — including
sessions that are already running.  An optional list of
``MCPServerConfig`` entries from ``.env`` acts as a fallback only when the
database is unreachable.

Usage::

    pool = MCPServerPool(session_factory, env_fallback=cfg.mcp_servers)

    # List current servers (fresh DB read on every call)
    servers = await pool.list_servers()

    # Open a live MCP session by name
    async with pool.connect("k8s-dev") as mcp_session:
        tools = await list_tools(mcp_session)
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncIterator, Sequence

from mcp import ClientSession
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.config_loader import MCPServerConfig
from backend.db.models import MCPServer
from backend.db.repos import MCPServerRepo
from backend.mcp.client import connect as mcp_connect


class MCPPoolError(Exception):
    """Raised when the pool cannot resolve a requested server."""


def _db_to_runtime(server: MCPServer) -> MCPServerConfig:
    """Project a DB row into the runtime config dataclass consumed by the client."""
    return MCPServerConfig(
        name=server.name,
        transport=server.transport,
        command=server.command,
        args=server.args,
        env=server.env_vars,
        url=server.url,
        token=server.token,
    )


class MCPServerPool:
    """Thin DB-backed registry of MCP servers.

    The pool itself holds no cached state.  Every call queries the DB so
    mutations made elsewhere (UI, CLI, tests) are picked up on the next
    lookup without any reload step.
    """

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession] | None,
        *,
        env_fallback: Sequence[MCPServerConfig] | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._env_fallback: list[MCPServerConfig] = list(env_fallback or [])

    # -- introspection ------------------------------------------------------

    async def list_servers(
        self, *, active_only: bool = True
    ) -> list[MCPServerConfig]:
        """Return every known MCP server as a runtime config.

        DB is the source of truth; the env fallback only kicks in if the
        DB is unreachable or no session factory is configured.
        """
        if self._session_factory is None:
            return list(self._env_fallback)
        try:
            async with self._session_factory() as db:
                rows = await MCPServerRepo.list_all(db, active_only=active_only)
                return [_db_to_runtime(row) for row in rows]
        except Exception:
            return list(self._env_fallback)

    async def get_server(self, name: str) -> MCPServerConfig | None:
        """Return a single server by name, re-reading the DB each call."""
        if self._session_factory is not None:
            try:
                async with self._session_factory() as db:
                    row = await MCPServerRepo.get_by_name(db, name)
                    if row is not None:
                        return _db_to_runtime(row)
            except Exception:
                pass
        for fallback in self._env_fallback:
            if fallback.name == name:
                return fallback
        return None

    # -- connections --------------------------------------------------------

    @asynccontextmanager
    async def connect(self, name: str) -> AsyncIterator[ClientSession]:
        """Resolve *name* fresh and open a live MCP session."""
        server = await self.get_server(name)
        if server is None:
            raise MCPPoolError(f"MCP server not found: {name}")
        async with mcp_connect(server) as session:
            yield session
