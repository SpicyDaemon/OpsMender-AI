"""MCP client wrapper for AI Incident Manager.

Provides a unified interface for connecting to MCP servers over stdio, SSE,
or streamable HTTP transport, listing available tools, and calling tools.
"""

from __future__ import annotations

import logging
import os
import shutil
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client
from mcp.client.sse import sse_client
from mcp.client.streamable_http import streamablehttp_client
from mcp.types import CallToolResult, Tool

from backend.config_loader import MCPServerConfig

logger = logging.getLogger(__name__)

# Commands that require Node.js to be installed.
_NODE_COMMANDS = frozenset({"node", "npx", "npm"})


class MCPClientError(Exception):
    """Raised when an MCP operation fails."""


def _resolve_node_command(command: str) -> str:
    """Resolve ``node``, ``npx``, or ``npm`` to a concrete path.

    Resolution order:

    1. **``AIM_NODE_PATH``** — if set, look for the command inside that
       directory first.  This lets operators point at a custom Node install
       (e.g. a bundled portable runtime placed next to the ``aim`` binary).
    2. **System ``$PATH``** — standard lookup via :func:`shutil.which`.
    3. **Fail-loud** — raise :class:`MCPClientError` with a human-readable
       install hint so the user isn't left staring at a cryptic *"file not
       found"* from the subprocess layer.

    Non-Node commands are returned unchanged.
    """
    if command not in _NODE_COMMANDS:
        return command

    # 1. Honour the explicit override.
    node_dir = os.environ.get("AIM_NODE_PATH")
    if node_dir:
        candidate = os.path.join(node_dir, command)
        if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            logger.debug("Resolved %s via AIM_NODE_PATH → %s", command, candidate)
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
        "  • Or set AIM_NODE_PATH=/path/to/node/bin in your .env to point "
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
