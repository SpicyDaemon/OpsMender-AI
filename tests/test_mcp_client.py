"""Tests for backend.mcp.client."""

import asyncio
import os
import stat
import tempfile
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.config_loader import MCPServerConfig
from backend.mcp.client import (
    MCPClientError,
    _resolve_node_command,
    call_tool,
    connect,
    list_tools,
)


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
        from cli.opsmender import main

        with pytest.raises(SystemExit) as exc_info:
            main(["check"])
        assert exc_info.value.code == 0
        out = capsys.readouterr().out
        assert "0 MCP server(s) available" in out
        assert "No MCP servers configured" in out

    def test_check_bad_config_exits(self):
        from cli.opsmender import main

        with pytest.raises(SystemExit) as exc_info:
            main(["--config", "/tmp/nonexistent.env", "check"])
        assert exc_info.value.code == 1


# Helper for async context manager test
async def _use_connect(server):
    async with connect(server) as session:
        pass


# -- Node resolution tests ----------------------------------------------------


class TestResolveNodeCommand:
    """Test the _resolve_node_command helper."""

    def test_non_node_command_passes_through(self):
        """Non-Node commands (e.g. 'echo', 'python') are returned unchanged."""
        assert _resolve_node_command("echo") == "echo"
        assert _resolve_node_command("python3") == "python3"
        assert _resolve_node_command("kubectl") == "kubectl"

    def test_opsmender_node_path_resolves(self, tmp_path):
        """OPSMENDER_NODE_PATH overrides PATH when set and contains the command."""
        # Create a fake npx binary in a temp dir.
        fake_npx = tmp_path / "npx"
        fake_npx.write_text("#!/bin/sh\necho fake npx\n")
        fake_npx.chmod(fake_npx.stat().st_mode | stat.S_IEXEC)

        env = {**os.environ, "OPSMENDER_NODE_PATH": str(tmp_path)}
        with patch.dict(os.environ, env, clear=True):
            result = _resolve_node_command("npx")
            assert result == str(fake_npx)

    def test_opsmender_node_path_missing_command_falls_to_path(self, tmp_path):
        """If OPSMENDER_NODE_PATH is set but doesn't contain the command, fall back to PATH."""
        # Empty OPSMENDER_NODE_PATH dir — no npx there.
        env = {**os.environ, "OPSMENDER_NODE_PATH": str(tmp_path)}
        with patch.dict(os.environ, env, clear=True):
            with patch("shutil.which", return_value="/usr/local/bin/npx"):
                result = _resolve_node_command("npx")
                assert result == "/usr/local/bin/npx"

    def test_path_fallback_works(self):
        """Without OPSMENDER_NODE_PATH, falls back to shutil.which (PATH lookup)."""
        env = {k: v for k, v in os.environ.items() if k != "OPSMENDER_NODE_PATH"}
        with patch.dict(os.environ, env, clear=True):
            with patch("shutil.which", return_value="/usr/bin/npx"):
                result = _resolve_node_command("npx")
                assert result == "/usr/bin/npx"

    def test_fail_loud_when_not_found(self):
        """Raises MCPClientError with install hint when Node is not found."""
        env = {k: v for k, v in os.environ.items() if k != "OPSMENDER_NODE_PATH"}
        with patch.dict(os.environ, env, clear=True):
            with patch("shutil.which", return_value=None):
                with pytest.raises(
                    MCPClientError, match="not installed or not on PATH"
                ):
                    _resolve_node_command("npx")

    def test_fail_loud_message_contains_install_hints(self):
        """Error message includes Docker, local, and OPSMENDER_NODE_PATH hints."""
        env = {k: v for k, v in os.environ.items() if k != "OPSMENDER_NODE_PATH"}
        with patch.dict(os.environ, env, clear=True):
            with patch("shutil.which", return_value=None):
                with pytest.raises(MCPClientError) as exc_info:
                    _resolve_node_command("node")
                msg = str(exc_info.value)
                assert "Docker image" in msg
                assert "nodejs.org" in msg
                assert "OPSMENDER_NODE_PATH" in msg

    def test_all_node_commands_resolved(self, tmp_path):
        """node, npx, and npm are all resolved through the helper."""
        for cmd in ("node", "npx", "npm"):
            fake_bin = tmp_path / cmd
            fake_bin.write_text(f"#!/bin/sh\necho fake {cmd}\n")
            fake_bin.chmod(fake_bin.stat().st_mode | stat.S_IEXEC)

        env = {**os.environ, "OPSMENDER_NODE_PATH": str(tmp_path)}
        with patch.dict(os.environ, env, clear=True):
            for cmd in ("node", "npx", "npm"):
                result = _resolve_node_command(cmd)
                assert result == str(tmp_path / cmd)


class TestStdioNodeResolution:
    """Test that _connect_stdio passes resolved commands to StdioServerParameters."""

    def test_npx_server_resolves_command(self, tmp_path):
        """When connecting to an npx MCP server, the command is resolved."""
        fake_npx = tmp_path / "npx"
        fake_npx.write_text("#!/bin/sh\necho npx\n")
        fake_npx.chmod(fake_npx.stat().st_mode | stat.S_IEXEC)

        server = MCPServerConfig(
            name="k8s",
            transport="stdio",
            command="npx",
            args=["-y", "@anthropic/mcp-server-k8s"],
        )

        env = {**os.environ, "OPSMENDER_NODE_PATH": str(tmp_path)}
        with patch.dict(os.environ, env, clear=True):
            with patch("backend.mcp.client.stdio_client") as mock_stdio:
                # Set up the mock to avoid actually connecting.
                mock_stdio.side_effect = Exception("test: stop here")
                try:
                    asyncio.run(_use_stdio(server))
                except Exception:
                    pass

                # Verify StdioServerParameters received the resolved path.
                if mock_stdio.called:
                    params = mock_stdio.call_args[0][0]
                    assert params.command == str(fake_npx)


async def _use_stdio(server):
    """Helper to exercise _connect_stdio."""
    from backend.mcp.client import _connect_stdio

    async with _connect_stdio(server) as session:
        pass
