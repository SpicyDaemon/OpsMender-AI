"""Tests for the Claude Code + Codex MCP-server importers (Sprint 42 Step 8)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from backend.mcp.import_from_claude import (
    ImportableServer,
    default_project_config_path as claude_project_path,
    default_user_config_path as claude_user_path,
    discover as claude_discover,
    parse as claude_parse,
)
from backend.mcp.import_from_codex import (
    default_project_config_path as codex_project_path,
    default_user_config_path as codex_user_path,
    discover as codex_discover,
    parse as codex_parse,
)


# ---------------------------------------------------------------------------
# Claude Code importer
# ---------------------------------------------------------------------------


class TestClaudeDiscovery:
    def test_paths_default_to_home_and_cwd(self):
        assert claude_user_path() == Path.home() / ".claude.json"
        assert claude_project_path().name == ".mcp.json"

    def test_discover_finds_existing(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        monkeypatch.chdir(tmp_path)
        (tmp_path / ".claude.json").write_text(
            json.dumps({"mcpServers": {}})
        )
        found = claude_discover()
        assert (tmp_path / ".claude.json") in found

    def test_discover_empty_when_nothing_present(self, tmp_path, monkeypatch):
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        monkeypatch.chdir(tmp_path)
        assert claude_discover() == []


class TestClaudeParse:
    def test_missing_file_returns_empty(self, tmp_path):
        assert claude_parse(tmp_path / "missing.json") == []

    def test_project_scope_shape(self, tmp_path):
        path = tmp_path / ".mcp.json"
        path.write_text(
            json.dumps(
                {
                    "mcpServers": {
                        "kube": {
                            "type": "stdio",
                            "command": "npx",
                            "args": ["-y", "@anthropic/mcp-server-k8s"],
                            "env": {"KUBECONFIG": "/etc/kube/config"},
                        }
                    }
                }
            )
        )
        servers = claude_parse(path)
        assert len(servers) == 1
        s = servers[0]
        assert s.name == "kube"
        assert s.transport == "stdio"
        assert s.command == "npx"
        assert s.args == ["-y", "@anthropic/mcp-server-k8s"]
        assert s.env_vars == {"KUBECONFIG": "/etc/kube/config"}
        assert s.source.startswith("claude:")

    def test_user_scope_shape_with_multiple_projects(self, tmp_path):
        path = tmp_path / ".claude.json"
        path.write_text(
            json.dumps(
                {
                    "projects": {
                        "/home/user/proj-a": {
                            "mcpServers": {
                                "a-server": {"type": "stdio", "command": "echo"}
                            }
                        },
                        "/home/user/proj-b": {
                            "mcpServers": {
                                "b-server": {
                                    "type": "http",
                                    "url": "https://example/mcp",
                                }
                            }
                        },
                    }
                }
            )
        )
        servers = claude_parse(path)
        names = {s.name for s in servers}
        assert names == {"a-server", "b-server"}
        b = next(s for s in servers if s.name == "b-server")
        assert b.transport == "http"
        assert b.url == "https://example/mcp"
        assert "/home/user/proj-b" in b.source

    def test_streamable_http_alias(self, tmp_path):
        path = tmp_path / ".mcp.json"
        path.write_text(
            json.dumps(
                {"mcpServers": {"x": {"type": "streamable-http", "url": "https://x"}}}
            )
        )
        servers = claude_parse(path)
        assert servers[0].transport == "http"

    def test_bearer_header_extracted(self, tmp_path):
        path = tmp_path / ".mcp.json"
        path.write_text(
            json.dumps(
                {
                    "mcpServers": {
                        "github": {
                            "type": "http",
                            "url": "https://api.githubcopilot.com/mcp/",
                            "headers": {"Authorization": "Bearer ghp_xyz"},
                        }
                    }
                }
            )
        )
        servers = claude_parse(path)
        assert servers[0].token == "ghp_xyz"

    def test_malformed_json_raises(self, tmp_path):
        path = tmp_path / "bad.json"
        path.write_text("{not json")
        with pytest.raises(ValueError, match="not valid JSON"):
            claude_parse(path)

    def test_non_object_root_raises(self, tmp_path):
        path = tmp_path / "list.json"
        path.write_text("[]")
        with pytest.raises(ValueError, match="JSON object"):
            claude_parse(path)


# ---------------------------------------------------------------------------
# Codex importer
# ---------------------------------------------------------------------------


CODEX_STDIO = """
[mcp_servers.kube-prod]
command = "npx"
args = ["-y", "@anthropic/mcp-server-k8s"]
env_vars = ["KUBECONFIG"]

[mcp_servers.kube-prod.env]
KUBECONFIG = "/etc/kube/config"

[mcp_servers.disabled]
command = "echo"
enabled = false
"""

CODEX_HTTP = """
[mcp_servers.sentry]
url = "https://mcp.sentry.dev/mcp"
bearer_token_env_var = "SENTRY_TOKEN"

[mcp_servers.sentry.http_headers]
X-Org-Slug = "acme"
"""


class TestCodexDiscovery:
    def test_paths(self):
        assert codex_user_path() == Path.home() / ".codex" / "config.toml"
        assert codex_project_path().name == "config.toml"

    def test_discover_empty_when_nothing(self, tmp_path, monkeypatch):
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        monkeypatch.chdir(tmp_path)
        assert codex_discover() == []


class TestCodexParse:
    def test_missing_file_returns_empty(self, tmp_path):
        assert codex_parse(tmp_path / "missing.toml") == []

    def test_stdio_with_inline_env(self, tmp_path):
        path = tmp_path / "config.toml"
        path.write_text(CODEX_STDIO)
        servers = codex_parse(path)
        names = {s.name for s in servers}
        # `disabled` entry is dropped because enabled=false.
        assert names == {"kube-prod"}
        kube = next(s for s in servers if s.name == "kube-prod")
        assert kube.transport == "stdio"
        assert kube.command == "npx"
        assert kube.args == ["-y", "@anthropic/mcp-server-k8s"]
        assert kube.env_vars == {"KUBECONFIG": "/etc/kube/config"}
        assert kube.source.startswith("codex:")

    def test_http_with_bearer_env_and_static_headers(self, tmp_path):
        path = tmp_path / "config.toml"
        path.write_text(CODEX_HTTP)
        servers = codex_parse(path)
        assert len(servers) == 1
        s = servers[0]
        assert s.transport == "http"
        assert s.url == "https://mcp.sentry.dev/mcp"
        assert s.token is None  # never inline the env var's value
        assert s.env_vars["OPSMENDER_BEARER_TOKEN_ENV_VAR"] == "SENTRY_TOKEN"
        assert s.env_vars["X-Org-Slug"] == "acme"

    def test_malformed_toml_raises(self, tmp_path):
        path = tmp_path / "bad.toml"
        path.write_text("[broken")
        with pytest.raises(ValueError, match="not valid TOML"):
            codex_parse(path)

    def test_no_mcp_servers_section(self, tmp_path):
        path = tmp_path / "config.toml"
        path.write_text('model = "gpt-4"\n')
        assert codex_parse(path) == []


class TestImportableServerDataclass:
    def test_defaults(self):
        s = ImportableServer(name="x", transport="stdio")
        assert s.command is None
        assert s.args is None
        assert s.url is None
        assert s.env_vars is None
        assert s.token is None
        assert s.source == ""
