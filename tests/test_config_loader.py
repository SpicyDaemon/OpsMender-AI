"""Tests for backend.config_loader."""

from __future__ import annotations

import json

import pytest

from backend.config_loader import Config, MCPServerConfig, set_env_path


@pytest.fixture(autouse=True)
def reset_env_path():
    set_env_path(None)
    yield
    set_env_path(None)


@pytest.fixture()
def valid_env(tmp_path):
    """Write a valid .env file with MCP servers and return its path."""
    env_file = tmp_path / ".env"
    env_file.write_text(
        "OPSMENDER_MCP_SERVERS_JSON="
        + json.dumps(
            [
                {
                    "name": "local-k8s",
                    "transport": "stdio",
                    "command": "npx",
                    "args": ["-y", "@anthropic/mcp-server-k8s"],
                },
                {
                    "name": "remote",
                    "transport": "sse",
                    "url": "http://localhost:8080/sse",
                },
            ]
        )
        + "\n"
        + "OPSMENDER_TIER=2\n"
        + "OPSMENDER_LOG_LEVEL=DEBUG\n"
        + "OPSMENDER_APPROVAL_TIMEOUT_SECONDS=120\n"
    )
    return env_file


class TestConfigLoad:
    def test_loads_valid_env_file(self, valid_env):
        cfg = Config.load(valid_env)
        assert len(cfg.mcp_servers) == 2
        assert cfg.mcp_servers[0].name == "local-k8s"
        assert cfg.mcp_servers[0].transport == "stdio"
        assert cfg.mcp_servers[0].command == "npx"
        assert cfg.mcp_servers[1].name == "remote"
        assert cfg.mcp_servers[1].transport == "sse"
        assert cfg.mcp_servers[1].url == "http://localhost:8080/sse"
        assert cfg.tiers["default"] == 2
        assert cfg.logging["level"] == "DEBUG"
        assert cfg.approvals.timeout_seconds == 120
        assert cfg.sessions.approval_hold_ttl_seconds == 120

    def test_missing_explicit_env_file_raises(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            Config.load(tmp_path / "nonexistent.env")

    def test_missing_keys_default_cleanly(self, tmp_path):
        env_file = tmp_path / ".env"
        env_file.write_text("# empty env\n")
        cfg = Config.load(env_file)
        assert cfg.mcp_servers == []
        assert cfg.tiers == {"default": 2}
        assert cfg.logging == {"level": "INFO"}
        assert cfg.audit.output == "./logs/audit.jsonl"
        assert cfg.approvals.timeout_seconds == 900
        assert cfg.sessions.queue_ttl_seconds == 900
        assert cfg.sessions.approval_hold_ttl_seconds == 900
        assert cfg.sessions.approval_warning_seconds == 60
        assert cfg.sessions.sweep_interval_seconds == 30
        assert cfg.cors.origins == ["*"]

    def test_session_orchestration_env_overrides(self, tmp_path):
        env_file = tmp_path / ".env"
        env_file.write_text(
            "OPSMENDER_SESSION_QUEUE_TTL_SECONDS=300\n"
            "OPSMENDER_APPROVAL_HOLD_TTL_SECONDS=600\n"
            "OPSMENDER_APPROVAL_EXTENSION_WARNING_SECONDS=45\n"
            "OPSMENDER_SESSION_QUEUE_SWEEP_SECONDS=20\n"
        )
        cfg = Config.load(env_file)
        assert cfg.sessions.queue_ttl_seconds == 300
        assert cfg.sessions.approval_hold_ttl_seconds == 600
        assert cfg.sessions.approval_warning_seconds == 45
        assert cfg.sessions.sweep_interval_seconds == 20

    def test_invalid_mcp_servers_json_raises(self, tmp_path):
        env_file = tmp_path / ".env"
        env_file.write_text("OPSMENDER_MCP_SERVERS_JSON=not-json\n")
        with pytest.raises(ValueError, match="OPSMENDER_MCP_SERVERS_JSON"):
            Config.load(env_file)

    def test_process_env_overrides_env_file(self, tmp_path, monkeypatch):
        env_file = tmp_path / ".env"
        env_file.write_text("OPSMENDER_TIER=2\n")
        monkeypatch.setenv("OPSMENDER_TIER", "3")
        cfg = Config.load(env_file)
        assert cfg.tiers["default"] == 3

    def test_people_visibility_flags_default_to_false(self, tmp_path):
        """Sprint 64 — both visibility flags default to false so a fresh
        install lands on the simple-by-default auth UX."""
        env_file = tmp_path / ".env"
        env_file.write_text("")
        cfg = Config.load(env_file)
        assert cfg.people.multi_org_enabled is False
        assert cfg.people.advanced_auth_enabled is False

    def test_advanced_auth_enabled_reads_env_flag(self, tmp_path):
        """Sprint 64 — operators opt in to SSO/SAML admin surfaces via
        OPSMENDER_ADVANCED_AUTH_ENABLED. The flag is a visibility hint
        only; SSO/SAML runtime routes keep working regardless."""
        env_file = tmp_path / ".env"
        env_file.write_text("OPSMENDER_ADVANCED_AUTH_ENABLED=true\n")
        cfg = Config.load(env_file)
        assert cfg.people.advanced_auth_enabled is True
        # multi_org stays off — flags are independent of each other.
        assert cfg.people.multi_org_enabled is False


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
        server = MCPServerConfig(
            name="k8s",
            transport="stdio",
            command="npx",
            args=["-y", "server"],
        )
        assert server.name == "k8s"
        assert server.command == "npx"
        assert server.args == ["-y", "server"]

    def test_valid_sse(self):
        server = MCPServerConfig(
            name="remote",
            transport="sse",
            url="http://localhost:8080/sse",
        )
        assert server.url == "http://localhost:8080/sse"

    def test_valid_http(self):
        server = MCPServerConfig(
            name="sourcebot",
            transport="http",
            url="https://sb.example.com/api/mcp",
            token="secret",
        )
        assert server.transport == "http"
        assert server.url == "https://sb.example.com/api/mcp"
        assert server.token == "secret"
