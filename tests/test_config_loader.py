"""Tests for backend.config_loader."""

import pytest

from backend.config_loader import Config, MCPServerConfig


@pytest.fixture()
def valid_yaml(tmp_path):
    """Write a valid config.yaml with MCP servers and return its path."""
    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        "mcp_servers:\n"
        "  - name: local-k8s\n"
        "    transport: stdio\n"
        "    command: npx\n"
        "    args: ['-y', '@anthropic/mcp-server-k8s']\n"
        "  - name: remote\n"
        "    transport: sse\n"
        "    url: http://localhost:8080/sse\n"
        "tiers:\n"
        "  default: 2\n"
        "logging:\n"
        "  level: DEBUG\n"
    )
    return cfg


class TestConfigLoad:
    def test_loads_valid_yaml(self, valid_yaml):
        cfg = Config.load(valid_yaml)
        assert len(cfg.mcp_servers) == 2
        assert cfg.mcp_servers[0].name == "local-k8s"
        assert cfg.mcp_servers[0].transport == "stdio"
        assert cfg.mcp_servers[0].command == "npx"
        assert cfg.mcp_servers[1].name == "remote"
        assert cfg.mcp_servers[1].transport == "sse"
        assert cfg.mcp_servers[1].url == "http://localhost:8080/sse"
        assert cfg.tiers["default"] == 2
        assert cfg.logging["level"] == "DEBUG"

    def test_missing_file_raises(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            Config.load(tmp_path / "nonexistent.yaml")

    def test_missing_keys_default_to_empty(self, tmp_path):
        cfg_file = tmp_path / "empty.yaml"
        cfg_file.write_text("# empty config\n")
        cfg = Config.load(cfg_file)
        assert cfg.mcp_servers == []
        assert cfg.tiers == {}
        assert cfg.logging == {}
        assert cfg.approvals.timeout_seconds == 900

    def test_empty_mcp_servers(self, tmp_path):
        cfg_file = tmp_path / "no_servers.yaml"
        cfg_file.write_text("mcp_servers: []\ntiers:\n  default: 3\n")
        cfg = Config.load(cfg_file)
        assert cfg.mcp_servers == []
        assert cfg.tiers["default"] == 3

    def test_loads_approval_timeout(self, tmp_path):
        cfg_file = tmp_path / "approvals.yaml"
        cfg_file.write_text(
            "mcp_servers: []\n"
            "approvals:\n"
            "  timeout_seconds: 120\n"
        )
        cfg = Config.load(cfg_file)
        assert cfg.approvals.timeout_seconds == 120


class TestMCPServerConfig:
    def test_invalid_transport_raises(self):
        with pytest.raises(ValueError, match="transport must be"):
            MCPServerConfig(name="bad", transport="grpc")

    def test_stdio_without_command_raises(self):
        with pytest.raises(ValueError, match="requires 'command'"):
            MCPServerConfig(name="bad", transport="stdio")

    def test_sse_without_url_raises(self):
        with pytest.raises(ValueError, match="requires 'url'"):
            MCPServerConfig(name="bad", transport="sse")

    def test_http_without_url_raises(self):
        with pytest.raises(ValueError, match="requires 'url'"):
            MCPServerConfig(name="bad", transport="http")

    def test_valid_stdio(self):
        s = MCPServerConfig(name="k8s", transport="stdio", command="npx", args=["-y", "server"])
        assert s.name == "k8s"
        assert s.command == "npx"
        assert s.args == ["-y", "server"]

    def test_valid_sse(self):
        s = MCPServerConfig(name="remote", transport="sse", url="http://localhost:8080/sse")
        assert s.url == "http://localhost:8080/sse"

    def test_valid_http(self):
        s = MCPServerConfig(
            name="sourcebot", transport="http",
            url="https://sb.example.com/api/mcp", token="secret"
        )
        assert s.transport == "http"
        assert s.url == "https://sb.example.com/api/mcp"
        assert s.token == "secret"
