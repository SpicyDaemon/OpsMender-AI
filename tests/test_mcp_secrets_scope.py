"""M1-29: MCP server secrets are for admins only (review R-01, decision O-07).

An MCP server's command, args, URL and environment values often carry the
credentials the AI uses to reach infrastructure. Only admins (by the role the
request acts with) see them; everyone else gets what session detail and Paging
need: id, name, transport, state and the environment key names.
"""

from __future__ import annotations

import uuid

import pytest

from backend.config_loader import Config, MCPServerConfig
from backend.db.repos import MCPServerRepo
from tests.test_ownership_lifecycle import (
    TEST_ORG_ID,
    _headers,
    _user,
    app as _app_fixture,
    client as _client_fixture,
    world as _world_fixture,
)

app = pytest.fixture(_app_fixture.__wrapped__)
client = pytest.fixture(_client_fixture.__wrapped__)
world = pytest.fixture(_world_fixture.__wrapped__)

SECRET = "rv-synthetic-secret-29"


async def _servers(w):
    stdio = await w.client.post(
        "/mcp-servers",
        json={
            "name": "kube-tools",
            "transport": "stdio",
            "command": "echo",
            "args": ["--token", SECRET],
            "env_vars": {"API_KEY": SECRET},
        },
        headers=w.admin,
    )
    assert stdio.status_code == 201, stdio.text
    http = await w.client.post(
        "/mcp-servers",
        json={
            "name": "remote-tools",
            "transport": "http",
            "url": f"https://mcp.example.test/mcp?key={SECRET}",
        },
        headers=w.admin,
    )
    assert http.status_code == 201, http.text
    async with w.app.state.session_factory() as db:
        await MCPServerRepo.mark_connection_failure(
            db,
            TEST_ORG_ID,
            uuid.UUID(stdio.json()["id"]),
            error=f"connect failed: {SECRET}",
        )
        await db.commit()
    return stdio.json()["id"], http.json()["id"]


async def _token(w, role: str) -> dict[str, str]:
    made = await w.client.post(
        "/api/v1/api-tokens",
        json={"name": f"m129-{role}", "role": role},
        headers=w.admin,
    )
    assert made.status_code == 201, made.text
    return {"Authorization": f"Bearer {made.json()['token']}"}


async def _callers(w) -> dict[str, dict[str, str]]:
    await _user(w.app, "m129-viewer", role="viewer")
    return {
        "viewer": await _headers(w.client, "m129-viewer"),
        "operator": await _headers(w.client, "lc-l1"),
        "viewer token": await _token(w, "viewer"),
        "operator token": await _token(w, "operator"),
    }


async def test_only_admins_see_mcp_secret_values(world):
    stdio_id, http_id = await _servers(world)
    for who, headers in (await _callers(world)).items():
        listed = await world.client.get("/mcp-servers", headers=headers)
        assert listed.status_code == 200, (who, listed.text)
        assert SECRET not in listed.text, who
        rows = {row["id"]: row for row in listed.json()["items"]}
        stdio, http = rows[stdio_id], rows[http_id]
        assert (stdio["name"], stdio["transport"], stdio["is_active"]) == (
            "kube-tools",
            "stdio",
            True,
        ), who
        assert stdio["env_keys"] == ["API_KEY"], who
        for row in (stdio, http):
            assert row["command"] is None and row["args"] is None, who
            assert row["url"] is None and row["env_vars"] is None, who

        statuses = await world.client.get("/mcp-servers/status", headers=headers)
        assert statuses.status_code == 200, (who, statuses.text)
        assert SECRET not in statuses.text, who
        assert all(row["last_error"] is None for row in statuses.json()["items"]), who


async def test_admins_still_read_and_keep_the_values(world):
    stdio_id, http_id = await _servers(world)
    listed = await world.client.get("/mcp-servers", headers=world.admin)
    rows = {row["id"]: row for row in listed.json()["items"]}
    assert rows[stdio_id]["env_vars"] == {"API_KEY": SECRET}
    assert rows[stdio_id]["args"] == ["--token", SECRET]
    assert rows[stdio_id]["env_keys"] == ["API_KEY"]
    assert rows[http_id]["url"] == f"https://mcp.example.test/mcp?key={SECRET}"
    statuses = await world.client.get("/mcp-servers/status", headers=world.admin)
    errors = {row["server_id"]: row["last_error"] for row in statuses.json()["items"]}
    assert errors[stdio_id] == f"connect failed: {SECRET}"
    # Saving the form back unchanged keeps the stored values.
    row = rows[stdio_id]
    saved = await world.client.put(
        f"/mcp-servers/{stdio_id}",
        json={
            "name": row["name"],
            "transport": row["transport"],
            "command": row["command"],
            "args": row["args"],
            "env_vars": row["env_vars"],
        },
        headers=world.admin,
    )
    assert saved.status_code == 200, saved.text
    async with world.app.state.session_factory() as db:
        server = await MCPServerRepo.get_by_id(db, TEST_ORG_ID, uuid.UUID(stdio_id))
    assert server.env_vars == {"API_KEY": SECRET}


async def test_environment_configured_servers_hide_their_launch_details(
    world, monkeypatch
):
    config = Config.load()
    config.mcp_servers = [
        MCPServerConfig(
            name="env-tools",
            transport="http",
            url=f"https://mcp.example.test/env?key={SECRET}",
        ),
        MCPServerConfig(
            name="env-stdio", transport="stdio", command="run", args=["--key", SECRET]
        ),
    ]
    monkeypatch.setattr(Config, "load", classmethod(lambda cls, *a, **k: config))
    operator = await _headers(world.client, "lc-l1")
    as_operator = await world.client.get("/config", headers=operator)
    assert as_operator.status_code == 200, as_operator.text
    assert SECRET not in as_operator.text
    assert [s["name"] for s in as_operator.json()["mcp_servers"]] == [
        "env-tools",
        "env-stdio",
    ]
    as_admin = await world.client.get("/config", headers=world.admin)
    assert SECRET in as_admin.text
