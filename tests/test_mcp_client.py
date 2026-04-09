"""Tests for backend.mcp.client."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.config_loader import MCPServerConfig
from backend.mcp.client import MCPClientError, call_tool, connect, list_tools


# -- Unit tests using mocks ---------------------------------------------------


@pytest.fixture()
def stdio_server():
    return MCPServerConfig(name="test-stdio", transport="stdio", command="echo")


@pytest.fixture()
def sse_server():
    return MCPServerConfig(
        name="test-sse", transport="sse", url="http://localhost:9999/sse"
    )


class TestConnect:
    def test_unknown_transport_raises(self):
        # Bypass __post_init__ validation to test connect() guard
        server = MCPServerConfig.__new__(MCPServerConfig)
        server.name = "bad"
        server.transport = "grpc"
        server.command = None
        server.args = None
        server.env = None
        server.url = None
        with pytest.raises(MCPClientError, match="Unknown transport"):
            asyncio.run(_use_connect(server))


class TestListTools:
    def test_list_tools_returns_tool_list(self):
        mock_tool = MagicMock()
        mock_tool.name = "get_pods"
        mock_session = AsyncMock()
        mock_session.list_tools.return_value = MagicMock(tools=[mock_tool])
        tools = asyncio.run(list_tools(mock_session))
        assert len(tools) == 1
        assert tools[0].name == "get_pods"

    def test_list_tools_empty(self):
        mock_session = AsyncMock()
        mock_session.list_tools.return_value = MagicMock(tools=[])
        tools = asyncio.run(list_tools(mock_session))
        assert tools == []


class TestCallTool:
    def test_call_tool_forwards_args(self):
        mock_session = AsyncMock()
        expected = MagicMock()
        mock_session.call_tool.return_value = expected
        result = asyncio.run(call_tool(mock_session, "restart_pod", {"name": "api"}))
        mock_session.call_tool.assert_called_once_with("restart_pod", {"name": "api"})
        assert result is expected

    def test_call_tool_default_empty_args(self):
        mock_session = AsyncMock()
        asyncio.run(call_tool(mock_session, "list_pods"))
        mock_session.call_tool.assert_called_once_with("list_pods", {})


# -- CLI check subcommand tests -----------------------------------------------


class TestCheckCommand:
    def test_check_no_servers(self, capsys):
        from cli.aim import main

        with pytest.raises(SystemExit) as exc_info:
            main(["check"])
        assert exc_info.value.code == 0
        out = capsys.readouterr().out
        assert "0 MCP server(s) configured" in out
        assert "No MCP servers configured" in out

    def test_check_bad_config_exits(self):
        from cli.aim import main

        with pytest.raises(SystemExit) as exc_info:
            main(["--config", "/tmp/nonexistent.yaml", "check"])
        assert exc_info.value.code == 1


# Helper for async context manager test
async def _use_connect(server):
    async with connect(server) as session:
        pass
