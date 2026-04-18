"""API-level tests for POST /sessions/{id}/rollback (Sprint 17)."""

from __future__ import annotations

import json
import uuid
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from backend.api.app import create_app
from backend.api.deps import get_db, set_mcp_pool, set_session_factory
from backend.audit.logger import AuditEntryType
from backend.config_loader import set_env_path
from backend.db.models import Base
from backend.db.repos import (
    AuditEntryRepo,
    MCPServerRepo,
    SessionRepo,
    SkillRepo,
)
from backend.mcp.pool import MCPServerPool


# ---------------------------------------------------------------------------
# Fixtures (mirrors test_api.py minimal setup)
# ---------------------------------------------------------------------------


@pytest.fixture
async def app(tmp_path):
    engine = create_async_engine("sqlite+aiosqlite://", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    set_session_factory(factory)

    tmp_env = tmp_path / ".env"
    tmp_env.write_text(
        "AIM_TIER=0\n"
        "AIM_LOG_LEVEL=INFO\n"
        "AIM_JWT_SECRET=test-secret\n"
        "AIM_DATABASE_URL=sqlite+aiosqlite://\n"
        f"AIM_MCP_SERVERS_JSON={json.dumps([])}\n"
    )
    set_env_path(tmp_env)

    application = create_app()
    application.state.session_factory = factory
    pool = MCPServerPool(factory, env_fallback=[])
    set_mcp_pool(pool)
    application.state.mcp_pool = pool

    async def _override_get_db():
        async with factory() as s:
            try:
                yield s
                await s.commit()
            except Exception:
                await s.rollback()
                raise

    application.dependency_overrides[get_db] = _override_get_db
    yield application
    set_env_path(None)
    await engine.dispose()


@pytest.fixture
async def client(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest.fixture
async def admin_headers(client: AsyncClient) -> dict[str, str]:
    await client.post(
        "/auth/register",
        json={
            "username": "rbadmin",
            "email": "rb@test.com",
            "password": "securepass123",
        },
    )
    resp = await client.post(
        "/auth/login",
        json={"username": "rbadmin", "password": "securepass123"},
    )
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


@pytest.fixture
async def viewer_headers(client: AsyncClient, admin_headers) -> dict[str, str]:
    await client.post(
        "/auth/register",
        json={
            "username": "rbviewer",
            "email": "v@test.com",
            "password": "pw12345678",
            "role": "viewer",
        },
    )
    resp = await client.post(
        "/auth/login",
        json={"username": "rbviewer", "password": "pw12345678"},
    )
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


_SKILL_MD = (
    "---\n"
    "version: '1'\n"
    "environment: test\n"
    "operations:\n"
    "  - tool: cordon_node\n"
    "    classification: caution\n"
    "    reversible: true\n"
    "    compensating_inverse: uncordon_node\n"
    "  - tool: uncordon_node\n"
    "    classification: caution\n"
    "    reversible: true\n"
    "    compensating_inverse: cordon_node\n"
    "  - tool: rollout_restart\n"
    "    classification: caution\n"
    "---\n"
)


async def _seed_session_with_cordon(app) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:
    """Create: MCP server + skill + session + tool_call_start/end pair."""
    factory = app.state.session_factory
    async with factory() as db:
        server = await MCPServerRepo.create(
            db, name="k8s-test", transport="stdio", command="echo"
        )
        skill = await SkillRepo.create(
            db,
            name="test",
            content_md=_SKILL_MD,
            mcp_server_id=server.id,
        )
        session = await SessionRepo.create(db, tier=0)
        await AuditEntryRepo.create(
            db,
            session_id=session.id,
            tier=0,
            entry_type=AuditEntryType.TOOL_CALL_START.value,
            tool_name="cordon_node",
            tool_parameters={"node": "n1"},
            permitted=True,
        )
        await AuditEntryRepo.create(
            db,
            session_id=session.id,
            tier=0,
            entry_type=AuditEntryType.TOOL_CALL_END.value,
            tool_name="cordon_node",
            result={"ok": True},
            permitted=True,
        )
        await db.commit()
        return session.id, server.id, skill.id



# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestAuth:

    async def test_viewer_forbidden(self, client: AsyncClient, viewer_headers, app):
        session_id, _, _ = await _seed_session_with_cordon(app)
        resp = await client.post(
            f"/sessions/{session_id}/rollback",
            json={"dry_run": True},
            headers=viewer_headers,
        )
        assert resp.status_code == 403

    async def test_unauth(self, client: AsyncClient, app):
        session_id, _, _ = await _seed_session_with_cordon(app)
        resp = await client.post(
            f"/sessions/{session_id}/rollback", json={"dry_run": True}
        )
        assert resp.status_code == 401


class TestDryRun:

    async def test_empty_session_returns_zero_plan(
        self, client: AsyncClient, admin_headers, app
    ):
        factory = app.state.session_factory
        async with factory() as db:
            session = await SessionRepo.create(db, tier=0)
            await db.commit()
            sid = session.id
        resp = await client.post(
            f"/sessions/{sid}/rollback",
            json={"dry_run": True},
            headers=admin_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["attempted"] == 0
        assert data["steps"] == []

    async def test_resolves_inverse_plan(
        self, client: AsyncClient, admin_headers, app
    ):
        sid, _, _ = await _seed_session_with_cordon(app)
        resp = await client.post(
            f"/sessions/{sid}/rollback",
            json={"dry_run": True, "mcp_server": "k8s-test"},
            headers=admin_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["dry_run"] is True
        assert data["succeeded"] == 1
        assert data["steps"][0]["original_tool"] == "cordon_node"
        assert data["steps"][0]["inverse_tool"] == "uncordon_node"
        assert data["steps"][0]["parameters"] == {"node": "n1"}


class TestErrorPaths:

    async def test_unknown_session(self, client: AsyncClient, admin_headers):
        resp = await client.post(
            f"/sessions/{uuid.uuid4()}/rollback",
            json={"dry_run": True},
            headers=admin_headers,
        )
        assert resp.status_code == 404

    async def test_live_rollback_requires_mcp_server(
        self, client: AsyncClient, admin_headers, app
    ):
        sid, _, _ = await _seed_session_with_cordon(app)
        resp = await client.post(
            f"/sessions/{sid}/rollback",
            json={"dry_run": False},
            headers=admin_headers,
        )
        assert resp.status_code == 400
        assert "mcp_server is required" in resp.json()["detail"]

    async def test_no_skill_definition(
        self, client: AsyncClient, admin_headers, app
    ):
        factory = app.state.session_factory
        async with factory() as db:
            session = await SessionRepo.create(db, tier=0)
            await AuditEntryRepo.create(
                db,
                session_id=session.id,
                tier=0,
                entry_type=AuditEntryType.TOOL_CALL_START.value,
                tool_name="cordon_node",
                tool_parameters={"node": "n1"},
                permitted=True,
            )
            await AuditEntryRepo.create(
                db,
                session_id=session.id,
                tier=0,
                entry_type=AuditEntryType.TOOL_CALL_END.value,
                tool_name="cordon_node",
                permitted=True,
            )
            await db.commit()
            sid = session.id
        resp = await client.post(
            f"/sessions/{sid}/rollback",
            json={"dry_run": True, "mcp_server": "nonexistent-server"},
            headers=admin_headers,
        )
        # No such MCP server → 400 before the skill lookup
        assert resp.status_code == 400
        # And with no mcp_server provided + no global skill either → 409
        resp2 = await client.post(
            f"/sessions/{sid}/rollback",
            json={"dry_run": True},
            headers=admin_headers,
        )
        assert resp2.status_code == 409


class TestLiveRollback:

    async def test_live_invokes_mcp_inverse(
        self, client: AsyncClient, admin_headers, app, monkeypatch
    ):
        sid, _, _ = await _seed_session_with_cordon(app)

        calls: list[tuple[str, dict]] = []

        class _FakeTool:
            def __init__(self, name: str):
                self.name = name

        async def _fake_list_tools(_session):
            return [_FakeTool("cordon_node"), _FakeTool("uncordon_node")]

        async def _fake_call_tool(_session, tool_name, params):
            calls.append((tool_name, dict(params)))
            return SimpleNamespace(isError=False, content=[])

        @asynccontextmanager
        async def _fake_connect(_self, _name):
            yield SimpleNamespace()

        monkeypatch.setattr(
            "backend.api.routes.sessions.mcp_list_tools", _fake_list_tools
        )
        monkeypatch.setattr("backend.tiers.sandbox.call_tool", _fake_call_tool)
        monkeypatch.setattr(
            "backend.mcp.pool.MCPServerPool.connect", _fake_connect
        )

        resp = await client.post(
            f"/sessions/{sid}/rollback",
            json={"dry_run": False, "mcp_server": "k8s-test"},
            headers=admin_headers,
        )
        assert resp.status_code == 200, resp.json()
        data = resp.json()
        assert data["succeeded"] == 1
        assert data["dry_run"] is False
        assert calls == [("uncordon_node", {"node": "n1"})]
