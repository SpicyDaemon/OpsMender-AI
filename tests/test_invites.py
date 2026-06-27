"""Sprint 56 Step 4 — org invite CRUD + public accept."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from backend.api.app import create_app
from backend.api.deps import set_session_factory
from backend.db.models import Base, Organization


@pytest.fixture
async def env(tmp_path, monkeypatch):
    db_path = tmp_path / "invites.db"
    url = f"sqlite+aiosqlite:///{db_path}"
    engine = create_async_engine(url, echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    set_session_factory(factory)

    monkeypatch.setenv("OPSMENDER_DATABASE_URL", url)
    monkeypatch.setenv("OPSMENDER_JWT_SECRET", "test-secret-32-chars-long-enough-ok")

    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield {"client": client, "factory": factory}
    await engine.dispose()


async def _admin_setup(env) -> tuple[dict[str, str], str]:
    """Register the first user (becomes admin), seed an org, return
    (auth headers, org id)."""

    client: AsyncClient = env["client"]
    factory = env["factory"]
    resp = await client.post(
        "/auth/register",
        json={
            "username": "admin",
            "email": "admin@test.com",
            "password": "securepass123",
        },
    )
    assert resp.status_code == 201

    # Bootstrap a known org so we can target it explicitly.
    org_id = uuid.uuid4()
    async with factory() as db:
        db.add(Organization(id=org_id, name="Acme", slug="acme"))
        await db.commit()

    # Link admin to the new org.
    from backend.db.repos import UserRepo

    async with factory() as db:
        admin = await UserRepo.get_by_username(db, "admin")
        await UserRepo.add_to_organization(
            db, user_id=admin.id, org_id=org_id, role="admin"
        )
        await UserRepo.set_primary_org(db, admin.id, org_id)
        await db.commit()

    login = await client.post(
        "/auth/login",
        json={"username": "admin", "password": "securepass123"},
    )
    headers = {
        "Authorization": f"Bearer {login.json()['access_token']}",
    }
    return headers, str(org_id)


# ---------------------------------------------------------------------------
# Admin: create / list / revoke
# ---------------------------------------------------------------------------


async def test_create_invite_returns_one_time_url(env):
    client = env["client"]
    headers, org_id = await _admin_setup(env)

    resp = await client.post(
        f"/organizations/{org_id}/invites",
        json={"email": "newbie@example.com", "role": "operator"},
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["url"].startswith("http://test/invite?token=")
    assert body["email_sent"] is False  # SMTP not configured
    invite = body["invite"]
    assert invite["email"] == "newbie@example.com"
    assert invite["role"] == "operator"
    assert invite["status"] == "pending"
    expires_at = datetime.fromisoformat(invite["expires_at"])
    remaining = expires_at - datetime.now(timezone.utc)
    assert timedelta(hours=71, minutes=59) < remaining <= timedelta(hours=72)


async def test_create_invite_requires_admin(env):
    client = env["client"]
    headers, org_id = await _admin_setup(env)

    # Make an operator and try as them
    await client.post(
        "/auth/register",
        json={
            "username": "oper",
            "email": "o@b.com",
            "password": "securepass123",
            "role": "operator",
        },
    )
    op_login = await client.post(
        "/auth/login", json={"username": "oper", "password": "securepass123"}
    )
    op_headers = {"Authorization": f"Bearer {op_login.json()['access_token']}"}

    resp = await client.post(
        f"/organizations/{org_id}/invites",
        json={"email": "x@b.com", "role": "viewer"},
        headers=op_headers,
    )
    assert resp.status_code == 403


async def test_create_invite_rejects_existing_member(env):
    client = env["client"]
    headers, org_id = await _admin_setup(env)
    # admin@test.com is already an org member
    resp = await client.post(
        f"/organizations/{org_id}/invites",
        json={"email": "admin@test.com", "role": "viewer"},
        headers=headers,
    )
    assert resp.status_code == 409


async def test_list_invites_returns_all_states(env):
    client = env["client"]
    headers, org_id = await _admin_setup(env)
    await client.post(
        f"/organizations/{org_id}/invites",
        json={"email": "a@b.com", "role": "viewer"},
        headers=headers,
    )
    await client.post(
        f"/organizations/{org_id}/invites",
        json={"email": "c@b.com", "role": "operator"},
        headers=headers,
    )
    resp = await client.get(
        f"/organizations/{org_id}/invites", headers=headers
    )
    assert resp.status_code == 200
    items = resp.json()["items"]
    assert len(items) == 2
    assert all(i["status"] == "pending" for i in items)


async def test_revoke_invite(env):
    client = env["client"]
    headers, org_id = await _admin_setup(env)
    created = await client.post(
        f"/organizations/{org_id}/invites",
        json={"email": "a@b.com", "role": "viewer"},
        headers=headers,
    )
    invite_id = created.json()["invite"]["id"]

    resp = await client.post(
        f"/organizations/{org_id}/invites/{invite_id}/revoke", headers=headers
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "revoked"

    # Idempotent
    resp = await client.post(
        f"/organizations/{org_id}/invites/{invite_id}/revoke", headers=headers
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "revoked"


async def test_revoke_invite_unknown_returns_404(env):
    client = env["client"]
    headers, org_id = await _admin_setup(env)
    fake_id = uuid.uuid4()
    resp = await client.post(
        f"/organizations/{org_id}/invites/{fake_id}/revoke", headers=headers
    )
    assert resp.status_code == 404


async def test_resend_invite_reissues_pending_invite(env):
    client = env["client"]
    headers, org_id = await _admin_setup(env)
    created = await client.post(
        f"/organizations/{org_id}/invites",
        json={"email": "a@b.com", "role": "viewer"},
        headers=headers,
    )
    invite_id = created.json()["invite"]["id"]
    old_url = created.json()["url"]
    old_raw = old_url.split("token=", 1)[-1]

    resent = await client.post(
        f"/organizations/{org_id}/invites/{invite_id}/resend", headers=headers
    )
    assert resent.status_code == 200, resent.text
    body = resent.json()
    assert body["invite"]["email"] == "a@b.com"
    assert body["invite"]["role"] == "viewer"
    assert body["invite"]["status"] == "pending"
    assert body["invite"]["id"] != invite_id
    assert body["url"].startswith("http://test/invite?token=")
    assert body["url"] != old_url

    listing = await client.get(
        f"/organizations/{org_id}/invites", headers=headers
    )
    items = listing.json()["items"]
    states = {item["id"]: item["status"] for item in items}
    assert states[invite_id] == "revoked"
    assert states[body["invite"]["id"]] == "pending"

    old_validate = await client.get(f"/invites/{old_raw}")
    assert old_validate.status_code == 400

    new_raw = body["url"].split("token=", 1)[-1]
    new_validate = await client.get(f"/invites/{new_raw}")
    assert new_validate.status_code == 200


async def test_resend_invite_rejects_non_pending(env):
    client = env["client"]
    headers, org_id = await _admin_setup(env)
    created = await client.post(
        f"/organizations/{org_id}/invites",
        json={"email": "accepted@example.com", "role": "viewer"},
        headers=headers,
    )
    raw = created.json()["url"].split("token=", 1)[-1]
    invite_id = created.json()["invite"]["id"]

    accepted = await client.post(
        f"/invites/{raw}/accept",
        json={"username": "accepteduser", "password": "accept-pass-123"},
    )
    assert accepted.status_code == 200

    resent = await client.post(
        f"/organizations/{org_id}/invites/{invite_id}/resend", headers=headers
    )
    assert resent.status_code == 409


# ---------------------------------------------------------------------------
# Public: validate + accept
# ---------------------------------------------------------------------------


async def test_get_invite_returns_safe_fields(env):
    client = env["client"]
    headers, org_id = await _admin_setup(env)
    created = await client.post(
        f"/organizations/{org_id}/invites",
        json={"email": "newbie@example.com", "role": "viewer"},
        headers=headers,
    )
    raw = created.json()["url"].split("token=", 1)[-1]

    resp = await client.get(f"/invites/{raw}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["email"] == "newbie@example.com"
    assert body["role"] == "viewer"
    assert body["org_name"] == "Acme"
    assert "expires_at" in body
    # No internal IDs leaked
    assert "id" not in body
    assert "token" not in body
    assert "org_id" not in body


async def test_get_invite_invalid_token_returns_400(env):
    client = env["client"]
    resp = await client.get("/invites/not-a-real-token")
    assert resp.status_code == 400


async def test_accept_invite_creates_user_and_returns_jwt(env):
    client = env["client"]
    headers, org_id = await _admin_setup(env)
    created = await client.post(
        f"/organizations/{org_id}/invites",
        json={"email": "newbie@example.com", "role": "operator"},
        headers=headers,
    )
    raw = created.json()["url"].split("token=", 1)[-1]

    resp = await client.post(
        f"/invites/{raw}/accept",
        json={"username": "newbie", "password": "accept-pass-123"},
    )
    assert resp.status_code == 200, resp.text
    token = resp.json()["access_token"]
    assert token

    # The new user can log in
    login = await client.post(
        "/auth/login",
        json={"username": "newbie", "password": "accept-pass-123"},
    )
    assert login.status_code == 200

    # Invite is marked accepted; status is "accepted" in the list
    listing = await client.get(
        f"/organizations/{org_id}/invites", headers=headers
    )
    statuses = [i["status"] for i in listing.json()["items"]]
    assert "accepted" in statuses


async def test_accept_invite_is_single_use(env):
    client = env["client"]
    headers, org_id = await _admin_setup(env)
    created = await client.post(
        f"/organizations/{org_id}/invites",
        json={"email": "newbie@example.com", "role": "viewer"},
        headers=headers,
    )
    raw = created.json()["url"].split("token=", 1)[-1]

    first = await client.post(
        f"/invites/{raw}/accept",
        json={"username": "newbie", "password": "accept-pass-123"},
    )
    assert first.status_code == 200

    replay = await client.post(
        f"/invites/{raw}/accept",
        json={"username": "newbie2", "password": "accept-pass-456"},
    )
    assert replay.status_code == 400


async def test_accept_invite_rejects_revoked_token(env):
    client = env["client"]
    headers, org_id = await _admin_setup(env)
    created = await client.post(
        f"/organizations/{org_id}/invites",
        json={"email": "newbie@example.com", "role": "viewer"},
        headers=headers,
    )
    raw = created.json()["url"].split("token=", 1)[-1]
    invite_id = created.json()["invite"]["id"]

    await client.post(
        f"/organizations/{org_id}/invites/{invite_id}/revoke", headers=headers
    )

    resp = await client.post(
        f"/invites/{raw}/accept",
        json={"username": "newbie", "password": "accept-pass-123"},
    )
    assert resp.status_code == 400


async def test_accept_invite_rejects_duplicate_username(env):
    client = env["client"]
    headers, org_id = await _admin_setup(env)
    # Pre-existing user with username "newbie"
    await client.post(
        "/auth/register",
        json={
            "username": "newbie",
            "email": "different@b.com",
            "password": "securepass123",
        },
    )

    created = await client.post(
        f"/organizations/{org_id}/invites",
        json={"email": "newbie-invite@example.com", "role": "viewer"},
        headers=headers,
    )
    raw = created.json()["url"].split("token=", 1)[-1]

    resp = await client.post(
        f"/invites/{raw}/accept",
        json={"username": "newbie", "password": "accept-pass-123"},
    )
    assert resp.status_code == 409


async def test_invite_carries_names_through_acceptance(env):
    """Optional first/last name on the invite prefill the public response and
    are applied to the created user on acceptance."""
    client: AsyncClient = env["client"]
    headers, org_id = await _admin_setup(env)

    created = await client.post(
        f"/organizations/{org_id}/invites",
        json={
            "email": "grace@example.com",
            "role": "operator",
            "first_name": "Grace",
            "last_name": "Hopper",
        },
        headers=headers,
    )
    assert created.status_code == 201, created.text
    raw = created.json()["url"].split("token=", 1)[-1]

    # Public validate exposes the prefill names.
    public = await client.get(f"/invites/{raw}")
    assert public.status_code == 200
    assert public.json()["first_name"] == "Grace"
    assert public.json()["last_name"] == "Hopper"

    # Accept (recipient can override; here they keep the prefill last name and
    # change the first name).
    accept = await client.post(
        f"/invites/{raw}/accept",
        json={
            "username": "ghopper",
            "password": "a-strong-pass-1",
            "first_name": "Grace B.",
        },
    )
    assert accept.status_code == 200, accept.text

    me = await client.get(
        "/auth/me",
        headers={"Authorization": f"Bearer {accept.json()['access_token']}"},
    )
    assert me.json()["first_name"] == "Grace B."
    assert me.json()["last_name"] == "Hopper"
