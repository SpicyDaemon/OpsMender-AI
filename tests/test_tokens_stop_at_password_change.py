"""M1-33 (R-10, O-09): a password change or reset revokes every API token the
person created, at that moment: their own change, an admin's temporary
password and the reset link. The tokens get 401 afterwards and show as
revoked, each with an Activity entry; another person's token keeps working."""

from __future__ import annotations

import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from backend.api.auth import hash_password
from backend.db.models import AuditEntry
from backend.db.repos import UserRepo
from tests.test_api_tokens import (
    TEST_ORG_ID,
    _bearer,
    _create_token,
    admin_headers as _admin_headers_fixture,
    app as _app_fixture,
    client as _client_fixture,
)

app = pytest.fixture(_app_fixture.__wrapped__)
client = pytest.fixture(_client_fixture.__wrapped__)
admin_headers = pytest.fixture(_admin_headers_fixture.__wrapped__)

OTHER_PASSWORD = "other-admin-pass1"


async def _other_admin(app, client: AsyncClient) -> tuple[uuid.UUID, dict]:
    async with app.state.session_factory() as db:
        user = await UserRepo.create(
            db,
            username="other-admin",
            email="other-admin@test.com",
            password_hash=hash_password(OTHER_PASSWORD),
            role="admin",
            primary_org_id=TEST_ORG_ID,
        )
        await UserRepo.add_to_organization(
            db, user_id=user.id, org_id=TEST_ORG_ID, role="admin"
        )
        await db.commit()
    resp = await client.post(
        "/auth/login", json={"username": "other-admin", "password": OTHER_PASSWORD}
    )
    assert resp.status_code == 200, resp.text
    return user.id, {"Authorization": f"Bearer {resp.json()['access_token']}"}


async def _admin_id(app) -> uuid.UUID:
    async with app.state.session_factory() as db:
        user = await UserRepo.get_by_username(db, "api-admin")
        return user.id


async def _works(client: AsyncClient, token: dict) -> int:
    return (await client.get("/incidents", headers=_bearer(token["token"]))).status_code


async def _revocations(app) -> list[dict]:
    async with app.state.session_factory() as db:
        rows = await db.execute(
            select(AuditEntry).where(
                AuditEntry.entry_type == "api_token_change",
                AuditEntry.tool_name == "api_token_revoke",
            )
        )
        return [row.tool_parameters for row in rows.scalars()]


async def _tokens(client: AsyncClient, headers) -> dict[str, dict]:
    resp = await client.get("/api/v1/api-tokens", headers=headers)
    assert resp.status_code == 200, resp.text
    return {row["name"]: row for row in resp.json()["items"]}


async def _setup(app, client: AsyncClient, admin_headers):
    """Two tokens of the first admin (one revoked already) and one of another."""
    mine = await _create_token(client, admin_headers, name="mine", role="operator")
    old = await _create_token(client, admin_headers, name="old", role="viewer")
    resp = await client.delete(f"/api/v1/api-tokens/{old['id']}", headers=admin_headers)
    assert resp.status_code == 204, resp.text
    other_id, other_headers = await _other_admin(app, client)
    theirs = await _create_token(client, other_headers, name="theirs", role="operator")
    assert await _works(client, mine) == await _works(client, theirs) == 200
    return mine, theirs, other_headers


def _assert_only_mine_revoked(entries, mine, actor, reason):
    [entry] = [e for e in entries if e.get("reason")]
    assert entry["token_id"] == mine["id"] and entry["name"] == "mine"
    assert (entry["actor"], entry["reason"]) == (actor, reason)


async def test_your_own_password_change_revokes_the_tokens_you_created(
    app, client: AsyncClient, admin_headers
):
    mine, theirs, other_headers = await _setup(app, client, admin_headers)

    resp = await client.post(
        "/auth/me/password",
        json={"current_password": "securepass123", "new_password": "fresh-pass-789"},
        headers=admin_headers,
    )

    assert resp.status_code == 200, resp.text
    assert await _works(client, mine) == 401
    assert await _works(client, theirs) == 200
    listed = await _tokens(client, other_headers)
    assert listed["mine"]["revoked_at"] is not None
    assert listed["theirs"]["revoked_at"] is None
    _assert_only_mine_revoked(
        await _revocations(app), mine, str(await _admin_id(app)), "password_changed"
    )


async def test_a_temporary_password_revokes_the_persons_tokens(
    app, client: AsyncClient, admin_headers
):
    mine, theirs, other_headers = await _setup(app, client, admin_headers)
    admin_id = await _admin_id(app)

    resp = await client.post(
        f"/auth/users/{admin_id}/set-temporary-password", headers=other_headers
    )

    assert resp.status_code == 200, resp.text
    assert await _works(client, mine) == 401
    assert await _works(client, theirs) == 200
    other = await _tokens(client, other_headers)
    assert other["mine"]["revoked_at"] is not None
    async with app.state.session_factory() as db:
        other_id = (await UserRepo.get_by_username(db, "other-admin")).id
    _assert_only_mine_revoked(
        await _revocations(app), mine, str(other_id), "temporary_password"
    )


async def test_the_reset_link_revokes_the_persons_tokens(
    app, client: AsyncClient, admin_headers
):
    mine, theirs, other_headers = await _setup(app, client, admin_headers)
    admin_id = await _admin_id(app)
    mint = await client.post(
        f"/auth/users/{admin_id}/reset-password", headers=other_headers
    )
    assert mint.status_code == 200, mint.text
    # Minting the link changes nothing yet.
    assert await _works(client, mine) == 200

    resp = await client.post(
        f"/auth/password-reset/{mint.json()['url'].split('token=', 1)[-1]}",
        json={"password": "reset-pass-456"},
    )

    assert resp.status_code == 204, resp.text
    assert await _works(client, mine) == 401
    assert await _works(client, theirs) == 200
    _assert_only_mine_revoked(
        await _revocations(app), mine, str(admin_id), "password_reset"
    )


async def test_a_failed_password_change_keeps_the_tokens(
    app, client: AsyncClient, admin_headers
):
    mine, _, _ = await _setup(app, client, admin_headers)

    resp = await client.post(
        "/auth/me/password",
        json={"current_password": "wrong-password", "new_password": "fresh-pass-789"},
        headers=admin_headers,
    )

    assert resp.status_code == 400
    assert await _works(client, mine) == 200
    assert [e for e in await _revocations(app) if e.get("reason")] == []
