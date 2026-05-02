"""Tests for the Next.js static export mount on the FastAPI app.

The frontend is built with ``output: 'export'`` and served from
``frontend/out/`` by ``backend/api/static.mount_frontend``. These tests
verify:

* root path ``/`` → ``index.html``
* per-page routes like ``/login`` → ``login.html``
* nested routes like ``/dashboard/incidents/detail`` → ``.../detail.html``
* API routes (``/health``) still win over the static mount
* unknown paths fall through to ``404.html`` with a 404 status
* path-traversal attempts are rejected

Skipped when ``frontend/out/`` hasn't been built.
"""

from __future__ import annotations

import json
import pathlib

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from backend.api.app import create_app
from backend.api.deps import get_db, set_session_factory
from backend.config_loader import set_env_path
from backend.db.models import Base

PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[1]
FRONTEND_OUT = PROJECT_ROOT / "frontend" / "out"

pytestmark = pytest.mark.skipif(
    not (FRONTEND_OUT / "index.html").is_file(),
    reason="frontend/out/ not built — run `npx next build` in frontend/ first",
)


@pytest.fixture
async def client(tmp_path):
    db_path = tmp_path / "frontend-mount.db"
    database_url = f"sqlite+aiosqlite:///{db_path}"
    engine = create_async_engine(database_url, echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    factory = async_sessionmaker(engine, expire_on_commit=False)
    set_session_factory(factory)

    tmp_env = tmp_path / ".env"
    tmp_env.write_text(
        "AIM_TIER=2\n"
        "AIM_LOG_LEVEL=INFO\n"
        "AIM_AUDIT_LOG=./logs/audit.jsonl\n"
        "AIM_JWT_SECRET=test-secret\n"
        f"AIM_DATABASE_URL={database_url}\n"
        f"AIM_MCP_SERVERS_JSON={json.dumps([])}\n"
        f"AIM_FRONTEND_STATIC_DIR={FRONTEND_OUT}\n"
    )
    set_env_path(tmp_env)

    app = create_app()
    app.state.session_factory = factory

    async def _get_db_override():
        async with factory() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    app.dependency_overrides[get_db] = _get_db_override

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c

    set_env_path(None)
    await engine.dispose()


class TestFrontendMount:
    async def test_root_serves_index(self, client: AsyncClient):
        resp = await client.get("/")
        assert resp.status_code == 200
        assert "text/html" in resp.headers["content-type"]

    async def test_login_page(self, client: AsyncClient):
        resp = await client.get("/login")
        assert resp.status_code == 200
        assert "text/html" in resp.headers["content-type"]

    async def test_dashboard_nested_route(self, client: AsyncClient):
        resp = await client.get("/dashboard/incidents/detail")
        assert resp.status_code == 200
        assert "text/html" in resp.headers["content-type"]

    async def test_api_route_wins(self, client: AsyncClient):
        """The /health API route must still be reachable, not shadowed by /{full_path:path}."""
        resp = await client.get("/health")
        assert resp.status_code == 200
        assert resp.json() == {"status": "ok"}

    async def test_unknown_route_returns_404_html(self, client: AsyncClient):
        resp = await client.get("/totally/made/up/path")
        assert resp.status_code == 404
        assert "text/html" in resp.headers["content-type"]

    async def test_traversal_rejected(self, client: AsyncClient):
        resp = await client.get("/../etc/passwd")
        # Client / FastAPI normalises the path so traversal collapses to a
        # regular "not found" response. Either way we must not leak files
        # from outside the static root.
        assert resp.status_code in (400, 404)

    async def test_next_asset_served(self, client: AsyncClient):
        """Grab any _next asset produced by the build to confirm chunk serving works."""
        assets = list((FRONTEND_OUT / "_next").rglob("*.js"))
        if not assets:
            pytest.skip("no _next chunks found in build output")
        rel = assets[0].relative_to(FRONTEND_OUT).as_posix()
        resp = await client.get(f"/{rel}")
        assert resp.status_code == 200
        # Next emits .js; content-type may be application/javascript or text/*
        assert (
            "javascript" in resp.headers["content-type"]
            or "text" in resp.headers["content-type"]
        )
