"""M1-49 (N-02, O-18): while a person's temporary password must still change,
their session and every API token they created reach only their own profile,
the password change and MFA. Everything else, minting a token included,
answers 403 with ``password_change_required``, and their live streams are
refused. Changing the password opens the API again; nobody else is affected."""

from __future__ import annotations

import asyncio
import contextlib
import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from backend.api.routes.ws import notifications_stream, session_stream
from backend.db.models import ApiToken
from backend.db.repos import SessionRepo, UserMFARepo, UserRepo
from tests.test_api_tokens import (
    TEST_ORG_ID,
    _bearer,
    _create_token,
    _FakeWebSocket,
    admin_headers as _admin_headers_fixture,
    app as _app_fixture,
    client as _client_fixture,
)
from tests.test_tokens_stop_at_password_change import _works

app = pytest.fixture(_app_fixture.__wrapped__)
client = pytest.fixture(_client_fixture.__wrapped__)
admin_headers = pytest.fixture(_admin_headers_fixture.__wrapped__)

TEMPORARY = "temporary-pass-1"
CHOSEN = "chosen-pass-123"
REFUSAL = {
    "detail": "Change your temporary password before using OpsMender.",
    "code": "password_change_required",
}


async def _operator(
    client: AsyncClient, admin_headers, name: str, *, temporary: bool = True
) -> tuple[uuid.UUID, dict]:
    """An operator an admin created, signed in with the password they got."""
    created = await client.post(
        "/auth/users",
        headers=admin_headers,
        json={
            "username": name,
            "email": f"{name}@test.com",
            "role": "operator",
            "password": TEMPORARY,
            "require_password_change": temporary,
        },
    )
    assert created.status_code == 201, created.text
    assert created.json()["must_change_password"] is temporary
    login = await client.post(
        "/auth/login", json={"username": name, "password": TEMPORARY}
    )
    assert login.status_code == 200, login.text
    return uuid.UUID(created.json()["id"]), _bearer(login.json()["access_token"])


async def test_a_temporary_password_reaches_only_profile_password_and_mfa(
    app, client: AsyncClient, admin_headers
):
    user_id, headers = await _operator(client, admin_headers, "new-hire")

    incidents = await client.get("/incidents", headers=headers)
    assert incidents.status_code == 403
    assert incidents.json() == REFUSAL
    mint = await client.post(
        "/api/v1/api-tokens",
        json={"name": "new-hire-script", "role": "operator"},
        headers=headers,
    )
    assert mint.status_code == 403
    assert mint.json() == REFUSAL
    # Reading the profile is allowed, editing it is not.
    rename = await client.patch(
        "/auth/me", json={"first_name": "Renamed"}, headers=headers
    )
    assert rename.status_code == 403
    assert rename.json() == REFUSAL

    me = await client.get("/auth/me", headers=headers)
    assert me.status_code == 200, me.text
    assert me.json()["must_change_password"] is True
    assert me.json()["first_name"] is None
    mfa = await client.get("/auth/mfa/status", headers=headers)
    assert mfa.status_code == 200, mfa.text
    setup = await client.post("/auth/mfa/setup", headers=headers)
    assert setup.status_code == 200, setup.text

    async with app.state.session_factory() as db:
        minted = await db.scalars(
            select(ApiToken).where(ApiToken.created_by == user_id)
        )
        assert minted.all() == []
        pending = await UserMFARepo.get(db, user_id)
        assert pending is not None and pending.enabled_at is None


async def test_changing_the_temporary_password_opens_the_api(
    app, client: AsyncClient, admin_headers
):
    user_id, headers = await _operator(client, admin_headers, "first-day")
    assert (await client.get("/incidents", headers=headers)).status_code == 403

    changed = await client.post(
        "/auth/me/password",
        json={"current_password": TEMPORARY, "new_password": CHOSEN},
        headers=headers,
    )
    assert changed.status_code == 200, changed.text

    # The web app keeps the token the change returns; a new sign-in works too.
    kept = _bearer(changed.json()["access_token"])
    assert (await client.get("/incidents", headers=kept)).status_code == 200
    login = await client.post(
        "/auth/login", json={"username": "first-day", "password": CHOSEN}
    )
    assert login.status_code == 200, login.text
    fresh = _bearer(login.json()["access_token"])
    assert (await client.get("/incidents", headers=fresh)).status_code == 200
    async with app.state.session_factory() as db:
        user = await UserRepo.get_by_id(db, user_id)
        assert user.must_change_password is False


async def test_api_tokens_of_a_person_with_a_temporary_password_are_refused(
    app, client: AsyncClient, admin_headers
):
    """A token minted before the flag stays active but answers 403 while the
    flag is set. Another person's token and session keep working."""
    user_id, headers = await _operator(
        client, admin_headers, "scripted", temporary=False
    )
    theirs = await _create_token(client, headers, name="their-script", role="operator")
    mine = await _create_token(
        client, admin_headers, name="admin-script", role="operator"
    )
    assert await _works(client, theirs) == 200

    async with app.state.session_factory() as db:
        user = await UserRepo.get_by_id(db, user_id)
        user.must_change_password = True
        await db.commit()

    refused = await client.get("/incidents", headers=_bearer(theirs["token"]))
    assert refused.status_code == 403
    assert refused.json() == REFUSAL
    assert (await client.get("/incidents", headers=headers)).json() == REFUSAL
    async with app.state.session_factory() as db:
        row = await db.get(ApiToken, uuid.UUID(theirs["id"]))
        assert row is not None and row.revoked_at is None
    assert await _works(client, mine) == 200
    assert (await client.get("/incidents", headers=admin_headers)).status_code == 200


async def test_streams_refuse_a_temporary_password_session(
    app, client: AsyncClient, admin_headers
):
    _, headers = await _operator(client, admin_headers, "watcher")
    token = headers["Authorization"].removeprefix("Bearer ")
    async with app.state.session_factory() as db:
        ai_session = await SessionRepo.create(db, TEST_ORG_ID, tier=2, status="active")
        await db.commit()
        session_id = ai_session.id

    for open_stream in (
        lambda ws: session_stream(ws, session_id, token=token),
        lambda ws: notifications_stream(ws, token=token),
    ):
        ws = _FakeWebSocket()
        await asyncio.wait_for(open_stream(ws), timeout=2)
        assert ws.closed_code == 1008
        assert not ws.accepted

    # The same session stream opens for someone without a temporary password.
    admin_token = admin_headers["Authorization"].removeprefix("Bearer ")
    ws = _FakeWebSocket()
    task = asyncio.create_task(session_stream(ws, session_id, token=admin_token))
    for _ in range(100):
        if ws.accepted:
            break
        await asyncio.sleep(0.02)
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task
    assert ws.accepted
