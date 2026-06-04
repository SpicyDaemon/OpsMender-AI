"""Sprint 56 Step 3 — user CRUD + password reset + soft delete."""

from __future__ import annotations

import uuid

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from backend.api.app import create_app
from backend.api.deps import set_session_factory
from backend.db.models import (
    Base,
    Organization,
    Roster,
    RosterMember,
    Team,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
async def env(tmp_path, monkeypatch):
    db_path = tmp_path / "user_admin.db"
    database_url = f"sqlite+aiosqlite:///{db_path}"
    engine = create_async_engine(database_url, echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    set_session_factory(factory)

    monkeypatch.setenv("OPSMENDER_DATABASE_URL", database_url)
    monkeypatch.setenv("OPSMENDER_JWT_SECRET", "test-secret-32-chars-long-enough-ok")
    # Single-org bootstrap not needed; tests register users directly.

    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield {"client": client, "factory": factory}
    await engine.dispose()


async def _admin_token(client: AsyncClient) -> tuple[dict[str, str], str]:
    """Register an admin (first user gets admin) and return (headers, user_id)."""

    resp = await client.post(
        "/auth/register",
        json={
            "username": "admin",
            "email": "admin@test.com",
            "password": "securepass123",
        },
    )
    assert resp.status_code == 201, resp.text
    admin_id = resp.json()["id"]
    login = await client.post(
        "/auth/login",
        json={"username": "admin", "password": "securepass123"},
    )
    assert login.status_code == 200, login.text
    return (
        {"Authorization": f"Bearer {login.json()['access_token']}"},
        admin_id,
    )


async def _register_extra_user(
    client: AsyncClient, *, username: str, email: str, role: str = "viewer"
) -> str:
    """Register a second user (dev-mode register stays open in tests)."""

    resp = await client.post(
        "/auth/register",
        json={
            "username": username,
            "email": email,
            "password": "securepass123",
            "role": role,
        },
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


# ---------------------------------------------------------------------------
# PATCH /auth/users/{id}
# ---------------------------------------------------------------------------


async def test_patch_user_changes_role(env):
    client = env["client"]
    headers, _ = await _admin_token(client)
    target_id = await _register_extra_user(
        client, username="viewer", email="v@b.com", role="viewer"
    )

    resp = await client.patch(
        f"/auth/users/{target_id}",
        json={"role": "operator"},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["role"] == "operator"


async def test_patch_user_deactivates(env):
    client = env["client"]
    headers, _ = await _admin_token(client)
    target_id = await _register_extra_user(
        client, username="someone", email="s@b.com"
    )

    resp = await client.patch(
        f"/auth/users/{target_id}",
        json={"is_active": False},
        headers=headers,
    )
    assert resp.status_code == 200
    assert resp.json()["is_active"] is False

    # Deactivated user cannot log in
    login = await client.post(
        "/auth/login",
        json={"username": "someone", "password": "securepass123"},
    )
    assert login.status_code == 403


async def test_patch_user_requires_admin(env):
    client = env["client"]
    headers, _ = await _admin_token(client)
    target_id = await _register_extra_user(
        client, username="viewer", email="v@b.com"
    )
    # Operator cannot patch
    op_id = await _register_extra_user(
        client, username="oper", email="o@b.com", role="operator"
    )
    _ = op_id
    op_login = await client.post(
        "/auth/login", json={"username": "oper", "password": "securepass123"}
    )
    op_headers = {"Authorization": f"Bearer {op_login.json()['access_token']}"}
    resp = await client.patch(
        f"/auth/users/{target_id}",
        json={"role": "admin"},
        headers=op_headers,
    )
    assert resp.status_code == 403


async def test_patch_user_requires_at_least_one_field(env):
    client = env["client"]
    headers, _ = await _admin_token(client)
    target_id = await _register_extra_user(
        client, username="viewer", email="v@b.com"
    )
    resp = await client.patch(
        f"/auth/users/{target_id}", json={}, headers=headers
    )
    assert resp.status_code == 400


async def test_patch_user_404_on_missing(env):
    client = env["client"]
    headers, _ = await _admin_token(client)
    fake_id = uuid.uuid4()
    resp = await client.patch(
        f"/auth/users/{fake_id}",
        json={"role": "viewer"},
        headers=headers,
    )
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# POST /auth/users/{id}/reset-password + POST /auth/password-reset/{token}
# ---------------------------------------------------------------------------


async def test_mint_password_reset_returns_one_time_url(env):
    client = env["client"]
    headers, _ = await _admin_token(client)
    target_id = await _register_extra_user(
        client, username="viewer", email="v@b.com"
    )

    resp = await client.post(
        f"/auth/users/{target_id}/reset-password", headers=headers
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["url"].startswith("http://test/password-reset?token=")
    assert body["email_sent"] is False  # SMTP not configured in tests
    assert "expires_at" in body


async def test_password_reset_round_trip(env):
    client = env["client"]
    headers, _ = await _admin_token(client)
    target_id = await _register_extra_user(
        client, username="viewer", email="v@b.com"
    )

    mint = await client.post(
        f"/auth/users/{target_id}/reset-password", headers=headers
    )
    raw_token = mint.json()["url"].split("token=", 1)[-1]

    # Consume with new password
    resp = await client.post(
        f"/auth/password-reset/{raw_token}",
        json={"password": "new-password-456"},
    )
    assert resp.status_code == 204

    # Old password fails
    bad = await client.post(
        "/auth/login",
        json={"username": "viewer", "password": "securepass123"},
    )
    assert bad.status_code == 401

    # New password works
    good = await client.post(
        "/auth/login",
        json={"username": "viewer", "password": "new-password-456"},
    )
    assert good.status_code == 200

    # Token cannot be reused
    replay = await client.post(
        f"/auth/password-reset/{raw_token}",
        json={"password": "another-pass-789"},
    )
    assert replay.status_code == 400


async def test_password_reset_rejects_invalid_token(env):
    client = env["client"]
    resp = await client.post(
        "/auth/password-reset/not-a-real-token",
        json={"password": "another-pass-789"},
    )
    assert resp.status_code == 400


async def test_mint_password_reset_requires_admin(env):
    client = env["client"]
    await _admin_token(client)
    op_id = await _register_extra_user(
        client, username="oper", email="o@b.com", role="operator"
    )
    op_login = await client.post(
        "/auth/login", json={"username": "oper", "password": "securepass123"}
    )
    op_headers = {"Authorization": f"Bearer {op_login.json()['access_token']}"}
    resp = await client.post(
        f"/auth/users/{op_id}/reset-password", headers=op_headers
    )
    assert resp.status_code == 403


# ---------------------------------------------------------------------------
# Soft-delete
# ---------------------------------------------------------------------------


async def test_soft_delete_blocks_active_user(env):
    client = env["client"]
    headers, _ = await _admin_token(client)
    target_id = await _register_extra_user(
        client, username="active", email="a@b.com"
    )

    resp = await client.post(
        f"/auth/users/{target_id}/soft-delete", headers=headers
    )
    assert resp.status_code == 409  # must be deactivated first


async def test_soft_delete_blocks_self(env):
    client = env["client"]
    headers, admin_id = await _admin_token(client)
    resp = await client.post(
        f"/auth/users/{admin_id}/soft-delete", headers=headers
    )
    assert resp.status_code == 400


async def test_soft_delete_happy_path(env):
    client = env["client"]
    headers, _ = await _admin_token(client)
    target_id = await _register_extra_user(
        client, username="goner", email="g@b.com"
    )
    # Deactivate first
    await client.patch(
        f"/auth/users/{target_id}",
        json={"is_active": False},
        headers=headers,
    )

    pre = await client.get(
        f"/auth/users/{target_id}/delete-preconditions", headers=headers
    )
    assert pre.status_code == 200
    body = pre.json()
    assert body["is_active"] is False
    assert body["roster_memberships"] == 0
    assert body["can_delete"] is True

    resp = await client.post(
        f"/auth/users/{target_id}/soft-delete", headers=headers
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["deleted_at"] is not None
    # Email is scrubbed
    assert "deleted.opsmender.local" in resp.json()["email"]

    # Cannot delete twice (404 — deleted users hidden)
    again = await client.post(
        f"/auth/users/{target_id}/soft-delete", headers=headers
    )
    assert again.status_code == 404


async def test_soft_delete_blocks_when_on_roster(env):
    client = env["client"]
    factory = env["factory"]
    headers, _ = await _admin_token(client)
    target_id = await _register_extra_user(
        client, username="oncall", email="oc@b.com"
    )
    # Deactivate
    await client.patch(
        f"/auth/users/{target_id}",
        json={"is_active": False},
        headers=headers,
    )

    # Seed an org + team + roster + membership directly via the session
    async with factory() as db:
        org_id = uuid.uuid4()
        team_id = uuid.uuid4()
        roster_id = uuid.uuid4()
        db.add(Organization(id=org_id, name="O", slug="o"))
        db.add(Team(id=team_id, org_id=org_id, name="T", slug="t"))
        from datetime import date as _date

        db.add(
            Roster(
                id=roster_id,
                org_id=org_id,
                team_id=team_id,
                name="r",
                pattern="weekly",
                anchor_date=_date.today(),
                handoff_time="09:00",
                time_zone="UTC",
            )
        )
        db.add(
            RosterMember(
                id=uuid.uuid4(),
                org_id=org_id,
                roster_id=roster_id,
                user_id=uuid.UUID(target_id),
                position_index=0,
            )
        )
        await db.commit()

    pre = await client.get(
        f"/auth/users/{target_id}/delete-preconditions", headers=headers
    )
    assert pre.json()["can_delete"] is False
    assert pre.json()["roster_memberships"] == 1

    resp = await client.post(
        f"/auth/users/{target_id}/soft-delete", headers=headers
    )
    assert resp.status_code == 409
    assert "roster" in resp.json()["detail"].lower()


# ---------------------------------------------------------------------------
# Direct admin user creation (v1 — no invite link required)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_admin_creates_user_directly_and_they_can_log_in(env):
    client: AsyncClient = env["client"]
    headers, _ = await _admin_token(client)

    resp = await client.post(
        "/auth/users",
        headers=headers,
        json={
            "username": "operator1",
            "email": "Operator1@Test.com",
            "role": "operator",
            "password": "temp-pass-123",
            "is_active": True,
        },
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["username"] == "operator1"
    assert body["email"] == "operator1@test.com"  # normalized lowercase
    assert body["role"] == "operator"
    assert body["is_active"] is True

    # The created user can log in immediately with the temporary password.
    login = await client.post(
        "/auth/login",
        json={"username": "operator1", "password": "temp-pass-123"},
    )
    assert login.status_code == 200, login.text

    # And appears in the admin user list.
    listing = await client.get("/auth/users", headers=headers)
    usernames = {u["username"] for u in listing.json()["items"]}
    assert "operator1" in usernames


@pytest.mark.asyncio
async def test_create_user_rejects_duplicate_username(env):
    client: AsyncClient = env["client"]
    headers, _ = await _admin_token(client)
    payload = {
        "username": "dupe",
        "email": "dupe@test.com",
        "role": "viewer",
        "password": "temp-pass-123",
    }
    first = await client.post("/auth/users", headers=headers, json=payload)
    assert first.status_code == 201
    second = await client.post(
        "/auth/users",
        headers=headers,
        json={**payload, "email": "other@test.com"},
    )
    assert second.status_code == 409


@pytest.mark.asyncio
async def test_create_user_requires_admin(env):
    client: AsyncClient = env["client"]
    await _admin_token(client)
    # A non-admin (viewer) token must be rejected.
    viewer_id = await _register_extra_user(
        client, username="viewer1", email="viewer1@test.com", role="viewer"
    )
    assert viewer_id
    login = await client.post(
        "/auth/login", json={"username": "viewer1", "password": "securepass123"}
    )
    viewer_headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
    resp = await client.post(
        "/auth/users",
        headers=viewer_headers,
        json={
            "username": "nope",
            "email": "nope@test.com",
            "role": "viewer",
            "password": "temp-pass-123",
        },
    )
    assert resp.status_code == 403


# ---------------------------------------------------------------------------
# Self-service profile + password (Parts 6/7)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_update_me_profile_fields(env):
    client: AsyncClient = env["client"]
    headers, _ = await _admin_token(client)

    resp = await client.patch(
        "/auth/me",
        headers=headers,
        json={
            "first_name": "Ada",
            "last_name": "Lovelace",
            "avatar_color": "violet",
            "username": "ada",
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["first_name"] == "Ada"
    assert body["last_name"] == "Lovelace"
    assert body["avatar_color"] == "violet"
    assert body["username"] == "ada"

    me = await client.get("/auth/me", headers=headers)
    assert me.json()["first_name"] == "Ada"
    assert me.json()["username"] == "ada"


@pytest.mark.asyncio
async def test_update_me_username_conflict(env):
    client: AsyncClient = env["client"]
    headers, _ = await _admin_token(client)
    await _register_extra_user(client, username="taken", email="taken@test.com")

    resp = await client.patch("/auth/me", headers=headers, json={"username": "taken"})
    assert resp.status_code == 409


@pytest.mark.asyncio
async def test_change_my_password(env):
    client: AsyncClient = env["client"]
    headers, _ = await _admin_token(client)

    # Wrong current password is rejected.
    bad = await client.post(
        "/auth/me/password",
        headers=headers,
        json={"current_password": "wrong-pass", "new_password": "brand-new-pass-1"},
    )
    assert bad.status_code == 400

    # Correct current password rotates it.
    ok = await client.post(
        "/auth/me/password",
        headers=headers,
        json={"current_password": "securepass123", "new_password": "brand-new-pass-1"},
    )
    assert ok.status_code == 204

    # Old password no longer works; new one does.
    old_login = await client.post(
        "/auth/login", json={"username": "admin", "password": "securepass123"}
    )
    assert old_login.status_code == 401
    new_login = await client.post(
        "/auth/login", json={"username": "admin", "password": "brand-new-pass-1"}
    )
    assert new_login.status_code == 200
