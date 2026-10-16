"""M1-64 (O-22): a local MCP server receives only PATH, the OS basics and the
variables its configuration names; servers saved before v1.2 inherit the whole
process environment until an admin saves them again; mcp.json round-trips the
flag."""

from __future__ import annotations

import asyncio
import importlib
import uuid
from contextlib import asynccontextmanager
from unittest.mock import patch

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

from backend.config_loader import MCPServerConfig, _parse_mcp_servers
from backend.db.models import MCPServer
from backend.db.repos import MCPServerRepo
from backend.mcp.client import _connect_stdio, child_environment
from backend.mcp.mcp_json import entry_to_kwargs, server_to_entry
from tests.test_ownership_lifecycle import (
    TEST_ORG_ID,
    app as _app_fixture,
    client as _client_fixture,
    world as _world_fixture,
)

app = pytest.fixture(_app_fixture.__wrapped__)
client = pytest.fixture(_client_fixture.__wrapped__)
world = pytest.fixture(_world_fixture.__wrapped__)

PROCESS_ENV = {
    "PATH": "/usr/bin",
    "HOME": "/home/opsmender",
    "OPSMENDER_JWT_SECRET": "not-for-servers",
    "OPSMENDER_DATABASE_URL": "postgresql+asyncpg://db.internal/opsmender",
    "AWS_PROFILE": "prod",
}


def test_a_server_gets_the_base_names_and_its_own_variables_only():
    server = MCPServerConfig(
        name="k8s",
        transport="stdio",
        command="echo",
        env={"KUBECONFIG": "/etc/kube/config"},
    )
    with patch.dict("os.environ", PROCESS_ENV, clear=True):
        env = child_environment(server)
    assert env == {
        "PATH": "/usr/bin",
        "HOME": "/home/opsmender",
        "KUBECONFIG": "/etc/kube/config",
    }


def test_the_stdio_connection_passes_only_the_child_environment():
    server = MCPServerConfig(
        name="k8s",
        transport="stdio",
        command="echo",
        env={"KUBECONFIG": "/etc/kube/config"},
    )
    seen: dict[str, dict[str, str] | None] = {}

    @asynccontextmanager
    async def fake_stdio_client(params):
        seen["env"] = params.env
        raise RuntimeError("stop before a process starts")
        yield  # pragma: no cover

    async def use():
        async with _connect_stdio(server):
            pass  # pragma: no cover

    with (
        patch.dict("os.environ", PROCESS_ENV, clear=True),
        patch("backend.mcp.client.stdio_client", fake_stdio_client),
    ):
        with pytest.raises(Exception, match="stop before a process starts"):
            asyncio.run(use())
    assert seen["env"] == {
        "PATH": "/usr/bin",
        "HOME": "/home/opsmender",
        "KUBECONFIG": "/etc/kube/config",
    }


def test_a_server_that_inherits_gets_everything():
    server = MCPServerConfig(
        name="old",
        transport="stdio",
        command="echo",
        env={"EXTRA": "1"},
        inherit_env=True,
    )
    with patch.dict("os.environ", PROCESS_ENV, clear=True):
        env = child_environment(server)
    assert env == {**PROCESS_ENV, "EXTRA": "1"}


def test_environment_configured_servers_inherit_only_when_they_say_so():
    env = {
        "OPSMENDER_MCP_SERVERS_JSON": '[{"name": "a", "transport": "stdio", "command": "echo"},'
        ' {"name": "b", "transport": "stdio", "command": "echo", "inherit_env": true}]'
    }
    servers = {s.name: s for s in _parse_mcp_servers(env)}
    assert (servers["a"].inherit_env, servers["b"].inherit_env) == (False, True)


def test_the_migration_marks_existing_servers_as_inheriting(monkeypatch):
    migration = importlib.import_module(
        "backend.db.migrations.versions.b1c2d3e4f5a6_mcp_server_inherit_env"
    )
    engine = sa.create_engine("sqlite://")
    metadata = sa.MetaData()
    servers = sa.Table(
        "mcp_servers",
        metadata,
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("name", sa.String(100), nullable=False),
    )
    metadata.create_all(engine)
    old = uuid.uuid4()
    with engine.begin() as connection:
        connection.execute(servers.insert(), [{"id": old, "name": "before-v1.2"}])
        # The column change runs through a real Operations object (batch mode
        # on SQLite), not the module proxy Alembic only binds inside env.py.
        monkeypatch.setattr(
            migration, "op", Operations(MigrationContext.configure(connection))
        )
        migration.upgrade()
        connection.execute(
            sa.text("INSERT INTO mcp_servers (id, name) VALUES (:id, 'new')"),
            {"id": uuid.uuid4().hex},
        )
        rows = dict(
            connection.execute(
                sa.text("SELECT name, inherit_env FROM mcp_servers")
            ).all()
        )
        assert rows == {"before-v1.2": 1, "new": 0}
        migration.downgrade()
        columns = {
            row[1]
            for row in connection.execute(
                sa.text("PRAGMA table_info(mcp_servers)")
            ).all()
        }
        assert "inherit_env" not in columns


async def test_saving_a_server_stops_it_inheriting(world):
    w = world
    created = await w.client.post(
        "/mcp-servers",
        json={
            "name": "fresh",
            "transport": "stdio",
            "command": "echo",
            "env_vars": {"A": "1"},
        },
        headers=w.admin,
    )
    assert created.status_code == 201, created.text
    assert created.json()["inherit_env"] is False

    async with w.app.state.session_factory() as db:
        old = await MCPServerRepo.create(
            db,
            TEST_ORG_ID,
            name="old",
            transport="stdio",
            command="echo",
            inherit_env=True,
        )
        await db.commit()
        old_id = old.id
    listed = await w.client.get("/mcp-servers", headers=w.admin)
    flags = {item["name"]: item["inherit_env"] for item in listed.json()["items"]}
    assert flags["old"] is True and flags["fresh"] is False

    saved = await w.client.put(
        f"/mcp-servers/{old_id}",
        json={
            "name": "old",
            "transport": "stdio",
            "command": "echo",
            "env_vars": {"KUBECONFIG": "/k"},
        },
        headers=w.admin,
    )
    assert saved.status_code == 200, saved.text
    assert saved.json()["inherit_env"] is False


def test_mcp_json_round_trips_the_flag():
    srv = MCPServer(
        org_id=TEST_ORG_ID,
        name="old",
        transport="stdio",
        command="npx",
        is_active=True,
        inherit_env=True,
    )
    entry = server_to_entry(srv)
    assert entry == {
        "type": "stdio",
        "command": "npx",
        "opsmender": {"inherit_env": True},
    }
    assert entry_to_kwargs("old", entry)["inherit_env"] is True
    assert (
        entry_to_kwargs("new", {"type": "stdio", "command": "npx"})["inherit_env"]
        is False
    )
