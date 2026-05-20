"""Tests for the `mcp.json` file mirror (Sprint 42 Step 6)."""

from __future__ import annotations

import json
import uuid
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from backend.db.models import Base, MCPServer, Organization
from backend.db.repos import MCPServerRepo
from backend.mcp.mcp_json import (
    MCPJSONSyncer,
    default_path,
    entry_to_kwargs,
    read_from_disk,
    server_to_entry,
    sync_enabled,
    write_to_disk,
)


TEST_ORG_ID = uuid.UUID("00000000-0000-0000-0000-0000000000aa")


@pytest.fixture
async def db_factory(tmp_path, monkeypatch):
    monkeypatch.setenv("OPSMENDER_SECRET_KEY", "sprint-42-test-key")
    engine = create_async_engine("sqlite+aiosqlite://", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        session.add(Organization(id=TEST_ORG_ID, name="Test", slug="test"))
        await session.commit()
    yield factory
    await engine.dispose()


# ---------------------------------------------------------------------------
# Config helpers
# ---------------------------------------------------------------------------


class TestDefaultPath:
    def test_override_via_env(self, tmp_path, monkeypatch):
        target = tmp_path / "alt.json"
        monkeypatch.setenv("OPSMENDER_MCP_CONFIG_PATH", str(target))
        assert default_path() == target

    def test_falls_back_to_home(self, monkeypatch):
        monkeypatch.delenv("OPSMENDER_MCP_CONFIG_PATH", raising=False)
        assert default_path() == Path.home() / ".opsmender" / "mcp.json"


class TestSyncEnabledFlag:
    @pytest.mark.parametrize("value", ["1", "true", "TRUE", "yes", "on"])
    def test_truthy(self, monkeypatch, value):
        monkeypatch.setenv("OPSMENDER_MCP_JSON_SYNC", value)
        assert sync_enabled() is True

    @pytest.mark.parametrize("value", ["", "0", "false", "no", "off", "maybe"])
    def test_falsy(self, monkeypatch, value):
        monkeypatch.setenv("OPSMENDER_MCP_JSON_SYNC", value)
        assert sync_enabled() is False

    def test_default_off(self, monkeypatch):
        monkeypatch.delenv("OPSMENDER_MCP_JSON_SYNC", raising=False)
        assert sync_enabled() is False


# ---------------------------------------------------------------------------
# Serialization
# ---------------------------------------------------------------------------


class TestServerToEntry:
    def test_stdio(self):
        srv = MCPServer(
            org_id=TEST_ORG_ID,
            name="kube-prod",
            transport="stdio",
            command="npx",
            args=["-y", "@anthropic/mcp-server-k8s"],
            env_vars={"KUBECONFIG": "/etc/kube/config"},
            is_active=True,
        )
        entry = server_to_entry(srv)
        assert entry == {
            "type": "stdio",
            "command": "npx",
            "args": ["-y", "@anthropic/mcp-server-k8s"],
            "env": {"KUBECONFIG": "/etc/kube/config"},
        }

    def test_http(self):
        srv = MCPServer(
            org_id=TEST_ORG_ID,
            name="sentry",
            transport="http",
            url="https://mcp.sentry.dev/mcp",
            env_vars={"X-Org-Slug": "acme"},
            is_active=True,
        )
        entry = server_to_entry(srv)
        assert entry["type"] == "http"
        assert entry["url"] == "https://mcp.sentry.dev/mcp"
        assert entry["env"] == {"X-Org-Slug": "acme"}

    def test_inactive_carries_opsmender_block(self):
        srv = MCPServer(
            org_id=TEST_ORG_ID,
            name="off",
            transport="stdio",
            command="echo",
            is_active=False,
        )
        entry = server_to_entry(srv)
        assert entry["opsmender"] == {"is_active": False}

    def test_token_never_in_file(self):
        srv = MCPServer(
            org_id=TEST_ORG_ID,
            name="github",
            transport="http",
            url="https://api.githubcopilot.com/mcp/",
            token="secret-bearer-must-not-leak",
            is_active=True,
        )
        entry = server_to_entry(srv)
        serialized = json.dumps(entry)
        assert "secret-bearer-must-not-leak" not in serialized


class TestEntryToKwargs:
    def test_stdio_round_trip(self):
        kwargs = entry_to_kwargs(
            "kube-prod",
            {
                "type": "stdio",
                "command": "npx",
                "args": ["-y", "@anthropic/mcp-server-k8s"],
                "env": {"KUBECONFIG": "/etc/kube/config"},
            },
        )
        assert kwargs["name"] == "kube-prod"
        assert kwargs["transport"] == "stdio"
        assert kwargs["command"] == "npx"
        assert kwargs["args"] == ["-y", "@anthropic/mcp-server-k8s"]
        assert kwargs["env_vars"] == {"KUBECONFIG": "/etc/kube/config"}
        assert kwargs["is_active"] is True
        assert kwargs["token"] is None

    def test_http_with_bearer_header(self):
        kwargs = entry_to_kwargs(
            "github",
            {
                "type": "http",
                "url": "https://api.githubcopilot.com/mcp/",
                "headers": {"Authorization": "Bearer ghp_xyz"},
            },
        )
        assert kwargs["transport"] == "http"
        assert kwargs["url"] == "https://api.githubcopilot.com/mcp/"
        assert kwargs["token"] == "ghp_xyz"

    def test_opsmender_extension_inactive(self):
        kwargs = entry_to_kwargs(
            "disabled",
            {"type": "stdio", "command": "echo", "opsmender": {"is_active": False}},
        )
        assert kwargs["is_active"] is False

    def test_defaults_when_type_missing(self):
        kwargs = entry_to_kwargs("x", {"command": "echo"})
        assert kwargs["transport"] == "stdio"


# ---------------------------------------------------------------------------
# File IO
# ---------------------------------------------------------------------------


class TestFileIO:
    def test_read_missing_file(self, tmp_path):
        assert read_from_disk(tmp_path / "missing.json") == {}

    def test_read_malformed_json(self, tmp_path):
        path = tmp_path / "bad.json"
        path.write_text("{not json", encoding="utf-8")
        with pytest.raises(ValueError, match="not valid JSON"):
            read_from_disk(path)

    def test_read_rejects_non_object_root(self, tmp_path):
        path = tmp_path / "list.json"
        path.write_text("[1,2,3]", encoding="utf-8")
        with pytest.raises(ValueError, match="must be a JSON object"):
            read_from_disk(path)

    def test_write_then_read_round_trip(self, tmp_path):
        path = tmp_path / "sub" / "mcp.json"  # parent created on write
        servers = [
            MCPServer(
                org_id=TEST_ORG_ID,
                name="kube",
                transport="stdio",
                command="npx",
                args=["-y", "@anthropic/mcp-server-k8s"],
                env_vars={"KUBECONFIG": "/x"},
                is_active=True,
            ),
            MCPServer(
                org_id=TEST_ORG_ID,
                name="sentry",
                transport="http",
                url="https://mcp.sentry.dev/mcp",
                is_active=True,
            ),
        ]
        write_to_disk(path, servers)
        assert path.exists()
        entries = read_from_disk(path)
        assert set(entries.keys()) == {"kube", "sentry"}
        assert entries["kube"]["command"] == "npx"
        assert entries["sentry"]["url"] == "https://mcp.sentry.dev/mcp"

    def test_written_file_is_claude_code_shaped(self, tmp_path):
        path = tmp_path / "mcp.json"
        write_to_disk(path, [])
        raw = json.loads(path.read_text(encoding="utf-8"))
        assert raw == {"mcpServers": {}}


# ---------------------------------------------------------------------------
# Syncer
# ---------------------------------------------------------------------------


class TestSyncerExport:
    async def test_no_op_when_disabled(self, db_factory, tmp_path):
        async with db_factory() as session:
            await MCPServerRepo.create(
                session,
                TEST_ORG_ID,
                name="kube",
                transport="stdio",
                command="echo",
                is_active=True,
            )
            await session.commit()
        path = tmp_path / "mcp.json"
        syncer = MCPJSONSyncer(db_factory, path=path, enabled=False)
        await syncer.export_org(TEST_ORG_ID)
        assert not path.exists()

    async def test_export_writes_all_servers(self, db_factory, tmp_path):
        async with db_factory() as session:
            await MCPServerRepo.create(
                session,
                TEST_ORG_ID,
                name="kube",
                transport="stdio",
                command="npx",
                args=["-y", "x"],
                is_active=True,
            )
            await MCPServerRepo.create(
                session,
                TEST_ORG_ID,
                name="sentry",
                transport="http",
                url="https://mcp.sentry.dev/mcp",
                is_active=True,
            )
            await session.commit()
        path = tmp_path / "mcp.json"
        syncer = MCPJSONSyncer(db_factory, path=path, enabled=True)
        await syncer.export_org(TEST_ORG_ID)
        entries = read_from_disk(path)
        assert set(entries.keys()) == {"kube", "sentry"}

    async def test_pinned_org_skips_other_orgs(self, db_factory, tmp_path):
        other_org = uuid.UUID("11111111-1111-1111-1111-111111111111")
        async with db_factory() as session:
            session.add(Organization(id=other_org, name="Other", slug="other"))
            await session.commit()
        path = tmp_path / "mcp.json"
        syncer = MCPJSONSyncer(
            db_factory, path=path, enabled=True, pinned_org_id=TEST_ORG_ID
        )
        await syncer.export_org(other_org)
        assert not path.exists()


class TestSyncerReconcile:
    async def test_create_from_file(self, db_factory, tmp_path):
        path = tmp_path / "mcp.json"
        path.write_text(
            json.dumps(
                {
                    "mcpServers": {
                        "new-server": {
                            "type": "stdio",
                            "command": "echo",
                            "args": ["hello"],
                        }
                    }
                }
            ),
            encoding="utf-8",
        )
        syncer = MCPJSONSyncer(db_factory, path=path, enabled=True)
        result = await syncer.reconcile_on_startup(TEST_ORG_ID)
        assert result.created == ["new-server"]
        async with db_factory() as session:
            row = await MCPServerRepo.get_by_name(session, TEST_ORG_ID, "new-server")
            assert row is not None
            assert row.command == "echo"
            assert row.args == ["hello"]

    async def test_update_existing_by_name_file_wins(self, db_factory, tmp_path):
        async with db_factory() as session:
            await MCPServerRepo.create(
                session,
                TEST_ORG_ID,
                name="kube",
                transport="stdio",
                command="OLD",
                is_active=True,
            )
            await session.commit()
        path = tmp_path / "mcp.json"
        path.write_text(
            json.dumps(
                {"mcpServers": {"kube": {"type": "stdio", "command": "NEW"}}}
            ),
            encoding="utf-8",
        )
        syncer = MCPJSONSyncer(db_factory, path=path, enabled=True)
        result = await syncer.reconcile_on_startup(TEST_ORG_ID)
        assert result.updated == ["kube"]
        async with db_factory() as session:
            row = await MCPServerRepo.get_by_name(session, TEST_ORG_ID, "kube")
            assert row.command == "NEW"

    async def test_db_only_servers_preserved(self, db_factory, tmp_path):
        async with db_factory() as session:
            await MCPServerRepo.create(
                session,
                TEST_ORG_ID,
                name="db-only",
                transport="stdio",
                command="keep",
                is_active=True,
            )
            await session.commit()
        path = tmp_path / "mcp.json"
        path.write_text(json.dumps({"mcpServers": {}}), encoding="utf-8")
        syncer = MCPJSONSyncer(db_factory, path=path, enabled=True)
        result = await syncer.reconcile_on_startup(TEST_ORG_ID)
        assert result.db_only == ["db-only"]
        async with db_factory() as session:
            row = await MCPServerRepo.get_by_name(session, TEST_ORG_ID, "db-only")
            assert row is not None
            assert row.command == "keep"

    async def test_existing_token_preserved_when_file_omits(self, db_factory, tmp_path):
        async with db_factory() as session:
            await MCPServerRepo.create(
                session,
                TEST_ORG_ID,
                name="bear",
                transport="http",
                url="https://x",
                token="keep-me",
                is_active=True,
            )
            await session.commit()
        path = tmp_path / "mcp.json"
        path.write_text(
            json.dumps(
                {"mcpServers": {"bear": {"type": "http", "url": "https://x"}}}
            ),
            encoding="utf-8",
        )
        syncer = MCPJSONSyncer(db_factory, path=path, enabled=True)
        await syncer.reconcile_on_startup(TEST_ORG_ID)
        async with db_factory() as session:
            row = await MCPServerRepo.get_by_name(session, TEST_ORG_ID, "bear")
            assert row.token == "keep-me"

    async def test_missing_file_is_noop(self, db_factory, tmp_path):
        syncer = MCPJSONSyncer(
            db_factory, path=tmp_path / "absent.json", enabled=True
        )
        result = await syncer.reconcile_on_startup(TEST_ORG_ID)
        assert result.created == []
        assert result.updated == []
        assert result.errors == []

    async def test_malformed_file_records_error(self, db_factory, tmp_path):
        path = tmp_path / "mcp.json"
        path.write_text("{not json", encoding="utf-8")
        syncer = MCPJSONSyncer(db_factory, path=path, enabled=True)
        result = await syncer.reconcile_on_startup(TEST_ORG_ID)
        assert result.errors
        assert "not valid JSON" in result.errors[0]
