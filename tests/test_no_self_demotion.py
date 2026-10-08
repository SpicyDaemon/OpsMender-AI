"""M1-34 (R-14): nobody changes their own role or deactivates their own
account, through the web or an API token (400, as deleting yourself already
is), so one active admin always remains. Their role, Roster membership and
notices stay as they were; editing their own name still works, and another
admin can still demote or deactivate them."""

from __future__ import annotations

import uuid
from datetime import date

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select

from backend.api.auth import hash_password
from backend.db.models import InAppNotification, RosterMember, User
from backend.db.repos import RosterRepo, TeamRepo, UserRepo
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

REFUSED = (
    "You can't change your own role or deactivate your own account. Ask another admin."
)
OTHER_PASSWORD = "other-admin-pass1"


async def _admin(app) -> User:
    async with app.state.session_factory() as db:
        return await UserRepo.get_by_username(db, "api-admin")


async def _on_a_roster(app, user_id: uuid.UUID) -> None:
    async with app.state.session_factory() as db:
        team = await TeamRepo.create(db, TEST_ORG_ID, name="Core", slug="core")
        roster = await RosterRepo.create(
            db,
            TEST_ORG_ID,
            team_id=team.id,
            name="Primary",
            anchor_date=date(2026, 10, 5),
        )
        await RosterRepo.add_member(
            db, TEST_ORG_ID, roster_id=roster.id, user_id=user_id, position_index=0
        )
        await db.commit()


async def _state(app, user_id: uuid.UUID) -> tuple[str, bool, int, int]:
    """(role, active, Roster memberships, notices) for ``user_id``. A place
    taken off a Roster stays in its rotation, marked removed (O-03), so only
    current memberships count."""
    async with app.state.session_factory() as db:
        user = await db.get(User, user_id, populate_existing=True)
        memberships = await db.scalar(
            select(func.count())
            .select_from(RosterMember)
            .where(RosterMember.user_id == user_id, RosterMember.removed_at.is_(None))
        )
        notices = await db.scalar(
            select(func.count())
            .select_from(InAppNotification)
            .where(InAppNotification.user_id == user_id)
        )
    return user.role, user.is_active, memberships, notices


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


@pytest.mark.parametrize(
    "change",
    [{"role": "operator"}, {"role": "viewer"}, {"is_active": False}],
    ids=["to-operator", "to-viewer", "deactivate"],
)
@pytest.mark.parametrize("via", ["web", "api-token"])
async def test_you_cannot_demote_or_deactivate_yourself(
    app, client: AsyncClient, admin_headers, change, via
):
    me = await _admin(app)
    await _on_a_roster(app, me.id)
    headers = admin_headers
    if via == "api-token":
        token = await _create_token(client, admin_headers, name="ops", role="admin")
        headers = _bearer(token["token"])
    before = await _state(app, me.id)

    resp = await client.patch(f"/auth/users/{me.id}", json=change, headers=headers)

    assert resp.status_code == 400, resp.text
    assert resp.json()["detail"] == REFUSED
    assert await _state(app, me.id) == before == ("admin", True, 1, before[3])


async def test_you_can_still_edit_your_own_name_and_resend_your_role(
    app, client: AsyncClient, admin_headers
):
    me = await _admin(app)

    resp = await client.patch(
        f"/auth/users/{me.id}",
        json={
            "first_name": "Ada",
            "last_name": "Admin",
            "role": "admin",
            "is_active": True,
        },
        headers=admin_headers,
    )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert (body["first_name"], body["last_name"], body["role"]) == (
        "Ada",
        "Admin",
        "admin",
    )


async def test_another_admin_can_demote_and_deactivate_you(
    app, client: AsyncClient, admin_headers
):
    me = await _admin(app)
    await _on_a_roster(app, me.id)
    _, other_headers = await _other_admin(app, client)

    demoted = await client.patch(
        f"/auth/users/{me.id}", json={"role": "operator"}, headers=other_headers
    )
    deactivated = await client.patch(
        f"/auth/users/{me.id}", json={"is_active": False}, headers=other_headers
    )

    assert demoted.status_code == deactivated.status_code == 200
    role, active, memberships, notices = await _state(app, me.id)
    assert (role, active, memberships) == ("operator", False, 0)
    assert notices == 1  # the role-change notice
