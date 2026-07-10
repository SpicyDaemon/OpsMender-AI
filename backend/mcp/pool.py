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

import uuid
from contextlib import asynccontextmanager
from typing import AsyncIterator, Sequence

from mcp import ClientSession
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.config_loader import MCPServerConfig
from backend.db.models import MCPServer
from backend.db.repos import MCPServerOAuthTokenRepo, MCPServerRepo
from backend.mcp.client import connect as mcp_connect, resolve_oauth_access_token
from backend.mcp.oauth import MCPAuthorizationRequiredError


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
        self, org_id: uuid.UUID | None = None, *, active_only: bool = True
    ) -> list[MCPServerConfig]:
        """Return every known MCP server as a runtime config.

        DB is the source of truth; the env fallback only kicks in if the
        DB is unreachable or no session factory is configured.
        """
        if self._session_factory is None or org_id is None:
            return list(self._env_fallback)
        try:
            async with self._session_factory() as db:
                rows = await MCPServerRepo.list_all(db, org_id, active_only=active_only)
                return [_db_to_runtime(row) for row in rows]
        except Exception:
            return list(self._env_fallback)

    async def get_server(
        self, org_id: uuid.UUID | None = None, name: str | None = None
    ) -> MCPServerConfig | None:
        # Backward compatibility for old calls: get_server(name)
        if name is None and isinstance(org_id, str):
            name = org_id
            org_id = None

        if self._session_factory is not None and org_id is not None:
            try:
                async with self._session_factory() as db:
                    row = await MCPServerRepo.get_by_name(db, org_id, name)
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
    async def connect(
        self, org_id: uuid.UUID | None, name: str
    ) -> AsyncIterator[ClientSession]:
        """Resolve *name* fresh and open a live MCP session.

        For HTTP/SSE servers that have an OAuth token row in the DB, the
        access token is refreshed automatically (``OAUTH_REFRESH_MARGIN_SECONDS``
        before expiry) before the connection is opened.

        :raises MCPAuthorizationRequiredError: when the token is expiring
            and cannot be refreshed (no refresh token, invalid grant, etc.).
            The caller should propagate this so the operator can Reconnect
            via the Config page.
        """
        cfg, server_id = await self._resolve_server_config_with_oauth(org_id, name)
        if cfg is None:
            raise MCPPoolError(f"MCP server not found: {name}")
        try:
            async with mcp_connect(cfg) as session:
                await self._record_connection_success(org_id, server_id)
                yield session
        except Exception as exc:
            await self._record_connection_failure(org_id, server_id, str(exc))
            raise

    async def _resolve_server_config_with_oauth(
        self,
        org_id: uuid.UUID | None,
        name: str,
    ) -> tuple[MCPServerConfig | None, uuid.UUID | None]:
        """Return an ``MCPServerConfig``, injecting a refreshed OAuth bearer
        token when the server has an OAuth token row in the DB.

        The DB session is committed (if a token was rotated) and closed
        before the MCP connection is opened so the two lifetimes don't
        overlap.
        """
        if self._session_factory is not None and org_id is not None:
            try:
                async with self._session_factory() as db:
                    row = await MCPServerRepo.get_by_name(db, org_id, name)
                    if row is None:
                        return None, None

                    cfg = _db_to_runtime(row)

                    if row.transport in ("http", "sse") and row.url:
                        token_row = await MCPServerOAuthTokenRepo.get_for_server(
                            db, org_id, row.id
                        )
                        if token_row is not None:
                            try:
                                access_token = await resolve_oauth_access_token(
                                    db, org_id, row.id, row.url
                                )
                            except MCPAuthorizationRequiredError as exc:
                                await MCPServerRepo.mark_connection_failure(
                                    db,
                                    org_id,
                                    row.id,
                                    error=str(exc),
                                )
                                await db.commit()
                                raise
                            await db.commit()
                            return (
                                MCPServerConfig(
                                    name=cfg.name,
                                    transport=cfg.transport,
                                    command=cfg.command,
                                    args=cfg.args,
                                    env=cfg.env,
                                    url=cfg.url,
                                    token=access_token,
                                ),
                                row.id,
                            )
                    return cfg, row.id
            except MCPAuthorizationRequiredError:
                raise
            except Exception:
                pass  # DB unreachable — fall through to env fallback

        for fallback in self._env_fallback:
            if fallback.name == name:
                return fallback, None
        return None, None

    async def _record_connection_success(
        self,
        org_id: uuid.UUID | None,
        server_id: uuid.UUID | None,
    ) -> None:
        if self._session_factory is None or org_id is None or server_id is None:
            return
        try:
            async with self._session_factory() as db:
                await MCPServerRepo.mark_connection_success(db, org_id, server_id)
                await db.commit()
        except Exception:
            return

    async def _record_connection_failure(
        self,
        org_id: uuid.UUID | None,
        server_id: uuid.UUID | None,
        error: str,
    ) -> None:
        if self._session_factory is None or org_id is None or server_id is None:
            return
        try:
            async with self._session_factory() as db:
                await MCPServerRepo.mark_connection_failure(
                    db,
                    org_id,
                    server_id,
                    error=error,
                )
                await db.commit()
        except Exception:
            return
