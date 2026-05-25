"""End-to-end smoke test for the full Sprint 56 People flow.

Exercises every new route shipped in Sprint 56 in one continuous
scenario so the modal handoffs, token round-trips, and cascade gates
the unit tests cover individually are proven to compose correctly.

Scenario:
1. Admin bootstraps fresh system via /auth/register (dev mode lets
   the empty-table path through; first user is admin).
2. Admin mints an invite for a teammate (with the org_id pinned).
3. Teammate validates + accepts the invite (creates user, returns
   JWT).
4. Teammate logs in with their own credentials.
5. Admin mints a password reset for the teammate.
6. Teammate consumes the reset URL with a new password.
7. Teammate's old password fails, new password works.
8. Admin tries to delete the teammate while they're still active →
   blocked.
9. Admin deactivates the teammate → login refused.
10. Admin runs delete-preconditions → can_delete=True.
11. Admin soft-deletes the teammate.
12. Re-fetching the teammate's detail returns deleted_at != None
    with scrubbed email + empty password_hash.
13. Listing users shows the teammate as is_active=false.
"""

from __future__ import annotations

import uuid

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from backend.api.app import create_app
from backend.api.deps import set_session_factory
from backend.db.models import Base, Organization
from backend.db.repos import UserRepo


@pytest.fixture
async def env(tmp_path, monkeypatch):
    db_path = tmp_path / "people_e2e.db"
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


async def test_people_full_lifecycle(env):
    client: AsyncClient = env["client"]
    factory = env["factory"]

    # ----- (1) Admin bootstrap -------------------------------------------
    resp = await client.post(
        "/auth/register",
        json={
            "username": "admin",
            "email": "admin@acme.com",
            "password": "admin-pass-123",
        },
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["role"] == "admin"

    # Seed a known org and link the admin to it (the bootstrap path on
    # /auth/register creates a "Main" org; use that).
    async with factory() as db:
        admin = await UserRepo.get_by_username(db, "admin")
        from backend.db.repos import OrganizationRepo

        orgs = list(await OrganizationRepo.list_all(db))
        org = orgs[0]

    admin_id = str(admin.id)
    org_id = str(org.id)

    login = await client.post(
        "/auth/login",
        json={"username": "admin", "password": "admin-pass-123"},
    )
    assert login.status_code == 200
    admin_headers = {
        "Authorization": f"Bearer {login.json()['access_token']}",
        "X-Org-ID": org_id,
    }

    # ----- (2) Admin mints an invite -------------------------------------
    minted = await client.post(
        f"/organizations/{org_id}/invites",
        json={"email": "teammate@acme.com", "role": "operator"},
        headers=admin_headers,
    )
    assert minted.status_code == 201, minted.text
    invite_url = minted.json()["url"]
    invite_token = invite_url.rsplit("/", 1)[-1]
    assert minted.json()["invite"]["status"] == "pending"

    # ----- (3) Teammate validates + accepts ------------------------------
    public = await client.get(f"/invites/{invite_token}")
    assert public.status_code == 200
    assert public.json()["email"] == "teammate@acme.com"
    assert public.json()["org_name"] == org.name
    # No internal IDs leak in the public payload.
    assert "id" not in public.json()
    assert "org_id" not in public.json()

    accept = await client.post(
        f"/invites/{invite_token}/accept",
        json={"username": "teammate", "password": "first-pass-123"},
    )
    assert accept.status_code == 200, accept.text
    teammate_token = accept.json()["access_token"]
    assert teammate_token

    # Verify the invite flips to "accepted" in the admin list.
    listing = await client.get(
        f"/organizations/{org_id}/invites", headers=admin_headers
    )
    statuses = {i["email"]: i["status"] for i in listing.json()["items"]}
    assert statuses["teammate@acme.com"] == "accepted"

    # ----- (4) Teammate can log in with their own credentials ------------
    login = await client.post(
        "/auth/login",
        json={"username": "teammate", "password": "first-pass-123"},
    )
    assert login.status_code == 200

    # Fetch the teammate's id for later steps.
    async with factory() as db:
        teammate = await UserRepo.get_by_username(db, "teammate")
        teammate_id = str(teammate.id)
    assert teammate.role == "operator"
    assert teammate.primary_org_id == org.id

    # ----- (5) Admin mints a password reset ------------------------------
    reset = await client.post(
        f"/auth/users/{teammate_id}/reset-password", headers=admin_headers
    )
    assert reset.status_code == 200, reset.text
    reset_url = reset.json()["url"]
    reset_token = reset_url.rsplit("/", 1)[-1]

    # ----- (6+7) Teammate consumes the reset; old pw fails, new works ---
    consume = await client.post(
        f"/auth/password-reset/{reset_token}",
        json={"password": "second-pass-456"},
    )
    assert consume.status_code == 204

    old = await client.post(
        "/auth/login",
        json={"username": "teammate", "password": "first-pass-123"},
    )
    assert old.status_code == 401

    new = await client.post(
        "/auth/login",
        json={"username": "teammate", "password": "second-pass-456"},
    )
    assert new.status_code == 200

    # Token is single-use — replay must fail.
    replay = await client.post(
        f"/auth/password-reset/{reset_token}",
        json={"password": "third-pass-789"},
    )
    assert replay.status_code == 400

    # ----- (8) Admin tries to delete while teammate is still active -----
    blocked = await client.post(
        f"/auth/users/{teammate_id}/soft-delete", headers=admin_headers
    )
    assert blocked.status_code == 409  # must be deactivated first
    assert "deactivate" in blocked.json()["detail"].lower()

    # ----- (9) Admin deactivates ----------------------------------------
    deactivate = await client.patch(
        f"/auth/users/{teammate_id}",
        json={"is_active": False},
        headers=admin_headers,
    )
    assert deactivate.status_code == 200
    assert deactivate.json()["is_active"] is False

    # Deactivated user cannot log in.
    after_deactivate = await client.post(
        "/auth/login",
        json={"username": "teammate", "password": "second-pass-456"},
    )
    assert after_deactivate.status_code == 403

    # ----- (10) Preconditions report can_delete=True --------------------
    pre = await client.get(
        f"/auth/users/{teammate_id}/delete-preconditions",
        headers=admin_headers,
    )
    assert pre.status_code == 200
    body = pre.json()
    assert body["is_active"] is False
    assert body["roster_memberships"] == 0
    assert body["can_delete"] is True

    # ----- (11) Admin soft-deletes --------------------------------------
    deleted = await client.post(
        f"/auth/users/{teammate_id}/soft-delete", headers=admin_headers
    )
    assert deleted.status_code == 200, deleted.text
    assert deleted.json()["deleted_at"] is not None
    assert "deleted.opsmender.local" in deleted.json()["email"]
    # Username preserved per owner direction.
    assert deleted.json()["username"] == "teammate"

    # ----- (12) GET /auth/users/{id} returns the deleted state ----------
    fetched = await client.get(
        f"/auth/users/{teammate_id}", headers=admin_headers
    )
    assert fetched.status_code == 200
    body = fetched.json()
    assert body["deleted_at"] is not None
    assert "deleted.opsmender.local" in body["email"]
    # PATCH on a deleted user now 404s (deleted users are hidden from
    # mutation surfaces).
    patch = await client.patch(
        f"/auth/users/{teammate_id}",
        json={"role": "viewer"},
        headers=admin_headers,
    )
    assert patch.status_code == 404

    # ----- (13) Listing users still shows the deleted row but is_active=False
    all_users = await client.get("/auth/users", headers=admin_headers)
    found = [u for u in all_users.json()["items"] if u["id"] == teammate_id]
    assert len(found) == 1
    assert found[0]["is_active"] is False
    assert found[0]["deleted_at"] is not None
    assert found[0]["username"] == "teammate"  # historical-display preserved

    # ----- Sanity: admin can't accidentally delete themselves -----------
    self_delete = await client.post(
        f"/auth/users/{admin_id}/soft-delete", headers=admin_headers
    )
    assert self_delete.status_code == 400
