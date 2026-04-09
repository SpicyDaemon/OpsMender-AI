"""MCP client wrapper for AI Incident Manager.

Provides a unified interface for connecting to MCP servers over stdio, SSE,
or streamable HTTP transport, listing available tools, and calling tools.
"""

from __future__ import annotations

import os
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client
from mcp.client.sse import sse_client
from mcp.client.streamable_http import streamablehttp_client
from mcp.types import CallToolResult, Tool

from backend.config_loader import MCPServerConfig


class MCPClientError(Exception):
    """Raised when an MCP operation fails."""


@asynccontextmanager
async def _connect_stdio(
    server: MCPServerConfig,
) -> AsyncIterator[ClientSession]:
    """Open a stdio connection to a local MCP server process."""
    params = StdioServerParameters(
        command=server.command,
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
