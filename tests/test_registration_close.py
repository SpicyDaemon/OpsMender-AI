"""Sprint 56 Step 2 — self-registration close + /auth/registration-open."""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from backend.api.app import create_app
from backend.api.deps import set_session_factory
from backend.db.models import Base


@pytest.fixture
async def client(tmp_path, monkeypatch):
    """Fresh app instance per test with a file-backed SQLite DB.

    Mirrors the pattern from ``tests/test_e2e.py``: pre-create the
    schema, bind the session factory, then bring up the app. Each test
    starts with an empty users table.
    """

    db_path = tmp_path / "regclose.db"
    database_url = f"sqlite+aiosqlite:///{db_path}"
    engine = create_async_engine(database_url, echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    set_session_factory(factory)

    monkeypatch.setenv("OPSMENDER_DATABASE_URL", database_url)
    monkeypatch.setenv("OPSMENDER_JWT_SECRET", "test-secret-32-chars-long-enough-ok")

    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    await engine.dispose()


async def _register(client: AsyncClient, *, username: str, email: str) -> int:
    resp = await client.post(
        "/auth/register",
        json={
            "username": username,
            "email": email,
            "password": "securepass123",
        },
    )
    return resp.status_code


# ---------------------------------------------------------------------------
# Dev mode (set by conftest) — register always works
# ---------------------------------------------------------------------------


async def test_registration_open_when_empty(client):
    resp = await client.get("/auth/registration-open")
    assert resp.status_code == 200
    assert resp.json() == {"open": True}


async def test_registration_still_open_in_dev_after_first_user(client):
    """In development mode, /auth/register accepts unlimited registrations."""

    assert await _register(client, username="alice", email="a@b.com") == 201
    # In dev mode, /auth/registration-open stays True even after a user exists
    resp = await client.get("/auth/registration-open")
    assert resp.json()["open"] is True
    # And /auth/register itself keeps working
    assert await _register(client, username="bob", email="b@b.com") == 201


# ---------------------------------------------------------------------------
# Production mode — register closes after the first user
# ---------------------------------------------------------------------------


async def test_registration_closes_in_production_after_first_user(
    client, monkeypatch
):
    monkeypatch.setenv("OPSMENDER_DEPLOYMENT_MODE", "production")

    # Empty system in prod: first user still allowed (fresh-install path).
    resp = await client.get("/auth/registration-open")
    assert resp.json()["open"] is True
    assert await _register(client, username="founder", email="f@b.com") == 201

    # Second registration in prod is refused.
    resp = await client.get("/auth/registration-open")
    assert resp.json()["open"] is False
    status_code = await _register(client, username="late", email="l@b.com")
    assert status_code == 403


async def test_registration_open_endpoint_does_not_require_auth(client, monkeypatch):
    """The login page needs to call this anonymously to decide whether to
    render the register link."""

    monkeypatch.setenv("OPSMENDER_DEPLOYMENT_MODE", "production")
    resp = await client.get("/auth/registration-open")
    assert resp.status_code == 200
    # No Authorization header was sent and we still got a valid body.
    assert "open" in resp.json()
