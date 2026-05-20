"""Tests for backend.mcp.pool — dynamic MCP server pool."""

from __future__ import annotations

from contextlib import asynccontextmanager
import pytest
import uuid

TEST_ORG_ID = uuid.UUID("00000000-0000-0000-0000-000000000000")
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from backend.config_loader import MCPServerConfig
from backend.db.models import Base
from backend.db.repos import MCPServerRepo
from backend.mcp.pool import MCPPoolError, MCPServerPool


@pytest.fixture
async def session_factory():
    engine = create_async_engine("sqlite+aiosqlite://", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        from backend.db.models import Organization
        org = Organization(id=TEST_ORG_ID, name="Test Org", slug="test-org")
        session.add(org)
        await session.commit()
    yield factory
    await engine.dispose()


class TestMCPServerPoolFreshness:
    """Every query re-reads the DB — new rows are visible instantly."""

    async def test_list_empty_when_db_empty(self, session_factory):
        pool = MCPServerPool(session_factory)
        assert await pool.list_servers(TEST_ORG_ID) == []

    async def test_newly_added_server_is_visible_without_reload(self, session_factory):
        pool = MCPServerPool(session_factory)
        assert await pool.list_servers(TEST_ORG_ID) == []

        # Simulate a POST /mcp-servers — the pool was already constructed
        async with session_factory() as db:
            await MCPServerRepo.create(
                db,
                TEST_ORG_ID,
                name="k8s-dev",
                transport="stdio",
                command="/usr/bin/mcp-k8s",
                args=["--context", "dev"],
            )
            await db.commit()

        # No reload step — same pool instance must now see the new server
        servers = await pool.list_servers(TEST_ORG_ID)
        assert [s.name for s in servers] == ["k8s-dev"]
        assert servers[0].transport == "stdio"
        assert servers[0].command == "/usr/bin/mcp-k8s"

    async def test_updates_are_reflected_on_next_call(self, session_factory):
        pool = MCPServerPool(session_factory)
        async with session_factory() as db:
            server = await MCPServerRepo.create(
                db, TEST_ORG_ID, name="k8s", transport="sse", url="http://old.example"
            )
            await db.commit()
            server_id = server.id

        first = await pool.get_server(TEST_ORG_ID, "k8s")
        assert first is not None
        assert first.url == "http://old.example"

        async with session_factory() as db:
            await MCPServerRepo.update(
                db,
                TEST_ORG_ID,
                server_id,
                name="k8s",
                transport="sse",
                url="http://new.example",
            )
            await db.commit()

        second = await pool.get_server(TEST_ORG_ID, "k8s")
        assert second is not None
        assert second.url == "http://new.example"

    async def test_active_only_filters_inactive_servers(self, session_factory):
        pool = MCPServerPool(session_factory)
        async with session_factory() as db:
            await MCPServerRepo.create(
                db,
                TEST_ORG_ID,
                name="on",
                transport="stdio",
                command="/bin/on",
                is_active=True,
            )
            await MCPServerRepo.create(
                db,
                TEST_ORG_ID,
                name="off",
                transport="stdio",
                command="/bin/off",
                is_active=False,
            )
            await db.commit()

        active = await pool.list_servers(TEST_ORG_ID, active_only=True)
        assert [s.name for s in active] == ["on"]

        everything = await pool.list_servers(TEST_ORG_ID, active_only=False)
        assert sorted(s.name for s in everything) == ["off", "on"]


class TestMCPServerPoolFallback:
    """Env fallback is only consulted if DB is unavailable."""

    async def test_db_takes_precedence_over_env_fallback(self, session_factory):
        env_server = MCPServerConfig(
            name="env-only", transport="stdio", command="/bin/env"
        )
        pool = MCPServerPool(session_factory, env_fallback=[env_server])

        async with session_factory() as db:
            await MCPServerRepo.create(
                db, TEST_ORG_ID, name="db-only", transport="stdio", command="/bin/db"
            )
            await db.commit()

        # When DB is reachable the env fallback is ignored entirely —
        # otherwise the two sources would silently merge and drift.
        names = [s.name for s in await pool.list_servers(TEST_ORG_ID)]
        assert names == ["db-only"]

    async def test_falls_back_to_env_when_no_session_factory(self):
        env_server = MCPServerConfig(
            name="env-only", transport="http", url="http://example"
        )
        pool = MCPServerPool(None, env_fallback=[env_server])

        servers = await pool.list_servers(TEST_ORG_ID)
        assert [s.name for s in servers] == ["env-only"]

        found = await pool.get_server(TEST_ORG_ID, "env-only")
        assert found is not None
        assert found.url == "http://example"

    async def test_get_server_missing_returns_none(self, session_factory):
        pool = MCPServerPool(session_factory)
        assert await pool.get_server(TEST_ORG_ID, "does-not-exist") is None

    async def test_connect_unknown_raises(self, session_factory):
        pool = MCPServerPool(session_factory)
        with pytest.raises(MCPPoolError):
            async with pool.connect(TEST_ORG_ID, "nope"):
                pass

    async def test_connect_marks_successful_runtime_calls(
        self, session_factory, monkeypatch
    ):
        async with session_factory() as db:
            server = await MCPServerRepo.create(
                db,
                TEST_ORG_ID,
                name="k8s",
                transport="stdio",
                command="/bin/echo",
            )
            await db.commit()
            server_id = server.id

        @asynccontextmanager
        async def _fake_connect(_cfg):
            class _Session:
                pass

            yield _Session()

        monkeypatch.setattr("backend.mcp.pool.mcp_connect", _fake_connect)

        pool = MCPServerPool(session_factory)
        async with pool.connect(TEST_ORG_ID, "k8s"):
            pass

        async with session_factory() as db:
            refreshed = await MCPServerRepo.get_by_id(db, TEST_ORG_ID, server_id)
            assert refreshed is not None
            assert refreshed.last_successful_call_at is not None
            assert refreshed.last_error is None

    async def test_connect_marks_failures(self, session_factory, monkeypatch):
        async with session_factory() as db:
            server = await MCPServerRepo.create(
                db,
                TEST_ORG_ID,
                name="broken",
                transport="stdio",
                command="/bin/false",
            )
            await db.commit()
            server_id = server.id

        @asynccontextmanager
        async def _failing_connect(_cfg):
            raise RuntimeError("boom")
            yield None

        monkeypatch.setattr("backend.mcp.pool.mcp_connect", _failing_connect)

        pool = MCPServerPool(session_factory)
        with pytest.raises(RuntimeError, match="boom"):
            async with pool.connect(TEST_ORG_ID, "broken"):
                pass

        async with session_factory() as db:
            refreshed = await MCPServerRepo.get_by_id(db, TEST_ORG_ID, server_id)
            assert refreshed is not None
            assert refreshed.last_error == "boom"
