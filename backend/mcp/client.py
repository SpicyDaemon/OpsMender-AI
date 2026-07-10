"""MCP client wrapper for OpsMender AI.

Provides a unified interface for connecting to MCP servers over stdio, SSE,
or streamable HTTP transport, listing available tools, and calling tools.
"""

from __future__ import annotations

import logging
import os
import shutil
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any, AsyncIterator, Callable

from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client
from mcp.client.sse import sse_client
from mcp.client.streamable_http import streamablehttp_client
from mcp.types import CallToolResult, Tool

from backend.config_loader import MCPServerConfig

if TYPE_CHECKING:
    import httpx
    from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

# Commands that require Node.js to be installed.
_NODE_COMMANDS = frozenset({"node", "npx", "npm"})

# Refresh the access token when it expires within this margin.
OAUTH_REFRESH_MARGIN_SECONDS = 300


class MCPClientError(Exception):
    """Raised when an MCP operation fails."""


async def resolve_oauth_access_token(
    db: "AsyncSession",
    org_id: uuid.UUID,
    mcp_server_id: uuid.UUID,
    server_url: str,
    *,
    http_client_factory: "Callable[[], httpx.AsyncClient] | None" = None,
) -> str:
    """Return a valid OAuth access token for a DB-backed HTTP MCP server.

    Checks token expiry against ``OAUTH_REFRESH_MARGIN_SECONDS`` and
    refreshes automatically when the token is about to expire.  Rotation
    (OAuth 2.1 §4.3.1) is handled transparently — the new refresh token
    is persisted via ``MCPServerOAuthTokenRepo.rotate`` before this
    function returns.

    Raises :class:`backend.mcp.oauth.MCPAuthorizationRequiredError` when:

    * No token row exists for the server.
    * The access token is expiring but no refresh token is available.
    * The issuer or client credentials are missing from the token row.
    * The authorization server rejects the refresh (invalid_grant, etc.).

    The caller is responsible for committing the DB session after a
    successful refresh so the rotated tokens are persisted.
    """

    from backend.db.repos import MCPServerOAuthTokenRepo
    from backend.mcp.oauth import (
        ClientRegistration,
        MCPAuthorizationRequiredError,
        canonical_resource_uri,
        fetch_authz_server_metadata,
        refresh_access_token as _refresh,
    )

    row = await MCPServerOAuthTokenRepo.get_for_server(db, org_id, mcp_server_id)
    if row is None:
        raise MCPAuthorizationRequiredError(
            f"No OAuth credentials for MCP server {mcp_server_id}. "
            "Use the Config page to Connect the server via OAuth."
        )

    access_token, refresh_token = await MCPServerOAuthTokenRepo.read_plaintext(row)

    # Decide whether a refresh is needed.
    needs_refresh = False
    if row.expires_at is not None:
        # SQLite returns naive datetimes; Postgres returns tz-aware ones.
        # Normalise to UTC so the subtraction is always valid.
        expires_at = (
            row.expires_at.replace(tzinfo=timezone.utc)
            if row.expires_at.tzinfo is None
            else row.expires_at
        )
        margin = timedelta(seconds=OAUTH_REFRESH_MARGIN_SECONDS)
        needs_refresh = (expires_at - datetime.now(timezone.utc)) <= margin

    if not needs_refresh:
        return access_token

    # Token is within the expiry margin — attempt to refresh.
    if not refresh_token:
        await MCPServerOAuthTokenRepo.delete_for_server(db, org_id, mcp_server_id)
        raise MCPAuthorizationRequiredError(
            f"Access token for MCP server {mcp_server_id} is expiring and "
            "no refresh token is available. Re-authorize via the Config page."
        )

    if not row.issuer:
        raise MCPAuthorizationRequiredError(
            f"Cannot refresh token for MCP server {mcp_server_id}: "
            "no issuer recorded. Re-authorize via the Config page."
        )

    client_id, client_secret = await MCPServerOAuthTokenRepo.read_client_credentials(
        row
    )
    if not client_id:
        raise MCPAuthorizationRequiredError(
            f"Cannot refresh token for MCP server {mcp_server_id}: "
            "no client credentials recorded. Re-authorize via the Config page."
        )

    try:
        metadata = await fetch_authz_server_metadata(
            row.issuer, http_client_factory=http_client_factory
        )
        token_resp = await _refresh(
            metadata,
            refresh_token=refresh_token,
            resource=canonical_resource_uri(server_url),
            client_registration=ClientRegistration(
                client_id=client_id,
                client_secret=client_secret,
            ),
            http_client_factory=http_client_factory,
        )
    except MCPAuthorizationRequiredError:
        # Invalid grant — clear the stale token row so the status pill
        # immediately shows "reconnect needed" on the next Config page load.
        await MCPServerOAuthTokenRepo.delete_for_server(db, org_id, mcp_server_id)
        raise

    expires_at = (
        datetime.now(timezone.utc) + timedelta(seconds=token_resp.expires_in)
        if token_resp.expires_in is not None
        else None
    )
    await MCPServerOAuthTokenRepo.rotate(
        db,
        org_id,
        mcp_server_id=mcp_server_id,
        access_token=token_resp.access_token,
        refresh_token=token_resp.refresh_token,
        expires_at=expires_at,
        scopes=token_resp.scope,
    )
    return token_resp.access_token


def _resolve_node_command(command: str) -> str:
    """Resolve ``node``, ``npx``, or ``npm`` to a concrete path.

    Resolution order:

    1. **``OPSMENDER_NODE_PATH``** — if set, look for the command inside that
       directory first.  This lets operators point at a custom Node install
       (e.g. a bundled portable runtime placed next to the ``opsmender`` binary).
    2. **System ``$PATH``** — standard lookup via :func:`shutil.which`.
    3. **Fail-loud** — raise :class:`MCPClientError` with a human-readable
       install hint so the user isn't left staring at a cryptic *"file not
       found"* from the subprocess layer.

    Non-Node commands are returned unchanged.
    """
    if command not in _NODE_COMMANDS:
        return command

    # 1. Honour the explicit override.
    node_dir = os.environ.get("OPSMENDER_NODE_PATH")
    if node_dir:
        candidate = os.path.join(node_dir, command)
        if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            logger.debug("Resolved %s via OPSMENDER_NODE_PATH → %s", command, candidate)
            return candidate

    # 2. Fall back to $PATH.
    resolved = shutil.which(command)
    if resolved:
        logger.debug("Resolved %s via PATH → %s", command, resolved)
        return resolved

    # 3. Not found — fail with a helpful message.
    raise MCPClientError(
        f"'{command}' is not installed or not on PATH.\n\n"
        "npx-based MCP servers (e.g. @anthropic/mcp-server-k8s) require "
        "Node.js.\n"
        "  • Docker image: Node is bundled automatically.\n"
        "  • Local / binary install: install Node.js LTS from "
        "https://nodejs.org and ensure 'npx' is on your PATH.\n"
        "  • Or set OPSMENDER_NODE_PATH=/path/to/node/bin in your .env to point "
        "at a custom install.\n"
    )


@asynccontextmanager
async def _connect_stdio(
    server: MCPServerConfig,
) -> AsyncIterator[ClientSession]:
    """Open a stdio connection to a local MCP server process."""
    command = _resolve_node_command(server.command)
    params = StdioServerParameters(
        command=command,
        args=server.args or [],
        env={**os.environ, **(server.env or {})},
    )
    async with stdio_client(params) as (read_stream, write_stream):
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()
            yield session


@asynccontextmanager
async def _connect_sse(
    server: MCPServerConfig,
) -> AsyncIterator[ClientSession]:
    """Open an SSE connection to a remote MCP server."""
    headers: dict[str, str] = {}
    if server.token:
        headers["Authorization"] = f"Bearer {server.token}"
    async with sse_client(server.url, headers=headers) as (
        read_stream,
        write_stream,
    ):
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()
            yield session


@asynccontextmanager
async def _connect_http(
    server: MCPServerConfig,
) -> AsyncIterator[ClientSession]:
    """Open a streamable HTTP connection to a remote MCP server."""
    headers: dict[str, str] = {}
    if server.token:
        headers["Authorization"] = f"Bearer {server.token}"
    async with streamablehttp_client(server.url, headers=headers) as (
        read_stream,
        write_stream,
        _get_session_id,
    ):
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()
            yield session


@asynccontextmanager
async def connect(server: MCPServerConfig) -> AsyncIterator[ClientSession]:
    """Connect to an MCP server using the configured transport.

    Usage::

        async with connect(server_config) as session:
            tools = await list_tools(session)
    """
    if server.transport == "stdio":
        async with _connect_stdio(server) as session:
            yield session
    elif server.transport == "sse":
        async with _connect_sse(server) as session:
            yield session
    elif server.transport == "http":
        async with _connect_http(server) as session:
            yield session
    else:
        raise MCPClientError(f"Unknown transport: {server.transport}")


async def list_tools(session: ClientSession) -> list[Tool]:
    """Return the list of tools exposed by the connected MCP server."""
    result = await session.list_tools()
    return result.tools


async def call_tool(
    session: ClientSession, tool_name: str, arguments: dict[str, Any] | None = None
) -> CallToolResult:
    """Call a tool on the connected MCP server and return the result."""
    return await session.call_tool(tool_name, arguments or {})
