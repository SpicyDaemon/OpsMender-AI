"""M1-42 (R-27): an open session or notification stream closes soon after
its account is deactivated or its password changes, and a session stream
serves only sessions in the caller's workspace. Viewers may follow sessions
(O-07)."""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from httpx import AsyncClient

from backend.api.routes import ws
from backend.db.models import Organization
from backend.db.repos import SessionRepo, UserRepo

# A file-backed database: each stream recheck reads through its own
# connection, as in production, instead of sharing the test's transaction.
from tests.test_ownership_lifecycle import (
    TEST_ORG_ID,
    _headers,
    _user,
    app as _app_fixture,
    client as _client_fixture,
)

app = pytest.fixture(_app_fixture.__wrapped__)
client = pytest.fixture(_client_fixture.__wrapped__)


class _Socket:
    def __init__(self) -> None:
        self.accepted = False
        self.closed_code: int | None = None
        self.sent: list[dict] = []

    async def accept(self) -> None:
        self.accepted = True

    async def close(self, code: int) -> None:
        self.closed_code = code

    async def send_json(self, message: dict) -> None:
        self.sent.append(message)


@pytest.fixture(autouse=True)
def _fast_recheck(monkeypatch):
    monkeypatch.setattr(ws, "RECHECK_SECONDS", 0.05)


async def _viewer(app, client: AsyncClient) -> tuple[uuid.UUID, str]:
    user_id = await _user(app, "stream-viewer", role="viewer")
    headers = await _headers(client, "stream-viewer")
    return user_id, headers["Authorization"].removeprefix("Bearer ")


async def _session(app, org_id: uuid.UUID = TEST_ORG_ID) -> uuid.UUID:
    async with app.state.session_factory() as db:
        session = await SessionRepo.create(db, org_id, tier=2, status="active")
        await db.commit()
        return session.id


async def _other_workspace_session(app) -> uuid.UUID:
    async with app.state.session_factory() as db:
        other = Organization(name="Other", slug=f"other-{uuid.uuid4().hex[:6]}")
        db.add(other)
        await db.commit()
        other_id = other.id
    return await _session(app, other_id)


async def _change(app, user_id: uuid.UUID, how: str) -> None:
    async with app.state.session_factory() as db:
        user = await UserRepo.get_by_id(db, user_id)
        if how == "deactivated":
            user.is_active = False
        else:  # a password change a second after the token was issued
            user.password_changed_at = datetime.now(timezone.utc) + timedelta(seconds=1)
        await db.commit()


async def test_a_viewer_follows_a_session_in_their_workspace(app, client: AsyncClient):
    _, token = await _viewer(app, client)
    socket = _Socket()
    task = asyncio.create_task(
        ws.session_stream(socket, await _session(app), token=token)
    )
    await asyncio.sleep(0.2)

    assert socket.accepted and socket.closed_code is None
    assert {"type": "ping", "data": {}} in socket.sent
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)


async def test_another_workspace_session_is_refused(app, client: AsyncClient):
    _, token = await _viewer(app, client)
    socket = _Socket()

    await asyncio.wait_for(
        ws.session_stream(socket, await _other_workspace_session(app), token=token),
        timeout=2,
    )

    assert not socket.accepted and socket.closed_code == 1008
    assert socket.sent == []


@pytest.mark.parametrize("how", ["deactivated", "password-changed"])
@pytest.mark.parametrize("stream", ["session", "notifications"])
async def test_a_stream_closes_when_its_account_stops_signing_in(
    app, client: AsyncClient, how, stream
):
    user_id, token = await _viewer(app, client)
    socket = _Socket()
    if stream == "session":
        running = ws.session_stream(socket, await _session(app), token=token)
    else:
        running = ws.notifications_stream(socket, token=token)
    task = asyncio.create_task(running)
    await asyncio.sleep(0.1)
    assert socket.accepted and socket.closed_code is None

    await _change(app, user_id, how)

    await asyncio.wait_for(task, timeout=2)
    assert socket.closed_code == ws.ACCOUNT_ENDED
